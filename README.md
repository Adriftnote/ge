# ge — a work-graph coordinator for AI coding agents

[한국어](README.ko.md)

**Experimental.** A personal tool, published as is.

`ge` runs a task as a graph of nodes. Each node gets one worker. The worker's output must match a JSON schema.
A separate node checks it, and every step leaves a record in a ledger. Each node can use a different provider's
model — Claude, Codex, Cursor or Grok through their command-line tools — so one model makes and another reviews.

```
patch (model A) ──► probe (tests on a temp copy) ──► verify (model B, per-item verdicts) ──► apply (script, to a copy)
```

A wrong patch stopped by the probe, before verify and apply ran
(`./ge run examples/fix-and-test-bad.json --executor stub`, ledger trimmed to the main fields):

```
{"event": "run_started", "spec": "fix-and-test-bad", "executor": "stub"}
{"event": "guard", "phase": "baseline", "ok": true, "problems": [], "checks": {"tests_pass": true}}
{"event": "node_started", "node": "patch"}
{"event": "guard", "phase": "preflight", "node": "patch", "ok": true, "problems": []}
{"event": "call", "node": "patch", "attempt": 1, "ok": true}
{"event": "node_done", "node": "patch", "ok": true}
{"event": "guard", "phase": "runtime_probe", "node": "patch", "ok": false, "problems": ["검사 실패 tests_pass: {\"why\": \"FAILED (failures=1)\"}"], "checks": {"tests_pass": false}}
{"event": "run_aborted", "node": "patch", "cause": "probe: 검사 실패 tests_pass: {\"why\": \"FAILED (failures=1)\"}"}
{"event": "run_done", "ok": false, "cause": "probe: 검사 실패 tests_pass: {\"why\": \"FAILED (failures=1)\"}"}
```

## Why

I subscribe to models from several companies and wanted them to work on the same task: one makes, another
checks the result.

Before this I built [yar-mcp-server](https://github.com/Adriftnote/yar-mcp-server), an MCP server that lets coding
sessions chat with each other. In my tests the conversation did not turn into finished work, and it used a lot
of tokens. So I switched to the way node editors such as Blender or Houdini work: outputs become inputs to later
nodes, and the original is not modified along the way. What moves between nodes is an artifact, not a chat.

I also saw a model in one long context say it had checked something it never opened. Splitting the work into
nodes with a separate checker and a ledger made those cases visible.

## What it does

1. **Spec (graph)** — a JSON file of nodes and edges (`inputs`).
2. **One worker per node** — each attempt calls one worker CLI. The output must match the node's schema;
   on failure the errors are attached to the retry prompt.
3. **Preflight** — with a contract, the coordinator reads the declared inputs and checks their sha256 before
   calling a worker. If they changed or the worker cannot reach them, the worker is not called.
4. **Making and checking are separate nodes** — the coordinator includes the rubric only in verify prompts.
   Verdicts are per item (`pass` / `fail` / `unverifiable`) with a reason. Probes (tests, counts) can fill
   items a model should not judge.
5. **Apply gate** — a node marked `"apply": true` must take a verify node as input (checked at load) and runs
   only if all its inputs succeeded and every verdict is `pass` (checked at run time).
6. **Ledger** — `.ge/runs/<run-id>/ledger.jsonl` records each call, hash, token count and verdict.
   "The run finished" and "the checker passed it" are recorded separately. `ge ledger` shows pass rate and
   rework per role, split by a hash of prompt + rubric + lessons.

## Safety boundary

- Run only specs and scripts you trust. Script and probe nodes are ordinary Python with your permissions.
- Non-destructive is the behavior of the shipped apply, not a runtime sandbox. The generated `apply.py`
  writes only to a copy given as `GE_SRC` (a linked git worktree, or a folder marked with `.ge-copy`),
  and stops if the copy is missing, if `verify.json` is missing, or if any verdict is not `pass`.
- Workers run in their CLIs' read-only modes (see Executors). That is the CLI's enforcement, not ge's.
- The ledger stores the host name, absolute spec paths and pointers to raw worker output.
  Remove those before sharing a ledger.

## Quick start

Python 3.10+ and the standard library only (CI runs 3.10 and 3.12; 3.9.6 also passed locally). The examples need no AI:

```
git clone https://github.com/Adriftnote/ge && cd ge
./ge run examples/summarize.json        --executor stub
./ge run examples/fix-and-test.json     --executor stub
./ge run examples/fix-and-test-bad.json --executor stub    # blocked by the probe, exit 1
./ge progress
python3 -m unittest discover -s tests
```

`summarize`: summarize a document → separate verify → script renders Markdown.
`fix-and-test`: propose a patch → probe applies it to a temp copy and runs the tests → verify → acceptance gate →
apply writes the fixed files into the run folder. With `--executor stub`, nodes return the canned outputs in `stub_output`.

To start your own graph: `./ge new <name> --cwd <repo> --target <file>` writes `graphs/<name>.json` and
`graphs/<name>/`. See [docs/designing-graphs.md](docs/designing-graphs.md).

## Spec format

```json
{"name": "fix-and-test", "nodes": [
  {"id": "patch",  "kind": "model",  "prompt": "fix-and-test/patch.md", "inputs": [], "schema": {"...": "..."},
   "cwd": "fix-and-test/src", "retry": 2},
  {"id": "verify", "kind": "verify", "prompt": "fix-and-test/verify.md", "rubric": "fix-and-test/rubric.md",
   "inputs": ["patch"], "executor": "codex"},
  {"id": "apply",  "kind": "script", "script": "fix-and-test/apply.py", "apply": true, "inputs": ["patch", "verify"]}
],
 "contract": {"version": 1, "inputs": [{"id": "target", "path": "fix-and-test/src/stats.py", "mode": "inline", "sha256": "..."}],
              "scope": {"files": ["stats.py"]}, "runtime_checks": ["tests_pass"],
              "requires": {"tests_pass": ["tests_pass"]}, "mechanical": ["tests_pass"],
              "probe": {"script": "fix-and-test/probe.py"}}}
```

In prompts: `{{input.<node>}}` (that node's artifact), `{{rubric}}`, `{{item}}` (fanout), `{{<key>}}` (`--input key=value`).
Other node keys: `model`, `executor`, `timeout`, `fanout`, `when` (routing), `required`, `lessons`, `tools` (grok).
Relative paths resolve against the spec's folder (or the parent of a `graphs/` folder).

### Supported schema subset

| Keys | Applies to |
|---|---|
| `type` (one of `object` `array` `string` `integer` `number` `boolean` `null`; not a list) | all |
| `properties`, `required`, `additionalProperties: false` | object |
| `items`, `minItems`, `maxItems` | array |
| `minLength`, `maxLength`, `pattern` | string |
| `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum` | integer, number |
| `enum`, `const` | all |
| `title`, `description`, `$comment`, `default`, `examples` | ignored (annotations) |

Any other key (`oneOf`, `format`, `$ref`, …) makes `ge plan` / `ge run` refuse the spec instead of being ignored.

## Executors

| `--executor` | Needs | How it runs |
|---|---|---|
| `claude` (default) | `claude` CLI | `claude -p` with `--json-schema`; edit/shell/web tools disallowed |
| `codex` | `codex` CLI | `codex exec --sandbox read-only --output-schema` |
| `cursor` | `cursor-agent` CLI | `-p --mode ask` (read-only); schema enforced by validation |
| `grok` | `grok` CLI | headless with action tools disallowed; `tools: "read"` allows reading files |
| `stub` | nothing | fills the schema or returns `stub_output`; for tests and demos |

Each CLI is found on `PATH` (or `GE_CLAUDE`, `GE_CODEX`, `GE_CURSOR`, `GE_GROK`). `ge` uses each CLI's own login
and stores no API keys; for `claude` it removes `ANTHROPIC_API_KEY` from the worker environment so the CLI login
is used. A node's `executor` overrides `--executor`; with `--executor stub` every node uses the stub.
The tests use fake CLIs, which check the parsing contract, not compatibility with current releases.

Real CLIs checked:

| CLI | Version checked | Date | Result |
|---|---|---|---|
| claude | 2.1.289 | 2026-10-30 | not checked (local login had expired) |
| codex | codex-cli 0.148.0 | 2026-10-30 | `examples/summarize.json` ran end to end; the verify node failed 3 items and the gate stopped the run (expected behavior) |
| cursor-agent | — | — | not checked (not installed on the test machine) |
| grok | 1.0.46 | 2026-10-30 | `examples/summarize.json` passed (`ok: true`) |

## Structural rules checked with Alloy

The checker model is not the only check. `formal/ge_invariants.als` is a small Alloy 6 model of the intended
safe-execution policy. Within a bounded scope it finds no counterexample to four properties: apply runs only after
a passed (and, with a contract, approved) verify; nothing writes the original folder; a node that fails preflight
is not called and nothing downstream runs; execution is acyclic and verify comes after the node it checks.
A negative control drops the apply gate and a counterexample appears. The model checks the rules, not the Python
code; tests check that the code follows them. Outside the model: specs without a contract, nodes with
`required: false`, and arbitrary script nodes (see the comments in the `.als` file).

```
ALLOY_JAR=/path/to/org.alloytools.alloy.dist.jar ./ge check-model   # exit 2 if java or the jar is missing
```

## Other commands

`ge plan`, `ge new`, `ge workflows`, `ge stats --topologies`, `ge fabric`, `ge ledger`, `ge lesson`,
`ge contract-sha`, `ge approve`, `ge trace`, `ge note`, `ge report`, `ge check-model`.

`ge fabric` searches the [Fabric](https://github.com/danielmiessler/fabric) prompt patterns (MIT, Daniel Miessler)
while you write a spec. The corpus is not bundled; `ge fabric --sync` downloads it once. Borrow the structure
(steps, review axes, output layout); keep per-item verdicts with evidence rather than one overall score.

## Language

CLI messages, error strings and the `ge new` templates are in Korean. Commands, flags and JSON keys are English.

## Limits

- A personal tool, used mainly on my own work. Interfaces may change.
- The checker is also a model and has blind spots. Probes and the gates reduce this; they do not remove it.
- The coordinator does not compare the verdict ids with the rubric item ids; a skipped rubric item is caught
  only by the prompt and by mechanical items.
- `ge` is one 1,900-line file. Splitting out executors and reporting is a follow-up.
- Implementation was done together with AI coding agents. The design and the decisions are mine.

## Background

Numbers from my own logs before publishing (raw data is private; small samples; not a benchmark):
rules written only as prose were followed in 2 of 4 cases, rules backed by an automatic check in 11 of 13;
of 32 runs with a verify node, 11 had a mismatch between "the run succeeded" and the checker's verdict.

## License

MIT © 2026 Adriftnote
