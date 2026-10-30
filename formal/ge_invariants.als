-- GE structural invariants (Alloy 6): a small model of the INTENDED safe-execution policy of `ge run`.
-- It is not a proof about the Python code. Tests check that the code follows these rules; this model checks
-- that the rules, taken together, give the four properties below within a bounded scope (6 atoms).
-- Run with: ge check-model
--
-- Rules (preds) and where the code enforces them:
--   SpecShape   load_spec / topo: no cycles; an apply node (script with "apply": true) must take a verify node
--               as a direct input (refused at load otherwise). "verify takes a producing node" is a convention
--               of the scaffold, assumed here.
--   TopoExec    nodes run in topological order; a failed input with required=true aborts the run.
--   Preflight   with a contract, a worker is called only after its preflight passed.
--   Gate        Run.apply_block: an apply node runs only if all its inputs succeeded, every verify input has
--               only `pass` verdicts, and (with a contract) the acceptance gate passed, which includes human
--               approval of out-of-scope items. Without a contract there are no scope items to approve.
--   ReadOnly    workers are started with read-only modes of their CLIs (assumed, not sandboxed by ge);
--               the shipped apply writes only to a copy (linked git worktree or folder marked .ge-copy).
--
-- Outside the model (not covered by these checks):
--   - specs without a contract skip preflight, probe and the acceptance gate (apply_block still applies);
--   - nodes with required=false: downstream non-apply nodes may still run after they fail;
--   - script and probe nodes are arbitrary Python run with your permissions; only the shipped apply is
--     non-destructive. Run only specs and scripts you trust.
--
-- The negative control drops Gate and shows a counterexample.

abstract sig Kind {}
one sig Model, Verify, Script, Apply extends Kind {}

abstract sig Folder {}
one sig Original extends Folder {}
sig Copy extends Folder {}

sig Node {
  kind: one Kind,
  inputs: set Node
}

one sig Run {
  executed: set Node,
  pos: executed -> one Int,
  ok: set Node,
  preflightOk: set Node,
  called: set Node,
  passed: set Node,
  approved: set Node,
  writes: Node -> Folder
}

fun workers: set Node { kind.(Model + Verify) }

pred SpecShape {
  no n: Node | n in n.^inputs
  all v: kind.Verify | some p: v.inputs | p.kind = Model
  all a: kind.Apply | some v: a.inputs | v.kind = Verify
}

pred TopoExec {
  Run.ok in Run.executed
  all n: Run.executed, i: n.inputs | i in Run.ok and Run.pos[i] < Run.pos[n]
}

pred Preflight {
  Run.called in Run.executed & workers
  Run.called in Run.preflightOk
  all n: workers & Run.ok | n in Run.called
}

pred Gate {
  all v: kind.Verify & Run.ok | v in Run.passed and v in Run.approved
}

pred ReadOnly {
  no workers <: Run.writes
  Run.writes in Run.executed -> Copy
}

pred Rules { SpecShape and TopoExec and Preflight and Gate and ReadOnly }

-- 1) apply runs only after a verify that passed and whose scope items were approved
pred ApplyNeedsVerifiedApproval {
  all a: kind.Apply & Run.executed | some v: kind.Verify |
    v in Run.passed and v in Run.approved and Run.pos[v] < Run.pos[a]
}
assert ApplyGated { Rules => ApplyNeedsVerifiedApproval }

-- 2) non-destructive: nothing writes the original folder
assert NonDestructive { Rules => no Run.writes.Original }

-- 3) a node whose preflight failed never has its worker called, and nothing downstream of it runs
assert PreflightBlocks {
  Rules => all n: workers - Run.preflightOk |
    n not in Run.called and no d: Run.executed | n in d.^inputs
}

-- 4) execution is acyclic and every executed verify comes after the producing node it checks
assert VerifyAfterProducer {
  Rules => {
    no n: Run.executed | n in n.^inputs
    all v: kind.Verify & Run.executed | some p: v.inputs |
      p.kind = Model and p in Run.ok and Run.pos[p] < Run.pos[v]
  }
}

check ApplyGated for 6 but 5 Int
check NonDestructive for 6 but 5 Int
check PreflightBlocks for 6 but 5 Int
check VerifyAfterProducer for 6 but 5 Int

-- not vacuous: with all rules, a run that reaches apply exists
run Nonvacuous { Rules and some kind.Apply & Run.executed } for 6 but 5 Int

-- negative control: drop Gate and an apply can run after a failed / unapproved verify
run GateRemoved {
  SpecShape and TopoExec and Preflight and ReadOnly
  not ApplyNeedsVerifiedApproval
} for 6 but 5 Int
