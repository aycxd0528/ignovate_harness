# Final read-only review: bounded GUI completed-task handoff

Date: 2026-10-24. Reviewer performed this single final review personally; no subagents, checkout/index/branch/product/test/dependency/environment mutations, installations, or network requests. Only reviewer-owned /tmp logs and this report were written. Read requesting-code-review/code-reviewer.md, PUA runtime guidance, and docs/reviews/2026-10-24-gui-map-handoff.md. Expected RED evidence and prior unrelated gates were not counted as failed fixes in this review (review failure count 0).

## Scope and provenance

Reviewed exactly nailong/gui/controller.py's two guard replacements (lines 693 and 1044) and new tests/test_gui_map_handoff.py (99 lines). HEAD is not the baseline. Independently verified:

- Frozen diff /tmp/ignovate-map-handoff-review.diff SHA256: 5589f8849a4d2d8c2a9ede1b33f321df870146d1a617cbc2c68f3999bc3195b8.
- Exact GUI baseline candidate wheel /tmp/ignovate-constraint-capacity-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl SHA256: 6c089acc45435111278a4d3016cd4e23605ec32713907c0f786cc91809b6fc3d.
- Reconstructed unified diff from that wheel's controller bytes and current controller/new test equals the frozen diff exactly, both before and after reviewer execution.

The 6c wheel is the phase28 candidate, independently reviewed 0C/0I/1M, whose Python 3.13 full gate exposed this inherited GUI problem. It is not described here as fully accepted. The brief's older accepted-wheel RED evidence and prior 69-case GREEN are author evidence, not reviewer reruns or acceptance claims. Phase28's separate Minor is not regraded here.

## Strengths

- controller.py:693 and :1044 distinguish an unfinished Task from an existing completed Task. They cover the actual map→fork call chain while preserving queue rejection at the map entry. The decision does not depend on the run status becoming completed before final cleanup.
- The real _execute (controller.py:843-863) exits project_execution and run_scope before its Task becomes done. _execute_owned finishes checkpoint/node persistence and closeout before returning. The real _pump (817-826) retains ownership and awaits the Task, then clears the pointer. The change neither clears that pointer early nor starts a second worker.
- command() serializes these commands under command_lock (363-378). _fork is async in signature but contains no suspension, and cloning remains inside project_execution (1051-1074). A newly admitted command cannot yield halfway through cloning to let the pump begin another run. Another lease owner still blocks it.
- Exact historical-boundary checks, completed/recoverable node requirements, immutable checkpoint selection, pending-write/open-tool safeguards, ForkHistory archive rewriting, and durable map request identities remain in place. These were read in their surrounding implementations, not inferred solely from the two changed lines.
- The three new tests use the actual native factory/service/Graph/SQLite checkpoint/history path. Their copied pump delays retirement only after awaiting the real execution Task; it does not fake task completion, lease release, history, or cloning. The unfinished-owner case covers the distinction between completed inner work and an unfinished outer Task. Teardown opens the gates before controller.close, avoiding a cleanup deadlock on assertion failures.
- Tests check historical isolation, preserved origin, one child, stable request replay, conflicting replay rejection, pump continuation, queued-work rejection with no branch/map-intent creation, and active-owner rejection including public fork. Existing native tests independently cover task snapshot/request-history/archive preservation.

## Evidence from this review

Used the actual interpreter .venv/bin/python3.11 (Python 3.11.15); the task's spelling .venv/bin/python311 is absent. No interpreter, dependency, or venv changes were made. Every Python invocation used -B / PYTHONDONTWRITEBYTECODE=1 and TMPDIR=/tmp. Test subprocesses installed a Python audit hook rejecting socket.connect, socket.connect_ex, and socket.getaddrinfo; all model responses were the existing deterministic offline fixture.

1. /tmp/ignovate-map-handoff-review-focused.log: 8 tests, 1.596s, OK. All 3 new handoff tests plus native historical map retry, exact fork isolation, durable fork retry after enqueue failure/restart, multi-revision task/archive preservation, and checkpoint clone rejection of incomplete tool exchanges.
2. /tmp/ignovate-map-handoff-review-probes.log: 3 independent bounded probes, 0.646s, OK. Public fork accepts a done owner and excludes later history; another asyncio Task holding the real project lease still prevents done-owner fork with zero child creation; map continuation from the current head accepts the done owner, keeps the source branch, and completes through the existing pump.
3. Post-execution frozen-diff equality remained true.

No full suite, wheel build, installed-package gate, API request, or browser/network check was duplicated. Author's final union source/wheel/installed gates are still the author's acceptance responsibility; this report makes no completion claim about them.

## Issues

### Critical (Must Fix): 0

None found in the reviewed delta.

### Important (Should Fix): 0

None found in the reviewed delta.

### Minor (Nice to Have): 0

None raised. The copied pump is an explicit bounded-fixture maintenance cost below, not a defect requiring another implementation pass.

## Adjacent behavior assessed within the changed path

- Public fork with queued work has no queue guard, unlike map_send. Before this change it already permitted a fork with queued work and no current Task. Allowing the equivalent done-owner gap is safe here: command locking, no suspension in _fork, exact saved checkpoints, and the project lease preserve isolation. It creates a branch rather than consuming/reordering queued requests. No finding.
- A done Task may represent failure/cancellation. Its owned finally/context cleanup has still completed; continuation/fork must still reference an independently completed recoverable historical node, and _enqueue's paused-queue policy remains intact. No finding from allowing a truly done owner.
- Stable map request replay returns its previous result before busy checks. This existing idempotency behavior creates no second branch/run and is correctly preserved.

## Declined to judge / proposed executor rulings and costs

Every behavior considered and set aside is listed explicitly; these are proposed scope rulings for the executor, not silently dropped findings.

1. Other controller busy controls: open_project (432), configuration (570), test_connection (595), reasoning (606), accept_plan (627). Reason: unchanged conservative truthiness checks belong to project/runtime/configuration/plan transitions beyond the explicit map/fork fix. User effect: an immediate action in the same short retirement gap may still report busy and require retry. Proposed ruling: accept/defer separately, and describe this release as a map/fork handoff fix. Cost: retained transient retry inconvenience; expanding requires transition/cleanup/queue-specific tests, especially project switching and factory replacement.
2. Model selection, provider changes, plugin mutation/catalog refresh, and work-control mutations: controls.py:24, settings.py:204, plugins.py:33/:50, work_control.py:144/:215. Reason: the same unchanged conservative window exists in adjacent modules, with different resource/state ownership requirements. User effect: transient busy rejection or delayed catalog refresh, not data/history loss. Proposed ruling: accept/defer as adjacent consistency work rather than widen this frozen delta. Cost: retained brief inconsistency; a later shared-idleness change needs tests for each mutation's lease, queued work, factory cleanup, and state-version semantics.
3. Production pump redesign or a reusable interception hook to eliminate test pump duplication. Reason: production _pump remains correct; the small copied structure matches it except for the explicit deterministic post-await gate, and teardown is now sound. Proposed ruling: defer; retain the inline rationale and update this fixture if production pump scheduling changes. Cost: future maintenance coupling at tests/test_gui_map_handoff.py:23-34; a hook/refactor now adds unnecessary production surface and broadens review/verification.
4. Full phase28 capacity changes, previous accepted dirty work, inherited baseline issues unrelated to this handoff, packaging, and final Python 3.11/3.13 union source/installed/CLI/JS gates. Reason: outside the frozen two-file GUI delta; the author is already running final union gates. Proposed ruling: preserve separate phase28 findings and require the existing union acceptance evidence before release, without treating this review as acceptance of those gates. Cost: author's already planned build/full/installed gate time and final byte-provenance accounting; no additional review round requested.

## Recommendation and assessment

Ready to merge this bounded GUI delta: **Yes**. Review result: **0 Critical / 0 Important / 0 Minor**. The minimum guard change correctly admits only finished execution ownership, keeps map queue rejection and real lease/checkpoint safety, and the focused offline evidence covers both the recovered action and the relevant rejection boundaries. No C/I fix is requested and no rereview is requested. Release acceptance remains contingent on the author completing and reconciling the already planned final union gates and explicitly ruling on the Declined items above.
