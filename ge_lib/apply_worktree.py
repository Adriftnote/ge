"""apply_worktree — write verified patches into a COPY of the repository (a git worktree). No model.

`ge new` generates <spec>/apply.py, which only calls run() here (one implementation, no forks).
Refuses, and writes nothing, when:
  - the target is not a copy: it must be a linked git worktree (`.git` is a file) or a folder marked with
    a `.ge-copy` file. A main checkout (`.git` directory) or an unmarked folder is treated as the original;
  - verify.json is missing, has no verdicts, or has any verdict other than pass;
  - a file is outside scope, an anchor does not occur exactly once, or a patched file fails its lint.
Patched text is linted in a temporary file first and only then moved into place, so a precheck failure
writes nothing. CRLF files stay CRLF.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from .patchpath import rel_to_src

COPY_MARK = ".ge-copy"


def is_copy(src):
    """True for a linked git worktree (.git is a file) or a folder marked as a ge copy."""
    git = os.path.join(src, ".git")
    return os.path.isfile(git) or os.path.isfile(os.path.join(src, COPY_MARK))


def _lint(path, ext):
    if ext == ".py":
        cmd = [sys.executable, "-m", "py_compile", path]
    elif ext in (".js", ".mjs") and shutil.which("node"):
        cmd = ["node", "--check", path]
    else:
        return "skip"
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return "ok" if r.returncode == 0 else (r.stdout or r.stderr)[:300]


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def plan(run_dir, src, patch_nodes=("patch",), scope=None):
    """Check everything and build the new texts. Returns (report, {rel: (text, crlf)})."""
    A = os.path.join(run_dir, "artifacts")
    report = {"applied": [], "failed": [], "lint": {}}
    if not src:
        report["failed"].append({"file": "-", "why": "사본 경로가 없다 (GE_SRC 로 worktree 를 준다)"})
        return report, {}
    src = os.path.abspath(src)
    if not is_copy(src):
        report["failed"].append({"file": src, "why": f"사본이 아니다: linked git worktree 나 {COPY_MARK} 표식이 있는 폴더만 쓴다"})
    vp = os.path.join(A, "verify.json")
    if not os.path.isfile(vp):
        report["failed"].append({"file": "verify", "why": "verify.json 이 없다"})
    else:
        vs = _load(vp).get("verdicts") or []
        bad = [v.get("item_id") for v in vs if v.get("verdict") != "pass"]
        if not vs or bad:
            report["failed"].append({"file": "verify", "why": "통과하지 않음: " + (", ".join(map(str, bad)) or "판정 없음")})
    files = {}
    if report["failed"]:
        return report, files
    for nid in patch_nodes:
        for p in _load(os.path.join(A, nid + ".json")).get("patches", []):
            try:
                f = rel_to_src(p["file"], src, must_exist=True)
            except ValueError as e:
                report["failed"].append({"file": p["file"], "why": str(e)}); continue
            if scope and f not in scope:
                report["failed"].append({"file": f, "why": "scope 밖"}); continue
            if f not in files:
                with open(os.path.join(src, f), encoding="utf-8", newline="") as fh:
                    raw = fh.read()
                files[f] = [raw.replace("\r\n", "\n"), "\r\n" in raw]
            n = files[f][0].count(p["old_str"])
            if n != 1:
                report["failed"].append({"file": f, "why": "anchor %d회" % n}); continue
            files[f][0] = files[f][0].replace(p["old_str"], p["new_str"])
            report["applied"].append({"file": f, "why": p.get("why", "")[:120]})
    return report, files


def run(run_dir, src, patch_nodes=("patch",), scope=None):
    report, files = plan(run_dir, src, patch_nodes, scope)
    staged = {}
    if not report["failed"]:
        for f, (text, crlf) in files.items():
            ext = os.path.splitext(f)[1].lower()
            fd, tmp = tempfile.mkstemp(suffix=ext, dir=os.path.dirname(os.path.join(src, f)))
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(text.replace("\n", "\r\n") if crlf else text)
            staged[f] = tmp
            report["lint"][f] = _lint(tmp, ext)
            if report["lint"][f] not in ("ok", "skip"):
                report["failed"].append({"file": f, "why": "lint: " + report["lint"][f]})
    if report["failed"]:
        report["applied"] = []      # all or nothing
        for tmp in staged.values():
            os.remove(tmp)
    else:
        for f, tmp in staged.items():
            os.replace(tmp, os.path.join(src, f))
        report["diff_stat"] = subprocess.run(["git", "-C", src, "diff", "--stat"], capture_output=True, text=True,
                                             encoding="utf-8", errors="replace").stdout
    A = os.path.join(run_dir, "artifacts")
    os.makedirs(A, exist_ok=True)
    with open(os.path.join(A, "apply.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    ok = not report["failed"]
    print(json.dumps({"ok": ok, "applied": len(report["applied"]), "failed": report["failed"][:5]}, ensure_ascii=False))
    sys.exit(0 if ok else 1)
