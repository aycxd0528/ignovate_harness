# Native acceptance and release implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, task by task. Maintain progress in this plan. Independent final review uses requesting-code-review.

**Goal:** Close native acceptance for ordinary tasks, measure repeated real delivery, then publish a validated macOS installation release.

**Architecture:** Add a native binding tool backed by the existing TaskStore and VerificationService. Keep model coverage declarations distinct from runtime execution proof. Preserve sustained goals and existing approval boundaries.

**Tech Stack:** Python 3.11, LangGraph/LangChain, unittest, Electron macOS packager, GitHub releases.

**Spec:** ../specs/2026-10-18-native-acceptance-loop-design.md

## Global constraints

- Existing total real-model budget $10; $2.213430574 conservatively accounted before this plan.
- No private evaluator feedback or access for target agent; original tests/data retained.
- Existing isolated codex/delivery-budget-authorization worktree; root IDE changes untouched.
- No false verified status; manual confirmation remains user-only.

## Review focus

- New user requirement with previously green plan: cannot reuse or renew it silently.
- Approval race or cancellation: task identity and evidence stay bound; no replay or false pass.
- Partial invalid configuration: no partial persistent change; original acceptance retained.
- Same-name modified command: cannot silently weaken prior checks.
- Packaging relocation: embedded interpreter and workspace choice work without local development paths.

## Task 1 — Ordinary acceptance binding and execution

Files: new nailong/core/task_acceptance.py; task_state.py, verification.py, capabilities.py, permissions.py, registry.py, tools.py, agent.py, agent_service.py; new tests/test_native_acceptance.py.

Interfaces: bind_task_acceptance(acceptance, verification, expected_revision) returns task identity/revision; current task plan resolves native verification. VerificationService remains owner of all execution evidence.

- [ ] Write real store/graph/process tests: success, failure, denied process, stale revision, invalid path/step, new requirement, file change, preserved criteria and idempotence. Witness failures first.
- [ ] Implement bounded atomic binding and native tool/schema/permission discovery.
- [ ] Execute current plan before final report; adopt only native binding revision for this turn; persist real evidence and errors.
- [ ] Run focused and complete suites; independent review; resolve important findings with red/green regressions; commit.

## Task 2 — Fixed multi-task repeated evaluation

Files: versioned evaluation protocol, operator adapter and private oracles under ignored artifacts; human-readable report and machine-readable metrics.

- [ ] Freeze candidate, model settings, prompts, budgets, expected deliverables and independent oracles before execution.
- [ ] Run three distinct tasks twice from clean workspaces; enforce remaining total budget and unknown-usage stop.
- [ ] Audit source immutability, original fixtures, actual native binding/proof, functionality and secret non-disclosure. Diagnose observed failures before changing source; a changed candidate requires a new frozen batch.

## Task 3 — Verified macOS release

Files: existing macOS packaging/CI, version declarations, docs/RELEASE_NOTES.md, release artifacts.

- [ ] Inspect remote main/tags/releases/auth and available local official packaging assets; choose non-conflicting version.
- [ ] Run release checks and build from committed source; verify DMG/checksums/signatures, relocation, installed app launch/close, bundled runtime and credential absence.
- [ ] Integrate reviewed source without overwriting user work, verify remote commit and required CI, publish GitHub release with tested installation assets and accurate architecture/signing notes.
- [ ] Audit the complete objective against evidence before completing the persistent goal.

## Ledger

- Baseline: 4a63aaf, worktree clean and isolated. Prior canonical evidence audited; main checkout has user IDE staging. Previous goal turn classified progress: two product fixes, actual six-round execution and evidence archive.
- Execution authorized by user's persistent end-to-end goal; implement inline without repeated approval gates.
