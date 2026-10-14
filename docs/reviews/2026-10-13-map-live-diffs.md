# Map live cards and committed file changes

Implemented the user's three requests in the existing checkout. No commits or staging; unrelated local changes retained.

## Cause and fix

- A map draft was cleared as soon as the native send request was accepted, while the map rendered only committed nodes. The resulting gap hid the card until completion. Native runs now expose the durable request ID and map origin; the map renders provisional cards with prompt, native status, tool activity and streamed/final text. Draft coordinates are reserved under the run ID and migrated to the immutable node ID when committed. Reload retains an active card without replaying the request. A layout error after acceptance reports that the question was sent and continues to the target branch.
- Pointer movement only changed card CSS; connectors were redrawn on release. Links and dependent draft coordinates now update on every movement. Movement publishes cloned layouts, and status/tool snapshot updates preserve the active map while it owns pointer capture. On release, the layout is flushed and the latest snapshot rendered. Draft placement persists the adjusted camera; captured send coordinates cannot overwrite subsequent panning.
- An older snapshot could arrive after a layout save and restore its old revision. Same-project snapshots now preserve newer acknowledged conversation layouts. This does not carry layouts across project identities.
- File operations had no review UI, and writes did not record committed patches. Native writes now capture a bounded unified patch and full operation counts; edits retain their recorded patch and full counts. AgentService persists redacted metadata, including truncation, and the GUI uses these saved operations rather than computing history from the current file. The tool summary opens a file review with folder/file navigation, filter, added/deleted counts, colored lines and old/new line numbers. Individual tool expansion lazily renders the patch. Repeated changes to a file show separate operations; totals are cumulative operation counts.
- Expanded icon buttons no longer keep a filled background. Keyboard focus outlines remain.

## Independent review

Read-only review found and verified fixes for drag loss during a run, missing final-newline/CRLF-only write changes, incorrect counters after a truncated patch gap, and stale snapshots overwriting saved layout revisions. Each has a reproduction or failing-first regression; focused recheck reported no remaining findings. CRLF lines are labeled, missing final newlines use the standard patch marker, and unknown line numbers after a truncation gap remain blank until another hunk establishes offsets.

Historical writes that never recorded a patch cannot reconstruct their old content. An existing execution preview is labeled as a preview; missing recorded differences are explicit. Large previews remain bounded, with full native counts and a truncation notice.

## Verification

- Python full suite: 1292 passed, 43 skipped, 634 subtests passed in 144.50 seconds, from a temporary workspace mirror excluding the user's local preferences.
- Three pure map/diff tests passed; the stale-snapshot browser regression passed with native revision submissions [0, 1].
- Fifteen original/new browser and Electron scripts passed on fresh isolated fixtures, plus the new stale-snapshot script. The delayed native map regression holds a drag through actual tool events, reloads while active, checks stable completion coordinates, verifies no request replay, and opens real write/edit patches (+2/-0 and +1/-1). The fixture excludes named internal task-context messages when choosing the current user input.
- Desktop syntax check and wheel build passed. Wheel contains diffs.py, diff.js, map.js and both upstream animation/Markdown licenses. The 500-node/30-branch sample measured switch p95 20.2 ms and about 13.2 MB JS heap on this machine.
- Actual idle browser service and desktop launcher refreshed. The actual browser map and expanded sidebar button were inspected; expanded background is transparent. Actual model/provider preferences were preserved. All execution tests use the native Harness with a local deterministic model; no provider requests.

Screenshots: [actual map](../previews/gui-map-diffs-live-2026-10-13.png), [isolated native map follow-up](../previews/gui-map-run-flow-2026-10-13.png), [isolated native edit review](../previews/gui-file-diff-review-2026-10-13.png).
