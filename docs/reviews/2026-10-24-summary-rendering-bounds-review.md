# Summary rendering bounds: one final read-only review

## Scope and provenance

Reviewed requirements and initial author rulings: `docs/reviews/2026-10-24-summary-rendering-bounds.md`. Reviewed production delta: only `nailong/core/compact.py::_render_summary`; reviewed permanent regression addition: `tests/test_compact_summary_bounds.py`.

The baseline is the accepted phase29 wheel, **not HEAD or the whole dirty checkout**:

- `/tmp/ignovate-map-handoff-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`, independently verified SHA256 `e40302a5913b4d905b1ea2a8946f15bf8aca8cdc095143b53b032c93cc84a7e3`.
- Frozen delta `/tmp/ignovate-summary-bounds-review.diff`, independently verified SHA256 `a133ac7ec21e9841b3680133aaa6caec86cbb28b14bd79bd4ea4eb2f397007e8`.
- Reconstructing both unified diffs from that wheel and the current new test exactly reproduces the frozen diff. Of the wheel's 158 production Python files, only `nailong/core/compact.py` differs from the checkout.

This is the one requested fresh final review. I performed it myself and dispatched no subagents. No checkout, index, HEAD, branch, product/test file, dependency, interpreter, or environment was mutated. Persistent reviewer artifacts were written only under `/tmp`. Execution used source `.venv/bin/python` 3.11.15, `-B`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/tmp`. No real model API or external network was used.

## Strengths

- `compact.py:239-248` measures exactly the mandatory text that is ultimately joined, including the recovery index, goal excerpt, complete constraints, completion banner, and double newlines. A one-character deficit refuses L2 rather than truncating a requirement or publishing an undiscoverable index.
- `compact.py:253-274` accounts for each optional section as a complete rendered unit. The omission count is recomputed after each retraction, and the section is appended only after its heading, list, omission footer, and separator fit. It preserves the existing section order and excerpt budgets.
- The production change is confined to rendering. `_summary_state`, original snapshots, archive completeness checks, tool/pin boundaries, gain policy, capacity validation, expanded-summary metadata, and runtime request admission remain the accepted implementation. This does not increase the real provider input budget.
- `test_compact_summary_bounds.py:35-75,97-120` tests successful exact fits as well as refusal, through the public compactor at 6000/20000/1000000. It also exercises Unicode and a real `HistoryArchive`, verifies a discoverable complete index and original assistant analysis, and checks that the overflowing input is retained.

## Issues

### Critical — must fix

None found in the frozen delta.

### Important — should fix

None found in the frozen delta.

### Minor — nonblocking

**M1: The permanent optional-section regression does not assert retention, order, or omission-count accuracy.**

- Location: `tests/test_compact_summary_bounds.py:77-95`.
- The test asserts an upper bound, mandatory prefix, recovery reference, and unchanged state, but never asserts that any optional content appears or that the displayed omission count matches the entries actually displayed. Its name and requirements include omission notices, but a renderer that drops every optional section still satisfies all its assertions.
- Concrete evidence: `/tmp/ignovate-summary-bounds-review-coverage-probe.py` patches only the imported renderer in an isolated process to return the mandatory prefix and discard **all** optional text. The existing optional test still passes in 0.001s; output is `/tmp/ignovate-summary-bounds-review-coverage-probe.log`. No repository file was patched.
- Actual effect: there is no demonstrated production defect in this change. The implementation's retention/order/count behavior passed independent review probes. The gap leaves a future loss of useful optional excerpts or misleading omission counts without a focused permanent assertion.
- Recommendation: in a later test maintenance increment, use distinct optional items and assert at least one generous-budget section is present, selected item order, and an exact omission count after a boundary forces an item retraction. This is Minor because current behavior is correct and the core strict-capacity regression is permanent.
- **Proposed author ruling:** record and defer M1 as nonblocking; do not add it to a C/I pass. **Cost:** the stronger temporary review probe is evidence for this revision, not retained CI coverage for those optional-content semantics.

## Independently executed evidence

1. `PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp .venv/bin/python -B -m unittest tests.test_compact_summary_bounds -v` — **6 tests passed in 0.062s**. The real archive test's files were created under `/tmp`.
2. `/tmp/ignovate-summary-bounds-review-probes.py` — **69,892 capacity checks over 10 states passed**, in 0.931s. The probe independently constructs the mandatory format and checks refusal below its exact size, mandatory-prefix preservation, final length, optional section order, selected excerpt identity/order, correct displayed omission counts, and state immutability. It encounters 19,081 omission-only section cases. It also confirms generous-budget output equals the accepted wheel's output for all 10 states.
3. The same probe loads the actual accepted wheel's compactor code and compares the public boundary: at 6000, 20000, and 1000000, the baseline accepts a summary of limit+1, the candidate refuses with `protected_context` and original messages retained, and the candidate succeeds at the exact limit for the adjacent fitting input. `index_truncated=True` remains a refusal. Output is `/tmp/ignovate-summary-bounds-review-probes.log`.
4. The isolated coverage mutation described under M1 confirms the permanent-test gap, without a source or environment mutation.
5. Accepted-wheel hash, frozen-diff hash, exact reconstructed-diff equality, production-file inventory, and source interpreter version were independently checked.

I inspected the author's `/tmp/ignovate-summary-bounds-targeted.log`: **111 tests passed in 5.110s**. I did not rerun that whole group or the full release matrix. The author reports the full source/installed-package gates are running and finite real SDK loopback probes have passed; these are author-owned results, not independent reviewer certifications. This report does not claim the new wheel, both full interpreter suites, installed-package/origin checks, CLI/JS checks, or complete final SDK acceptance are finished.

## All considered behaviors: proposed author rulings and costs

These are proposed dispositions for explicit author ruling, not claims that the author has already adopted them.

| ID | Behavior and evidence | Proposed ruling | Cost / practical limit |
| --- | --- | --- | --- |
| C1 | Final mandatory formatting, including banner and double joins; exact independent prefix and adjacent-fit probes | Accept the exact final-format measurement and refusal if it cannot fit | Previously tolerated formatting excess now conservatively refuses L2; originals remain available |
| C2 | Empty-state/tiny capacity and Unicode; new direct exact-fit tests passed | Accept fixed-format overhead as part of the same character capacity | A capacity below even the fixed banner/title cannot produce a summary |
| C3 | Public default/explicit 6000, 20000, and absolute 1000000 boundaries; actual baseline comparisons | Accept the existing public defaults, integer validation, and 1M ceiling with exact output enforcement | Capacity remains Python character count, not bytes, graphemes, or provider tokens |
| C4 | Complete constraints, first goal excerpt, and recovery index remain mandatory; renderer reads without mutating state | Accept refusal instead of trimming protected content | The existing first-goal excerpt is still limited to 800 characters; a large mandatory block can prevent L2 even if optional text is empty |
| C5 | Public L2 overflow retains original messages/content/metadata in the new tests and independent probe | Accept unchanged nondestructive L2 refusal behavior | This is not a rollback promise for a separately successful L1/pin operation in an `all` call; that accepted behavior is unchanged |
| C6 | Optional section order and excerpt budgets; independently checked across 10 states and generous output equal to baseline | Accept the existing request-first/reverse-other-items policy and fixed section budgets | Fitting more optional material after removing the old 200-character reserve can change a candidate's gain and which later sections fit |
| C7 | Accurate omission count after retraction, omission-only sections, and complete section omission; independent exhaustive probe passed | Accept conservative whole-entry retraction and recomputation | The renderer can leave spare room or show only an omission notice instead of retrying a shorter excerpt; no maximal-fill promise is introduced |
| C8 | Optional state immutability and full saved constraints/evidence; read surrounding `_summary_state` and metadata construction | Accept a rendering-only change with no state/metadata schema change | Complete metadata can remain larger than rendered text; this patch supplies no new persisted-state size guarantee |
| C9 | Real archive index and original assistant analysis recovery; new real `HistoryArchive` test passed | Accept unchanged complete-snapshot and discoverable-index semantics | The archive must remain available for later recovery; optional visible excerpts are not substitutes for originals |
| C10 | Archive-truncated refusal and archive publication before a possible renderer refusal; surrounding code inspected | Keep fail-closed completeness checks and existing immutable snapshot publication | A refused compaction may already have saved recoverable snapshots/index records; this is pre-existing nondestructive I/O, not an original-history deletion |
| C11 | Existing pin/tool exchange boundaries, L1 behavior, hard/soft gain checks; compact.py surrounding implementation inspected | Retain the accepted algorithms unchanged | A newly longer optional candidate can still fail the unchanged soft gain gate; a hard call still uses its existing gain policy |
| C12 | Unknown/known-window helper, three runtime entry points, expanded marker, complete request token gates | Retain the accepted phase28/29 contracts; exact production-byte inventory shows these files are unchanged | Strict character fit is candidate construction only; actual complete-request admission may still reject it; broader runtime acceptance belongs to the final author gates |
| C13 | Fixed completion banner still describes historical assertions as unverified | Keep that banner mandatory in the exact calculation | Its character overhead is charged even for an otherwise empty summary |
| C14 | Independent fixed-format expectations and the public fixture's documented 143-character maintenance constant | Accept the explicit fixture as a valid regression tradeoff | Future intentional wording/format changes must update the fixture; dynamic generous-render extraction in other cases alone is not the independent oracle |
| C15 | Permanent optional test can pass after all optional content is discarded | Record/defer Minor M1 | Current correctness is independently checked; retention/order/count semantics lack the proposed focused permanent assertion |
| C16 | Extra optional-section reconstruction/retraction work | Accept the local bounded rendering work | Existing small per-section excerpt budgets bound the candidate entry list; no complete long-run performance certification is inferred |

## Declined to judge: every set-aside behavior and proposed ruling/cost

- **D1 — Whole long-task goal, previous accepted phases, and unrelated dirty files:** this review authenticates only the wheel-to-frozen rendering delta. **Proposed ruling:** preserve prior acceptance evidence and keep the overall goal active. **Cost:** this result cannot certify all earlier lifecycle, history, GUI, frontend, or IDE work.
- **D2 — Final distribution/full-matrix acceptance:** the author owns the new wheel, full 3.11/3.13 runs, actual installed package tests, 158-file/46-origin reconciliation, CLI/JS/diff checks, and finite SDK loopbacks; duplicating them was expressly excluded. **Proposed ruling:** require those final actual outputs before release acceptance. **Cost:** focused review success does not mean those gates are complete, green, or reproducible.
- **D3 — Real remote-provider behavior and exact remote tokenization:** real model APIs/external network were prohibited. **Proposed ruling:** retain the calibrated complete-request estimator and the authorized offline/loopback acceptance boundary. **Cost:** no cloud model reliability, latency, token-count, or service-capacity guarantee is supplied here.
- **D4 — Other platforms, alternate loops, and future dependency/private SDK interfaces:** no platform branch or dependency changed in this delta and no such native matrix was run. **Proposed ruling:** keep those claims limited to their existing actual evidence. **Cost:** source portability and this 3.11 review do not certify unexecuted OS/loop/SDK combinations.
- **D5 — Atomic configuration mutation after the final policy read:** this renderer change does not alter the accepted runtime revalidation boundary or introduce an atomic config/network snapshot. **Proposed ruling:** retain that boundary rather than attribute a new guarantee to exact character sizing. **Cost:** concurrency after the last policy read remains outside this patch's certification.
- **D6 — Phase27 AnyIO fixture cleanup when `sdk.close()` itself fails:** the separately evidenced deferred Minor is in a different test increment and is unchanged. **Proposed ruling:** keep its explicit independent deferred disposition. **Cost:** that diagnostic failure path can still skip fixture server cleanup; this is not repaired or recertified by the summary patch.
- **D7 — Other GUI commands' conservative idle gap:** the previous map/fork fix and other controller actions are unchanged and not exercised by this renderer review. **Proposed ruling:** retain their recorded separate scope/deferment. **Cost:** those actions can still briefly return busy until owner retirement; this review supplies no broader GUI admission guarantee.
- **D8 — Arbitrarily large or indefinitely growing history/state/archive capacity:** probes cover the specified 1M rendered limit and bounded representative states, not unlimited persistent histories. **Proposed ruling:** keep the existing archive/state contracts and separate long-run capacity work. **Cost:** complete metadata/archive growth, all possible historical corruption, and indefinite resource scaling are not newly certified by a strict rendered-text limit.

## Recommendations and assessment

Explicitly record the ruling and cost for M1 and every considered/Declined item. Finish and reconcile the already planned author release gates against the final wheel. No Critical/Important correction is requested, and no rereview is requested.

**Ready to merge the frozen bounded delta: Yes.** Review result: **0 Critical / 0 Important / 1 Minor**. The implementation closes the demonstrated final-format overflow without truncating constraints or recovery information, and independent exact-boundary and optional-section probes found no product regression. M1 is a nonblocking permanent-test coverage improvement; final package acceptance and the broader goal remain separate unfinished author work.
