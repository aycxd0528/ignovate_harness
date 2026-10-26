# Native cancellation fixture phase handshakes

Date: 2026-10-26. This records a pre-validation amendment. Final current-head results belong to its PR and tag workflow; this note does not label pending checks successful.

## Failed evidence

The v1.2.4 source tag remains `868936b9054d0ed7b03b14738c7cc7f84acdc499`, annotated object `8dea38d7d7c6dc8d5683873b21d18e8ae51cc340`. It was not moved. The [Release run](https://github.com/aycxd0528/ignovate_harness/actions/runs/40269883932) passed eight jobs, failed Windows 3.11, and skipped publication. No v1.2.4 Release was created. The earlier natural-GUI worker amendment passed in this run; the new failure is a different fixture.

- Release Windows 3.11 job [121400189558](https://github.com/aycxd0528/ignovate_harness/actions/runs/40269883932/job/121400189558) completed 2233 tests in 1589.709 seconds with one error and 102 skips. `test_cancel_during_receipt_scan_retains_cost_and_restores_execution` expired its two-second `entered.wait()` before reaching the held receipt scan. The chronology shows model accounting still executing at 04:43:20 UTC, the consumer progressing at 04:43:21, and fixture timeout/cancellation at 04:43:21.659. Its failure-path `finally` canceled the consumer without explicitly joining it inside the patch scope.
- The separate [tag native run](https://github.com/aycxd0528/ignovate_harness/actions/runs/40269883843) failed Windows 3.13 preflight: 121 tests in 358.439 seconds, one failure and three skips. `test_repeated_cancellation_joins_native_head_publication_and_preserves_generation` expired its three-second startup poll while `WorkflowFixture.verify` was still progressing. Its `finally` released publication and asserted CancelledError even though the intended cancellation loop had never run, masking the startup timeout with a secondary assertion.

These logs establish invalid fixture phase assumptions and cleanup paths. They do not establish a production deadlock or a product startup SLA. The matching main run and other native jobs passed; neither failed run was rerun unchanged to obtain green.

## Amendment

`async_phases.wait_for_phase` observes the actual phase event and the operation concurrently. An operation that fails or finishes early is reported immediately; an absent phase fails after a finite 30-second observation bound. Its event waiter is joined on every path. Both callers retain ownership of their operation and always release, cancel if needed, and gather it before leaving their patch scope.

The publication fixture now receives the worker's phase signal through `call_soon_threadsafe` and an asyncio event. Its two cancellations, pending assertions while publication is held, expected CancelledError after release, cancelled verification event, unverified goal and successful later verification remain unchanged. The receipt fixture retains cancellation, permission restoration, reconciliation state, exact one receipt, absent fingerprint, round, cost and paused-goal assertions. Its worker release is bounded at 30 seconds as well.

The existing receipt-cancellation case is added to native preflight: 121 to 122 executions. The publication case was already present through the full output-provenance module. Full discovery remains 2233 cases; no new test cases, skipped cases or local test executions were added. Native per-test 120-second watchdogs and the 45-minute job budget are unchanged. Nearby short responsiveness/foreign-process probes are distinct contracts and were not bulk-adjusted.

Only test synchronization, release version metadata/public documentation, and the preflight selection change. Application code and dependency versions remain unchanged. Stable release preparation advances to v1.2.5 while preserving the failed v1.2.4 tag and all earlier published assets.

## Log representations

- `/tmp/ignovate-release124-tag-failed-native311.log`: gh decoded log SHA-256 `9eb62e8471e2074aff6cea54303ffcefce2e4f1e20438b5c79b0213c3c86da90`.
- `/tmp/ignovate-release124-tag-standalone-failures.log`: gh decoded failed-job log SHA-256 `34a200346715040c1b2ea9a3f26f0f213abdda30abe2473c0116f828fdfff462`.

No local Windows reproduction or timing guarantee is claimed. Native current-head preflight and full-suite results must show actual phase entry, cancellation and settlement before publication proceeds. Permanently stalled I/O remains outside this fixture's recovery guarantee.
