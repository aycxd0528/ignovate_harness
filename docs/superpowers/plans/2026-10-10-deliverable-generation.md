# Deliverable generation and identical-protocol retest implementation plan

> **For agentic workers:** Use native implementation in this session with independent final review. The user has authorized continued optimization, a new frozen-protocol test, and publication of the report to the specified Feishu document.

**Goal:** Enable genuine Agent F artifact delivery and verify it through a separate six-round run.

**Architecture:** Preserve model identity, max effort, original operator limits, prompts and authorization. Correct the provider generation profile, investigate request behavior using recorded evidence, and change only general Harness mechanisms. Freeze implementation before each separate benchmark run; never supply private evaluation feedback or other agents' answers.

**Tech Stack:** Existing Python 3.11 Harness, LangChain/DeepSeek SDK, frozen Python 3.9.6 benchmark and native macOS sandbox.

**Spec:** The user's current instruction and the frozen Agent F/run-002/protocol.json. Analysis and engineering failures are in the separate evaluator directories.

## Global constraints

- Preserve deepseek-flash (current provider metadata: DeepSeek-V4.1-Flash), reasoning_effort=max.
- Preserve 120000 provider input+output including cached input and child calls; original controller checks returned cumulative usage before requests and allows one in-flight overshoot.
- Preserve INCACHE-1.1 / RUBRIC-2.0, exact R1–R6 prompts, 5400 active seconds, isolated workspace and logged native approvals.
- R1/R2 are read-only; R3 scores never reach the model; force becomes authorized at R4.
- Keep historical runs and existing user modifications; do not produce the scanner solution outside the tested native Agent F process.
- Publish only verified outcomes and methods; unsuccessful runs remain in the report.
- The user separately authorized one 3000000-token delivery experiment on 2026-10-10, retaining the canonical 120000-token attempt. Label its changed budget explicitly; do not merge it into identical-budget results.

## Review focus

- Generated max/high allowance must survive real SDK serialization and model/effort switches.
- Context-window clipping must remain explicit and cannot masquerade as an adequate body budget.
- Internal cumulative preflight must not silently replace the original operator protocol during a same-condition run.
- Required reasoning/tool-call history must follow the provider protocol; inspect current request shape before trimming.
- Formal delivery requires actual source/tests/usage instructions and isolated contract checks, not a nonempty final response alone.

### Task 1: Correct documented generation profiles

**Files:** generation.py, test_generation_limits.py, test_generation_wire.py, docs/GENERATION_BUDGETS.md.

- [x] Change native-payload tests to require documented defaults: max 131072, thinking high/default/low 65536, none 8192; unknown aliases remain4096. Keep project override and small-window tests.
- [x] Run tests and verify expected failures against the first-batch caps.
- [x] Correct the profile policy; run focused SDK and recovery/accounting tests.
- [x] Freeze the source and run an engineering R1 using the original operator admission behavior. Inspect observed body and termination, not just requested cap.

### Task 2: Resolve remaining delivery blockers and freeze a formal candidate

**Files:** Only components implicated by measured request/response evidence; paired regression tests for each behavioral correction. The new evaluator adapter and evidence directory are outside model-visible workspace.

- [x] Diagnose real failures; add a reproducing test before modifying general runtime logic. Keep all trial failures distinct.
- [x] Obtain an independent code review and pass complete native regression before source synchronization. Final candidate: 1223 tests, 43 skipped; review closed one P2 and two P3 projection issues with permanent regressions.
- [ ] Synchronize only owned changes, preserve user's staging and unrelated dirty files, and verify the actual source checkout.
- [ ] Start a fresh run from frozen baseline, execute R1–R6 with the original native controller, capture every round and source seal. Stop according to the original budget and time rules.
- [ ] Run the separately authorized 3000000-token experiment from another fresh baseline, with all other conditions fixed and separate outcomes.
- [ ] Evaluate R3/R6 only outside the tested process with Python 3.9.6; verify artifact delivery, protected inputs, trajectory completeness and accounting.
- [ ] Assemble data-processing methodology, historical comparison, optimization evidence and retest outcome. Write XML to the specified Feishu document and fetch it back to verify.
