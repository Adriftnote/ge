"""Script node: write the verified patch to a NEW copy under the run folder (non-destructive).

The original src/ is never changed. The result is <run_dir>/artifacts/fixed/. To apply to a real repository,
use ge_lib/apply_worktree.py on a git worktree instead (see `ge new`).
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.environ["GE_HOME"])
from ge_lib.patchpath import rel_to_src  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


run_dir = sys.argv[1]
art = os.path.join(run_dir, "artifacts")
src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
gate = json.loads(read(os.path.join(art, "guard-acceptance.json")))
if not gate["ok"]:
    print(json.dumps({"ok": False, "why": "acceptance gate blocked"}))
    sys.exit(1)
dst = os.path.join(art, "fixed")
shutil.rmtree(dst, ignore_errors=True)
shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
applied = []
for p in json.loads(read(os.path.join(art, "patch.json")))["patches"]:
    f = rel_to_src(p["file"], dst, must_exist=True)
    path = os.path.join(dst, f)
    text = read(path)
    assert text.count(p["old_str"]) == 1, "anchor"
    write(path, text.replace(p["old_str"], p["new_str"], 1))
    applied.append(f)
print(json.dumps({"ok": True, "applied": applied, "written": dst}))
