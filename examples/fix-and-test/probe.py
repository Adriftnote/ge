"""Runtime probe: apply the patch to a temporary copy and run the tests. The original folder is never written.

Called by ge as: python probe.py <run_dir>   -> stdout {"checks": [{"id", "ok", "why"}]}
Baseline (GE_PROBE_BASELINE=1 or no patch yet): the tests must FAIL on the original — that proves the bug is real.
If the original already passes, the facts are wrong and ge stops before calling a worker.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
run_dir = sys.argv[1] if len(sys.argv) > 1 else ""
plan_path = os.path.join(run_dir, "artifacts", "patch.json") if run_dir else ""
baseline = os.environ.get("GE_PROBE_BASELINE") == "1" or not os.path.isfile(plan_path)


def run_tests(folder):
    r = subprocess.run([sys.executable, "-m", "unittest", "-q", "test_stats"], cwd=folder,
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1]


tmp = tempfile.mkdtemp(prefix="ge-probe-")
try:
    work = os.path.join(tmp, "src")
    shutil.copytree(SRC, work)
    if baseline:
        passed, tail = run_tests(work)
        check = {"id": "tests_pass", "ok": not passed,
                 "why": "baseline: 원본에서 테스트 실패 재현 (" + tail + ")" if not passed else "baseline: 원본이 이미 통과 — 고칠 버그가 없다"}
    else:
        for p in json.loads(read(plan_path)).get("patches", []):
            path = os.path.join(work, p["file"])
            text = read(path)
            if text.count(p["old_str"]) != 1:
                print(json.dumps({"checks": [{"id": "tests_pass", "ok": False,
                                              "why": "anchor %d회: %s" % (text.count(p["old_str"]), p["file"])}]}))
                sys.exit(0)
            write(path, text.replace(p["old_str"], p["new_str"], 1))
        passed, tail = run_tests(work)
        check = {"id": "tests_pass", "ok": passed, "why": tail or "ok"}
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print(json.dumps({"checks": [check]}, ensure_ascii=False))
