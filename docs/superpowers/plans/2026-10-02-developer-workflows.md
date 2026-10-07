# Developer Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. User approved inline execution and plan self-review without another approval gate on 2026-10-02.

**Goal:** Implement every selected P0/P1 command and interaction, with durable evidence and consistent behavior across Textual, inline and plain.

**Architecture:** Keep existing AgentService, permission and checkpoint contracts. Add focused core services plus shared command actions; a session runner owns serialization, queueing and cancellation. UI adapters render common results and supply input/editor/approval callbacks.

**Tech Stack:** Existing Python 3.11+, LangChain/LangGraph, SQLite/JSONL, Textual, prompt_toolkit and Rich; stdlib Git subprocess, process lifecycle and HTTP probes.

**Spec:** `docs/superpowers/specs/2026-10-02-developer-workflows-design.md`

## Global Constraints

- Implement all four P0 and four P1 groups; do not call the goal complete after a subset.
- Preserve existing workspace content and staged changes; no merge, push, publishing or unrelated commits.
- Project root/protected paths, read-only modes, explicit approvals and secret redaction remain enforced.
- CLI doctor must work without API credentials; default diagnostics have no network side effects.
- Unknown usage/prices remain unknown, with conservative goal reservations.
- Queue length 10; review selection 200 files/256 KiB and batches of 20; verify timeout 1–300 seconds.
- Existing API authorization covers bounded real smoke verification; local fixture checks precede API calls.

## Review Focus

- Git filenames containing whitespace, newlines, leading hyphens, deleted paths and symlinks: preserve names and exclude protected/escaping contents.
- Model or Skill changes while tasks wait: apply in queue order and preserve identity, costs and permission scope.
- Cancelling a provider call, shell process or pending approval: preserve charges, clean owned processes and execute no rejected operation.
- Damaged/old metadata and concurrent file edits: retain usable history, reject conflicting overwrites and never fabricate historical prices.
- Configuration exclusions or URL tricks: cannot hide verification inputs, leak authentication or redirect HTTP probes outside loopback.

---

### Task 1: Restore per-call accounting and provenance

**Files:** Modify `agent.py`, `agent_service.py`, `nailong/core/costs.py`, `nailong/core/usage.py`; extend `tests/test_safety_regressions.py` and `tests/test_workflow_accounting.py`.

**Interfaces:** Produces `ModelAccountingMiddleware.awrap_model_call(request, handler)`; records actual model, provider host and immutable price snapshot on usage events. Uses existing token/cost ContextVars and usage collector.

- [x] Run existing safety regressions and capture the seven known baseline failures.
- [x] Add tests for immutable per-model price snapshots and non-finite price rejection; run and observe failure.
- [x] Wire accounting around every parent/child model request; reserve input/output, narrow output, settle usage or retain unknown reservations; return child usage accurately on cancellation.
- [x] Run safety, agent and accounting tests, then full suite. Expected: known budget regressions fixed; provenance matches actual calls.

### Task 2: Preferences, Skills and fixed memory

**Files:** Create `nailong/core/preferences.py`; extend `nailong/core/skills.py`, `nailong/core/memory.py`, `agent.py`, `ui/theme.py`; create `tests/test_workflow_preferences.py`, `tests/test_workflow_skills_memory.py`.

**Interfaces:** `PreferenceStore(project_root, user_path=None).effective(default_model, cli=None) -> dict`, `.set(key, value, global_scope=False) -> dict`; `SkillRegistry.reload() -> dict`, `.diagnostics`, `.set_enabled(name, enabled)`; factory `.reload_skills()`, `.reload_memory()`, `.set_model(model_id)` keep checkpointers alive. Memory editing uses staged content and SHA checks.

- [x] Add failing tests for precedence, atomic preservation, allowed model fields, invalid/symlink config, hot discovery, invalid Skill diagnostics, durable disable, shadowing, memory conflicts and dark/light/ANSI fallbacks.
- [x] Implement stores and lifecycle refresh using the approved schemas; runtime overrides take priority only for this session.
- [x] Run new tests plus existing Skills/config/UI tests. Expected: no restart required; no privilege changes or conflicting overwrite.

### Task 3: Safe Git differences and review scope

**Files:** Create `nailong/core/git_changes.py`; extend `agent.py`, `agent_service.py`, `tools.py`, `nailong/tools/registry.py`; create `tests/test_workflow_git.py`.

**Interfaces:** `GitChanges(project_root, api_key='').select(kind='working', ref=None) -> ChangeSet`; changes carry paths, patch, skipped/truncated coverage and batches. `review_paths` is an optional frozenset passed from command request to service, factory and read-only tools.

- [x] Add fixture repository tests for working/staged/branch, initial/no Git, new/deleted/renamed, protected/symlink/binary and unusual paths; observe missing feature failure.
- [x] Implement argv-only safe Git selection, bounded patches and batches; validate ref, disable external diff/textconv/pager.
- [x] Add failing scope tests and pass selected paths through existing review profile; default review requires no explicit path, no changes avoid API call.
- [x] Run Git, registry, agent and service tests. Expected: selection matches fixture Git and review cannot read outside scope.

### Task 4: Verification execution and goal evidence

**Files:** Create `nailong/core/verification.py`, `nailong/core/processes.py`; extend `nailong/core/goal.py`, `local_tools.py`, `agent_service.py`, `headless.py`; create `tests/test_workflow_verification.py`.

**Interfaces:** `VerificationService(project_root, session_store, goal_store=None, api_key='').run(thread_id, name=None, approval=None, emit=None, permission_engine=None, permission_mode='default') -> dict`; `.list_steps() -> list`; process execution returns exit, output, timeout and observation results. Goals consume a complete persisted verification run, not model-authored claims.

- [x] Add failing tests using actual successful/failed/timeout processes, startup stdout/HTTP, rejection, plan mode, cancellation, invalid config/exclusions and external input changes.
- [x] Validate config; execute approved commands with cancellable process groups and loopback-only probes; persist sanitized records and input fingerprint.
- [x] Link full successful runs to goal completion; invalidate on mutation, resume, context change or incomplete fingerprints. Partial/failed runs never satisfy complete.
- [x] Run verification, goal, safety and headless tests. Expected: honest results, zero rejected execution, no leaked service process.

### Task 5: Diagnostics and installable launch

**Files:** Create `nailong/core/diagnostics.py`, `nailong/cli.py`, `pyproject.toml`; extend `main.py`, `config.py`; create `tests/test_workflow_diagnostics.py`.

**Interfaces:** `diagnose(project_root) -> dict`, shared terminal capability checks, `status_snapshot(service, thread_id, runner=None) -> dict`; `nailong.cli:main` provides console entry using caller cwd.

- [x] Add failing checks for missing credentials, malformed configs, optional rg absence, dependencies, non-TTY, protected URL authentication and unknown usage/pricing.
- [x] Handle doctor before loading required model config; share capability checks with normal startup and package existing modules without a new runtime dependency.
- [x] Run offline CLI subprocess and diagnostics tests. Expected: doctor functions with no API settings, status adds no model call.

### Task 6: Session actions and request context breakdown

**Files:** Create `nailong/core/session_actions.py`, `nailong/core/context.py`; extend `nailong/core/sessions.py`, `agent_service.py`, `agent.py`; create `tests/test_workflow_sessions_context.py`.

**Interfaces:** Session metadata `.rename(thread_id, name)`, `.search(query)`; `SessionActions.recap(thread_id)`, `.export(thread_id, path=None, approval=None)`; context report categories count actual system parts, tools and saved messages without duplication.

- [x] Add failing tests for durable names, filtered selectors, corrupted old logs, evidence-aware recap, protected exports, rejected overwrites and mutually exclusive token categories.
- [x] Implement atomic metadata, safe checkpoint-backed search/export, recap sources and side model summarization if needed; preserve paid usage across rewind.
- [x] Include model system prompt, memory, plans, skills and tool schema in context estimate; distinguish actual last main usage from estimates.
- [x] Run session/context tests and existing sessions/headless tests. Expected: cross-process durability, clean Markdown and correct classified totals.

### Task 7: Shared commands and cancellable queue

**Files:** Create `ui/actions.py`, `nailong/core/runner.py`; extend `ui/commands.py`, `ui/controller.py`; create `tests/test_workflow_commands_runner.py`.

**Interfaces:** `CommandActions.execute(request, service, thread_id, approval=None, emit=None, edit=None) -> CommandResult`; requests carry selection/options; results may carry text and ordered model requests. `SessionRunner.submit(item)`, `.stop()`, `.resume()`, `.remove(index)`, `.clear()` serialize work and expose state/queue.

- [x] Add failing parser/action tests for every requested command, quoted args and unknown flags; queue tests for identity, order, cap, cancellation, approval interruption and configuration ordering.
- [x] Register all commands once; execute via focused services; immediate status/cost/context/stop work while generation runs.
- [x] Implement runner cancellation and paused queue; safe tool detail storage omits raw arguments and protected content.
- [x] Run command/runner and old CLI/dashboard tests. Expected: same command results across adapters, stopped items never restart implicitly.

### Task 8: Textual, inline and plain adapters

**Files:** Modify `tui.py`, `ui/app.py`, `ui/prompt.py`, `main.py`, `ui/render.py`, `ui/presentation.py`, `ui/theme.py`; create `tests/test_workflow_ui.py`.

**Interfaces:** Each adapter supplies approved editor and approval callbacks to CommandActions and routes model work through SessionRunner. Renderers consume common events and preference theme; shutdown closes each owned factory exactly once.

- [x] Add failing cross-adapter command tests, generation-time input, Ctrl+C keeps session, approval cancel, tool expansion and theme refresh tests.
- [x] Integrate shared command actions and queue; do not disable composer during generation; prevent session/project transfer with pending work.
- [x] Apply theme/output preference and reload command completion, names and counters; external memory editor always edits a temporary copy.
- [x] Render Textual at 100×32 and 80×24 and exercise inline/plain subprocess inputs. Expected: readable themes, persistent statistics, no duplicated conversation or lost queued messages.

### Task 9: Documentation and complete audit

**Files:** Update `README.md`, `.env.example` if needed; create `docs/reviews/2026-10-02-developer-workflows-acceptance.md`; maintain this plan's ledger and evidence logs.

- [x] Document exact commands, configuration, installation, verification observations, permissions, queue cancellation and known estimates.
- [x] Run full regression suite and actual CLI/local process/UI scenarios, then bounded authorized real API scenarios for review/model/Skills/accounting.
- [x] Obtain one fresh final review as required by executing-plans; fix critical/important findings with reproducing tests.
- [x] Audit every spec requirement against current implementation and authoritative artifacts; do not complete the overall goal while any requirement is missing or unverified.

## Plan Self-Review

All eight feature groups map to Tasks 1–8; Task 9 verifies the whole end state. Shared interfaces are optional extensions of existing service/factory signatures so old custom factories remain compatible. Preference/Skill/memory lifecycle precedes command actions; Git and verification precede review/verify actions; runner precedes UI wiring. User expressly waived a separate plan approval gate; no further check-in is required.

## End-state acceptance

Completed 2026-10-03. All P0/P1 requirements are mapped to implementation and actual evidence in `docs/reviews/2026-10-02-developer-workflows-acceptance.md`. Final regression: 361 tests, 38.251s, OK. Actual CLI/PTY:12/12; real API:5/5 scenarios,8 calls; Textual renders:12. The required independent review found and rechecked multiple safety defects; its last follow-up stopped on account usage limits, so the remaining closures are explicitly recorded as self-audit plus regression. Existing index preserved; no commits or external publishing.
