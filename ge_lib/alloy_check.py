"""`ge check-model` — check the structural invariants in formal/ge_invariants.als with Alloy 6.

Alloy exits 0 even when it finds a counterexample, so the verdict comes from receipt.json:
a `check` with a solution is a counterexample (fail); a `run` without a solution is impossible (fail).
Expected: every check UNSAT; `Nonvacuous` SAT (the rules allow a real run); `GateRemoved` SAT
(negative control: without the acceptance gate an apply can run after a failed verify).

Exit codes: 0 all as expected / 1 unexpected result / 2 cannot run (no java or no Alloy jar).
Java: $GE_JAVA, $JAVA_HOME/bin/java, or `java` on PATH. Jar: --jar or $ALLOY_JAR.
"""
import json
import os
import shutil
import subprocess
import tempfile


def _rt(path, encoding="utf-8", errors=None, newline=None):
    """Read a text file and close it."""
    with open(path, encoding=encoding, errors=errors, newline=newline) as f:
        return f.read()


HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(os.path.dirname(HERE), "formal", "ge_invariants.als")
EXPECT = {"ApplyGated": False, "NonDestructive": False, "PreflightBlocks": False, "VerifyAfterProducer": False,
          "Nonvacuous": True, "GateRemoved": True}      # True = a solution must exist


def find_java():
    cands = [os.environ.get("GE_JAVA"),
             os.path.join(os.environ["JAVA_HOME"], "bin", "java") if os.environ.get("JAVA_HOME") else None,
             shutil.which("java")]
    for c in cands:
        if not c or not os.path.exists(c):
            continue
        try:
            if subprocess.run([c, "-version"], capture_output=True, timeout=30).returncode == 0:
                return c
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def run_model(java, jar, model=MODEL):
    out = tempfile.mkdtemp(prefix="ge-alloy-")
    try:
        r = subprocess.run([java, "-jar", jar, "exec", "-f", "-t", "text", "-o", out, model],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError((r.stdout + r.stderr)[-800:])
        receipt = os.path.join(out, "receipt.json")
        return json.loads(_rt(receipt, encoding="utf-8"))["commands"]
    finally:
        shutil.rmtree(out, ignore_errors=True)


def verdicts(commands):
    rows = []
    for name, want in EXPECT.items():
        got = bool((commands.get(name) or {}).get("solution"))
        rows.append({"command": name, "kind": "run" if want else "check",
                     "solution": got, "expected": want, "ok": got == want})
    return rows


def main(jar=None, quiet=False):
    jar = jar or os.environ.get("ALLOY_JAR")
    java = find_java()
    if not java or not jar or not os.path.isfile(jar):
        if not quiet:
            print(json.dumps({"ok": None, "status": "실행 불가", "java": java, "jar": jar,
                              "hint": "Java 17+ 와 Alloy 6 jar 를 준비하고 ALLOY_JAR 를 지정한다"}, ensure_ascii=False))
        return 2
    rows = verdicts(run_model(java, jar))
    ok = all(r["ok"] for r in rows)
    if not quiet:
        print(json.dumps({"ok": ok, "model": os.path.relpath(MODEL), "results": rows}, ensure_ascii=False, indent=1))
    return 0 if ok else 1
