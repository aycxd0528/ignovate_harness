# PR5 execution transcript readiness review

## Scope and identity

Sole fresh read-only review, continued after the transport interruption; no second review cycle. Base HEAD: `70643c94b8628cef2d3e8a070b2f2f175118880d`. Reviewed the entire frozen diff for `tests/test_execution_display.py` and `.github/workflows/windows-native.yml`. The frozen file and the checkout's two-path diff both have SHA256 `5df25e81893a7e8a138039dd3c2bae94f67663bbcf5ee127a9632c02993a8ecc`.

Read all execution display tests, the workflow, the review brief, `ui/transcript.py`, relevant append/final/theme/tool-detail paths in `tui.py`, `nailong/core/runner.py:151`, the native watchdog implementation, the installed Pilot.pause implementation, the deterministic probe and its retained result logs. No tests, network access, dependency changes, environment changes, checkout/index/HEAD changes, or subagents were used. This report is the only file written.

## Strengths

- `tests/test_execution_display.py:131` promotes the existing resize readiness helper rather than introducing a new production scheduling mechanism. It waits for actual screen/log geometry, pending resize restoration, reflow, full reflow, and follow completion. It does not invoke rendering, refreshing, or scrolling to make the assertion pass.
- All six dashboard methods use that helper before their rendered observations. Explicit resize targets remain, including the new explicit `(40, 18)` at line 238. Original text, theme color, viewport width, visible tail, scroll position, screen identity, inline details, pending activity, and exact final count assertions remain intact.
- The streaming test still holds the service at separate tool-start, tool-end, and final-reply barriers. It checks that the tool result and expanded details appear before releasing the final reply; waiting cannot make the final reply appear early because `finish_reply` remains unset.
- `.github/workflows/windows-native.yml:67` adds the whole module to the existing native preflight while retaining the full-suite invocation and its diagnostics and budgets.

## Issues

- Critical: **0**.
- Important: **0**.
- Minor: **0**.

No code changes are requested for the frozen two-path diff. Pending validation below is a merge gate, not a newly discovered implementation defect.

## Rulings and costs

| Item | Ruling | Reason and retained cost |
| --- | --- | --- |
| R1: Share render readiness across the six dashboard tests | Accept | `tui.py:1580` appends the final answer before the turn returns, but `_append_assistant` at line 918 updates a retained live turn and calls `refresh_renderable`. `ui/transcript.py:201` marks dirty entries and schedules `_refresh_cached` with `call_after_refresh`; `wait_idle` only joins the session pump. The helper at lines 131–154 therefore waits for a different, real lifecycle boundary. It preserves content assertions rather than polling for expected text. Cost: reliance on existing private TranscriptLog state, future maintenance when that contract changes, and up to three seconds of asynchronous waiting per observation. The timeout is a cancellation policy, not an absolute wall-clock SLA. |
| R2: Delayed real rendering RED/GREEN/negative evidence | Accept as mechanism evidence | The probe uses the old 706 test source for RED and delays the actual final render callbacks. Its corrected `== 3` condition releases the original real `_reflow` once, without synthesizing final text. Recorded old RED is `0 != 1`, 0.422 s, one pause; new GREEN is 0.501 s, four pauses. The never-release negative fails readiness in 3.373 s with `reflow=True`, so a permanently unrendered reply is not accepted. Cost/limit: this is controlled local scheduling evidence, not reproduction or certification of Windows timing, and the release uses the real full reflow path rather than proving every cached-refresh timing variant. The initially erroneous repeated-release probe and its log must remain disclosed as probe error, not product evidence. |
| R3: Native preflight 100 to 114 | Accept | The module contains eight synchronous display tests and six asynchronous dashboard tests. Loading the entire module adds fourteen cases; the full discovery command remains unchanged. NativeTestResult's 120-second watchdog, async diagnostics, Python 3.11/3.13 matrix, and 45-minute runtime job are preserved. Cost: for the described push plus PR runs across both Python versions, up to 56 additional preflight test executions, each duplicating coverage that still runs in the full suite. The recorded local 14-case 1.705 s result is not a Windows runtime estimate. |
| R4: One author disposition and final-head gates before merge | Accept; still required | Record disposition of this report, then obtain final current-head source, installed-artifact, native, and closing evidence with exact identities. The 706 failed run remains historical evidence and must not be relabeled as green. Coordinator reports all 706 jobs now terminal, eleven successes and one failure, with three native full-suite greens and the push-3.11 final-count failure. That supports the bounded diagnosis but cannot certify an amended head. Cost: author validation and native CI latency remain; the enclosing goal stays active until its actual completion criteria are met. No second reviewer is requested. |

## Considered matters and disposition

The following are all matters considered beyond the direct findings above, including items declined as changes. The author should explicitly retain or rule on each item in the single disposition pass.

1. **Lost or duplicate final output hidden by waiting:** rejected as a defect in this diff. The helper tests scheduling state; the original `transcript.count("检查完成") == 1` remains at line 304. A missing or duplicate answer still fails after readiness.
2. **Tool ordering weakened by waiting:** rejected. Original positive and negative assertions remain at the service's explicit event barriers; the final reply is withheld until the `finally` block releases it.
3. **Forced scrolling could hide follow regressions:** rejected. The helper performs no scroll operation. The original deliberate scroll to zero remains in the resize test, followed by the original no-forced-scroll assertion after resizing.
4. **Theme and viewport observations could be accepted before actual layout:** addressed by the geometry and pending work conditions, with explicit resize sizes where needed. The original color and rendered viewport assertions remain responsible for behavior.
5. **Private attributes, optional size, and helper naming/type annotations:** accepted maintenance tradeoffs, not requested changes. The class owns six specific TranscriptLog fixtures, and none remounts the transcript. Promoting a local helper is proportionate; extracting a framework or production readiness API is outside this two-path fix.
6. **Selection-deferred reflow and arbitrary widget replacement:** declined as additional scope. These six fixtures do not establish text selection or replace the transcript. The helper is not advertised as a universal idle oracle; future selection/remount tests need their own contract.
7. **Persistent background spinner/worker activity:** considered, with no new defect found. The pending transcript flags are checked after a Pilot pause. Live tool phases remain active intentionally; requiring all application workers or timers to stop would contradict those phase assertions.
8. **Three-second threshold and cancellation behavior:** accepted as inherited from the existing helper. It bounds asynchronous settling attempts and emits the relevant state, but blocking event-loop work or cancellation cleanup may exceed three seconds. No new user-facing render latency guarantee is claimed; changing the threshold without native evidence is declined.
9. **Async tool-details invocation:** considered and accepted. `_show_tool_details` is decorated with Textual `@work`; calling it schedules its worker. The change retains the original call and assertions and gives rendering the same state-based wait. No unrelated await/API rewrite is warranted.
10. **Manufactured success in the probe:** rejected after reading the probe. It does not insert text or alter assertions. The real full reflow release is sufficient for the bounded stale-render mechanism demonstration; claiming it proves all Windows behavior is declined.
11. **Probe's initial `>= 3` repeated-reflow failure:** retained as an invalid probe attempt, superseded for result claims by corrected single-release RED/GREEN/negative logs. It is not a product failure and does not justify production edits.
12. **Native preflight duplication and module import effects:** accepted deliberate coverage cost. The module is already in full discovery, uses local fake services, and its temporary directories close within test scopes. Splitting out a smaller ad hoc list would weaken the stated whole-module preflight decision.
13. **Full-suite counts, watchdogs, job limits, dependency/platform setup:** no changed behavior found in the diff. Broader workflow or production optimization is declined as unrelated scope.
14. **SQLite/resource warnings:** inspected push-3.11 log has no `branches.sqlite`, `WinError32`, or `Exception ignored` match; coordinator reports the same across all four completed native logs. No SQLite fix is justified by the reviewed evidence. A general resource-lifecycle audit is outside this diff.
15. **Broader PR5 production correctness, security, packaging reproducibility, and historical acceptance criteria:** declined as fresh review scope. This is a two-path amendment review, with adjacent source read to establish its synchronization contract. Unchanged wheel identity and old-head CI do not replace final artifact and current-head evidence.
16. **Additional tests, reruns, live CI retrieval, or merge action by the reviewer:** declined under the explicit read-only review assignment. The author owns the final gates and merge identity; no merge was attempted here.

## Evidence and limits

Directly read `/tmp/ignovate-pr5-706-native311-push.log`: preflight ran 100 tests in 226.282 s and passed with three skips; full discovery ran 2230 tests in 1106.429 s, failing only the final transcript count assertion with 99 skips. The final count failure is at old test line 303. This supports a stale rendered-cache observation together with the source path and probes; it does not independently establish all causes of Windows scheduling delay.

Read the recorded focused module result (14 passing in 1.705 s), which predates the final explicit resize-target refinement. Read all three final probe outcomes and the retained initial probe-error log. No fresh test execution was performed in this review. The other three completed native green results and overall eleven-success/one-failure status are coordinator-supplied terminal evidence, not a live CI query by this reviewer. They remain old-head evidence.

## Assessment

**Implementation accepted; ready for the single author disposition and final gates. Ready to merge now: No.** There are no C/I/M findings in the frozen amendment. Merge requires the promised final current-head source, installed, native, and closing checks and their exact identities; neither the local probes nor 706 results satisfy that requirement.
