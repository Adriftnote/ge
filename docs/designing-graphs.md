# Designing a work graph

How to split a task into nodes for `ge`. Short version: start from a recipe, let the coordinator own
everything two workers must agree on, and keep checking separate from making.

## 0. Look for a similar shape first

```
./ge workflows                 # recipes (scaffold layouts) in ge_lib/workflows/
./ge stats --topologies        # node/edge shapes already used in your specs and ledgers
```

- If a recipe or an existing spec has the shape you need, copy it: `./ge new <name> --workflow <recipe> ...`.
- If nothing fits, draw a new graph by hand.
- When the same new shape shows up in two or more specs, `ge stats` marks it `[promote]`. Only then add it as a
  recipe in `ge_lib/workflows/`. One-off shapes stay one-off.

Example output on the two shipped examples:

```
Spec topologies:
- 3 specs, 3 runs (2 ok): model -> verify -> script  [recipe: patch, research]
  edges: 0:model→1:verify, 0:model→2:script, 1:verify→2:script
```

## 1. Start from the scaffold, not a blank file

```
./ge new fix-login --root ~/work/my-graphs --cwd ~/work/app --target src/login.py
```

This writes `graphs/fix-login.json` and `graphs/fix-login/{patch.md, verify.md, rubric.md, probe.py, apply.py, fabric.md}`
under `--root` (default: the current folder; the folder name can be changed with `GE_GRAPHS_DIR`). It never overwrites.
`--cwd` is the repository the worker reads. The generated apply writes to a separate copy:

```
git -C ~/work/app worktree add ~/work/app-ge
GE_SRC=~/work/app-ge ./ge run ~/work/my-graphs/graphs/fix-login.json --executor codex
```

Without `GE_SRC`, or when `GE_SRC` is a main checkout instead of a linked worktree, apply stops and writes nothing. The scaffold exists because hand-written specs kept missing the same four things:
the contract inside the prompt, a separate checker, a script for the apply step, and a lessons slot.

## 2. Three kinds of node

| Kind | Does | Example |
|---|---|---|
| `model` (making) | asks one worker for one artifact, forced to a JSON schema | write a patch, summarize a doc |
| `verify` (checking) | a separate call judges the artifact against a rubric | per-item pass / fail / unverifiable + reason |
| `script` (counting) | plain code: apply, count, lint, render, diff | apply a verified patch to a copy |

A script node that writes results is marked `"apply": true`. It must take the verify node as a direct input
(`ge plan` refuses otherwise) and runs only if every input succeeded and every verdict is `pass`.

Rules of thumb:

- Anything you can count or check with code goes to a `script` node, not a model.
- A making node produces one thing. If it produces two unrelated things, split it.
- The checker is its own node. Prefer a different model lineage from the maker
  (set `executor` / `model` on the verify node), so they do not share blind spots.

## 3. The coordinator owns the contract

Anything two nodes must agree on — key names, number of slots, formulas, file paths — is decided once in the
spec and written **into each worker's prompt**. Workers never talk to each other.

- Facts the workers need go in `contract.inputs`. The coordinator reads them, checks the sha256 (preflight),
  and attaches the same text to every node, the checker included.
- `contract.scope` lists the files (and tables) a change may touch. Anything outside needs `ge approve`.
- `contract.runtime_checks` + `contract.probe` run real code after the making node (tests, row counts, timing).
  `contract.mechanical` marks rubric items the probe answers, so the model does not judge them.

## 4. Verify: one verdict per rubric item, with evidence

- Number the rubric items and give each an id. The verify prompt says: do not skip an item; write a line even
  when nothing is wrong. Otherwise "not looked at" and "no problem" look the same.
- No evidence, no pass. If the checker cannot see it, the answer is `unverifiable` and why.
- Only the verify node reads the rubric. The making node does not see it.
- An apply node never runs after a `fail` or `unverifiable`. With a contract, the whole run aborts at the
  acceptance gate.
- The coordinator does not compare verdict ids with rubric ids. Completeness depends on the prompt and on
  `contract.mechanical` items that the probe fills.

## 5. Paths are relative

- `patches[].file` is relative to the worker's `cwd`. Absolute paths and `..` are refused when applying.
- Paths inside the spec (prompt, rubric, script, contract inputs) are relative to the spec's folder
  (or the parent of a `graphs/` folder).

## 6. Lessons go to making nodes only

```
./ge lesson graphs/fix-login.json patch "old_str must be copied from the file, not retyped"
```

One line per lesson, then link the file from the node's `lessons` key. The checker never gets lessons — it would
start seeing the work with the author's eyes. `load_spec` refuses a verify node with lessons.
Lessons change the role hash, so `ge ledger` shows rework before and after.

## 7. Borrow prompt skeletons, not formats (optional)

`ge fabric` searches the [Fabric](https://github.com/danielmiessler/fabric) prompt patterns (MIT) by name,
purpose and tag. It is a spec-writing aid, not a runtime node.

```
./ge fabric --sync             # once: download upstream and build a local manifest (~/.cache/ge/fabric)
./ge fabric review             # unranked candidates; read the purpose and choose by meaning
./ge fabric --show review_code # body to .ge/fabric/review_code.md
```

Borrow the structure only: steps, review axes, output layout. Keep per-item verdicts with evidence rather than
one overall score.
`ge new` writes `fabric.md` with a `선택:` (choice) line. If you want this step to be mandatory, set
`GE_REQUIRE_FABRIC=1`; then `ge run` refuses a spec whose choice line is empty (`선택: 없음` = nothing to borrow).
It is off by default.

## 8. Run, then read the ledger

```
./ge plan graphs/fix-login.json
GE_SRC=~/work/app-ge ./ge run graphs/fix-login.json --executor codex
./ge progress
./ge trace .ge/runs/<run-id> --node patch     # raw worker output to a file
./ge report .ge/runs/<run-id>
```

`run ok` and the verify verdict are recorded separately. A run that finished is not the same as work that passed.

## Language

CLI messages and the `ge new` templates are in Korean.

## When not to use a graph

Work with no spec and no acceptance criteria, or work that needs a human decision at many steps, is better done
in a normal chat session.
