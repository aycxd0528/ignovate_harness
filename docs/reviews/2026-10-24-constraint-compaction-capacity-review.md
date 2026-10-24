# Phase28 long-constraint compaction: single final read-only review

Reviewer: fallback completion of the one fresh final review. The initial Astra reviewer terminated at its usage limit without a final report; this report does not re-review an accepted report. No subagents were dispatched.

## Reviewed scope and provenance

Requirements and author rulings: `docs/reviews/2026-10-24-constraint-compaction-capacity.md`.

Accepted baseline: `/tmp/ignovate-mcp-closing-receipt-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`, independently verified SHA256 `50704687774c9e903a64f946329be563f52b875e637c0b379f9b7c726f559845`. HEAD was not used as the baseline.

Only the final frozen delta `/tmp/ignovate-constraint-capacity-review.diff` was reviewed. Its SHA256 is `b274fa06fa2c949746a50193ac8034fd5526df8debbadba8e483aa122a3dfaba`. Reconstructing the unified diff from the accepted wheel to the current four paths matched the frozen diff exactly, including the external-factory adapter correction:

| Current path | SHA256 |
| --- | --- |
| `nailong/core/compact.py` | `0680a4b5bfcc72f8cc4611471f0f0764801b281021446c0a3de7f6ec65f2ed7f` |
| `nailong/core/context_runtime.py` | `76df6d02be4be775b4748ee5075d0d551eeaeee041217bb6e4a73d65590a5c44` |
| `agent_service.py` | `5999c78b92d3804de9054ad04f8c43a131f2bc003aff49968d4967e61fc5f281` |
| `tests/test_constraint_compaction_capacity.py` | `f51f877993d7993c6e830e79548868c9c5772a1f84d826aee09d78a60dfe4448` |

The checkout, index, HEAD, branch, and dependencies were not mutated. Reviewer probes used Python `-B`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/tmp`; persistent reviewer artifacts are under `/tmp`. No real model API or external service was called.

## Strengths

- `compact.py:20-27, 270-303`: the shared helper expands the representation candidate under a known input limit, validates the new explicit argument, and leaves standalone/unknown defaults at 6000. It does not treat four characters per estimated token as token permission.
- `compact.py:180-234, 243-246`: all recognized persistent constraints remain whole. The change does not replace the archive/index recovery path with excerpts or remove earlier requirements. The new runtime regression verifies both the complete 9000-character requirement and recovery of the old 900000-character assistant analysis from the archive index.
- `context_runtime.py:493-501, 602-619` and `agent_service.py:723-731`: automatic preflight, request-only prior history, and manual compaction use the same capacity rule. Request-only compaction preserves checkpoint originals; manual compaction remains an owned worker operation and makes no model call.
- `context_runtime.py:438-446, 475-477, 668-679`: persistent expanded-summary metadata is checked at preflight and at final request preparation. Losing a verifiable window refuses the expanded summary. The existing task-projection protection and complete request estimate, calibration, system/tools/framing accounting, output reserve, and final input limit check remain in place.
- `agent_service.py:725-730`: external factories lacking model/root metadata use the unknown-window fallback. The old external-factory test passes unchanged; the adapter correction is part of this reviewed final delta.
- `tests/test_constraint_compaction_capacity.py:43-145`: regression coverage follows the real Factory → Graph → SQLite path, including reopen, archive recovery, request-only behavior, all three entry points, unknown/small windows, removal of configuration after preflight and checkpointing, and invalid/absolute capacities. Fake model calls are observed at the actual runtime boundary rather than by stubbing the compactor.

## Issues

### Critical (Must Fix)

None found in the reviewed delta.

### Important (Should Fix)

None found in the reviewed delta.

### Minor (Nice to Have)

**M1 — The renderer's formatting can slightly exceed the explicit character capacity.**

- Location: `nailong/core/compact.py:246-247, 252, 267`.
- Effect: `_render_summary` measures the protected prefix with single newline joins, then adds the completion banner and returns double newline joins. A successful result can therefore be a few dozen characters longer than `summary_max_chars`; the nominal 1000000-character bound is not exact. The new tests reject clearly oversized constraint bodies but do not exercise the exact rendered boundary.
- Proof: the reviewer probe's public call with `[HumanMessage('Investigate'), AIMessage('analysis '*10000), HumanMessage('must '+'c'*(limit-142)), AIMessage('analysis '*10000), HumanMessage('Continue')]`, `keep_turns=1, level='L2', hard=True, summary_max_chars=limit`, returns a 6001-character summary for limit 6000 and a 1000001-character summary for limit 1000000. Direct renderer probes returned 134/6034/1000034 characters for limits 100/6000/1000000. See `/tmp/ignovate-constraint-capacity-review-probes.log`; the public 1000000 case was additionally run inline during review.
- Baseline comparison: the 6001-character result is text-identical to the accepted wheel's default output for the same history. This is an inherited renderer accounting edge now exposed by the new explicit capacity API; it is not loss of constraints or a bypass of the final token gate. The small bounded formatting excess is Minor by effect, not a C/I blocker.
- Recommendation: in a follow-up, account for the final join and fixed banner before allocating optional sections, then enforce the final rendered size by omitting optional material or refusing L2. Keep the complete constraint block. Add boundary cases for default/explicit/absolute capacities. Author should explicitly record or defer M1; no production changes were made by the reviewer.

## Tests and evidence

Independently run on the source Python 3.11 environment:

1. `TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python -B -m unittest test_constraint_compaction_capacity test_v1_features.CompactionTests` — **13 tests passed in 0.758s**. This includes all ten new methods and the unchanged legacy adapter class.
2. `/tmp/ignovate-constraint-capacity-review-probes.py` — **4 native Graph/SQLite probes passed in 0.324s**. They verify: expansion without an active TaskStore task; persistence through Factory/SQLite reopen followed by unknown-window refusal; known smaller-window refusal after checkpointing; known smaller-window refusal after preflight; and calibration raised only after preflight still refusing before any model call. The latter two late mutations are separate test methods; the reopen test covers both absence of an active task and marker persistence. Script and final output are preserved under `/tmp`.
3. Helper boundary checks confirmed unknown/0/1/1500 inputs retain a 6000-character candidate, 122880 maps to 491520, and large budgets saturate at 1000000. Renderer/public-capacity probes established M1, including a byte-equivalent accepted-wheel control.
4. Exact wheel hash, four current file hashes, and wheel→current frozen-diff equality were independently checked.

Reviewer probe history: an initial three-case inline probe had one fixture error because it used `factory.calibration` instead of the actual `factory.context_calibration`/middleware calibration. No product assertion failed in that case. The corrected saved probe operates on `manager.calibration` after preflight, and all four final cases pass. No implementation or dependency change was made to obtain that result.

Inspected author evidence, without claiming to have rerun it:

- `/tmp/ignovate-constraint-capacity-adapter-targeted.log`: 100 tests passed in 9.185s.
- `/tmp/ignovate-constraint-capacity-native-wire-fixed311.log` and its script: two actual default HarnessChatDeepSeek/OpenAI/HTTPX streaming requests to a finite 127.0.0.1 SSE fixture across Factory/SQLite close/reopen retain the full requirement, omit the old full analysis, and report 8221/8243 estimated input tokens within 122880. The log records file descriptors 4→4 and no new threads. The previous non-streaming fixture failures are not used as passing evidence.

The author owns the final full source/installed package/CLI/JS gates. This report does not claim those gates are complete or green. During review the author reported missing dependency files in an earlier owned 3.13 installation, then reported rebuilding an isolated 3.13 environment from the same offline hashes, checking all dependency/application RECORD files, and passing its two-request native streaming probe. The author reports that final full/installed gates are still running. These later author results were not independently rerun by this reviewer; the reviewer did not modify either environment or attribute the earlier condition to this delta.

## Considered behaviors and dispositions

Each considered item below has an explicit disposition so the author can rule on it.

- Complete recognized constraint content — accepted: construction is unchanged except for the candidate ceiling; tests inspect the entire requirement and archive/index content.
- Earlier requirements and task revision/history fidelity — accepted: real reopen test compares task ID, revision, constraints, requirements, and request history; no TaskStore writes are introduced by the delta.
- Complete assistant analysis/archive recovery — accepted: the real index points to the complete original assistant analysis; no new truncation path was introduced.
- Automatic/manual/request-only capacity parity — accepted: all three modified call sites use the shared helper; owned regressions cover each.
- Request-only checkpoint immutability — accepted: the new test confirms the prior full analysis remains in SQLite while the actual request uses its compact view.
- Unknown-window initial behavior — accepted: the 6000 default and original raw history remain available; this preserves the previous unknown-window contract rather than inventing a token allowance.
- Known small-window behavior — accepted: initial small-window test and reviewer late-smaller-window probes keep model calls at zero.
- Expanded metadata persistence and config removal — accepted: current tests and reviewer Factory/SQLite reopen probe demonstrate refusal after the window becomes unknown.
- Existing unmarked summaries — accepted: the guard relies on the explicit new marker, as required for legacy compatibility; it does not retroactively infer expansion from content length.
- Explicit `_auto_task_context=False` behavior — accepted by inspection: summary protection runs independently of that flag; the previous task-projection condition retains the flag.
- Four-character candidate approximation — accepted: it is bounded construction capacity, and the complete calibrated actual request still controls admission.
- Calibration and late known-window changes — accepted: reviewer mutations only after preflight still hit the final gate and make zero model calls.
- Actual system/tool schemas/framing and output reserve — accepted by inspection: final `request_report`, reserve/cap calculation, and hard input comparison remain unchanged and after candidate construction.
- Invalid candidate types and absolute argument bounds — accepted: positive exact integers only; bool, zero, negative, float, string, and capacity above 1000000 are rejected. Formatting precision is separately M1.
- Tiny known input budgets mapping to the 6000 default — accepted: this preserves the minimum representation target but grants no provider permission; the final gate rejects an oversized complete request.
- External factories with absent settings/model/root metadata — accepted: metadata lookup uses a 6000 unknown fallback, and the existing adapter test passes unchanged.
- Manual compaction not doing a full provider fit check itself — accepted: it makes no provider call, only prepares/persists a recovery-backed summary; later actual requests recheck the window and complete input.
- Worker ownership, cancellation publication, and checkpoint concurrency — accepted by inspection: existing `_owned_worker`, state recheck, and whole-sequence replacement are retained; config lookup now runs inside the worker.
- Existing gain thresholds and completed-tool boundaries — accepted by inspection: these algorithms are unchanged; the new capacity argument does not relax the gain check or split tool exchanges.
- Larger checkpoint text and memory/I/O — accepted with bounded cost: expanded rendered content is at most the nominal 1000000 plus M1's small formatting excess; existing history metadata/archive resource behavior is unchanged. No unlimited candidate permission is added.
- Optional summary excerpts and index discoverability — accepted: optional section budgets remain fixed; the larger capacity is chiefly available to complete constraints, and the full archive index remains visible.
- Recompaction of a previously expanded summary — accepted by inspection: the full saved constraint state is merged and the new summary is marked again from its rendered size; it is not silently downgraded by copying only excerpts.
- Private helper rename — accepted: repository call sites were updated; no public compatibility promise for `_check_expanded_task_window` was found.
- Caller-supplied expanded metadata — accepted: it can cause a conservative refusal under unknown configuration, not additional provider permission.
- Exact character ceiling — Minor M1: inherited formatting accounting does not enforce the new nominal ceiling precisely; explicitly record/defer rather than silently dropping it.

## Declined to judge

- Whole ongoing goal and earlier phases/platforms — not certified: this is the final review of the frozen phase28 delta, not a replacement for earlier acceptance evidence or the remaining author gates.
- Cause or repair of the owned 3.13 dependency-file condition — not judged: no dependency changes are in this delta, no environment repair was authorized as reviewer work, and the reviewer did not inspect/modify that environment.
- Exact tokenization or behavior of a real remote model — not certified: external model APIs were prohibited; the implementation retains an estimate with calibration, and inspected native SDK evidence uses an owned loopback fixture.
- Atomic configuration guarantees after the final policy read and before network I/O — not newly certified: the runtime has no atomic config snapshot contract, and this delta adds revalidation at the established final request boundary. The reviewed late-change cases before that boundary are tested.

## Proposed author rulings and costs

These are recommendations for the author's explicit ruling, not claims that the author has already ruled.

- M1 — proposed ruling: record and defer the inherited formatting precision issue as nonblocking. Cost: callers treating `summary_max_chars`/1000000 as an exact rendered limit can receive a small formatting excess; complete-request token admission remains enforced. A later boundary fix must retain all constraints.
- Declined whole-goal/prior-platform certification — proposed ruling: retain the review's delta-only scope and keep the original goal active. Cost: final source/package/platform acceptance still needs its existing evidence.
- Declined 3.13 environment cause/repair — proposed ruling: leave diagnosis and replacement-environment acceptance to the author, with failure history preserved. Cost: successful reviewer 3.11 evidence does not independently certify the rebuilt 3.13 environment.
- Declined remote-model exact tokenization — proposed ruling: accept the authorized offline/native-loopback boundary and retain the existing calibrated estimator contract. Cost: this review supplies no measured remote-provider token count or remote-service guarantee.
- Declined atomic config changes after the final read — proposed ruling: retain the established final-request revalidation boundary. Cost: concurrent changes after that last read are not made atomic by this patch; the demonstrated before-boundary unknown/smaller-window changes remain refused.

## Recommendations

Record the author's explicit ruling for M1 and each declined item. Complete the already planned final source and installed-package gates against the final author wheel; do not count the pre-adapter green subset as final acceptance. No Critical/Important implementation correction is requested by this review, and no second review is requested.

## Assessment

**Ready to merge? Yes, for the frozen phase28 code delta, with M1 recorded as nonblocking.**

**Reasoning:** The change repairs the demonstrated recoverable long-constraint failure across the three entry points while retaining complete constraints and recovery data and enforcing the final actual-request budget. Independent focused tests and bounded native probes found no C/I regression. Distribution/platform acceptance remains the author's unfinished gate; the overall long-task stability goal remains active.
