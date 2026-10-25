# Bounded review: TUI render readiness

Reviewed the frozen two-path diff `/tmp/ignovate-pr5-selection-ready-review.diff` against base `d7d3eb00eed52b9a4c3b52c139b7f6b5b8103a05`. SHA256 verified as `91c87e44fb1c2cb8ffb1bf2d84fa3481934168c9804d768bc89e640e7b5c18ac`. This is the sole fresh review of this increment. Checkout, index, HEAD, dependencies and environment were not changed; no network access, test runs or subagents were used. This report is the only output file written.

## Strengths

The new drag gate observes the same viewport `render_line` text that supplies the mouse coordinates. It does not scroll, mutate text or bypass the original selection operation. The existing Chinese display-cell calculation and mouse down/hover/up sequence are retained. Clipboard, visible highlight, streaming selection preservation and Escape assertions remain intact.

The native 3.11 PR log confirms the reported failure was the old immediate visibility assertion inside `drag`, before the mouse sequence, rather than a clipboard/selection failure. Production `TranscriptLog.refresh_renderable` schedules `_refresh_cached` with `call_after_refresh`; the locked Textual 8.2.8 `Pilot.pause` waits for screen/CPU idle and then calls `_on_timer_update`, without asserting this particular text is rendered. Waiting for the actual observation is a legitimate fixture synchronization repair.

## Issues

- Critical: none found.
- Important: none found in the frozen increment.
- Minor: none requiring a change in the frozen increment.

## R1–R4 dispositions and costs

**R1 — accept.** `tests/test_tui_readability.py:59` gates `drag` using the existing three-second `wait_for_condition`. On success the chosen origin came from a matching visible line; on permanent absence it fails before mouse input. The additional pause before the first scan is consistent with the existing helper contract. Cost: one additional scheduling/idle turn on a ready path and up to three seconds before a real visibility failure is reported. The existing timeout is retained; no arbitrary sleep or product timeout is introduced.

**R2 — accept with explicit evidence limits.** `tests/test_tui_readability.py:25` starts with unavailable text and makes it available through the fake pilot pause. Its expected mouse events verify the original Chinese cell offsets and event order; the source gate ensures these events follow readiness. The supplied green log records all eight module tests passing in 4.528 seconds. The author's reported red run against the old helper is consistent with its immediate scan, but I did not rerun either revision. Cost: one small deterministic fixture regression. It proves the helper handles a deferred first render; it does not establish actual Windows Textual behavior or eliminate all timing risks. The original real-app tests remain the integration oracles.

**Adjacent Escape observation — apply in the one author disposition if desired.** The two existing final-text observations after Escape (`tests/test_tui_readability.py:117` and `:193`) cross the same asynchronous boundary: `selection_updated(None)` schedules `_reflow` with `call_after_refresh`. Using the existing three-second condition helper to wait for the expected updated/final `log.lines` text, then retaining each original `assertIn`, is technically justified. It avoids carrying the same immediate-observation assumption into the next stage. This is a bounded recommendation based on the inspected scheduling path, not a claim that the native log demonstrated an Escape failure. Cost: ordinary polling scheduling plus at most three seconds per failed observation (six seconds if both fail in their separate tests); no production change, weaker assertion or new timeout. These prospective edits were not present in the frozen diff; the author should inspect and validate their final disposition.

**R3 — accept.** `.github/workflows/windows-native.yml:67` adds the entire eight-test readability module to the existing early regressions. Full discovery is unchanged and uses the same native result/watchdog setup. Both the threaded per-test watchdog and async deadline remain 120 seconds. Raising the runtime job cap from 35 to 45 minutes is supported by the prior healthy 3.11 push log, whose recorded work spans approximately 34 minutes 15 seconds (the supplied job metadata gives 34 minutes 17 seconds); the full suite alone took 1662.077 seconds. Cost: the eight tests execute twice per runtime job, on both Python matrix entries and on each existing workflow trigger; the actual Windows increment is not established by the 4.528-second local module run. The cap permits an extra ten minutes per matrix job in the worst case, not ten minutes of mandatory work. It does not hide a 120-second stalled test.

**R4 — accept, merge gate remains pending.** This review clears the narrow code increment for final validation, not merge. The final head after the author's single disposition still needs the required full/native Windows jobs, installation evidence and closure logs. Prior d7 green jobs and this local green module log cannot stand in for that final-head evidence. No failed-job rerun was performed by this reviewer.

## Considered and declined

- Production rendering or selection redesign: declined; the observed failure precedes mouse input, production scheduling explains the missing readiness guarantee, and this increment intentionally changes only fixture/CI behavior. Cost avoided: product behavior changes and another broad regression surface.
- Automatic scrolling, writing the sought text, selecting internal data directly or weakening selection/clipboard assertions: declined; each would change the observation or interaction under test. Their absence is confirmed in the diff.
- Fixed sleeps, longer fixture deadlines or weaker per-test watchdogs: declined; semantic readiness within the existing deadline is sufficient. Cost avoided: unconditional delay or slower diagnosis of stalls.
- Extra fake cases for perpetual absence, multiple pending frames or every mouse callback's state: considered but not required for this bounded repair. The existing helper supplies the timeout, the loop retries on false, the regression distinguishes the original immediate scan, and real-app tests retain the selection contract. Cost avoided: redundant test surface; limitation retained: the fake is not a model of all Textual scheduling interleavings.
- Restricting preflight to just the previously failing method: declined in favor of the whole small module, which preserves the neighboring selection regressions. Cost accepted: eight repeated tests rather than one; the final Windows run must reveal the actual duration.
- Expanding full-discovery coverage or adding more Windows matrices/triggers: declined; unchanged full discovery already includes the module. Cost avoided: additional CI work beyond the targeted early signal.
- Fresh test execution, duplicate native jobs, dependency or environment changes and further reviewers: declined per the bounded review instructions. Evidence inspected, not regenerated.
- Historical producer logic, prior MCP behavior, long Git-directory limits and the overall long-task completion claim: outside this two-path increment. They are not resolved by this review and remain subject to their existing disposition/closure requirements.

## Assessment

Ready for the author's single disposition and final validation. No Critical, Important or mandatory Minor findings in the frozen increment. Actual final-head Windows evidence is still required before merge.
