"""ge regression tests. Fake executors and the stub only (zero model calls). Run: python3 -m unittest discover -s tests"""
import contextlib, importlib.util, io, json, os, shutil, sys, tempfile, unittest
from importlib.machinery import SourceFileLoader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
_loader = SourceFileLoader("ge", os.path.join(ROOT, "ge"))
_spec = importlib.util.spec_from_loader("ge", _loader)
ge = importlib.util.module_from_spec(_spec)
_loader.exec_module(ge)


def _rt(path, encoding="utf-8", errors=None, newline=None):
    """Read a text file and close it."""
    with open(path, encoding=encoding, errors=errors, newline=newline) as f:
        return f.read()


def _rb(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TempLedger(unittest.TestCase):
    """Each test gets its own ledger folder and spec folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._ge_dir, ge.GE_DIR = ge.GE_DIR, os.path.join(self.tmp, ".ge")
        for k in ("STUB_FAIL_ONCE", "_STUB_FAILED"):
            os.environ.pop(k, None)

    def tearDown(self):
        ge.GE_DIR = self._ge_dir
        for k in ("STUB_FAIL_ONCE", "_STUB_FAILED"):
            os.environ.pop(k, None)
        ge.EXECUTORS.pop("fake", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spec_file(self, spec, name="spec.json"):
        return write(os.path.join(self.tmp, name), json.dumps(spec))

    def rows(self, run_id):
        p = os.path.join(ge.GE_DIR, "runs", run_id, "ledger.jsonl")
        return [json.loads(l) for l in _rt(p, encoding="utf-8").splitlines(True) if l.strip()]


S = {"type": "object", "additionalProperties": False, "required": ["a"],
     "properties": {"a": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": ["x", "y"]}}}}


class Validate(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(ge.validate({"a": ["x"]}, S), [])

    def test_errors(self):
        errs = ge.validate({"a": [], "z": 1}, S)
        self.assertTrue(any("minItems" in e for e in errs))
        self.assertTrue(any("허용되지 않은" in e for e in errs))
        self.assertTrue(ge.validate({"a": ["q"]}, S))


class Examples(TempLedger):
    """The two shipped examples run end to end with the stub executor."""

    def run_example(self, name, rid):
        spec = ge.load_spec(os.path.join(ROOT, "examples", name + ".json"))
        ok = ge.Run(spec, rid, "stub").run()
        return ok, os.path.join(ge.GE_DIR, "runs", rid)

    def test_summarize(self):
        ok, d = self.run_example("summarize", "s1")
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(os.path.join(d, "artifacts", "summary.md")))
        m = ge.metrics(d)
        self.assertEqual(m["verify_pass"], 4)
        self.assertNotIn("Pour-over", json.dumps(m))          # metrics carry no artifact bodies

    def test_fix_and_test_probe_gate_and_copy(self):
        ok, d = self.run_example("fix-and-test", "f1")
        self.assertTrue(ok)
        phases = [r["phase"] for r in self.rows("f1") if r["event"] == "guard"]
        self.assertEqual(phases, ["baseline", "preflight", "runtime_probe", "preflight", "acceptance_gate"])
        fixed = _rt(os.path.join(d, "artifacts", "fixed", "stats.py"), encoding="utf-8")
        self.assertIn("n % 2 == 0", fixed)
        orig = _rt(os.path.join(ROOT, "examples", "fix-and-test", "src", "stats.py"), encoding="utf-8")
        self.assertNotIn("n % 2 == 0", orig)                  # non-destructive: original untouched
        vs = {v["item_id"]: v for v in json.loads(_rt(os.path.join(d, "artifacts", "verify.json")))["verdicts"]}
        self.assertIn("mechanical", vs["tests_pass"]["reason"])   # filled by the probe, not the model

    def test_bad_stub_patch_is_blocked_by_probe(self):
        src = os.path.join(ROOT, "examples")
        dst = os.path.join(self.tmp, "ex")
        shutil.copytree(src, dst)
        p = os.path.join(dst, "fix-and-test", "stub-patch.json")
        d = json.loads(_rt(p, encoding="utf-8"))
        d["patches"][0]["new_str"] = d["patches"][0]["old_str"].replace("return s[n // 2]", "return s[0]")
        write(p, json.dumps(d))
        ok = ge.Run(ge.load_spec(os.path.join(dst, "fix-and-test.json")), "f2", "stub").run()
        self.assertFalse(ok)
        done = [r for r in self.rows("f2") if r["event"] == "run_done"][0]
        self.assertIn("tests_pass", done["cause"])
        self.assertNotIn("verify", [r.get("node") for r in self.rows("f2") if r["event"] == "node_started"])

    def test_shipped_bad_example_is_blocked(self):
        ok, d = self.run_example("fix-and-test-bad", "f3")
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(os.path.join(d, "artifacts", "fixed")))

    def test_cli_run_stub(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            ge.main(["run", os.path.join(ROOT, "examples", "summarize.json"), "--executor", "stub", "--run-id", "c1"])
        self.assertEqual(cm.exception.code, 0)
        self.assertTrue(json.loads(buf.getvalue().strip().splitlines()[0])["ok"])


class Runner(TempLedger):
    def two_node(self, retry=1):
        write(os.path.join(self.tmp, "p.md"), "make it")
        write(os.path.join(self.tmp, "v.md"), "check {{rubric}} {{input.facts}}")
        write(os.path.join(self.tmp, "r.md"), "1. ok — fine")
        return self.spec_file({"name": "t", "nodes": [
            {"id": "facts", "kind": "model", "prompt": "p.md", "inputs": [], "retry": retry, "schema": S},
            {"id": "verify", "kind": "verify", "prompt": "v.md", "rubric": "r.md", "inputs": ["facts"]}]})

    def test_ledger_events_and_rework(self):
        os.environ["STUB_FAIL_ONCE"] = "[node:facts]"
        ok = ge.Run(ge.load_spec(self.two_node()), "r1", "stub").run()
        self.assertTrue(ok)
        ev = [r["event"] for r in self.rows("r1")]
        self.assertEqual(ev[0], "run_started"); self.assertEqual(ev[-1], "run_done")
        m = ge.metrics(os.path.join(ge.GE_DIR, "runs", "r1"))
        self.assertEqual(m["rework"], 1)
        self.assertEqual(m["rework_cause"], {"schema": 1})
        t = {n["id"]: n for n in m["node_table"]}
        self.assertEqual(t["facts"]["attempts_max"], 2)

    def test_retry_prompt_carries_previous_errors(self):
        prompts = []

        def fake(prompt, schema, timeout, model=None, cwd=None, tag=None, tools=None):
            if "[node:facts]" in prompt:
                prompts.append(prompt)
                return ({"a": ["q"]} if len(prompts) == 1 else {"a": ["x"]}), {"executor": "fake"}
            return {"verdicts": [{"item_id": "ok", "verdict": "pass", "reason": "fine"}]}, {"executor": "fake"}
        ge.EXECUTORS["fake"] = fake
        self.assertTrue(ge.Run(ge.load_spec(self.two_node()), "r2", "fake").run())
        self.assertNotIn("직전 시도가 실패한 이유", prompts[0])
        self.assertIn("enum 밖", prompts[1])

    def test_note_counts_intervention(self):
        ge.Run(ge.load_spec(self.two_node()), "r3", "stub").run()
        d = os.path.join(ge.GE_DIR, "runs", "r3")
        with contextlib.redirect_stdout(io.StringIO()):
            ge.main(["note", d, "반려: 너무 길다"])
        self.assertEqual(ge.metrics(d)["human_interventions"], 1)

    def test_script_node_gets_run_dir_and_args(self):
        write(os.path.join(self.tmp, "dir with space", "a.py"),
              "import json, sys\nprint(json.dumps({'argc': len(sys.argv), 'rest': sys.argv[2:]}))\n")
        sp = ge.load_spec(self.spec_file({"name": "scr", "nodes": [
            {"id": "s", "kind": "script", "script": "dir with space/a.py", "args": ["--arm", "B"]}]}))
        self.assertTrue(ge.Run(sp, "r4", "stub").run())
        art = json.loads(_rt(os.path.join(ge.GE_DIR, "runs", "r4", "artifacts", "s.json")))
        self.assertEqual(art, {"argc": 4, "rest": ["--arm", "B"]})

    def test_max_cost_stops_before_next_call(self):
        def fake(prompt, schema, timeout, model=None, cwd=None, tag=None, tools=None):
            return {"a": ["x"]}, {"executor": "fake", "cost_usd": 1.0}
        ge.EXECUTORS["fake"] = fake
        sp = ge.load_spec(self.spec_file({"name": "c", "nodes": [
            {"id": "a", "kind": "model", "inputs": [], "schema": S},
            {"id": "b", "kind": "model", "inputs": ["a"], "schema": S}]}))
        self.assertFalse(ge.Run(sp, "r5", "fake", max_cost=0.5).run())
        calls = [r for r in self.rows("r5") if r["event"] == "call"]
        self.assertTrue(calls[-1].get("capped"))

    def test_stub_run_never_calls_node_executor(self):
        sp = ge.load_spec(self.spec_file({"name": "s", "nodes": [
            {"id": "a", "kind": "model", "executor": "codex", "inputs": [], "schema": S}]}))
        self.assertTrue(ge.Run(sp, "r6", "stub").run())
        self.assertEqual([r["executor"] for r in self.rows("r6") if r["event"] == "call"], ["stub"])

    def test_cycle_and_missing_input_refused(self):
        with self.assertRaises(AssertionError):
            ge.load_spec(self.spec_file({"name": "x", "nodes": [{"id": "a", "inputs": ["nope"]}]}))
        with self.assertRaises(SystemExit):
            ge.topo({"nodes": [{"id": "a", "inputs": ["b"]}, {"id": "b", "inputs": ["a"]}]})


class TopoOrder(unittest.TestCase):
    def test_declaration_order_for_ready_nodes(self):
        self.assertEqual(ge.topo({"nodes": [{"id": "q"}, {"id": "p"}]}), ["q", "p"])
        self.assertEqual(ge.topo({"nodes": [{"id": "b", "inputs": ["a"]}, {"id": "a"}]}), ["a", "b"])


class Routing(TempLedger):
    def test_when_routes_to_matching_branch_only(self):
        write(os.path.join(self.tmp, "route.py"), "import json; print(json.dumps({'branch': 'code'}))")
        sp = ge.load_spec(self.spec_file({"name": "r", "nodes": [
            {"id": "route", "kind": "script", "script": "route.py"},
            {"id": "code", "kind": "model", "inputs": ["route"], "schema": S,
             "when": {"node": "route", "key": "branch", "equals": "code"}},
            {"id": "doc", "kind": "model", "inputs": ["route"], "schema": S,
             "when": {"node": "route", "key": "branch", "in": ["doc"]}}]}))
        self.assertTrue(ge.Run(sp, "w1", "stub").run())
        ev = {(r["event"], r.get("node")) for r in self.rows("w1")}
        self.assertIn(("node_done", "code"), ev)
        self.assertIn(("node_skipped", "doc"), ev)


class Ledger(TempLedger):
    def test_roles_and_role_sha_change_with_lessons(self):
        write(os.path.join(self.tmp, "p.md"), "make")
        spec = {"name": "L", "nodes": [{"id": "a", "kind": "model", "prompt": "p.md", "inputs": [], "schema": S}]}
        path = self.spec_file(spec, "L.json")
        ge.Run(ge.load_spec(path), "l1", "stub").run()
        r = ge.add_lesson(path, "a", "앵커는  파일에서 복사한다")
        self.assertTrue(r["added"].endswith("/ 앵커는 파일에서 복사한다"))
        spec["nodes"][0]["lessons"] = os.path.relpath(ge.lessons_path(path, "a"), self.tmp)
        write(path, json.dumps(spec))
        sp = ge.load_spec(path)
        self.assertIn("이 자리에서 이미 배운 것", ge.render(sp["nodes"][0], {}))
        ge.Run(sp, "l2", "stub").run()
        t = ge.roles_table(ge.ledger_rows())
        self.assertEqual({o["role"] for o in t}, {"L/a"})
        self.assertEqual(len({o["role_sha"] for o in t}), 2)     # before / after the lesson
        self.assertIn("L/a", ge.format_roles(t))

    def test_verify_with_lessons_refused(self):
        write(os.path.join(self.tmp, "l.md"), "- x")
        with self.assertRaises(AssertionError):
            ge.load_spec(self.spec_file({"name": "x", "nodes": [{"id": "v", "kind": "verify", "lessons": "l.md"}]}))


class ProgressAndTrace(TempLedger):
    def test_progress_and_trace(self):
        write(os.path.join(self.tmp, "p.md"), "make")
        sp = ge.load_spec(self.spec_file({"name": "P", "nodes": [
            {"id": "a", "kind": "model", "prompt": "p.md", "inputs": [], "schema": S},
            {"id": "b", "kind": "model", "inputs": ["a"], "schema": S}]}))
        call_dir = tempfile.mkdtemp()
        write(os.path.join(call_dir, "stdout.json"), "RAW WORKER OUTPUT")

        def fake(prompt, schema, timeout, model=None, cwd=None, tag=None, tools=None):
            return None, {"executor": "fake", "error": "boom", "call_dir": call_dir}
        ge.EXECUTORS["fake"] = fake
        self.assertFalse(ge.Run(sp, "p1", "fake").run())
        runs = ge.progress()
        self.assertEqual([n["state"] for n in runs[0]["nodes"]], ["fail", "pending"])
        txt = ge.format_progress(runs, unicode_marks=False)
        self.assertIn("[x] p1", txt); self.assertIn("boom", txt)
        t = ge.trace(os.path.join(ge.GE_DIR, "runs", "p1"))
        self.assertEqual(_rt(t["traced"][0]["written"]), "RAW WORKER OUTPUT")


class Stats(TempLedger):
    def test_topologies_match_recipe_and_promote(self):
        d = os.path.join(self.tmp, "specs")
        patch_like = {"nodes": [{"id": "patch", "kind": "model"}, {"id": "verify", "kind": "verify", "inputs": ["patch"]},
                                {"id": "apply", "kind": "script", "inputs": ["patch", "verify"]}]}
        two = {"nodes": [{"id": "a", "kind": "model"}, {"id": "b", "kind": "script", "inputs": ["a"]}]}
        write(os.path.join(d, "p1.json"), json.dumps({"name": "p1", **patch_like}))
        write(os.path.join(d, "t1.json"), json.dumps({"name": "t1", **two}))
        write(os.path.join(d, "t2.json"), json.dumps({"name": "t2", **two}))
        write(os.path.join(d, "one.json"), json.dumps({"name": "one", "nodes": [{"id": "x", "kind": "verify"}]}))
        st = ge.ge_stats(spec_roots=[d])
        by = {" -> ".join(t["nodes"]): t for t in st["topologies"]}
        self.assertIn("patch", by["model -> verify -> script"]["workflow"])
        self.assertTrue(by["model -> script"]["promote"])        # used twice, no recipe yet
        self.assertFalse(by["verify"]["promote"])                # one-off stays one-off
        self.assertIn("[promote", ge.format_ge_stats(st))


class Scaffold(TempLedger):
    def test_new_spec_in_checkout_root_and_optin_fabric_gate(self):
        from ge_lib import scaffold
        write(os.path.join(self.tmp, "ge"), "#!/usr/bin/env python3\n")    # a checkout root has a `ge` file
        tgt = os.path.join(self.tmp, "work")
        write(os.path.join(tgt, "a.py"), "x = 1\n")
        r = scaffold.new_spec("demo", tgt, "a.py", root=self.tmp)
        self.assertEqual(r["spec"], "graphs/demo.json")
        path = os.path.join(self.tmp, r["spec"])
        sp = ge.load_spec(path)                                 # fabric gate is off by default
        self.assertEqual(ge.topo(sp), ["patch", "verify", "apply"])
        self.assertTrue(sp["nodes"][2]["apply"])
        prompt = ge.render(sp["nodes"][0], {})
        self.assertIn("## 계약", prompt)
        self.assertIn("{{rubric}}", _rt(os.path.join(self.tmp, "graphs", "demo", "verify.md")))
        self.assertNotIn("rubric", sp["nodes"][0])               # only verify gets the rubric
        os.environ["GE_REQUIRE_FABRIC"] = "1"
        try:
            with self.assertRaises(AssertionError) as cm:       # opt-in: empty `선택:` refuses
                ge.load_spec(path)
            self.assertIn("선택:", str(cm.exception))
            fab = os.path.join(self.tmp, "graphs", "demo", "fabric.md")
            write(fab, _rt(fab).replace("선택:\n", "선택: 없음\n"))
            ge.load_spec(path)
        finally:
            os.environ.pop("GE_REQUIRE_FABRIC")
        with self.assertRaises(SystemExit):
            scaffold.new_spec("demo", tgt, "a.py", root=self.tmp)


class GeneratedApply(TempLedger):
    """The generated apply writes only to a copy, only after verify passed, all or nothing."""

    def setUp(self):
        super().setUp()
        from ge_lib import scaffold
        self.orig = os.path.join(self.tmp, "repo")
        write(os.path.join(self.orig, "a.py"), "x = 1\r\ny = 2\r\n")
        os.makedirs(os.path.join(self.orig, ".git"))                       # a main checkout = the original
        self.copy = os.path.join(self.tmp, "wt")
        write(os.path.join(self.copy, "a.py"), "x = 1\r\ny = 2\r\n")
        write(os.path.join(self.copy, ".git"), "gitdir: " + os.path.join(self.orig, ".git", "worktrees", "wt") + "\n")
        scaffold.new_spec("ap", self.orig, "a.py", root=self.tmp)
        self.run_dir = os.path.join(self.tmp, "run")
        self.patch([{"file": "a.py", "old_str": "x = 1\n", "new_str": "x = 10\n", "why": "w"}])
        self.verify([{"item_id": "i", "verdict": "pass", "reason": "ok"}])

    def patch(self, patches):
        write(os.path.join(self.run_dir, "artifacts", "patch.json"), json.dumps({"patches": patches}))

    def verify(self, verdicts):
        write(os.path.join(self.run_dir, "artifacts", "verify.json"), json.dumps({"verdicts": verdicts}))

    def apply(self, src):
        import subprocess
        env = {**os.environ, "GE_HOME": ROOT}
        env.pop("GE_SRC", None)
        if src:
            env["GE_SRC"] = src
        return subprocess.run([sys.executable, os.path.join(self.tmp, "graphs", "ap", "apply.py"), self.run_dir],
                              capture_output=True, text=True, env=env)

    def original_unchanged(self):
        self.assertEqual(_rt(os.path.join(self.orig, "a.py"), newline=""), "x = 1\r\ny = 2\r\n")

    def test_writes_worktree_copy_keeps_crlf(self):
        p = self.apply(self.copy)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(_rt(os.path.join(self.copy, "a.py"), newline=""), "x = 10\r\ny = 2\r\n")
        self.original_unchanged()

    def test_no_copy_path_stops(self):
        p = self.apply(None)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("GE_SRC", p.stdout)
        self.original_unchanged()

    def test_original_checkout_refused(self):
        p = self.apply(self.orig)
        self.assertNotEqual(p.returncode, 0)
        self.original_unchanged()

    def test_missing_or_failed_verify_stops(self):
        os.remove(os.path.join(self.run_dir, "artifacts", "verify.json"))
        self.assertNotEqual(self.apply(self.copy).returncode, 0)
        self.verify([{"item_id": "i", "verdict": "unverifiable", "reason": "cannot see"}])
        self.assertNotEqual(self.apply(self.copy).returncode, 0)
        self.verify([])
        self.assertNotEqual(self.apply(self.copy).returncode, 0)
        self.assertEqual(_rt(os.path.join(self.copy, "a.py"), newline=""), "x = 1\r\ny = 2\r\n")

    def test_all_or_nothing_including_lint(self):
        self.patch([{"file": "a.py", "old_str": "y = 2\n", "new_str": "y = 3\n", "why": "w"},
                    {"file": "a.py", "old_str": "missing\n", "new_str": "z\n", "why": "w"}])
        self.assertNotEqual(self.apply(self.copy).returncode, 0)
        self.patch([{"file": "a.py", "old_str": "y = 2\n", "new_str": "y = (\n", "why": "w"}])   # syntax error
        p = self.apply(self.copy)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("lint", p.stdout)
        self.assertEqual(_rt(os.path.join(self.copy, "a.py"), newline=""), "x = 1\r\ny = 2\r\n")
        self.assertEqual(sorted(os.listdir(self.copy)), [".git", "a.py"])   # no temp files left


class ApplyNode(TempLedger):
    """load_spec and the run enforce: an apply node takes a verify input and runs only after it passed."""

    def spec(self, verdict="pass", contract=False, required=True, wire_verify=True):
        write(os.path.join(self.tmp, "ap.py"), "import json; open('APPLIED', 'w').close(); print(json.dumps({'ok': True}))")
        write(os.path.join(self.tmp, "v.md"), "{{rubric}}")
        write(os.path.join(self.tmp, "r.md"), "1. i — ok")
        out = {"verdicts": [{"item_id": "i", "verdict": verdict, "reason": "because"}]}

        def fake(prompt, schema, timeout, model=None, cwd=None, tag=None, tools=None):
            return (out, {"executor": "fake"}) if "[node:verify]" in prompt else ({"a": ["x"]}, {"executor": "fake"})
        ge.EXECUTORS["fake"] = fake
        nodes = [{"id": "make", "kind": "model", "inputs": [], "schema": S},
                 {"id": "verify", "kind": "verify", "prompt": "v.md", "rubric": "r.md", "inputs": ["make"],
                  "required": required},
                 {"id": "apply", "kind": "script", "script": "ap.py", "apply": True,
                  "inputs": ["make", "verify"] if wire_verify else ["make"]}]
        return ge.load_spec(self.spec_file({"name": "a", "nodes": nodes}))

    def applied(self):
        return os.path.exists(os.path.join(self.tmp, "APPLIED"))

    def test_apply_without_verify_input_refused_at_load(self):
        with self.assertRaises(AssertionError):
            self.spec(wire_verify=False)

    def test_apply_runs_after_pass(self):
        self.assertTrue(ge.Run(self.spec(), "a1", "fake").run())
        self.assertTrue(self.applied())

    def test_verify_fail_blocks_apply_without_contract(self):
        self.assertFalse(ge.Run(self.spec("fail"), "a2", "fake").run())
        self.assertFalse(self.applied())
        done = [r for r in self.rows("a2") if r["event"] == "node_done" and r["node"] == "apply"][0]
        self.assertIn("verify", done["why"])

    def test_verify_not_required_still_blocks_apply(self):
        self.assertFalse(ge.Run(self.spec("unverifiable", required=False), "a3", "fake").run())
        self.assertFalse(self.applied())


class SchemaSubset(TempLedger):
    def test_integer_and_ranges(self):
        self.assertTrue(ge.validate(1.5, {"type": "integer"}))
        self.assertTrue(ge.validate(True, {"type": "integer"}))
        self.assertEqual(ge.validate(2, {"type": "integer", "minimum": 0, "maximum": 2}), [])
        self.assertTrue(ge.validate(-1, {"type": "number", "minimum": 0}))
        self.assertTrue(ge.validate(0, {"type": "number", "exclusiveMinimum": 0}))
        self.assertTrue(ge.validate(3, {"type": "integer", "enum": [1, 2]}))
        self.assertTrue(ge.validate("b", {"type": "string", "const": "a"}))
        self.assertTrue(ge.validate(1, {"type": "null"}))

    def test_unsupported_keys_refused_at_load(self):
        for bad in ({"type": "string", "format": "email"}, {"type": ["string", "null"]},
                    {"type": "object", "properties": {"a": {"oneOf": []}}},
                    {"type": "object", "additionalProperties": {"type": "string"}}):
            with self.assertRaises(AssertionError, msg=bad):
                ge.load_spec(self.spec_file({"name": "s", "nodes": [{"id": "a", "kind": "model", "schema": bad}]}))
        ge.load_spec(self.spec_file({"name": "s", "nodes": [{"id": "a", "kind": "model",
                                                             "schema": {"type": "string", "description": "ok"}}]}))


class ApplyPathSafety(unittest.TestCase):
    def test_rel_to_src(self):
        from ge_lib.patchpath import rel_to_src
        src = tempfile.mkdtemp()
        write(os.path.join(src, "pkg", "a.py"), "")
        self.assertEqual(rel_to_src(os.path.join(src, "pkg", "a.py"), src), "pkg/a.py")
        self.assertEqual(rel_to_src("repo/pkg/a.py", src, must_exist=True), "pkg/a.py")
        with self.assertRaises(ValueError):
            rel_to_src("/elsewhere/a.py", src)
        with self.assertRaises(ValueError):
            rel_to_src("../a.py", src)


class Fabric(unittest.TestCase):
    BODY = "# IDENTITY and PURPOSE\n\nYou review code for bugs.\n\n# STEPS\n\n- read\n\n# OUTPUT\n\n- list\n"

    def setUp(self):
        from ge_lib import fabric
        self.fabric = fabric
        self.tmp = tempfile.mkdtemp()
        self.man = os.path.join(self.tmp, "manifest.json")
        man = fabric.build_manifest({"review_code": self.BODY, "summarize": "# IDENTITY\n\nYou summarize.\n"},
                                    {"review_code": ["DEVELOPMENT"]})
        write(self.man, json.dumps(man))

    def test_parse_and_search(self):
        r = self.fabric.search("bugs", path=self.man)
        self.assertEqual([h["name"] for h in r["hits"]], ["review_code"])
        self.assertEqual(r["hits"][0]["sections"], ["IDENTITY and PURPOSE", "STEPS", "OUTPUT"])
        self.assertEqual(self.fabric.search("development", path=self.man)["matched"], 1)
        self.assertNotIn("body", json.dumps(r))                  # search results carry no body

    def test_show_writes_file(self):
        r = self.fabric.show("review_code", os.path.join(self.tmp, "out"), path=self.man)
        self.assertEqual(_rt(r["written"]), self.BODY)
        self.assertFalse(self.fabric.show("nope", self.tmp, path=self.man)["ok"])

    def test_missing_manifest_and_offline(self):
        r = self.fabric.search("x", path=os.path.join(self.tmp, "none.json"))
        self.assertFalse(r["ok"]); self.assertIn("--sync", r["error"])

        def offline(url, timeout):
            raise OSError("no network")
        self.assertIn("네트워크", self.fabric.sync(path=self.man, fetch=offline)["error"])
        self.assertIn("네트워크", self.fabric.check(path=self.man, fetch=offline)["error"])

    def test_check_by_blob_sha(self):
        sha = self.fabric.git_blob_sha(self.BODY)
        tree = {"tree": [{"path": "data/patterns/review_code/system.md", "sha": sha},
                         {"path": "data/patterns/summarize/system.md", "sha": "0" * 40},
                         {"path": "data/patterns/new_one/system.md", "sha": "1" * 40}]}
        r = self.fabric.check(path=self.man, fetch=lambda u, t: json.dumps(tree).encode())
        self.assertEqual(r["changed"], ["summarize"]); self.assertEqual(r["new"], ["new_one"])
        self.assertTrue(self.fabric.body_matches(self.BODY, self.fabric.git_blob_sha(self.BODY.replace("\n", "\r\n"))))

    def test_sync_from_tarball(self):
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = self.BODY.encode()
            ti = tarfile.TarInfo("fabric-main/data/patterns/review_code/system.md"); ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        out = os.path.join(self.tmp, "synced.json")
        r = self.fabric.sync(path=out, fetch=lambda u, t: buf.getvalue())
        self.assertTrue(r["ok"]); self.assertEqual(r["count"], 1)
        self.assertEqual(self.fabric.search(None, path=out)["total"], 1)

    def test_choice_gate(self):
        spec = os.path.join(self.tmp, "s.json")
        self.assertIsNone(self.fabric.choice_problem(spec))      # no fabric.md -> not checked
        write(os.path.join(self.tmp, "s", "fabric.md"), "선택:\n")
        self.assertIn("선택:", self.fabric.choice_problem(spec))
        write(os.path.join(self.tmp, "s", "fabric.md"), "선택: review_code (STEPS 만)\n")
        self.assertIsNone(self.fabric.choice_problem(spec))


class Executors(unittest.TestCase):
    """Optional worker CLIs through fake executables (no model calls)."""
    KEYS = ("GE_GROK", "GE_CURSOR", "GE_CODEX", "GE_CLAUDE", "GE_CODEX_MODEL", "FAKE_CODEX_MODE", "FAKE_CODEX_RECORD")
    OK = {"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)
        ge.PROJ = ROOT                       # other tests leave PROJ at a deleted temp folder
        os.environ["GE_GROK"] = os.path.join(TESTS, "fake_grok.py")
        os.environ["GE_CURSOR"] = os.path.join(TESTS, "fake_cursor.py")
        os.environ["GE_CODEX"] = os.path.join(TESTS, "fake_codex.py")

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_grok(self):
        payload, meta = ge.exec_grok("p", self.OK, 15)
        self.assertEqual(payload, {"ok": True}); self.assertEqual(meta["model"], "fake-grok")

    def test_grok_argv_read_tools(self):
        here = tempfile.mkdtemp(); write(os.path.join(here, "schema.json"), "{}")
        a = ge._grok_argv(here, None, cwd=here, read=False)
        self.assertIn(ge.GROK_OVERRIDE, a); self.assertEqual(a[a.index("--max-turns") + 1], "4")
        self.assertIn("read_file", a[a.index("--disallowed-tools") + 1])
        b = ge._grok_argv(here, None, cwd=here, read=True)
        self.assertIn(ge.GROK_OVERRIDE_READ, b)
        self.assertNotIn("read_file", b[b.index("--disallowed-tools") + 1])
        self.assertIn("run_terminal_command", b[b.index("--disallowed-tools") + 1])

    def test_grok_producer_without_read_refused(self):
        node = {"id": "p", "kind": "model", "schema": {"properties": {"patches": {}}}}
        self.assertIn("tools=read", ge.grok_generate_requires_read(node, "grok"))
        self.assertIsNone(ge.grok_generate_requires_read({**node, "tools": "read"}, "grok"))
        self.assertIsNone(ge.grok_generate_requires_read(node, "cursor"))

    def test_cursor(self):
        payload, meta = ge.exec_cursor("p", self.OK, 15)
        self.assertEqual(payload, {"ok": True}, meta); self.assertEqual(meta["input_tokens"], 12)

    def test_codex_flags_and_fail_closed(self):
        rec = os.path.join(tempfile.mkdtemp(), "rec.json"); os.environ["FAKE_CODEX_RECORD"] = rec
        payload, meta = ge.exec_codex("p", self.OK, 15, cwd=tempfile.gettempdir())
        self.assertEqual(payload, {"ok": True})
        a = json.loads(_rt(rec))["args"]
        self.assertEqual(a[a.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--model", a)                          # no default model: the CLI decides
        for mode in ("nonzero_valid_output", "turn_failed", "missing", "malformed"):
            os.environ["FAKE_CODEX_MODE"] = mode
            self.assertIsNone(ge.exec_codex("p", self.OK, 15)[0], mode)
        self.assertIsNone(ge.exec_codex("p", self.OK, 15, model="openai/gpt")[0])

    def test_missing_cli_is_error_meta_not_exception(self):
        os.environ["GE_CLAUDE"] = os.path.join(tempfile.gettempdir(), "no-such-claude")
        payload, meta = ge.exec_claude("p", self.OK, 5)
        self.assertIsNone(payload); self.assertIn("error", meta)

    def test_trailing_json(self):
        self.assertEqual(ge._trailing_json('서술.{"a":"b {c}"}'), {"a": "b {c}"})
        self.assertEqual(ge._trailing_json('{"a":1} {"a":2}'), {"a": 2})
        with self.assertRaises(Exception):
            ge._trailing_json("no json")

    def test_patch_anchor_must_exist_once(self):
        d = tempfile.mkdtemp(); write(os.path.join(d, "f.py"), "abc\nabc\nxyz\n")
        pl = lambda s: {"patches": [{"file": "f.py", "old_str": s}]}
        self.assertEqual(ge.patch_old_str_anchors(pl("xyz"), d), [])
        self.assertTrue(ge.patch_old_str_anchors(pl("abc"), d))
        self.assertTrue(ge.patch_old_str_anchors(pl("nope"), d))


class LedgerLocation(unittest.TestCase):
    def test_env_and_non_git(self):
        d = tempfile.mkdtemp()
        os.environ["GE_RUNS_DIR"] = os.path.join(d, "x", "runs")
        try:
            self.assertEqual(ge.resolve_ge_dir(d), os.path.join(d, "x"))
        finally:
            os.environ.pop("GE_RUNS_DIR")
        self.assertEqual(ge.resolve_ge_dir(d), os.path.join(d, ".ge"))


class CheckModel(unittest.TestCase):
    def test_returns_2_without_java_or_jar(self):
        from ge_lib import alloy_check
        old = os.environ.pop("ALLOY_JAR", None)
        try:
            self.assertEqual(alloy_check.main(jar=os.path.join(tempfile.gettempdir(), "no-alloy.jar"), quiet=True), 2)
        finally:
            if old is not None:
                os.environ["ALLOY_JAR"] = old

    @unittest.skipUnless(os.environ.get("ALLOY_JAR") and os.path.isfile(os.environ["ALLOY_JAR"]), "Alloy jar 없음")
    def test_model_checks_pass_and_negative_control_finds_counterexample(self):
        from ge_lib import alloy_check
        java = alloy_check.find_java()
        if not java:
            self.skipTest("java 없음")
        rows = {r["command"]: r for r in alloy_check.verdicts(alloy_check.run_model(java, os.environ["ALLOY_JAR"]))}
        self.assertTrue(all(r["ok"] for r in rows.values()), rows)
        self.assertTrue(rows["GateRemoved"]["solution"])


class Guard(unittest.TestCase):
    """결정적 훅(ge_lib/guard.py). 실제로 있었던 네 실패를 각각 fixture 로 고정한다.
    가짜 실행자만 — 모델 호출 0. 계약(contract) 이 없는 명세는 옛 동작 그대로여야 한다(비회귀)."""
    PATCH_SCHEMA = {"type": "object", "required": ["patches"], "properties": {"patches": {"type": "array", "items": {
        "type": "object", "required": ["file", "old_str", "new_str", "why"], "properties": {
            "file": {"type": "string"}, "old_str": {"type": "string", "minLength": 10},
            "new_str": {"type": "string"}, "why": {"type": "string", "minLength": 10}}}}}}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_dir, ge.GE_DIR = ge.GE_DIR, os.path.join(self.tmp, ".ge")
        os.makedirs(os.path.join(self.tmp, "ge", "t"), exist_ok=True)
        self._w("ge/t/facts.md", "# facts\n모수: 59 (계정,검색어)\n")
        self._w("ge/t/prompt.md", "patch 를 내라")
        self._w("ge/t/verify.md", "{{rubric}}")
        self._w("ge/t/rubric.md", "row_unit: 행 단위가 맞는가")
        self._probe([{"id": "row_count", "ok": True, "actual": 59, "max": 200}])   # 기본 probe: 통과
        self.calls = {}

    def tearDown(self):
        ge.GE_DIR = self.old_dir
        ge.EXECUTORS.pop("fake", None)

    def _w(self, rel, text):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def _probe(self, checks):
        return self._w("ge/t/probe.py", "import json,sys; print(json.dumps({'checks': %r}))" % checks)

    def _spec(self, contract=None, cwd=None, verdicts=None, patches=None):
        patches = patches if patches is not None else [{"file": "report.py", "old_str": "0123456789ab",
                                                        "new_str": "x", "why": "because it is needed"}]
        verdicts = verdicts if verdicts is not None else [{"item_id": "row_unit", "verdict": "pass", "reason": "ok fine"}]
        outputs = {"patch": {"patches": patches}, "verify": {"verdicts": verdicts}}

        def fake(prompt, schema, timeout, model=None, cwd=None, tag=None, tools=None):
            nid = prompt.rsplit("[node:", 1)[1].rstrip("]")
            self.calls.setdefault(nid, []).append(prompt)
            return outputs[nid], {"executor": "fake", "model": "fake"}
        ge.EXECUTORS["fake"] = fake
        nodes = [{"id": "patch", "kind": "model", "prompt": "ge/t/prompt.md", "inputs": [], "schema": self.PATCH_SCHEMA},
                 {"id": "verify", "kind": "verify", "prompt": "ge/t/verify.md", "rubric": "ge/t/rubric.md", "inputs": ["patch"]},
                 {"id": "apply", "kind": "script", "script": "ge/t/apply.py", "inputs": ["verify"]}]
        self._w("ge/t/apply.py", "import json; print(json.dumps({'applied': True}))")
        if cwd:
            for n in nodes[:2]:
                n["cwd"] = cwd
        spec = {"name": "t", "nodes": nodes}
        if contract is not None:
            spec["contract"] = contract
        p = os.path.join(self.tmp, "spec.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        return ge.load_spec(p)

    def _run(self, spec, rid="g1", baseline=False, **kw):
        # 기본은 baseline 꺼짐 — 옛 시험이 probe 실패를 patch 뒤 단계에서 보게. baseline 은 아래 GuardSpeed 시험이 켠다.
        ok = ge.Run(spec, rid, "fake", baseline=baseline, **kw).run()
        run_dir = os.path.join(ge.GE_DIR, "runs", rid)
        rows = [json.loads(l) for l in _rt(os.path.join(run_dir, "ledger.jsonl"), encoding="utf-8").splitlines(True) if l.strip()]
        return ok, rows, run_dir

    def _art(self, run_dir, name):
        p = os.path.join(run_dir, "artifacts", name + ".json")
        return json.loads(_rt(p, encoding="utf-8")) if os.path.isfile(p) else None

    C = {"version": 1, "inputs": [{"id": "facts", "path": "ge/t/facts.md", "mode": "inline", "verified_by": "SELECT COUNT(*) ..."}],
         "scope": {"files": ["report.py"], "tables": ["orders"]},
         "runtime_checks": ["row_count"], "requires": {"row_unit": ["row_count"]},
         "probe": {"script": "ge/t/probe.py"}}

    # --- 비회귀: 계약 없는 명세는 옛 동작 ---
    def test_no_contract_keeps_old_behaviour(self):
        spec = self._spec(verdicts=[{"item_id": "row_unit", "verdict": "fail", "reason": "bad bad"}])
        ok, rows, run_dir = self._run(spec)
        self.assertTrue(ok)                                   # verify fail 이어도 런은 계속 (옛 동작)
        self.assertFalse([r for r in rows if r["event"] == "guard"])
        self.assertIsNone(self._art(run_dir, "guard-acceptance"))

    # --- 정상 경로: 계약 + probe ok + verify pass -> apply 까지 ---
    def test_script_producer_triggers_probe(self):
        # 패치를 script 노드(정해진 치환)가 내도 probe → acceptance gate 가 돈다.
        self._probe([{"id": "row_count", "ok": True, "actual": 59, "max": 200}])
        spec = self._spec(self.C)
        self._w("ge/t/patch.py", "import json; print(json.dumps({'patches': [{'file': 'report.py', 'old_str': '0123456789ab', "
                                 "'new_str': 'x', 'why': 'because it is needed'}]}))")
        spec["nodes"][0] = {"id": "patch", "kind": "script", "script": "ge/t/patch.py", "inputs": []}
        ok, rows, run_dir = self._run(spec)
        self.assertTrue(ok, rows)
        phases = [r["phase"] for r in rows if r["event"] == "guard"]
        self.assertEqual(phases, ["runtime_probe", "preflight", "acceptance_gate"])   # script 는 preflight 없음(워커 아님)
        self.assertEqual(self._art(run_dir, "guard-probe")["after_node"], "patch")

    def test_script_without_patches_skips_probe(self):
        spec = self._spec(self.C)
        self._w("ge/t/patch.py", "import json; print(json.dumps({'note': 'no patches'}))")
        spec["nodes"][0] = {"id": "patch", "kind": "script", "script": "ge/t/patch.py", "inputs": []}
        ok, rows, run_dir = self._run(spec)
        self.assertNotIn("runtime_probe", [r.get("phase") for r in rows if r["event"] == "guard"])

    def test_contract_happy_path(self):
        self._probe([{"id": "row_count", "ok": True, "actual": 59, "max": 200}])
        ok, rows, run_dir = self._run(self._spec(self.C))
        self.assertTrue(ok, rows)
        phases = [r["phase"] for r in rows if r["event"] == "guard"]
        self.assertEqual(phases, ["preflight", "runtime_probe", "preflight", "acceptance_gate"])
        self.assertTrue(all(r["ok"] for r in rows if r["event"] == "guard"))
        self.assertTrue(self._art(run_dir, "guard-acceptance")["ok"])
        self.assertIn("apply", [r["node"] for r in rows if r["event"] == "node_done"])
        # 검사자도 같은 계약 입력을 받는다 — facts 를 못 읽었다는 핑계가 사라진다
        self.assertIn("모수: 59", self.calls["verify"][0])
        self.assertIn("모수: 59", self.calls["patch"][0])
        self.assertNotIn("미검증", self.calls["patch"][0])

    # --- 실패 1: facts 가 워커 cwd 에서 안 닿는다 ---
    def test_preflight_blocks_unreachable_ref_input(self):
        c = dict(self.C, inputs=[{"id": "facts", "path": "ge/t/facts.md", "mode": "ref"}])
        other = tempfile.mkdtemp()                            # 다른 레포 흉내: ge/ 폴더가 없다
        ok, rows, run_dir = self._run(self._spec(c, cwd=other))
        self.assertFalse(ok)
        pre = [r for r in rows if r["event"] == "guard" and r["phase"] == "preflight"]
        self.assertEqual(len(pre), 1)
        self.assertFalse(pre[0]["ok"])
        self.assertTrue(any("cwd 기준으로 못 읽음" in p for p in pre[0]["problems"]))
        self.assertNotIn("patch", self.calls)                 # 워커를 부르지 않았다
        self.assertIn("run_aborted", [r["event"] for r in rows])

    def test_preflight_sha_mismatch(self):
        c = dict(self.C, inputs=[{"id": "facts", "path": "ge/t/facts.md", "mode": "inline", "sha256": "0" * 64}])
        ok, rows, run_dir = self._run(self._spec(c))
        self.assertFalse(ok)
        self.assertTrue(any("sha256 불일치" in p for r in rows if r["event"] == "guard" for p in r.get("problems", [])))

    def test_unverified_input_is_flagged_to_worker(self):
        c = dict(self.C, inputs=[{"id": "facts", "path": "ge/t/facts.md", "mode": "inline"}])
        self._probe([{"id": "row_count", "ok": True}])
        ok, rows, run_dir = self._run(self._spec(c))
        self.assertTrue(ok)
        self.assertIn("[미검증", self.calls["patch"][0])       # 틀린 모수를 그대로 믿지 않게 표시한다

    # --- 실패 2: verify 는 pass 인데 실행하면 22,796행 ---
    def test_probe_failure_blocks_run(self):
        self._probe([{"id": "row_count", "ok": False, "actual": 22796, "max": 200}])
        ok, rows, run_dir = self._run(self._spec(self.C))
        self.assertFalse(ok)
        pr = self._art(run_dir, "guard-probe")
        self.assertFalse(pr["ok"])
        self.assertTrue(any("22796" in p for p in pr["problems"]))
        self.assertNotIn("verify", self.calls)                 # probe 가 죽으면 verify 도 안 부른다 (토큰 절약)

    def test_requires_unmet_when_probe_missing_check(self):
        self._probe([{"id": "latency", "ok": True}])           # row_count 를 안 냈다
        ok, rows, run_dir = self._run(self._spec(self.C))
        self.assertFalse(ok)
        pr = self._art(run_dir, "guard-probe")
        self.assertTrue(any("안 낸 검사" in p for p in pr["problems"]))

    # --- 신선도 같은 품질 신호는 기록만 (record_only) ---
    def test_record_only_check_does_not_block(self):
        c = dict(self.C, runtime_checks=["row_count", "stale_rate"], record_only=["stale_rate"])
        self._probe([{"id": "row_count", "ok": True, "actual": 59, "max": 200},
                     {"id": "stale_rate", "ok": False, "actual": 1.0, "max": 0.9, "stale": 29}])
        ok, rows, run_dir = self._run(self._spec(c))
        self.assertTrue(ok, rows)                              # 런이 죽지 않는다
        pr = self._art(run_dir, "guard-probe")
        self.assertTrue(pr["ok"])
        self.assertEqual(pr["problems"], [])
        self.assertTrue(any("기록 stale_rate" in r for r in pr["recorded"]))   # 그래도 남는다
        g = [r for r in rows if r["event"] == "guard" and r["phase"] == "runtime_probe"][0]
        self.assertTrue(any("stale_rate" in r for r in g["recorded"]))
        self.assertIn("verify", self.calls)

    def test_record_only_does_not_soften_runaway(self):
        c = dict(self.C, runtime_checks=["row_count", "stale_rate"], record_only=["stale_rate"])
        self._probe([{"id": "row_count", "ok": False, "actual": 22796, "max": 200},
                     {"id": "stale_rate", "ok": False, "actual": 1.0, "max": 0.9}])
        ok, rows, run_dir = self._run(self._spec(c))
        self.assertFalse(ok)                                   # 폭주는 여전히 막는다
        pr = self._art(run_dir, "guard-probe")
        self.assertTrue(any("22796" in p for p in pr["problems"]))
        self.assertFalse(any("stale_rate" in p for p in pr["problems"]))

    def test_record_only_check_still_must_be_measured(self):
        c = dict(self.C, runtime_checks=["row_count", "stale_rate"], record_only=["stale_rate"])
        self._probe([{"id": "row_count", "ok": True}])          # stale_rate 를 안 쟀다
        ok, rows, run_dir = self._run(self._spec(c))
        self.assertFalse(ok)                                   # 기록만이라도 재기는 해야 한다
        self.assertTrue(any("안 낸 검사" in p for p in self._art(run_dir, "guard-probe")["problems"]))

    def test_record_only_must_be_subset_of_runtime_checks(self):
        c = dict(self.C, record_only=["stale_rate"])           # runtime_checks 에 없다
        with self.assertRaises(AssertionError):
            self._spec(c)

    def test_probe_timeout_is_failure(self):
        self._w("ge/t/probe.py", "import time; time.sleep(5)")
        c = dict(self.C, probe={"script": "ge/t/probe.py", "timeout": 1})
        ok, rows, run_dir = self._run(self._spec(c))
        self.assertFalse(ok)
        self.assertTrue(any("timeout" in p for p in self._art(run_dir, "guard-probe")["problems"]))

    def test_runtime_checks_require_probe_script(self):
        c = {k: v for k, v in self.C.items() if k != "probe"}
        with self.assertRaises(AssertionError):
            self._spec(c)

    # --- 실패 3: verify fail 인데 apply 로 갔다 ---
    def test_verify_fail_aborts_with_contract(self):
        self._probe([{"id": "row_count", "ok": True}])
        spec = self._spec(self.C, verdicts=[{"item_id": "row_unit", "verdict": "fail", "reason": "wrong unit"}])
        ok, rows, run_dir = self._run(spec)
        self.assertFalse(ok)
        g = self._art(run_dir, "guard-acceptance")
        self.assertIn("verify_fail", [b["rule"] for b in g["blocks"]])
        self.assertNotIn("apply", [r["node"] for r in rows if r["event"] == "node_started"])
        self.assertIn("run_aborted", [r["event"] for r in rows])

    def test_verify_unverifiable_aborts(self):
        self._probe([{"id": "row_count", "ok": True}])
        spec = self._spec(self.C, verdicts=[{"item_id": "row_unit", "verdict": "unverifiable", "reason": "cannot see"}])
        ok, rows, run_dir = self._run(spec)
        self.assertFalse(ok)
        self.assertIn("verify_unverifiable", [b["rule"] for b in self._art(run_dir, "guard-acceptance")["blocks"]])

    # --- 실패 4: 요청 밖 데이터 소스 ---
    def test_scope_violation_needs_approval_then_approve(self):
        self._probe([{"id": "row_count", "ok": True}])
        patches = [{"file": "report.py", "old_str": "0123456789ab", "why": "because it is needed",
                    "new_str": "SELECT * FROM bid_estimate JOIN orders k"},
                   {"file": "js/common.js", "old_str": "0123456789ab", "new_str": "y", "why": "because it is needed"}]
        ok, rows, run_dir = self._run(self._spec(self.C, patches=patches))
        self.assertFalse(ok)
        g = self._art(run_dir, "guard-acceptance")
        keys = sorted(x["key"] for x in g["needs_approval"])
        self.assertEqual(keys, ["file:js/common.js", "table:bid_estimate"])
        self.assertIn("scope", [b["rule"] for b in g["blocks"]])
        # 사람이 둘 다 승인하면 gate 가 지나간다 (원장 approvals.jsonl 에 남는다)
        for k in keys:
            ge.guard.approve(run_dir, k, who="tester")
        again = ge.guard.acceptance_gate(ge.load_spec(os.path.join(self.tmp, "spec.json"))["_contract"], run_dir,
                                         {"patch": self._art(run_dir, "patch")}, self._art(run_dir, "verify"),
                                         self._art(run_dir, "guard-probe"), [])
        self.assertTrue(again["ok"], again)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "approvals.jsonl")))

    def test_contract_changes_spec_sha(self):
        a = self._spec(self.C)["_sha"]
        b = self._spec(dict(self.C, scope={"files": ["other.py"]}))["_sha"]
        self.assertNotEqual(a, b)

    # ---------- A baseline / B resume / C mechanical / D cause ----------
    def test_A_baseline_aborts_before_worker(self):
        self._probe([{"id": "row_count", "ok": False, "why": "원본에 이미 금지 토큰"}])
        ok, rows, run_dir = self._run(self._spec(self.C), rid="ba1", baseline=True)
        self.assertFalse(ok)
        self.assertNotIn("patch", self.calls)                       # 워커 0회
        g = [r for r in rows if r["event"] == "guard"]
        self.assertEqual([x["phase"] for x in g], ["baseline"])
        self.assertFalse(g[0]["ok"])
        self.assertIn("facts 오류", [r for r in rows if r["event"] == "run_aborted"][0]["cause"])

    def test_A_baseline_pass_and_off(self):
        ok, rows, _ = self._run(self._spec(self.C), rid="ba2", baseline=True)
        self.assertTrue(ok, rows)
        self.assertEqual([r["phase"] for r in rows if r["event"] == "guard"][0], "baseline")
        self._probe([{"id": "row_count", "ok": False, "why": "x"}])
        self.calls.clear()
        ok, rows, _ = self._run(self._spec(self.C), rid="ba3", baseline=False)     # --no-baseline: 워커까지 간다
        self.assertFalse(ok)
        self.assertIn("patch", self.calls)
        self.assertFalse([r for r in rows if r["event"] == "guard" and r["phase"] == "baseline"])

    def test_B_resume_reuses_patch(self):
        self._probe([{"id": "row_count", "ok": False, "why": "probe 버그"}])
        ok, _, _ = self._run(self._spec(self.C), rid="rs1")
        self.assertFalse(ok)
        self.assertEqual(len(self.calls["patch"]), 1)
        self._probe([{"id": "row_count", "ok": True}])               # probe 고침
        self.calls.clear()
        ok, rows, run_dir = self._run(self._spec(self.C), rid="rs2", resume="rs1", resume_from="guard")
        self.assertTrue(ok, rows)
        self.assertNotIn("patch", self.calls)                       # 워커 재호출 없음
        self.assertIn("verify", self.calls)
        self.assertEqual([r for r in rows if r["event"] == "run_started"][0]["resumed_from"], "rs1")
        self.assertTrue([r for r in rows if r["event"] == "node_resumed" and r["node"] == "patch"])
        self.assertEqual(self._art(run_dir, "patch"), self._art(os.path.join(ge.GE_DIR, "runs", "rs1"), "patch"))

    def test_B_resume_without_artifacts_aborts(self):
        ok, rows, _ = self._run(self._spec(self.C), rid="rs3", resume="nope", resume_from="guard")
        self.assertFalse(ok)
        self.assertNotIn("patch", self.calls)

    def _mech_spec(self, model=None, thinking=None):
        c = dict(self.C, runtime_checks=["row_count", "lat"], requires={"row_unit": ["row_count"], "lat_ok": ["lat"]},
                 mechanical=["row_unit"])
        self._probe([{"id": "row_count", "ok": True}, {"id": "lat", "ok": True}])
        self._w("ge/t/rubric.md", "1. row_unit — 행 단위가 맞는가\n   (이어지는 줄)\n2. lat_ok — 판단 항목\n")
        spec = self._spec(c, verdicts=[{"item_id": "lat_ok", "verdict": "pass", "reason": "ok fine"}])
        v = spec["nodes"][1]
        if model:
            v["model"] = model
        if thinking:
            v["verify_thinking"] = thinking
        return spec

    def test_C_mechanical_items_filled_by_probe(self):
        ok, rows, run_dir = self._run(self._mech_spec(), rid="mc1")
        self.assertTrue(ok, rows)
        prompt = self.calls["verify"][0]
        self.assertNotIn("row_unit", prompt)                         # LLM 은 못 본다
        self.assertNotIn("이어지는 줄", prompt)
        self.assertIn("lat_ok", prompt)
        vs = {v["item_id"]: v for v in self._art(run_dir, "verify")["verdicts"]}
        self.assertEqual(vs["row_unit"]["verdict"], "pass")
        self.assertIn("mechanical", vs["row_unit"]["reason"])
        c = ge.guard.mechanical_verdicts(self._mech_spec()["_contract"], {"checks": [{"id": "row_count", "ok": False, "why": "22796행"}]})
        self.assertEqual(c[0]["verdict"], "fail")
        self.assertIn("22796", c[0]["reason"])

    def test_C_mechanical_needs_requires(self):
        c = dict(self.C, mechanical=["ghost"])
        with self.assertRaises(AssertionError):
            self._spec(c)

    def test_C_verify_thinking_rewrites_model_suffix(self):
        seen = {}
        spec = self._mech_spec(model="claude-sonnet-5-thinking-high", thinking="medium")
        inner = ge.EXECUTORS["fake"]

        def spy(prompt, schema, timeout, model=None, **kw):
            seen[prompt.rsplit("[node:", 1)[1].rstrip("]")] = model
            return inner(prompt, schema, timeout, model, **kw)
        ge.EXECUTORS["fake"] = spy
        self._run(spec, rid="mc2")
        self.assertEqual(seen["verify"], "claude-sonnet-5-thinking-medium")
        seen.clear()
        spec = self._mech_spec(model="claude-sonnet-5-thinking-high")        # 필드 없으면 그대로
        inner = ge.EXECUTORS["fake"]
        ge.EXECUTORS["fake"] = spy
        self._run(spec, rid="mc3")
        self.assertEqual(seen["verify"], "claude-sonnet-5-thinking-high")

    def test_D_cause_in_events_and_stdout(self):
        import contextlib, io
        self._probe([{"id": "row_count", "ok": False, "actual": 22796, "max": 200}])
        ok, rows, _ = self._run(self._spec(self.C), rid="cz1")
        self.assertFalse(ok)
        ab = [r for r in rows if r["event"] == "run_aborted"][0]
        done = [r for r in rows if r["event"] == "run_done"][0]
        self.assertIn("검사 실패 row_count", ab["cause"])
        self.assertEqual(done["cause"], ab["cause"])
        buf = io.StringIO()
        sp = os.path.join(self.tmp, "spec.json")
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            ge.main(["run", sp, "--executor", "fake", "--run-id", "cz2", "--no-baseline"])
        self.assertEqual(cm.exception.code, 1)
        last = buf.getvalue().strip().splitlines()[-1]
        self.assertTrue(last.startswith("실패 원인: "), last)
        self.assertIn("row_count", last)

    def test_D_verify_fail_cause(self):
        spec = self._spec(self.C, verdicts=[{"item_id": "row_unit", "verdict": "fail", "reason": "bad bad"}])
        ok, rows, _ = self._run(spec, rid="cz3")
        self.assertFalse(ok)
        self.assertIn("verify_fail row_unit", [r for r in rows if r["event"] == "run_done"][0]["cause"])


if __name__ == "__main__":
    unittest.main()
