"""Scaffold a GE spec (`ge new`). The same steps were done by hand often enough to move them into code.

What the scaffold forces — each was skipped once and caused a failure:
  1) the contract goes **inside each worker prompt** (workers never talk to each other)
  2) verify is a **separate node**, ideally a different model lineage (set its executor/model)
  3) apply/render is a **script node** — anything countable is not given to a model
  4) a lessons slot only for producing nodes; never for the checker
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPHS = os.environ.get("GE_GRAPHS_DIR", "graphs")    # folder under --root that holds specs
WORKFLOWS = os.path.join(HERE, "workflows")

PATCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "patches", "risks", "test_plan", "evidence"],
    "properties": {
        "summary": {"type": "string", "minLength": 20},
        "patches": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["file", "old_str", "new_str", "why"],
            "properties": {"file": {"type": "string"}, "old_str": {"type": "string", "minLength": 10},
                           "new_str": {"type": "string"}, "why": {"type": "string", "minLength": 10}}}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "test_plan": {"type": "string", "minLength": 20},
        "evidence": {"type": "array", "minItems": 3, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["claim", "file", "line_hint"],
            "properties": {"claim": {"type": "string"}, "file": {"type": "string"},
                           "line_hint": {"type": "string"}}}}}}

PROMPT_TPL = """너는 이 폴더의 수정안을 내는 사람이다. 파일을 고치지 마라 — **패치안만** JSON 으로 낸다.

대상 파일은 하나뿐이다: `{target}`
절대 경로: `{cwd}/{target}`
`patches[].file` 에는 **네 작업 폴더(cwd) 기준 상대경로**를 적는다 — 여기서는 `{target}`.
레포 루트 기준으로 적으면 경로가 겹쳐 적용이 실패한다. 절대경로는 적용 스크립트가 거부한다.

## 왜 (실측만 적는다. 추정은 '추정'이라고 쓴다)
TODO — 무엇이 어떻게 틀렸는지, 숫자로.

## 요구
TODO — 1, 2, 3.

## 금지 (하나라도 건드리면 불합격)
TODO — 건드리면 안 되는 함수·상수·기본값을 이름으로.

{contract}
## 형식
`patches[].old_str` 은 **그 파일에 정확히 한 번만 나오는 문자열**이어야 한다(공백·들여쓰기까지 원문 그대로).
짧으면 중복되니 앞뒤 줄을 충분히 포함하라. `new_str` 은 그 자리를 대체할 전체 문자열.
`evidence` 에는 네가 열어 확인한 근거를 파일·검색가능한 문자열로 최소 3개.
못 하겠으면 지어내지 말고 `summary` 에 `보류: 이유` 를 적어라.

## 일하는 법 (토큰·왕복을 줄인다)
- 결과가 같으면 파일 전체를 다시 쓰지 말고 **그 자리만** 고쳐라. 패치는 작을수록 좋다.
- 다음에 읽어야 할 파일을 먼저 속으로 목록화하고, 서로 의존하지 않는 읽기는 **한 번에 같이** 요청하라. 한 턴에 하나씩 읽지 마라.
- 지시에 없는 버그·성능·리팩터를 발견해도 **고치지 말고** `risks` 에 "후속: …" 로만 적어라. 지시가 모호하면 원문과 주변 코드가 가장 직접 지지하는 해석 하나로 구현하고 `summary` 에 그 가정을 적어라. 다른 해석까지 같이 만들지 마라.
- 되묻지 마라. 사람은 실시간으로 보고 있지 않다. 요청에서 따라 나오는 가역적 판단은 스스로 내리고, 파괴적이거나 범위가 바뀌는 것만 `보류` 로 남겨라.
"""

CONTRACT_TPL = """## 계약 (코디가 미리 정했다 — 바꾸지 마라)
워커끼리 통신하지 않는다. 이름·형식·값은 여기서만 온다.

TODO — 키 이름, 형식, 계산식, 칸 수 같은 '둘 이상이 맞춰야 하는 것'.

"""

RUBRIC_TPL = """1. anchor_unique — 각 `old_str` 이 그 파일에 **정확히 한 번** 나오는가. 공백·들여쓰기까지 원문과 같은가.
2. file_scope — 지정한 파일만 건드렸는가. 남의 파일을 건드렸으면 fail.
3. does_what_asked — 지시한 동작을 실제로 하는가. 주변 코드의 이름·들여쓰기와 맞물리는가.
4. forbidden_untouched — 금지 목록을 건드리지 않았는가. 건드렸으면 무엇인지 적는다.
5. failure_policy — 실패할 때 이 일에 정한 실패 정책(멈춤 / 건너뜀 / 재시도)을 따르는가. 조용히 삼키지 않는가.
6. evidence_real — evidence 의 file/line_hint 를 열어 그 내용이 실제로 있는가. 지어낸 게 있으면 fail.
7. test_plan — 외부 서비스·운영 환경을 부르지 않고 확인할 방법이 들어 있는가.
8. standing_axes — 아래 여섯 축을 **하나씩** 보고 한 줄로 적는다. 해당 없으면 "해당 없음"이라고 쓰되 빈칸으로 두지 마라.
   (Fabric `review_code` 의 축 목록에서 빌려왔다 — 우리 rubric 은 일마다 달라서 상시 축이 없었고,
    그래서 보안·성능·경계값을 **한 번도 안 봤다**.)
   · 정확성(버그·논리 오류·경쟁 조건)  · 보안(주입·비밀 노출·권한)  · 성능(불필요한 조회·N+1·큰 루프)
   · 가독성(이름·주석이 주변과 맞물리나)  · 관용구(그 언어·그 레포의 방식인가)  · 오류·경계값(빈 결과·null·0건·실패 경로)
"""

VERIFY_TPL = """검토자다. 아래 패치안을 rubric 으로 판정한다. 같은 폴더의 원본 파일을 직접 열어 대조하라. 파일을 고치지는 마라.

item_id 는 rubric 의 키로 하나씩 낸다. **하나도 빼지 마라.** 이상 없는 것도 한 줄씩 적어라 —
안 적으면 *안 본 것*과 *문제 없는 것*이 같아진다.
근거 없이 pass 주지 마라. 파일에서 확인 못 하면 `unverifiable` 이고 왜 못 봤는지 적는다.

rubric:
{{{{rubric}}}}

패치안:
{{{{input.{patch_node}}}}}
"""


APPLY_TPL = '''"""{name} apply — generated. Writes verified patches to a COPY: a linked git worktree given as GE_SRC.
The worker read the original ({cwd}); this script never writes there. Example:
    git -C {cwd} worktree add ../ge-{name} && GE_SRC=../ge-{name} ge run <spec>
Without GE_SRC it stops (body: ge_lib/apply_worktree.py)."""
import os, sys

sys.path.insert(0, os.environ["GE_HOME"])
from ge_lib.apply_worktree import run

run(sys.argv[1], src=os.environ.get("GE_SRC"), patch_nodes={patch_nodes!r}, scope={scope!r})
'''


# probe 템플릿. GE_PROBE_BASELINE=1(ge run 의 baseline) 이거나 patch.json 이 없으면 **원본 전체**를 검사한다.
# 왜: baseline 에서 토큰 검사를 건너뛰면 원본에 이미 있던 금지 토큰을 패치 뒤에야 잡아 워커 시간을 버린다.
PROBE_TPL = '''"""{name} 실행 검사. 호출: python <이 파일> <run_dir>  -> stdout JSON {{"checks":[{{"id","ok","why"}}]}}
원본(SRC)은 안 건드린다. patch.json 이 없거나 GE_PROBE_BASELINE=1 이면 원본 전체를 검사한다."""
import json, os, sys

SRC = os.environ.get("GE_SRC", r"{cwd}")
TARGET = "{target}"
FORBIDDEN = []   # TODO: 이 일에서 들어가면 안 되는 토큰. 원본에 이미 있으면 baseline 이 facts 오류로 멈춘다 -> 목록을 좁혀라
REQUIRED = []    # TODO: 패치 뒤에 있어야 하는 토큰 (원본 검사에선 안 본다)

run_dir = sys.argv[1] if len(sys.argv) > 1 else ""
plan_path = os.path.join(run_dir, "artifacts", "patch.json") if run_dir else ""
baseline = os.environ.get("GE_PROBE_BASELINE") == "1" or not os.path.isfile(plan_path)
with open(os.path.join(SRC, TARGET), encoding="utf-8") as f:
    orig = f.read()
body, added = orig, ""
if not baseline:
    with open(plan_path, encoding="utf-8") as f:
        patches = json.load(f).get("patches", [])
    for it in patches:
        if orig.count(it["old_str"]) != 1:
            print(json.dumps({{"checks": [{{"id": "contract_tokens", "ok": False, "why": "anchor %d회" % orig.count(it["old_str"])}}]}}))
            sys.exit(0)
        body = body.replace(it["old_str"], it["new_str"], 1)
        added += it["new_str"] + "\\n"
scan = orig if baseline else added                     # baseline: 원본 전체 / 패치 뒤: 추가된 줄만
bad = [t for t in FORBIDDEN if t in scan]
miss = [] if baseline else [t for t in REQUIRED if t not in body]
ok = not bad and not miss
why = "ok" if ok else "금지 토큰=%s 없음=%s%s" % (bad, miss, " (원본에 이미 있음)" if baseline and bad else "")
print(json.dumps({{"checks": [{{"id": "contract_tokens", "ok": ok, "why": why}}]}}, ensure_ascii=False))
'''


def load_workflow(kind):
    """ge_lib/workflows/<kind>.json — 자주 쓰는 노드 배치. 없으면 목록을 알려주고 죽는다."""
    d = WORKFLOWS
    p = os.path.join(d, kind + ".json")
    if not os.path.isfile(p):
        have = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
        raise SystemExit(f"그런 뼈대 없다: {kind}. 있는 것: {', '.join(have)}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def workflows():
    d = WORKFLOWS
    out = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            w = json.load(fh)
        out.append({"name": f[:-5], "doc": w.get("doc", ""),
                    "nodes": [f"{n['id']}({n['kind']})" for n in w.get("nodes", [])]})
    return out


FABRIC_TPL = """# Fabric 먼저 보기 — {name}

새 프롬프트를 쓰기 전에 빌릴 뼈대가 있는지 본다 (Fabric: github.com/danielmiessler/fabric, MIT).
Fabric 은 뼈대를 빌리는 곳이지 그대로 실행하는 곳이 아니다. 1-100 같은 감사 불가능한 점수 형식은 가져오지 않는다.

0. 처음 한 번 `ge fabric --sync` 로 로컬 manifest 를 만든다.
1. `ge fabric <영어 키워드>` 로 후보를 좁힌다 (purpose 가 영어다. 비랭킹 grep).
2. 후보의 purpose/본문(`ge fabric --show <이름>`)을 읽고 의미로 고른다.
3. 아래 두 줄을 채운다. 빌릴 게 없으면 `선택: 없음` 과 이유를 쓴다. GE_REQUIRE_FABRIC=1 이면 이 줄이 비었을 때 `ge run` 이 거부한다.

찾은 키워드:
선택:
이유:
"""


def new_spec(name, cwd, target, patch_node="patch", apply_script=None, force=False, workflow="patch", root="."):
    """<root>/graphs/<name>.json 과 <root>/graphs/<name>/{prompt,rubric,verify,probe,apply,fabric}.* 를 만든다. 이미 있으면 안 덮는다.

    apply_script 를 안 주면 <명세>/apply.py 를 자동 생성한다 (worktree 직접 적용, ge_lib/apply_worktree.py).
    workflow: ge_lib/workflows/<이름>.json 의 노드 배치 (기본 patch). `ge workflows` 로 목록."""
    root = os.path.abspath(root)
    d = os.path.join(root, GRAPHS, name)
    spec_path = os.path.join(root, GRAPHS, name + ".json")
    if os.path.exists(spec_path) and not force:
        raise SystemExit("이미 있다: " + spec_path)
    os.makedirs(d, exist_ok=True)
    wf = load_workflow(workflow)
    model_nodes = [n["id"].format(patch_node=patch_node) if "{" in n["id"] else n["id"]
                   for n in wf["nodes"] if n["kind"] == "model"]
    if workflow == "patch":
        model_nodes = [patch_node]
    files = {"rubric.md": RUBRIC_TPL,
             "verify.md": VERIFY_TPL.format(patch_node=", ".join(model_nodes)),
             "probe.py": PROBE_TPL.format(name=name, cwd=cwd.rstrip("/"), target=target)}
    if apply_script is None and workflow != "research":
        apply_script = f"{GRAPHS}/{name}/apply.py"
        files["apply.py"] = APPLY_TPL.format(name=name, cwd=cwd.rstrip("/"), patch_nodes=tuple(model_nodes), scope=[target])
    apply_script = apply_script or f"{GRAPHS}/{name}/tally.py"
    for mn in model_nodes:
        files[mn + ".md"] = PROMPT_TPL.format(target=target, cwd=cwd.rstrip("/"), contract=CONTRACT_TPL)
    files["fabric.md"] = FABRIC_TPL.format(name=name)
    written = []
    for fn, body in files.items():
        p = os.path.join(d, fn)
        if os.path.exists(p) and not force:
            continue
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        written.append(os.path.relpath(p, root).replace("\\", "/"))

    def _ids(lst):
        return [x.format(patch_node=patch_node) if "{" in x else x for x in (lst or [])]

    nodes = []
    for n in wf["nodes"]:
        nid = n["id"].format(patch_node=patch_node) if "{" in n["id"] else n["id"]
        if workflow == "patch" and n["kind"] == "model":
            nid = patch_node
        if n["kind"] == "model":
            nodes.append({"id": nid, "kind": "model", "cwd": cwd,
                          "prompt": f"{GRAPHS}/{name}/{nid}.md", "inputs": _ids(n.get("inputs")),
                          "retry": 2, "timeout": 1200, "schema": PATCH_SCHEMA})
        elif n["kind"] == "verify":
            nodes.append({"id": nid, "kind": "verify", "cwd": cwd, "prompt": f"{GRAPHS}/{name}/verify.md",
                          "rubric": f"{GRAPHS}/{name}/rubric.md",
                          "inputs": _ids(n.get("inputs")) or model_nodes, "timeout": 1800})
        else:
            node = {"id": nid, "kind": "script", "script": n.get("script") or apply_script,
                    "inputs": _ids(n.get("inputs")) or (model_nodes + ["verify"])}
            if n.get("apply"):
                node["apply"] = True           # load_spec requires a verify input; the run blocks it unless verify passed
            nodes.append(node)
    spec = {"name": name,
            "doc": f"TODO — 한 줄. 워커 읽기전용, 적용은 사본에. (뼈대: {workflow})",
            "nodes": nodes}
    with open(spec_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(spec, ensure_ascii=False, indent=1))
    written.append(os.path.relpath(spec_path, root).replace("\\", "/"))
    return {"spec": os.path.relpath(spec_path, root).replace("\\", "/"), "written": written,
            "todo": ["(선택) 프롬프트를 쓰기 전에 Fabric 에서 뼈대를 찾는다: `ge fabric <영어 키워드>` → fabric.md 의 `선택:` (GE_REQUIRE_FABRIC=1 이면 필수)",
                     "프롬프트의 TODO 넷(왜·요구·금지·계약)을 채워라",
                     "rubric 을 이 일에 맞게 항목화하고 verify.md 의 item_id 목록과 맞춰라",
                     "verify 노드에 만드는 노드와 다른 executor/model 을 걸어라 (같은 눈으로 검사하지 않게)",
                     f"적용 스크립트: {apply_script} — 사본(linked git worktree)을 GE_SRC 로 준다. 없으면 멈춘다. research 뼈대는 직접 써라",
                     "probe.py 의 FORBIDDEN/REQUIRED 를 채우고 명세에 contract.probe 를 건다. 계약 sha 는 `ge contract-sha <명세> --write`",
                     "교훈이 쌓이면 `ge lesson <명세> <노드> \"한 줄\"` — 만드는 노드만"]}
