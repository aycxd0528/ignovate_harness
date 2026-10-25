# Fresh review: GUI scan semantic gates

## Scope and identity

Reviewed the complete frozen diff `/tmp/ignovate-pr5-gui-scan-semantic-review.diff` against base HEAD `5ec1a07f267f791be3abe553d10add76e8093124`. Its SHA256 is `58eef6e58d7fd678679f0b4dab6bdeea6a19dba4a5e55a0c8f80e8ac381f6089`; the current two-path working diff independently hashes to the same value. The reviewed paths are `tests/test_gui_async_scans.py` and `.github/workflows/windows-native.yml`. Other checkout changes are outside this review.

This was one fresh reviewer pass, read only apart from this report. I ran no tests, network operations, environment changes, subagents, checkout/index/HEAD mutations, or merge operations. I inspected adjacent controller, work-control, worker-ownership, native-runner, server, and fixture code as needed. Findings and evidence below concern this frozen diff; a later author disposition or amendment is not automatically covered by its hash.

## Strengths

- `PausedFingerprint` still calls the real fingerprint implementation before pausing. `started` establishes an actual observation, and `finished` is set before returning that observation. Checking `finished` after the competing operation therefore rejects completion that depends on the paused fingerprint returning, including its automatic fallback. The assertion adds an ordering condition that the former 300 ms timeout alone did not express.
- All original task replacement, same-revision mutation, project identity, unknown/stale evidence, feedback candidate/document, publication recovery, and observed-fingerprint assertions remain. The implementation does not replace real project construction, task mutation, or SQLite shutdown with fake success.
- The whole 15-test module is added to the existing native preflight. The full discovery runner, Python versions, triggers, 120-second per-test watchdog, and 45-minute runtime job limit remain intact.

## Findings

### Critical

None identified in the frozen diff.

### Important

None identified in the frozen diff.

### Minor

**M1 — The snapshot client retains a shorter aggregate allowance than the newly permitted sequence.** `tests/test_gui_async_scans.py:68`, `:79`, `:80`, `:95`.

The background snapshot client still has a four-second socket timeout, while the foreground test can legitimately spend nearly two seconds waiting for the scan to start and nearly three seconds receiving an independent heartbeat before releasing the scan. For example, a snapshot reaches the gate after 1.5 seconds and an independent heartbeat returns 2.8 seconds later: both foreground waits meet their limits and `finished` remains false, but the snapshot client can already have timed out. Its thread records no result and the final `result[0]` then errors. This is a possible slow-runner false negative, not evidence of a production failure or a false green. It was not reproduced during this read-only review.

Suggested disposition: give the background response a budget that covers the intended startup/heartbeat/release sequence, or use a shared explicit deadline and collect the thread exception for a direct diagnostic. Socket timeouts apply to individual blocking operations, so merely adding the advertised numbers is not an exact end-to-end bound. Cost: a deliberately stalled background request can take longer to report; keep active release/join and the native watchdog. This is advisory and does not undermine the demonstrated project-switch repair.

## R1–R4 rulings and costs

| Item | Ruling | Cost and limits |
| --- | --- | --- |
| R1: three-second operation/HTTP waits plus unfinished-scan assertions | Accept the semantic change. All former `.3` waits in this module are replaced, and the HTTP response has the equivalent ordering assertion. The shared helper checks the actual gate after successful operation return. M1 is an advisory timeout-composition concern. | The tests cease detecting an otherwise independent operation that takes between 300 ms and three seconds. They continue detecting dependence on scan completion. `asyncio.wait_for` cannot preempt synchronous constructor work and can wait for cancellation cleanup; HTTP socket timeouts are per blocking operation. Thus “three-second bounded wait” describes configured timeout policy, not a hard three-second wall-clock certificate. |
| R2: fallback two to ten seconds, with `finished` | Accept. A scan that blocks the event loop or retains a required command/task lock cannot become a green solely because the fallback eventually releases it: the competing operation either times out or observes `finished`. | An actual blocked-loop regression can take up to eight extra seconds per paused invocation before the fallback helps unwind it. Scheduling and owned cancellation cleanup can add time. Explicit release plus pending-task gathering / HTTP thread joining remain; the native 120-second watchdog is unchanged. The fallback is a fixture escape hatch, not a product timeout. |
| R3: full module in native preflight | Accept. The module contains 15 tests and is loaded alongside the original seven preflight modules; full native discovery is unchanged. | Preflight grows from 85 to 100 tests. These 15 are repeated before the existing full 2230-test run for each Python version and existing trigger. A paired PR/push cycle across both versions adds 60 preflight test executions. Actual new Windows elapsed cost is pending; the local 1.638-second module result is not a Windows estimate. No added matrix/trigger/job-time allowance. |
| R4: one frozen-diff review, one author disposition, current-head gates | This review requirement is satisfied for the stated hash. An author disposition pass can accept or decline M1 with its cost recorded. Merge remains conditional on the required current-head source, installed, real native, and cleanup-log evidence. | Old-head greens cannot substitute for a new-head run, and a repeated old failure should not be erased by rerunning until green. The requested single-review process adds no second reviewer cycle. I have not assessed pending runs or approved a merge identity. |

## Evidence assessed

The four supplied old-head native logs show all four 85-test preflights green, three full 2230-test runs green with 99 skips (PR 3.11: 1482.068 s; PR 3.13: 1569.073 s; push 3.11: 1684.704 s), and push 3.13 with exactly one error in 2230 tests/99 skips (1495.681 s). Searches of these four files found no `branches.sqlite`, `WinError32`/`WinError 32`, or `Exception ignored` text. This search is evidence about those logs, not a proof of every cleanup path. The broader “11 checks green, one red” status is supplied task context, not independently queried CI state.

The failing push 3.13 log at lines 1779–1782 records setup at 0.467 seconds, a monitored task execution at 0.318 seconds, then ERROR. Its final traceback identifies the external `.3` `wait_for` cancellation while `open_project` is joining the retired controller's owned close. Source confirms project construction is synchronous, the project state is replaced before that close, and `await_owned` joins cleanup before propagating cancellation. This supports a bad fixture timing assumption; it does not isolate every millisecond of the real native delay or prove all shutdown scenarios healthy.

The provided probe wraps the real `GUIController` constructor with a 0.4-second delay. The old test errors with `TimeoutError` (0.930 s); the revised test completes with its original project/task assertions (0.906 s). The early-release probe waits for the real gate's `finished` event before returning from construction, and the revised test fails specifically at the unfinished-scan assertion (0.933 s). The harness then prints its expected-negative confirmation after checking one failure and zero errors. This negative result is not counted as a green application test. The separate module log records all 15 tests green in 1.638 s. These are inspected supplied artifacts; I did not rerun them or independently establish their execution environment beyond their source/tracebacks.

## Considered and set aside for author disposition

Each row is an explicit scope decision, not an assertion that the behavior is harmless.

| Behavior considered | Disposition / reason | Cost retained |
| --- | --- | --- |
| Moving real project construction and all native IO off the event loop | Declined as production work outside this test/workflow repair. The real constructor remains in the test. | Synchronous native latency remains observable; no blanket GUI IO responsiveness certification. |
| Treating 300 ms as a project-open SLA | Declined: the located 300 ms requirement is a loaded map branch-switch p95 budget for 500 nodes/30 branches (`docs/reviews/2026-10-14-loopx-map-optimization-report.md:342`), a different operation and measurement. | This module no longer supplies 300 ms regression coverage; a separately established project-open SLA would require separate measurement. The map requirement is not waived. |
| Keeping `.3` on the otherwise similar send/stop/approve/create/task mutation/HTTP checks | Declined as inconsistent fixture timing policy; these now use the same scan-order criterion. | Sensitivity to independent delays below three seconds is reduced across the module, not just project switching. |
| An operation really waiting for the scan lock / scan completion | Judged in scope and rejected by timeout or `finished`, not set aside. | A true regression can spend the larger timeout/fallback allowance before failing. |
| A finished gate racing with the assertion | Judged conservatively: `finished` is monotonic and precedes returning the fingerprint. A completed scan is rejected even if the competing operation was independently fast. | Heavy scheduling pauses can produce false negatives; there is no demonstrated false-green path from fallback recovery. |
| Converting `wait_for(3)` into a hard wall-clock performance guarantee | Declined: synchronous work and cancellation ownership prevent that guarantee. | Document and interpret it as timeout policy plus ordering evidence; the native watchdog remains the hard CI backstop. |
| HTTP background-client budget and thread error reporting | Retained as M1 above, not silently dismissed. | Possible near-budget false negative and indirect `IndexError`; author may fix or explicitly defer the advisory issue. |
| Existing startup waits, teardown order, arbitrary pending-operation failures, and broader fixture cleanup redesign | Declined as broader hardening absent a demonstrated new failure in this diff. Existing explicit release/cleanup paths were inspected. | Original fixture failure-diagnostic and scheduling limits remain; no universal teardown guarantee is asserted. |
| Expanding platforms, versions, triggers, watchdog, or job limit | Declined; unchanged existing native gate is adequate scope for this repair. | New coverage consumes some existing 45-minute budget; actual current-head CI must establish it fits. |
| Rebuilding an identical production wheel | Declined: no product source changes in the reviewed diff. The evidence brief supplies the retained wheel hash; I did not recompute or validate the installed artifact. | Installed checks must still establish the intended artifact/version; no new package-build claim from this review. |
| Long-goal completion, unresolved MCP behavior, long Git refusal boundaries, and whole-PR correctness | Declined as outside the two-path review and not newly certified by a fixture repair. | Those existing limitations and any other required PR checks remain for the author to carry forward. |
| Accepting old-head greens or local probes as current Windows certification | Declined. They explain the repair and its negative control; they do not test the final commit on Windows. | Required current-head native/full/installed gates and cleanup-log inspection still cost real runs. |

## Assessment

The frozen change is acceptable for the one author disposition pass. No Critical or Important finding blocks it; M1 is advisory. Merge readiness is conditional on the required current-head real gates and their recorded identities, which this review has not observed. The supported conclusion is that the revised fixture checks completion before a paused real scan returns while tolerating the demonstrated constructor delay. It does not establish a universal latency SLA, production constructor fix, or completion of the broader project goal.
