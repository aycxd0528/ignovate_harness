# Cancellation fixture PID publication

Date: 2026-10-26. Pre-validation amendment to the v1.2.6 icon release; pending checks are not success.

## Failure evidence

At `1bcead35fd759ecbe54d214ecd7ac4725a3b9c20`, [Linux Python 3.11 job 121506834835](https://github.com/aycxd0528/ignovate_harness/actions/runs/40303502303/job/121506834835) ran all 2233 cases in 589.017 seconds with one error and 55 existing skips. `ReviewVerificationTests.test_repeated_cancel_cleans_process_and_preserves_step` read `pid` after observing its existence, but `int()` received the empty string. Both Mac and Windows icon packaging/installed-app acceptance had passed. The other tests, source assets and runtime logic were not implicated by this stack trace.

The fixture child used `open('pid','w').write(...)`: opening publishes an empty file before the write. Its parent used file existence as the publication signal, so a read between those two operations is possible. The PID read was also outside the cleanup try/finally. This is an observable publication race, not evidence of failed product cancellation. The failed run was not rerun unchanged to obtain green.

## Amendment

The same child writes and closes `pid.pending`, then atomically replaces `pid` in the same directory. It still ignores SIGTERM before publishing, prints started and sleeps for 30 seconds. Existence now signals a complete PID value.

The existing test owns its verification task before waiting or parsing. Phase entry has a finite 30-second observation bound and surfaces early task completion/exception; every exit cancels if needed and gathers the verification task before fallback PID cleanup. Both cancellations and their original spacing, expected CancelledError, process-absence assertion before fallback killing, cancelled receipt, exact step name and non-null exit-code assertions remain.

No test case is added; no skip, product code, dependency or workflow is changed. No local test suite was run. Complete CI for the new head and the eventual tag remains required. This note does not claim permanent-I/O or repeated external cancellation recovery beyond existing owners/watchdogs.

Decoded failed-job log SHA-256: `a98e6de808db014920abf1768619649e9fb77bb4f5020f31af531e2a3c301cce` (`/tmp/ignovate-release126-push-linux311-failed.log`).
