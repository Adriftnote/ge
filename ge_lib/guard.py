"""GE Guard — deterministic hooks that do not depend on the worker model (preflight / runtime_probe / acceptance_gate).

Why: a verify node can pass every rubric item while the real thing is broken. Seen in practice:
 - the checker's working folder could not reach the facts file, yet it passed the fact-check items;
 - the facts handed to the workers had a wrong denominator;
 - a feature nobody asked for was proposed;
 - a verify fail did not stop the next node.

This module makes the coordinator (ge), not the worker model, block those cases.
It does not add more rules to prompts: enforcement in code instead of instructions.

명세에 넣는 것:

  "contract": {
    "version": 1,
    "inputs": [ {"id": "facts", "path": "ge/x/facts.md", "mode": "inline|ref", "sha256": "<선택>",
                 "verified_by": "<선택: 이 숫자를 낸 명령>"} ],
    "scope": { "files": ["src/report.py", "src/report_view.py"],
               "tables": ["orders", ...],
               "approval_required_for": ["새 테이블", "새 지표"] },
    "runtime_checks": ["tests_pass", "latency"],
    "requires": { "<rubric item_id>": ["<check id>", ...] },
    "probe": {"script": "ge/x/probe.py", "timeout": 120}
  }

훅은 코디가 세 곳에서 부른다 (Run.run_node in ge):
  preflight        — 모델 호출 전. 입력 파일을 **실제로 읽고** 해시를 대조한다. 못 읽으면 노드 시작 안 한다.
  runtime_probe    — 생산 노드 산출 뒤. probe 스크립트를 격리 실행해 checks[] 를 남긴다.
  acceptance_gate  — verify 산출 뒤. verify fail/unverifiable, probe 실패, 범위 밖 파일/테이블, requires 매핑을 본다.
                     하나라도 걸리면 run_aborted. 승인은 `ge approve <run> <item>` 으로 원장에 남긴다.

산출은 run_dir/artifacts/guard-<phase>[-<node>].json. 성공은 조용하고 실패는 이유를 전부 적는다.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

from .schema import validate


def _rt(path, encoding="utf-8", errors=None, newline=None):
    """Read a text file and close it."""
    with open(path, encoding=encoding, errors=errors, newline=newline) as f:
        return f.read()


CHECK_IDS = ("row_count", "uncollected_rate", "latency")
INLINE_MAX = 60_000          # 이보다 큰 입력은 inline 금지 — 워커 문맥을 먹는다. ref 로 바꿔라.
PROBE_SCHEMA = {
    "type": "object", "required": ["checks"],
    "properties": {"checks": {"type": "array", "minItems": 1, "items": {
        "type": "object", "required": ["id", "ok"],
        "properties": {"id": {"type": "string", "minLength": 1}, "ok": {"type": "boolean"}}}}}}


def sha256_file(path):
    """줄끝을 LF 로 정규화한 뒤 해시한다.

    왜: 해시를 git blob(LF)에서 땄는데 Windows 체크아웃은 CRLF 라 같은 파일이 다른 해시가 됐다.
    preflight 가 내용이 같은 입력을 막았다. 내용 동일성만 보면 되므로 CRLF -> LF 로 맞춘다.
    """
    with open(path, "rb") as f:
        data = f.read()
    return sha256_bytes(data)


def sha256_bytes(data):
    """줄끝 정규화 해시. 텍스트 입력의 동일성만 본다 (CRLF/LF 차이는 같은 내용)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _abs(ws, p):
    return p if os.path.isabs(p) else os.path.join(ws, p)


def validate_contract(spec, ws):
    """load_spec 이 부른다. 형식 오류는 AssertionError — 명세 로드 단계에서 죽인다(런 시작 전)."""
    c = spec.get("contract")
    if c is None:
        return None
    assert isinstance(c, dict), "contract 는 객체"
    assert c.get("version") == 1, "contract.version 은 1"
    ids = set()
    for it in c.get("inputs", []):
        assert isinstance(it, dict) and it.get("id") and it.get("path"), "contract.inputs[]: id, path 필요"
        assert it["id"] not in ids, f"contract.inputs 중복 id {it['id']}"
        ids.add(it["id"])
        mode = it.get("mode", "ref")
        assert mode in ("inline", "ref"), f"contract.inputs[{it['id']}].mode 는 inline|ref"
        if it.get("sha256"):
            assert re.fullmatch(r"[0-9a-f]{64}", it["sha256"]), f"contract.inputs[{it['id']}].sha256 형식"
    scope = c.get("scope") or {}
    for k in ("files", "tables", "approval_required_for"):
        v = scope.get(k, [])
        assert isinstance(v, list) and all(isinstance(x, str) for x in v), f"contract.scope.{k} 는 문자열 목록"
    for chk in c.get("runtime_checks", []):
        assert isinstance(chk, str), "contract.runtime_checks 는 문자열 목록"
    # record_only: 재기는 하되 낙제시키지 않는 검사. 폭주(행 수·시간)만 apply 를 막고
    # 신선도 같은 품질 신호는 기록만 남긴다.
    ro = c.get("record_only", [])
    assert isinstance(ro, list) and all(isinstance(x, str) for x in ro), "contract.record_only 는 문자열 목록"
    extra = sorted(set(ro) - set(c.get("runtime_checks", [])))
    assert not extra, f"contract.record_only 는 runtime_checks 안에 있어야 한다: {extra}"
    req = c.get("requires") or {}
    assert isinstance(req, dict), "contract.requires 는 {item_id: [check_id]}"
    for item, checks in req.items():
        assert isinstance(checks, list) and checks, f"contract.requires[{item}] 는 비어있지 않은 목록"
    # mechanical: probe 가 이미 재는 rubric 항목. LLM verify 에 안 보내고 probe 결과로 채운다.
    mech = c.get("mechanical", [])
    assert isinstance(mech, list) and all(isinstance(x, str) for x in mech), "contract.mechanical 은 문자열 목록"
    extra = sorted(set(mech) - set(req))
    assert not extra, f"contract.mechanical 은 requires 의 항목이어야 한다 (대응 검사가 있어야 채운다): {extra}"
    probe = c.get("probe")
    if probe is not None:
        assert isinstance(probe, dict) and probe.get("script"), "contract.probe.script 필요"
        assert os.path.isfile(_abs(ws, probe["script"])), f"contract.probe.script 없음: {probe['script']}"
    if c.get("runtime_checks") and probe is None:
        raise AssertionError("contract.runtime_checks 가 있으면 contract.probe 도 있어야 한다 (실행 없이 pass 금지)")
    return c


# ---------------- preflight ----------------
def preflight(contract, node, ws):
    """입력 문서를 노드가 실제로 읽을 수 있는지 **읽어서** 본다. 존재 검사만으로는 부족하다.
    반환 {ok, inputs:[{id, path, resolved, readable, sha256, sha_ok, bytes, mode, verified}], problems:[...]}."""
    out = {"phase": "preflight", "node": node.get("id"), "ok": True, "inputs": [], "problems": []}
    if not contract:
        return out
    cwd = node.get("cwd")
    for it in contract.get("inputs", []):
        rec = {"id": it["id"], "path": it["path"], "mode": it.get("mode", "ref"),
               "verified": bool(it.get("verified_by"))}
        # 1) 코디(WS) 기준으로 읽는다 — 이게 정본이다
        ws_path = _abs(ws, it["path"])
        try:
            with open(ws_path, "rb") as f:
                data = f.read()
            rec["resolved"] = ws_path
            rec["bytes"] = len(data)
            rec["sha256"] = sha256_bytes(data)
        except OSError as e:
            rec["readable"] = False
            out["problems"].append(f"{it['id']}: 코디가 읽지 못함 {ws_path}: {e.strerror or e}")
            out["inputs"].append(rec)
            continue
        rec["readable"] = True
        if it.get("sha256"):
            rec["sha_ok"] = rec["sha256"] == it["sha256"]
            if not rec["sha_ok"]:
                out["problems"].append(f"{it['id']}: sha256 불일치 (명세 {it['sha256'][:12]} vs 실제 {rec['sha256'][:12]}) — 문서가 바뀌었다. 명세를 갱신하라")
        # 2) ref 모드는 워커가 자기 cwd 에서 그 상대경로로 닿아야 한다
        if rec["mode"] == "ref" and cwd and not os.path.isabs(it["path"]):
            cwd_path = os.path.join(cwd, it["path"])
            rec["cwd_resolved"] = cwd_path
            rec["cwd_readable"] = os.path.isfile(cwd_path)
            if not rec["cwd_readable"]:
                out["problems"].append(f"{it['id']}: 워커 cwd 기준으로 못 읽음 {cwd_path} — mode=inline 으로 바꾸거나 절대경로를 줘라")
        if rec["mode"] == "inline" and rec["bytes"] > INLINE_MAX:
            out["problems"].append(f"{it['id']}: inline 이 {rec['bytes']}B — {INLINE_MAX}B 초과. ref 로 바꾸고 절대경로를 줘라")
        if not rec["verified"]:
            rec["note"] = "verified_by 없음 — 이 문서의 숫자는 미검증으로 워커에게 표시된다"
        out["inputs"].append(rec)
    out["ok"] = not out["problems"]
    return out


def render_inputs(contract, ws, pre=None):
    """워커 프롬프트 끝에 붙일 입력 블록. inline 은 본문을, ref 는 절대경로+해시를. 미검증 표시를 붙인다.
    모델이 무엇이든 같은 텍스트를 받는다."""
    if not contract or not contract.get("inputs"):
        return ""
    parts = ["\n\n## 계약 입력 (코디가 확인한 문서. 이 밖의 사실을 지어내지 마라)\n"]
    for it in contract["inputs"]:
        p = _abs(ws, it["path"])
        tag = "" if it.get("verified_by") else " [미검증: 이 문서의 수치는 확인 명령이 없다. 근거로 쓰면 unverified 로 표시하라]"
        if it.get("mode", "ref") == "inline":
            try:
                body = _rt(p, encoding="utf-8")
            except OSError:
                body = "(읽기 실패)"
            parts.append(f"### 입력 {it['id']} — {it['path']}{tag}\n\n{body.strip()}\n")
        else:
            sha = ""
            try:
                sha = sha256_file(p)[:12]
            except OSError:
                pass
            parts.append(f"### 입력 {it['id']} — 절대경로 {p} (sha256 {sha}){tag}\n")
    scope = contract.get("scope") or {}
    if scope:
        parts.append("## 범위 (계약)\n")
        if scope.get("files"):
            parts.append("- 손대도 되는 파일: " + ", ".join(scope["files"]))
        if scope.get("tables"):
            parts.append("- 써도 되는 테이블: " + ", ".join(scope["tables"]))
        if scope.get("approval_required_for"):
            parts.append("- 승인 없이는 금지: " + ", ".join(scope["approval_required_for"]))
        parts.append("- 범위 밖이 필요하면 만들지 말고 risks[] 에 '승인 필요: ...' 로 적어라.\n")
    return "\n".join(parts)


# ---------------- runtime probe ----------------
def _last_meaningful_line(s):
    """트레이스백의 마지막 의미 있는 줄 (예: shutil.Error: ...)."""
    for line in reversed((s or "").splitlines()):
        line = line.strip()
        if line and not line.startswith(("File \"", "  ", "Traceback")):
            return line[:300]
    return None


def _salvage_json(stdout):
    """stdout 전체가 JSON 이 아니어도 마지막 JSON 한 줄은 건진다 (경고·로그가 섞인 경우)."""
    s = (stdout or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass
    for line in reversed(s.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return None


def run_probe(contract, run_dir, ws, timeout=None, baseline=False):
    """probe 스크립트를 [python, script, run_dir] 로 돌린다. stdout JSON {checks:[{id, ok, ...}]}.
    시간 제한을 넘기면 그 자체가 실패다 — 42초짜리 실행을 되풀이하지 않는다."""
    out = {"phase": "runtime_probe", "ok": False, "checks": [], "problems": []}
    probe = (contract or {}).get("probe")
    if not probe:
        out["problems"].append("contract.probe 없음")
        return out
    script = _abs(ws, probe["script"])
    t = timeout or probe.get("timeout", 120)
    t0 = time.time()
    try:
        ge_home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # ge checkout (ge_lib/guard.py -> 2 up)
        r = subprocess.run([sys.executable, script, run_dir], cwd=probe.get("cwd") or ws, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=t,
                           env={**os.environ, "GE_HOME": ge_home, "GE_PROJ": ws,
                                                      **({"GE_PROBE_BASELINE": "1"} if baseline else {})})
    except subprocess.TimeoutExpired:
        out["problems"].append(f"probe timeout {t}s — 실행 자체가 상한을 넘는다")
        out["duration_s"] = round(time.time() - t0, 1)
        return out
    out["duration_s"] = round(time.time() - t0, 1)
    out["exit"] = r.returncode
    payload = _salvage_json(r.stdout)
    if payload is None:
        # probe 가 트레이스백으로 죽었다. "JSON 이 아님" 만 남기면 원인을 못 짚는다
        # 여기서 checks 를 합성해
        # 어떤 검사가 왜 못 쟀는지 남긴다 — 실행 없이 pass 는 여전히 없다.
        tail = _last_meaningful_line(r.stderr) or _last_meaningful_line(r.stdout) or "(출력 없음)"
        out["problems"].append(f"probe 가 JSON 을 내지 않았다 (exit {r.returncode}): {tail}")
        out["checks"] = [{"id": cid, "ok": False, "why": tail}
                         for cid in contract.get("runtime_checks", [])]
        out["stderr_tail"] = (r.stderr or "")[-600:]
        return out
    errs = validate(payload, PROBE_SCHEMA)
    if errs:
        out["problems"] += ["probe 출력 형식: " + e for e in errs]
        return out
    out["checks"] = payload["checks"]
    want = set(contract.get("runtime_checks", []))
    got = {c["id"] for c in out["checks"]}
    missing = sorted(want - got)
    if missing:
        out["problems"].append("probe 가 안 낸 검사: " + ", ".join(missing))
    record_only = set(contract.get("record_only", []))
    out["recorded"] = []
    for c in out["checks"]:
        if not c.get("ok"):
            detail = json.dumps({k: v for k, v in c.items() if k not in ('id', 'ok')}, ensure_ascii=False)
            if c["id"] in record_only:
                # 기록만. 낙제 아님 — 원장·artifact 에는 남고 problems 에는 안 들어간다.
                out["recorded"].append(f"기록 {c['id']}: " + detail)
            else:
                out["problems"].append(f"검사 실패 {c['id']}: " + detail)
    if r.returncode != 0:
        out["problems"].append(f"probe exit {r.returncode}")
    out["ok"] = not out["problems"]
    return out


# ---------------- scope (범위) ----------------
_SQL_TABLE = re.compile(r"\b(?:from|join|into|update)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?", re.I)


def _norm_file(p):
    return p.replace("\\", "/").lstrip("./").lower()


_SQL_ALIAS = re.compile(r"\)\s*(?:as\s+)?`?([A-Za-z_][A-Za-z0-9_]*)`?", re.I)
_SQL_TABLE_ALIAS = re.compile(r"\b(?:from|join)\s+`?[A-Za-z_][A-Za-z0-9_]*`?\s+(?:as\s+)?`?([A-Za-z_][A-Za-z0-9_]*)`?", re.I)
_SQL_WORDS = {"select", "where", "on", "using", "inner", "left", "right", "outer", "join", "group",
              "order", "having", "limit", "set", "values", "as", "and", "or", "straight_join", "cross"}


_COMMENT = re.compile(r"(?:/\*.*?\*/)|(?://|#|--)[^\\\n]*(?:\\n|$)", re.S)


def _strip_comments(blob):
    """주석을 지운 뒤 테이블을 찾는다.

    왜: 워커가 쓴 주석 `// ... INNER JOIN preview ...` 의 `preview` 를
    테이블로 읽어 범위 밖이라며 런을 막았다. 주석은 실행되지 않는다.
    """
    return _COMMENT.sub(" ", blob)


def _sql_aliases(blob):
    """파생 테이블·별칭 이름을 모은다 — 실제 테이블이 아니다.

    왜: `JOIN ( SELECT ... ) preview` 의 `preview` 를 테이블로 읽어
    acceptance_gate 가 범위 밖이라며 런을 막았다. 별칭은 범위 판정 대상이 아니다.
    """
    out = {m.group(1).lower() for m in _SQL_ALIAS.finditer(blob)}
    out |= {m.group(1).lower() for m in _SQL_TABLE_ALIAS.finditer(blob)}
    return out - _SQL_WORDS


def scope_violations(contract, produced):
    """생산 노드 산출(patches[].file, new_files[].file, SQL 본문)을 계약 범위와 대조한다. 결정적."""
    scope = (contract or {}).get("scope") or {}
    files_ok = {_norm_file(f) for f in scope.get("files", [])}
    tables_ok = {t.lower() for t in scope.get("tables", [])}
    viol = []
    for nid, art in produced.items():
        if not isinstance(art, dict) or art.get("skipped"):
            continue
        touched = []
        for key in ("patches", "new_files"):
            for it in art.get(key) or []:
                if isinstance(it, dict) and it.get("file"):
                    touched.append(it["file"])
        if files_ok:
            for f in touched:
                nf = _norm_file(f)
                if nf not in files_ok and not any(nf.endswith("/" + ok) or nf == ok for ok in files_ok):
                    viol.append({"kind": "file", "node": nid, "item": f})
        if tables_ok:
            blob = _strip_comments(json.dumps(art, ensure_ascii=False))
            aliases = _sql_aliases(blob)
            for tbl in sorted({m.group(1).lower() for m in _SQL_TABLE.finditer(blob)}):
                if tbl in aliases or tbl in _SQL_WORDS:
                    continue
                if tbl not in tables_ok:
                    viol.append({"kind": "table", "node": nid, "item": tbl})
    return viol


# ---------------- approvals ----------------
def approvals(run_dir):
    p = os.path.join(run_dir, "approvals.jsonl")
    out = set()
    if os.path.isfile(p):
        for line in _rt(p, encoding="utf-8").splitlines(True):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("item"):
                out.add(d["item"])
    return out


def approve(run_dir, item, who=None, note=None):
    """사람이 범위 밖 항목을 승인한다. 원장(run_dir/approvals.jsonl)에 남는다 — 명세를 고치지 않는다."""
    p = os.path.join(run_dir, "approvals.jsonl")
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "item": item, "who": who or os.environ.get("USER") or "human",
           "note": note}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def viol_key(v):
    return f"{v['kind']}:{v['item']}"


# ---------------- acceptance gate ----------------
def acceptance_gate(contract, run_dir, produced, verify_payload, probe_result, preflight_results):
    """차단 규칙 (하나라도 걸리면 ok=False):
      1) verify verdict 에 fail 또는 unverifiable 이 있다
      2) requires 에 적힌 rubric 항목이 pass 인데 대응 probe 검사가 실패/부재다  (코드만 읽고 pass 한 것)
      3) runtime_checks 가 있는데 probe 결과가 없다
      4) 범위 밖 파일/테이블이 승인 없이 있다 -> needs_approval
      5) preflight 문제가 남아 있다
    verify verdict 자체는 고치지 않는다. 별도 산출(guard-acceptance.json)이다."""
    out = {"phase": "acceptance_gate", "ok": True, "blocks": [], "needs_approval": [], "approved": []}
    vs = (verify_payload or {}).get("verdicts") or []
    bad = [v for v in vs if v.get("verdict") in ("fail", "unverifiable")]
    if not vs:
        out["blocks"].append({"rule": "verify_missing", "why": "verify verdict 가 없다"})
    for v in bad:
        out["blocks"].append({"rule": "verify_" + v["verdict"], "item": v.get("item_id"), "why": (v.get("reason") or "")[:200]})
    if contract:
        checks = {c["id"]: c for c in (probe_result or {}).get("checks", [])}
        if contract.get("runtime_checks"):
            if probe_result is None:
                out["blocks"].append({"rule": "probe_missing", "why": "runtime_checks 가 있는데 probe 를 안 돌렸다"})
            elif not probe_result.get("ok"):
                for p in probe_result.get("problems", []):
                    out["blocks"].append({"rule": "probe_failed", "why": p})
        passed = {v.get("item_id") for v in vs if v.get("verdict") == "pass"}
        for item, need in (contract.get("requires") or {}).items():
            if item in passed:
                for cid in need:
                    c = checks.get(cid)
                    if c is None:
                        out["blocks"].append({"rule": "requires_unmet", "item": item, "check": cid,
                                              "why": f"verify 가 {item} 을 pass 했지만 검사 {cid} 결과가 없다 — 실행 없이 통과시켰다"})
                    elif not c.get("ok") and cid not in set(contract.get("record_only", [])):
                        out["blocks"].append({"rule": "requires_failed", "item": item, "check": cid,
                                              "why": f"verify 가 {item} 을 pass 했지만 실행 검사 {cid} 는 실패다"})
        ok_items = approvals(run_dir)
        for v in scope_violations(contract, produced):
            k = viol_key(v)
            (out["approved"] if k in ok_items else out["needs_approval"]).append({**v, "key": k})
        if out["needs_approval"]:
            out["blocks"].append({"rule": "scope", "why": "범위 밖 항목 %d개 — `ge approve <run-dir> <key>` 로 승인하거나 빼라" % len(out["needs_approval"]),
                                  "items": [x["key"] for x in out["needs_approval"]]})
        for pre in preflight_results or []:
            for p in pre.get("problems", []):
                out["blocks"].append({"rule": "preflight", "node": pre.get("node"), "why": p})
    out["ok"] = not out["blocks"]
    return out


# ---------------- mechanical 항목 (LLM verify 에서 빼고 probe 로 채운다) ----------------
def strip_mechanical(rubric_text, contract):
    """rubric 본문에서 mechanical 항목 줄(과 그 들여쓴 이어지는 줄)을 뺀다. 형식: `3. id — ...` / `id: ...` / `- id ...`."""
    mech = set((contract or {}).get("mechanical") or [])
    if not mech:
        return rubric_text
    alt = "|".join(re.escape(m) for m in mech)
    head = re.compile(r"^\s*(?:\d+[.)]\s*|[-*]\s*)?(?:%s)(?![\w-])\s*(?:[—:\-]|$)" % alt)
    out, skipping = [], False
    for line in rubric_text.splitlines():
        if head.match(line):
            skipping = True
            continue
        if skipping and line[:1] in (" ", "\t") and line.strip():
            continue
        skipping = False
        out.append(line)
    return "\n".join(out)


def mechanical_verdicts(contract, probe_result):
    """mechanical 항목마다 requires 의 검사가 모두 ok 면 pass. 없거나 실패면 fail (record_only 는 낙제 아님)."""
    if not contract:
        return []
    checks = {c["id"]: c for c in (probe_result or {}).get("checks", [])}
    rec = set(contract.get("record_only", []))
    out = []
    for item in contract.get("mechanical") or []:
        bad = []
        for cid in contract["requires"][item]:
            c = checks.get(cid)
            if c is None:
                bad.append(f"{cid}: 결과 없음")
            elif not c.get("ok") and cid not in rec:
                bad.append(f"{cid}: {str(c.get('why', '실패'))[:120]}")
        out.append({"item_id": item, "verdict": "fail" if bad else "pass",
                    "reason": ("probe 실패 — " + "; ".join(bad)) if bad else "probe 검사 통과 (mechanical)"})
    return out
