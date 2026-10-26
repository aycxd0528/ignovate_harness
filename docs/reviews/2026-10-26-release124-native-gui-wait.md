# v1.2.4 native GUI acceptance wait

Date: 2026-10-26. Release publication remains pending until the new head passes its checks and the tag release workflow succeeds.

## Observed failure

Frozen head `dffacb2e5f36f34b3ad17ab431e2d98285050ad1` passed 13/14 checks. PR Windows 3.11 job [121380962176](https://github.com/aycxd0528/ignovate_harness/actions/runs/40263579392/job/121380962176) failed only `NaturalIntakeTests.test_native_gui_runs_natural_goal_through_shared_outer_adapter`: its 10-second `asyncio.timeout` expired while polling GUI run status. Full discovery ran 2233 tests in 1655.768 seconds, with one error and 102 skips. The same-head push Windows 3.11 and both Windows 3.13 jobs passed. This is not recorded as an all-green head or resolved by rerunning the failed job.

The native log shows the GUI executing at 03:30:37 through 03:30:46 UTC, two ExecutionTool completions at 03:30:39 and 03:30:41, a model completion at 03:30:42, then cancellation at 03:30:47 after the fixture deadline. This supports an insufficient fixture wait rather than proof of a stationary deadlock; a passing amended native run must still demonstrate actual completion.

`GUIController._execute_owned` publishes completed status before checkpoint and ledger closeout. `controller.worker` owns `_pump`, which joins `_execute`, including that closeout. Existing native GUI coverage also shields and awaits this worker. The old status-only poll could assert before final publication finished and imposed a 10-second performance assumption on real graph/filesystem/SQLite work.

## Amendment

- Join the actual worker with a 30-second bounded wait; report the run snapshot on timeout.
- Retain all existing completed status, file content, goal thread and round assertions. No product code, fake model responses or task requirements changed.
- Add the existing single natural-GUI case to native preflight (120 to 121 executions); retain full discovery, both Python versions, the 120-second watchdogs and the 45-minute job budget.
- No new test cases or local test executions were added for release preparation. Static checks are separate from pending native validation.

## Evidence scope

The Windows failure is the witnessed red evidence. No local Windows reproduction is claimed; native current-head preflight/full-suite results determine whether the amendment resolves it. A longer bounded fixture wait does not establish a new product latency guarantee or hide noncompletion: timeout, failed/stopped run status, missing/wrong output or incorrect goal identity/round still fails.

Downloaded native log (gh decoded representation): `/tmp/ignovate-release124-failed-native311.log`; SHA-256 `28c602b85f5b71fd7d5a83439ff58f058842f6d9775655c60ba29e0f0832e17c`.
