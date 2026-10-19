# First-run desktop guide implementation plan

> **For agentic workers:** Use executing-plans inline; dispatch independent native state and desktop-entry tasks to isolated-context agents. Tests precede behavior changes.

**Goal:** A new computer opens a usable, animated first-run guide that leads from welcome through workspace and model configuration into the workbench.

**Architecture:** Reuse the existing React islands, native workspace picker and configuration command. Fetch the actual React Bits Stepper through the configured MCP registry; bundle locally. Persist guide completion outside project state and browser origin storage. Preserve the frozen 601bdd9 paid batch and original release gates.

**Tech Stack:** React 19.3.1, existing Motion/Hugeicons, official React Bits Stepper, vanilla ES-module bridge, Python GUIController and Electron.

## Design and constraints

- Four steps: welcome, workspace, model connection, ready. Existing neutral surfaces/blue accent, matching light/dark themes; deliberate short step transitions, reduced-motion support, keyboard focus and narrow-width layouts.
- Back/skip available. Skip saves status but never claims configuration ready. Settings and command menu can reopen the guide. Successful finish requires an actual workspace and valid local connection configuration; no automatic paid connection test or first task execution.
- Model API key is password input, sent only through existing native configuration command, never returned/prepopulated or stored in browser persistence. Existing configured connection can be reused.
- Native snapshot adds `onboarding: {version:1,status:'pending'|'completed'|'skipped',required:bool,workspace_placeholder:bool,error?:str}`. Command `onboarding` accepts only `{action:'complete'|'skip'}` (plus existing project_id transport). Native marker is BootstrapStore directory sibling `onboarding.json`, schema `{version:1,status:'completed'|'skipped'}`; safe atomic writes, no secrets.
- Desktop no-argument packaged launch displays welcome before requiring directory selection: remember a previously chosen valid workspace in app userData, else use a private empty `Welcome` workspace. Set child env `IGNOVATE_DESKTOP_WELCOME_PROJECT` to that private path; native detects placeholder by resolved equality. Explicit --project/source startup preserved. A completed user whose remembered workspace disappeared gets workspace recovery; skipped users may enter the empty workspace and reopen manually.
- Freeze/source/operator/oracles currently running under ignored artifacts/native-delivery-stability-2026-10-19 are immutable. New guide changes are separate worktree source and require new installer and release verification.

## Tasks and acceptance

- [x] Native state/API (agent): missing/read-only state, complete/skip across reload and workspace, prerequisite refusal, invalid/schema/symlink safety and atomic failures; 78 adjacent tests/69 subtests passed.
- [x] Desktop entry (agent): explicit/source/persisted/missing-workspace/cancel behavior; 15 Node tests passed, both packaging copy lists updated for workspace.cjs.
- [x] Guide UI (root): first-run browser test witnessed missing guide before implementation; fetched actual Stepper through MCP, mounted dialog and native callbacks with return/skip/reopen/error/cleanup behavior.
- [x] Real browser flows: both first-run/reopen/skip and missing-connection/cancel/invalid-retry/key-privacy/draft-only scripts passed using disposable current-worktree backend. First attempted fixture imported the old editable checkout; discarded those checks, restarted with explicit PYTHONPATH and verified the actual new snapshot/API before passing. Independent reviewer additionally verified workspace-switch failure and key clearing, no send/verify/test_connection calls, 74 Python and 10 desktop tests; no important findings.
- [ ] Review diff, run appropriate full Python/Node/frontend checks after concurrent edits finish, rebuild wheel and actual DMG, test installed first-run path from clean HOME/config. Update PR and preserve paid results/failures before release.

## Verification ledger

- Independent read-only review found no important source issue; actual installed first-run remains a release gate.
- Integrated reviewed source: 1764 Python tests in 179.115s, 1719 passed and 45 skipped. Node 29/29 passed; frontend production bundle and desktop syntax checks passed. New browser tests use real disposable native backend without API/model requests.
- Added packaged first-run smoke for macOS and Windows CI: no --project argument, native picker cancel/select, local dummy connection save, no task commands, remembered workspace across restarts and missing-directory recovery. This script still needs to pass on the rebuilt installation asset before release claims.
- The concurrently running paid source remains frozen at 601bdd9. Its observed file-bound coverage failures are retained; pure evaluator replay with the new fix is diagnostic only and does not relabel its original verdicts.
