# Phase32 two-test maintenance: final read-only review

## Scope and review identity

This is the one completed fresh review of the frozen phase32 test delta. The primary `/root/review_gap_tests_review` attempt failed immediately with a usage limit and produced no review; that attempt remains a failed review, not a pass. This fallback reviewer performed the review directly and dispatched no subagents.

Reviewed the requirements and four initial rulings in `docs/reviews/2026-10-24-review-gap-tests.md`, the reviewer template, both complete touched test files, the local HTTP server/handler, the renderer, runtime model-client ownership/cleanup, native HTTP backend selection, and the installed `AsyncOpenAI.close`/`is_closed` implementations. The baseline is the saved before-phase32 text, not HEAD or the whole dirty tree.

Frozen diff: `/tmp/ignovate-review-gap-tests-review.diff`

SHA256: `53920c1c887f16b2d34f80ecd9c3d98595873d187a9e01263c5ba2c3d69a205a`

The reviewer independently regenerated the unified diff from the saved baselines and current files; it equals the frozen diff byte for byte. Both baseline hashes match `/tmp/ignovate-review-gap-tests-frozen.json`.

Current reviewed test hashes:

- `tests/test_model_http_backends.py`: `835cc15bcb88fe12328df41495308e24c17543f3f3e877c3cc0cec6c502a3fbd`
- `tests/test_compact_summary_bounds.py`: `12515703822183aa477149e4b76e90bc92871a4f05bd6f8270fd3f32d450e3af`

Independently checked the accepted wheel SHA256 `85fb1423e204fcc4f7fcdd80f8ced29b61d355f55a9f2732c0f21567275f19bc`: all 159 Python members match the working source bytes, with zero missing or differing files. The wheel does not contain the tests. No new wheel or production change is part of this reviewed delta.

## Strengths

1. **Cleanup now survives the reported redundant SDK-close failure.** At `tests/test_model_http_backends.py:115`, the nested `finally` makes shutdown, listening-socket close, and serving-thread join run when the final `await sdk.close()` raises. With those ordinary server operations succeeding, the original SDK-close exception propagates. The patch changes the test fixture rather than production lifecycle ownership.
2. **The permanent regression exercises the real integration path.** At `tests/test_model_http_backends.py:122`, the regression invokes the actual existing AnyIO fixture, uses both HTTPX families with real loopback requests and native client closure, and injects only when the matching SDK client is already closed. Inspection confirmed explicit AnyIO backends are left undecorated by `own_model_http`; `RuntimeResourceOwner` calls the SDK's native close path. The SDK implementation awaits the HTTP client's `aclose`, and its closed check reads the actual client state. Listener/thread assertions occur before rescue, so rescue cannot make the old fixture pass. For the HTTPX2 injection, the preceding HTTPX fixture iteration also completes normally.
3. **Summary expectations are independent and distinguish all optional sections.** At `tests/test_compact_summary_bounds.py:122`, the expected literal output protects each distinct request, skill, action, conclusion, and evidence value, section order, early-to-late request order, and reverse order for other sections. The expectation is not derived from the renderer result.
4. **The small-capacity regression exercises actual retraction.** At `tests/test_compact_summary_bounds.py:137`, the 148-character case requires the omission count to change to three after one initially admitted entry is removed. At 147, only the four-item omission notice remains. A smaller allowance omits the whole optional section. Snapshot comparisons cover state immutability after generous rendering and after the three boundary calls. The existing six summary tests remain unchanged.

## Issues

### Critical — none

No Critical issue found in the frozen delta.

### Important — none

No Important issue found in the frozen delta.

### Minor — none

No actionable Minor issue found. Format coupling and the limited injected failure are explicit, reasonable maintenance costs, not newly discovered defects.

## Independent verification

- Ran `PYTHONPATH=tests .venv/bin/python -B -m unittest test_model_http_backends test_compact_summary_bounds`: **20 tests passed in 6.936s**, exit 0. Log: `/tmp/ignovate-review-gap-tests-review-focused.log`.
- Independently loaded the saved baseline module in memory, substituted only its old AnyIO fixture method into the current test class, and ran the new regression. **One method produced the expected two assertion failures in 1.868s**, one for each HTTPX family; both observed listener descriptor 6 instead of -1. There were no test errors, and the regression's rescue completed. The bounded probe asserted these expected failures and exited 0. Log: `/tmp/ignovate-review-gap-tests-review-red.log`. No source file was patched.
- Inspected and independently reran `/tmp/ignovate-review-gap-summary-mutations.py`. All six renderer mutants were killed by assertion failures with **zero errors**: drop all optional sections (2 failures), reverse requests (1), keep oldest other items (2), retain stale omission count after retraction (1), omit omission notices (1), and skip retraction (1). Each mutant ran the two new summary methods. Mutations existed only in the disposable process.
- Independently verified frozen-diff equality, both before-text hashes, accepted-wheel hash, and all 159 production Python members. Rechecked reviewed test hashes when writing this report.

All reviewer writes were confined to `/tmp` reports/logs. The reviewer did not install packages, run a full suite, contact a real provider, modify a user database, or mutate product/test/docs/index/HEAD state.

## Four initial rulings: proposed disposition and cost

1. **Accept:** nested `finally` for this fixture's server cleanup while retaining the redundant SDK-close exception. **Cost:** no new universal guarantee for server cleanup failures or repeated cancellation; no production lifecycle change.
2. **Accept:** real SDK/socket regression with failure only at the final redundant close and resource inspection before rescue. **Cost:** tests depend on the current client-family module identity, SDK internals already used by this integration fixture, and the fixture method's current interface; legitimate future changes may need test maintenance.
3. **Accept:** literal content/order expectations plus the 148/147/small-allowance boundary cases and state snapshots. **Cost:** intentional wording or formatting changes require updated expectations; these methods do not exhaust all capacities or require maximal capacity utilization.
4. **Accept for this delta:** retain the accepted wheel, compare production bytes to it, and use frozen before-text test baselines. **Cost:** this test-only review neither builds a new release nor substitutes for the author's final source, installed-package, CLI/JS, environment, and whole-goal gates.

## Considered behaviors and disposition

The following current behaviors were judged in scope and are satisfactory: original final-close failure propagation with ordinary server cleanup, actual listener/serving-thread release, both SDK transport families, real native client closure before injection, absence of rescue masking, all optional sections and their ordering, omission-count recomputation after retraction, omission-only and whole-section omission, and preservation of input state.

## Declined to judge

Each line below identifies a behavior set aside from this delta's acceptance, with a proposed executor ruling and its cost. These are boundaries, not findings requiring speculative changes.

- **Server shutdown/close/join itself failing, or arbitrary repeated cancellation while cleaning up:** no new general server-cleanup contract is requested or introduced. **Proposed ruling: accept the stated ordinary-cleanup boundary. Cost:** this patch alone does not guarantee complete cleanup or preserve the SDK error as the primary exception if cleanup itself fails.
- **Failure of the first native SDK close, TLS/provider/network failure recovery, and production cancellation safety:** the new regression deliberately preserves the first native close; the reviewed production bytes are unchanged. **Proposed ruling: retain the already accepted production coverage and keep this maintenance regression scoped to redundant-close failure. Cost:** this review does not newly certify those production fault paths.
- **Future SDK private API/module changes or future setup/teardown added to the directly invoked fixture:** the current fixture needs no separate setup/teardown and the current families are verified. **Proposed ruling: accept current integration coupling. Cost:** future legitimate fixture or SDK changes may require adapting this regression.
- **Every capacity, maximal packing, Unicode truncation policy, or allocation fairness between optional sections:** the new tests close the recorded semantic and retraction gaps; unchanged tests additionally cover required formatting and capacity bounds. **Proposed ruling: accept targeted permanent cases and the six mutation checks. Cost:** no exhaustive renderer proof or new display policy follows from this review.
- **Complete summary-state construction/archive semantics and the broader long-goal/product lifecycle:** this delta changes only tests and leaves production behavior unchanged. **Proposed ruling: retain those existing acceptance gates separately. Cost:** renderer-level regression success does not certify every history, archive, or whole-goal behavior.
- **Final full-suite, installed release, CLI/JS, and whole-goal acceptance:** those belong to the author matrix and were not duplicated by this reviewer. **Proposed ruling: require the author to record terminal outputs before claiming those gates pass. Cost:** this verdict approves the test delta only.

## Author evidence, kept separate

The author reported both outside-checkout installed runs of 799 tests had exited 0. This reviewer did not rerun or independently certify that matrix.

The author reported the initial 2202-test source runs each had three welcome-UI failures after launching with explicit empty `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL` values. This reviewer inspected the subsequent unchanged-installed-wheel control logs: `/tmp/ignovate-review-gap-welcome-env-control.log` records unset → 0 and blank → 1; the unset log has three tests passing, while the blank log has the same three tests failing. This corroborates the environment explanation without modifying production or tests.

At report preparation, the author said corrected source runs were still live, and the inspected tails of `/tmp/ignovate-review-gap-tests-final-full311.log` and `/tmp/ignovate-review-gap-tests-final-full313.log` had no terminal summary. They are **pending author gates**, not independently green results. Initial failed logs are retained. Any later matrix outcome must be recorded by the author rather than inferred from this focused review.

## Recommendations

Accept this frozen delta and record the above rulings/costs in the author's single disposition pass. There are no Critical or Important findings requiring a code change. Finish and record the already running author gates from their actual terminal outputs. Preserve the primary usage-limit failure as a failed attempt. No additional review pass is requested.

## Assessment

**Ready to accept the frozen two-test maintenance delta: Yes.**

The fixture fix addresses the demonstrated listener leak, the new regression fails against the saved old fixture and passes against the current fixture, and the new summary assertions detect the six relevant semantic regressions. This is approval of the bounded test maintenance delta; it is not a whole-goal completion or release acceptance claim.
