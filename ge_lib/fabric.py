"""Fabric prompt-skeleton lookup — a spec-writing tool, not a runtime node.

Before writing a new prompt, look for a skeleton to borrow (steps, review axes, output structure) in the
Fabric pattern collection (https://github.com/danielmiessler/fabric, MIT License, (c) Daniel Miessler).
Borrow the skeleton only. Do not borrow unauditable output formats such as a single 1-100 score:
verification in ge is per-item pass/fail with evidence.

The corpus is NOT bundled. `ge fabric --sync` downloads the upstream tarball once and builds a local
manifest at $GE_FABRIC or ~/.cache/ge/fabric/manifest.json. Without network it fails with a clear error.
"""
import hashlib
import io
import json
import os
import re
import tarfile
import urllib.request


def _rt(path, encoding="utf-8", errors=None, newline=None):
    """Read a text file and close it."""
    with open(path, encoding=encoding, errors=errors, newline=newline) as f:
        return f.read()


REPO = "danielmiessler/fabric"
TREE_URL = "https://api.github.com/repos/%s/git/trees/main?recursive=1" % REPO
TARBALL_URL = "https://codeload.github.com/%s/tar.gz/refs/heads/main" % REPO


def manifest_path():
    p = os.environ.get("GE_FABRIC")
    if p:
        return p if p.endswith(".json") else os.path.join(p, "manifest.json")
    return os.path.join(os.path.expanduser("~"), ".cache", "ge", "fabric", "manifest.json")


def _load(path):
    if not os.path.isfile(path):
        return None
    man = json.loads(_rt(path, encoding="utf-8"))
    return man.get("patterns", man if isinstance(man, list) else [])


def _missing(path):
    return {"ok": False, "error": f"manifest 없음: {path} — `ge fabric --sync` 로 만든다 (GE_FABRIC 로 위치 지정)"}


def search(query=None, limit=12, path=None):
    """Unranked substring search over name, purpose and tags. It only narrows candidates;
    choose by reading the purpose/body."""
    path = path or manifest_path()
    pats = _load(path)
    if pats is None:
        return _missing(path)
    q = (query or "").lower()
    hits = []
    for p in pats:
        blob = " ".join([p.get("name", ""), p.get("purpose", ""), " ".join(p.get("tags") or [])]).lower()
        if not q or q in blob:
            hits.append({"name": p.get("name"), "sections": p.get("sections"), "chars": len(p.get("body", "")),
                         "purpose": " ".join(p.get("purpose", "").split())[:120]})
    return {"ok": True, "total": len(pats), "matched": len(hits), "hits": hits[:limit],
            "note": "비랭킹 검색이다. purpose 를 읽고 의미로 골라라. 본문은 `ge fabric --show <name>`."}


def show(name, out_dir, path=None):
    """Write one pattern body to <out_dir>/<name>.md (not to the chat)."""
    path = path or manifest_path()
    pats = _load(path)
    if pats is None:
        return _missing(path)
    p = next((x for x in pats if x.get("name") == name), None)
    if not p:
        return {"ok": False, "error": "그런 패턴 없음: " + name}
    dst = os.path.join(out_dir, name + ".md")
    os.makedirs(out_dir, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(p.get("body", ""))
    return {"ok": True, "name": name, "chars": len(p.get("body", "")), "written": dst,
            "sections": p.get("sections"), "purpose": p.get("purpose", "")[:300],
            "source": f"{REPO} (MIT)", "note": "뼈대만 빌리고 그대로 실행하지 마라."}


def parse_pattern(name, body, tags=None):
    """system.md -> manifest row. purpose = first non-heading paragraph; sections = # and ## headings."""
    sections = [m.group(1).strip() for m in re.finditer(r"^#{1,2}\s+(.+)$", body, re.M)]
    paras = [p.strip() for p in re.split(r"\n\s*\n", body)]
    purpose = next((" ".join(p.split()) for p in paras if p and not p.startswith("#")), "")
    return {"name": name, "purpose": purpose, "tags": tags or [], "sections": sections, "body": body}


def build_manifest(files, tags=None):
    """files: {pattern_name: system.md text} -> manifest dict (sorted by name)."""
    tags = tags or {}
    pats = [parse_pattern(n, files[n], tags.get(n)) for n in sorted(files)]
    return {"source": f"https://github.com/{REPO}", "license": "MIT", "count": len(pats), "patterns": pats}


def _fetch(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "ge-fabric", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def files_from_tarball(blob):
    """Upstream tarball -> ({name: system.md}, {name: [tags]})."""
    files, tags = {}, {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for m in tf.getmembers():
            parts = m.name.split("/")
            f = None
            if len(parts) >= 5 and parts[1] == "data" and parts[2] == "patterns" and parts[-1] == "system.md":
                f = tf.extractfile(m)
                if f:
                    files[parts[3]] = f.read().decode("utf-8", errors="replace").replace("\r\n", "\n")
            elif parts[-1] == "pattern_descriptions.json":
                f = tf.extractfile(m)
                if f:
                    try:
                        for d in json.load(f).get("patterns", []):
                            tags[d.get("patternName")] = d.get("tags") or []
                    except Exception:
                        pass
    return files, tags


def sync(path=None, fetch=_fetch, dry=False):
    """Download the upstream tarball once and (re)build the local manifest."""
    path = path or manifest_path()
    try:
        blob = fetch(TARBALL_URL, 120)
    except Exception as e:
        return {"ok": False, "error": "tarball 받기 실패(네트워크?): %s: %s" % (type(e).__name__, e)}
    files, tags = files_from_tarball(blob)
    if not files:
        return {"ok": False, "error": "tarball 에 data/patterns/*/system.md 가 없다 — 상류 구조가 바뀌었다"}
    before = {p["name"] for p in (_load(path) or [])}
    if dry:
        return {"ok": True, "dry": True, "patterns": len(files), "new": sorted(set(files) - before)}
    man = build_manifest(files, tags)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=1) + "\n")
    return {"ok": True, "manifest": path, "count": man["count"],
            "new": sorted(set(files) - before) if before else [], "gone": sorted(before - set(files))}


def git_blob_sha(text):
    """git blob sha1 — the same sha the upstream tree API returns, so bodies can be compared without download."""
    b = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\x00" % len(b) + b).hexdigest()


def body_matches(body, sha):
    """The manifest stores LF; some upstream files are CRLF. Accept either form."""
    return git_blob_sha(body) == sha or git_blob_sha(body.replace("\n", "\r\n")) == sha


def check(path=None, fetch=_fetch):
    """Compare the local manifest with upstream names and body shas (tree API only, no tarball)."""
    path = path or manifest_path()
    pats = _load(path)
    if pats is None:
        return _missing(path)
    bodies = {p.get("name"): p.get("body", "") for p in pats}
    try:
        tree = json.loads(fetch(TREE_URL, 30))
    except Exception as e:
        return {"ok": False, "error": "상류 조회 실패(네트워크?): %s: %s" % (type(e).__name__, e)}
    up = {p["path"].split("/")[2]: p.get("sha") for p in tree.get("tree", [])
          if p["path"].startswith("data/patterns/") and p["path"].endswith("/system.md")}
    new = sorted(n for n in up if n not in bodies)
    gone = sorted(n for n in bodies if n not in up)
    changed = sorted(n for n in up if n in bodies and up[n] and not body_matches(bodies[n], up[n]))
    unverifiable = sorted(n for n in up if n in bodies and not up[n])
    stale = new or gone or changed
    return {"ok": True, "local_count": len(bodies), "upstream_count": len(up), "new": new, "gone": gone,
            "changed": changed, "unverifiable": unverifiable,
            "verdict": "낡음 — `ge fabric --sync` 로 갱신" if stale else "최신 (이름·본문 모두)"}


def choice_problem(spec_path):
    """If <spec>/fabric.md (made by `ge new`) has an empty `선택:` line, return the reason; else None.
    Specs without fabric.md are not checked."""
    p = os.path.join(os.path.splitext(spec_path)[0], "fabric.md")
    if not os.path.isfile(p):
        return None
    for line in _rt(p, encoding="utf-8").splitlines(True):
        if line.strip().startswith("선택:"):
            val = line.split(":", 1)[1].strip()
            return None if val and val.upper() != "TODO" else \
                f"Fabric 뼈대 확인 안 함: {p} 의 `선택:` 을 채워라 (빌릴 게 없으면 `선택: 없음`)"
    return f"Fabric 뼈대 확인 안 함: {p} 에 `선택:` 줄이 없다"
