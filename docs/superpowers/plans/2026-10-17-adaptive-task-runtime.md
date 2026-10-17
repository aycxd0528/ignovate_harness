# Adaptive Task Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete all six prioritized improvements: adaptive input, explainable usage, natural persistent tasks, resumable child budgets, session coordination and safe recovery.

**Architecture:** Keep AgentService/TaskStore/GoalStore authoritative. Select the actual request's capabilities before context checks and binding; share existing permission and budget gates. Use a persisted serial dispatch service for cross-session work after the calling turn releases the project lease.

**Tech Stack:** Python 3.11+, LangChain/LangGraph, SQLite, existing vanilla JavaScript GUI, unittest and existing browser tests.

**Spec:** `docs/superpowers/specs/2026-10-17-adaptive-task-runtime-design.md`.

## Global Constraints

- Preserve user's existing staged, unstaged and untracked changes. Do not commit their pre-existing changes.
- Default to the current conversation. No mandatory standalone dots agent or writable child agent.
- Keep all parameter, mode, file-version, stage, permission, native approval and budget checks.
- Use actual request schemas for all context/budget checks. Cached input is a subset of input.
- Never replay uncertain side effects or infer successful acceptance from model prose.
- All six stages must reach real GUI/CLI/headless execution before goal completion.
- No default real-provider calls. Local fake-model usage is fixture data, not measured provider usage.

## Review Focus

- Discovery and a guessed tool in one batch: the guessed call cannot execute or request approval.
- New user message, narrowed allowed_tools, changed catalog, restart/fork: stale loads cannot widen capability.
- Missing/duplicate usage, cancellation and concurrent children: settle once and preserve unknown states.
- User pause or changed requirement during dispatch: invalidate old generation before another effect.
- Recovery after admitted/running but unknown tool outcome: reconcile receipts, never blindly replay.

### Task 1: Adaptive request capabilities and searchable Skills (P0)

**Files:** Create `nailong/core/capabilities.py`, `tests/test_adaptive_capabilities.py`; modify `agent.py`, `agent_service.py`, `tools.py`, `nailong/tools/registry.py`, `nailong/core/prompts.py`, `nailong/core/skills.py`, `nailong/core/context_runtime.py`, `nailong/core/tool_approval.py`.

**Interfaces:**
- `SkillRegistry.search(query: str = '', offset: int = 0, limit: int = 5) -> dict` returns paginated selection metadata, never all bodies.
- `CapabilityCatalog` owns current registered tools, digest, bounded search and per-request selection.
- `CapabilityMiddleware` persists scoped promotions and rejects tools outside the originating request's bound set before native approval/effects.
- `ContextManagerMiddleware` accepts an optional capability view provider; both preflight and final request use selected tools and prompt parts.

- [ ] Write runtime tests for greeting (no tools/Skill catalog), general dialogue (bounded discovery), 100 unrelated Skills, discovery→read, same-batch guessed mutation, next-turn reset, explicit tool restrictions, resume and changed catalog.
- [ ] Run `.venv/bin/python -m unittest discover -s tests -p 'test_adaptive_capabilities.py' -v`; observe missing behavior fail.
- [ ] Implement capability discovery through native registered tools and middleware. Initial greetings use a minimal prompt; general chat discovers capabilities; explicit caller tool sets remain available. Skill bodies and unrelated catalogs stay deferred.
- [ ] Compare captured request fixed input with the baseline using the same estimator: greeting reduction >=75%, no tool bindings; unrelated Skills do not grow ordinary-chat catalogs.
- [ ] Run new tests and context, permissions, approval, Skills, subagent routing regressions. Inspect failures; preserve behavior of explicit profile/allowed_tools callers.
- [ ] Commit only newly created or previously clean task files; ledger any shared dirty-file changes separately.

### Task 2: Unified per-turn usage and context projection (P0)

**Files:** Create `nailong/core/usage_projection.py`, `tests/test_adaptive_usage.py`; modify accounting/service, `ui/presentation.py`, GUI projections/trace/UI, headless output.

**Interfaces:** `usage_projection(events: list[dict]) -> dict` produces deduplicated actual per-turn/cumulative input/output/cache/total, call counts and separately labeled request estimates. Keys include stable call, turn, run, thread and optional task/goal/dispatch identities.

- [ ] Write failing fixture tests for multiple main/child calls, replayed events, missing usage, cached inputs, branch history and actual-vs-estimated presentation.
- [ ] Run `test_adaptive_usage.py` and verify RED.
- [ ] Reuse provider call records, preserve unknowns, expose a shared projection to CLI/GUI/JSON. Add expandable estimate categories beside actual usage without turning estimate totals into bills.
- [ ] Run usage/cost/context/GUI presentation tests; add browser assertions for narrow/light/dark display and replay.
- [ ] Commit task-owned changes and ledger verification.

### Task 3: Natural conversation-to-goal transitions (P1)

**Files:** Create `nailong/core/task_intake.py`, `tests/test_adaptive_task_flow.py`; modify TaskStore, task tools, AgentService, `ui/flows.py`, CLI/GUI/headless entry adapters.

**Interfaces:** Task intake persists objective/scope/constraints/acceptance/open_questions and request revision. A typed start intent identifies the live user instruction and draft revision. Shared outer-turn driving starts/resumes GoalStore only after the model turn finishes and the project lock is released.

- [ ] Write a failing integration transcript: vague intent→clarification→user answer→explicit implement→edit→failed verification→repair→verified delivery, all one thread without slash commands.
- [ ] Add negative cases for discussion-only, stale draft, forged archive approval, status-only, greeting and paused/cancelled work.
- [ ] Run `test_adaptive_task_flow.py` and verify RED.
- [ ] Implement bounded intake/acceptance tools and shared continuation adapter; preserve explicit /plan and /goal. User-required manual acceptance stays manual.
- [ ] Run goal, task, delivery, stage-constraint and GUI workflow tests; verify no duplicate first implementation round.
- [ ] Commit and ledger.

### Task 4: Configurable child stages and durable continuations (P1)

**Files:** Create `nailong/core/child_continuations.py`, runtime options module if not yet present, `tests/test_adaptive_child_continuations.py`; modify `agents.py`, budgets, task tool and runtime child creation.

**Interfaces:** Typed child results include native status/evidence/unresolved/stop_reason/resume_reference. Durable continuation identity binds project, parent task/revision, child run, input versions and archive references. Runtime options provide concurrency 1–3 (default 3), stage budget 30,000, output 4,096 and final output 1,024, each strictly validated and capped by model/window.

- [ ] Write RED tests for stage exhaustion→later continuation→completion, changed files, scoped references, unknown usage, cancellation, concurrent holds, failed persistence and one-time settlement.
- [ ] Implement runtime-derived evidence, conservative stop reasons and resumable records. Keep legacy string-worker compatibility without invented evidence.
- [ ] Reuse GoalCostBudget across stages; never reset target cumulative cost when beginning another stage.
- [ ] Run child-budget, native-bound, approval cancellation and report tests, then commit/ledger.

### Task 5: Shared serial cross-session coordination (P2)

**Files:** Create `nailong/core/session_coordination.py`, `tests/test_adaptive_coordination.py`; modify session/task tools and shared GUI/CLI/headless outer flow.

**Interfaces:** Persistent coordination store supports bounded session/task summaries, scoped history, lease acquisition, idempotent dispatch, dependency/results lookup and pause/release. Dispatch stores unique request_id, goal, target thread/task/revision, stage, allowed scope, dependencies and native run identity. Target tasks retain their own authoritative TaskStore identity.

- [ ] Write RED integration tests for continuing existing context, dependencies, duplicate requests, cycles, cross-project rejection, stale checkpoint and scoped permissions.
- [ ] Implement submission-only tools; consume serial work outside the caller's project lock using existing AgentService/verification paths.
- [ ] Attribute each worker call to the existing goal budget without rebinding total goal ownership to each worker.
- [ ] Verify GUI, CLI and headless consume the same persistent queue and report actual results and overall acceptance.
- [ ] Commit and ledger.

### Task 6: Control ownership, interruption and reconciliation (P2)

**Files:** Extend coordination/flows/execution guards; create `tests/test_adaptive_recovery.py`; update documentation and final review report.

**Interfaces:** Persist owner/scope/generation and boundaries; dispatch transitions queued/admitted/running/completed/failed/cancelled/needs_reconciliation. User intervention invalidates stale generation; global and session pause have distinct scopes.

- [ ] Write RED tests for user edits/pause between admission and execution, restart after uncertain effect, duplicate completion, corrupt state, independent-task progress and missing native receipt.
- [ ] Implement execution-time generation/revision checks and recovery reconciliation. Never automatically replay admitted/running unknown effects.
- [ ] Run focused recovery and native cancellation/branch/goal tests. Then run complete Python suite and relevant frontend/browser suites with results logged.
- [ ] Audit all six original requirements and four outcome metrics against runtime evidence; document missing real-provider measurements honestly.
- [ ] Obtain one fresh final code review, fix important findings with RED→GREEN verification, rerun affected/full suites and commit only task-owned changes.

## Execution record

User reviewed the design and explicitly said “开始实施” on 2026-10-17. Execute inline continuously; record decisions and completed stages in this plan's ledger. No additional checkpoint question is needed for the same authorized scope. Final goal completion still requires all six tasks and their end-to-end evidence.
