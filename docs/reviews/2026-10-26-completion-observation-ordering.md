# Completion observation ordering amendment

Date: 2026-10-26. Pre-validation amendment to PR #8; pending CI is not reported as successful.

## Evidence

At `efe6b817cca82d3dc6f00d4c221805856b81a470`, 13 of 14 PR checks passed. The PR Windows 3.13 [job 121412033530](https://github.com/aycxd0528/ignovate_harness/actions/runs/40273768588/job/121412033530) passed all 122 preflight executions, then completed 2233 full-suite cases in 1548.152 seconds with one failure and 102 skips. The sole failure was `test_goal_reads_and_user_pause_do_not_wait_for_completion_scan`: synchronous goal read and pause took 0.2220136 seconds against a 0.2-second assertion. The receipt-scan and publication cancellation amendments passed; no unchanged rerun was requested.

The test held the first completion fingerprint on a worker while calling `active()` and `update(paused)` on the event-loop thread. Its 0.4-second fallback timer was the only release during those calls. Inspection confirms `_completion_observation` collects input outside goal/task transactions; `update(paused)` does not run that observation, but both control calls perform normal goal JSON file I/O. The failed elapsed value does not establish that the control calls waited for the held scan. A sub-200ms filesystem scheduling assumption is not the intended lock contract.

## Amendment and falsification

Replace the wall-clock assertion with an ordering assertion: after both synchronous control calls return, the scan's release event must still be unset and the completion operation pending. The current goal ID and successful user pause remain checked. A regression that holds the goal lock through the scan cannot pass: its calls return only after the fallback releases the scan, at which point the event assertion fails. A separate finite 15-second fallback timer prevents that regression from deadlocking the test; the fixture's 30-second release bound is greater than that fallback. These are fixture escape bounds, not product latency guarantees.

The completion operation is gathered in the test's `finally` before leaving its patch, including assertion failures. Existing stale-completion rejection, saved pause reason and unfinished-task assertions remain. Other uses of this fingerprint fixture keep their original two-second release bound. Add this already existing case to native preflight (122 to 123 executions); full discovery remains 2233. No test case or skip is added, no production code or dependency changes, and no local test executions were run. The native 120-second per-test watchdog and 45-minute job limit remain unchanged.

This is the third publication-preparation failure signal. The accumulated issue is that several concurrency fixtures inferred phases or lock ordering from short wall-clock budgets while invoking real model/graph/file work. The response is to use observable operation/phase ownership and release ordering. It is not to bulk-increase every timeout, weaken business assertions, or bypass a failing job. The remaining complete current-head and tag gates must pass.

Decoded failed-job log SHA-256: `6017bfe6a1766985314dce890c02cad11352edf69a8999afbbcdd1ae74222939` (`/tmp/ignovate-release125-pr-native313-failed.log`).
