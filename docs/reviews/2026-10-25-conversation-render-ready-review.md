# Conversation render readiness review

Date: 2026-10-25. Sole fresh bounded review of the test/CI amendment. Base and checkout HEAD: `fe5814f139125ddeea32f89c56ecae910c31f132` on `codex/long-task-post-merge-audit`. Reviewed frozen `/tmp/ignovate-conversation-render-frozen.diff`, SHA256 `6099c6830fb3820afbd807496970c0f5d9cac29ef9e733fa37f49b76f8839f0e`; the two-path working diff matches its content. Scope is only `tests/test_ui_conversation.py` and `.github/workflows/windows-native.yml`. The report is the only file written by this reviewer. No tests, network queries, subagents, product edits, or merge actions were performed.

## Findings

- Critical: 0.
- Important: 0.
- Minor: 0.

Implementation accepted for the single author disposition. No requested code changes. Final source, installed-artifact, and current-head CI gates remain the author's responsibility; this report is not authorization to label pending gates successful.

The test waits for a real rendered observation before retaining every original assertion. `transcript()` reads RichLog's rendered `lines`; the supplied fake service's final ordinary response is uniquely `普通回答`, while the submitted prompt is `你好` and the earlier task reply is `任务回答`. The predicate therefore cannot pass from the prompt or first response. The existing helper yields through `Pilot.pause()` and uses the existing three-second `asyncio.wait_for` bound with a useful timeout description. The exact single delivery count, status label, task-answer-before-delivery order, ordinary answer presence, and secret absence assertions remain unchanged for all four status subcases.

Source inspection confirms distinct lifecycle boundaries: `nailong/core/runner.py:151` waits for the session pump (and applicable stop task); `ui/transcript.py:201` schedules dirty cached rendering through `call_after_refresh(self._refresh_cached)`. Joining the pump does not assert that the particular final answer is present in rendered lines. The amendment addresses this observation boundary without changing production scheduling or forcing refresh/scroll operations inside the test.

The workflow adds the full existing conversation module to native preflight. Full discovery remains the following step; `NativeTestResult`, async diagnostics, both Python versions, the 45-minute runtime job limit, and per-test watchdog machinery are retained. The whole-module preflight adds six executions per native job, duplicating coverage already present in discovery deliberately to surface this failure earlier.

## Evidence and limits

Directly inspected the frozen diff, full workflow, existing helper and amended test, relevant runner/transcript source, actual render probe, corrected RED/GREEN logs, permanent-gate log, focused module log, author acceptance note, and installed 3.11 log tail. No duplicate suite was run for this review.

- Corrected old-test probe: `/tmp/ignovate-conversation-render-red-corrected.log`, one test in 0.476 seconds, all four status subcases fail at the original missing `普通回答` assertion. Each rendered transcript retains the task answer and delivery label and shows `正在推理…` for the new response. Each gate has one pause and remains unreleased.
- Candidate under the same probe: `/tmp/ignovate-conversation-render-green.log`, one test in 0.667 seconds, all four subcases pass; each gate releases on the third pause. The probe gates actual finished ordinary-answer cached/reflow callbacks, establishes the already-painted first task response before dispatching the ordinary prompt, and releases the original native reflow. It does not insert expected text or replace assertions.
- Never-ready `--drop`: `/tmp/ignovate-conversation-render-never-ready.log`, one test in 12.264 seconds, four expected failures with `Timed out waiting for plain answer to render after delivery`; every gate remains unreleased. This demonstrates that permanent absence fails rather than being accepted after a delay.
- Original exploratory `/tmp/ignovate-conversation-render-red.log` is invalid product evidence: the probe attempted to reflow an unmounted old log and failed to establish the painted first-turn premise. The corrected probe and logs supersede it for mechanism claims. Its errors do not justify product changes.
- `/tmp/ignovate-conversation-module311.log`: all six conversation tests pass in 2.016 seconds. `/tmp/ignovate-postmerge-final-installed311.log`: 173 tests in 82.338 seconds, four skips, OK; its tail attests 198 wheel Python/static files, ten installed origins, and wheel SHA256 `ee0250e546cb9a9bbf5fa4288d438c3d2fd1e842c27ade154d42b325a8c7f4c9`. These are inspected recorded results, not reviewer executions.

Coordinator-supplied historical evidence: PR5 merged after 12 premerge green checks; main native run `40179640612` subsequently had a sole 3.11 failure in the verified subcase with the same missing ordinary answer/stale reasoning display; 3.13 and other jobs passed. This reviewer did not query live CI. Coordinator also reports the final installed 3.13 scope passed 173 tests in 82.643 seconds with four skips and unchanged attestation, and that YAML parsing, folded command compilation, and actual loader count 120 passed. Two source suites remain in progress at report time. Neither old-head checks nor recorded macOS probe success certifies amended Windows execution.

## Considered matters, rulings, and retained costs

| Matter | Disposition and cost |
| --- | --- |
| Waiting for expected text could conceal missing output | Declined as a defect here. The ordinary answer is unique to the second final event, and the never-ready probe fails all four subcases. A missing answer consumes the inherited timeout and fails. |
| Waiting could hide repeated delivery, wrong labels, secret leakage, or order changes | Declined. All original assertions remain after readiness; waiting supplies no replacement content and changes no state. |
| Waiting might permit later state to repair a transient intermediate defect | Accepted observation semantics. The assertion is about the transcript after the completed ordinary answer is rendered. It does not promise intermediate-frame invariants or certify arbitrary later mutations. Adding intermediate-frame requirements is outside this fix. |
| Increase timeout or add fixed sleep | Declined. The class already supplies the three-second bounded helper; the probe demonstrates condition-based progress. Retained cost is up to three seconds per failed subcase, about twelve seconds for four permanently absent answers, plus cleanup/scheduling overhead. This is a test cancellation policy, not a new product UI SLA or strict wall-clock maximum. |
| Force reflow or expose a new production readiness API | Declined. Production synchronization changes are unnecessary for the stated test observation. The temporary probe's original reflow release is experimental instrumentation, not candidate implementation. |
| Generalize the helper or broaden all conversation tests | Declined. Existing helper reuse resolves this one evidenced observation; a framework or unrelated test rewrite adds scope and maintenance cost. |
| Gate the first task answer separately in the permanent test | Declined as unnecessary. Both turns complete, the rendered second final is awaited, and unchanged assertions still require the first answer and delivery. The probe establishes the painted first-turn premise solely to reproduce the recorded failure faithfully. |
| Add whole module to preflight | Accepted. Six extra executions per native job (twelve across its two-version matrix for one workflow invocation), also covered in discovery. Push and PR invocations multiply that cost; 2.016-second local timing is not a Windows runtime estimate. Keeping the module intact avoids an ad hoc subtest list. |
| Remove discovery, watchdogs, diagnostics, or adjust job budget | Declined; none is changed or warranted. Loader count 120 is coordinator evidence; workflow content retains the full-suite invocation and its controls. |
| Treat the probe as proof of Windows root cause | Declined. It validates that the old test can inspect a stale rendered cache and the candidate waits correctly under that mechanism. It does not measure native scheduling or rule out every possible cause of a Windows delay. |
| Reopen POSIX/MCP repairs, broad PR5 correctness, or packaging audit | Declined as outside the frozen two-path review. Already committed repairs are not reviewed again. Unchanged wheel attestation and old-head successes do not replace final-head gates. |
| Additional reviewer runs, second review, or merge | Declined under the bounded assignment. Author records one disposition, completes final gates and exact identities, and owns any integration action. No second pass is requested. |

## Assessment

No C/I/M findings in the frozen amendment. Ready for one author disposition and remaining validation; not a claim that final Windows/current-head gates have completed. The source and controlled render evidence support the narrow test readiness repair and deliberate preflight expansion.
