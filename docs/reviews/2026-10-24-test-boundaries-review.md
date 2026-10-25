# Fresh bounded review: phase27 test boundaries

Read-only review of `docs/reviews/2026-10-24-test-boundaries.md` and the exact frozen delta `/tmp/ignovate-test-boundaries-review.diff`, following `.agents/skills/requesting-code-review/code-reviewer.md`. Scope: the diagnostic-gate fix in `tests/test_mcp_closing_receipt.py` and three new backend methods in `tests/test_model_http_backends.py`. No source, tests, dependencies, index, HEAD, or branch state was changed. No subagents, external services, real model APIs, or commits were used; reviewer artifacts and disposable old-wheel extraction were confined to `/tmp`, with bytecode writes disabled.

## Provenance and limits

- Independently verified accepted phase26 wheel SHA256 `50704687774c9e903a64f946329be563f52b875e637c0b379f9b7c726f559845`. All **158 packaged Python files** equal the current source bytes. The complete Python manifest under `nailong`, `ui`, and the eight configured top-level modules also equals the wheel manifest, so the comparison does not silently miss newly added production Python files.
- Independently reconstructed both baseline tests from their complete added-file sections in `/tmp/ignovate-model-backend-review.diff` and `/tmp/ignovate-mcp-closing-receipt-review.diff`. They exactly equal `/tmp/ignovate-test-boundaries-baseline/tests/...`. Generating their current unified diffs reproduces the phase27 frozen diff byte for byte; its SHA256 is `9bed7c06e477dd6b971beae5f4166363e0528c99faf416390e33c6563df13874`.
- Backend baseline/current SHA256: `9f3c9c25db8184192fb9ebeeb6b5003f490faa6846095186b298288b12611488` / `5e8276f3d28a89027bd14b191df87f684ca1ff8fa742f4de7bbd6abbf0b36f40`.
- MCP baseline/current SHA256: `9171f2733a48de10a21c1ae756ee834f118f67db0bc18c75606ccd77a6f48ba9` / `6ae52b83fd00240e1a734a924bd187f12438d5b08dbb66c5a027b89f5c9736a8`. The baseline hash agrees with the earlier MCP reviewer report.
- Tests are **not in the wheel**. Their historical provenance is the retained frozen review artifacts, with an independent earlier hash for the MCP file; it is not a Git commit baseline or wheel attestation. HEAD is not this review's baseline. Python-byte equality establishes no new production Python change in this bounded increment; it does not recertify the earlier implementation or all unrelated dirty files/assets.

## Strengths

- `tests/test_mcp_closing_receipt.py:36–37` puts only the observation inside `try`, with unconditional gate release in `finally`. It neither consumes the buffered receipt nor converts a diagnostic error into success. The SDK and ownership watcher can finish even when the observation fails; the existing receipt assertion still reports failure. The three original native regression cases and their outcome assertions remain intact.
- `tests/test_model_http_backends.py:22–31,78–86` derives from the actual default backend classes and deliberately copies their module/name. It therefore protects class identity, including both HTTPX families and sync/async operation. The subclasses delegate to public HTTPcore mock backends supplying HTTP bytes; real OpenAI request processing and SDK close run. This is stronger than checking a builder result or a mocked `is_closed` flag.
- `tests/test_model_http_backends.py:88–113` exercises explicit AnyIO backends through real requests to owned `127.0.0.1` listeners, holds the actual sockets, checks backend identity, and verifies closed descriptors after resource-owner shutdown. The socket list must be nonempty, so the descriptor assertion cannot pass vacuously.
- Independent negative control against the hash-verified phase24 wheel reproduced **4 errors and 2 failures across the 3 new methods**. The subclass cases fail on erroneous native-socket obligations; the AnyIO cases fail on replacement of the supplied backend. This directly demonstrates protection against the promised regression.

## Issues

### Critical (Must Fix)

None found.

### Important (Should Fix)

None found.

### Minor (Nice to Have)

**M1 — Make the new AnyIO test's server teardown independent of SDK close success.**

- File: `tests/test_model_http_backends.py:114–117`.
- `await sdk.close()` executes before `server.shutdown()`, `server_close()`, and `thread.join()` in the same `finally` suite. If close raises while diagnosing a lifecycle regression, server teardown is skipped. Each failed family can leave a listener and its daemon `serve_forever` thread alive until interpreter exit, adding noise and resources to subsequent tests.
- Proof: `/tmp/ignovate-test-boundaries-review-cleanup.py` executes both actual SDK/loopback subcases. Its SDK-close wrapper performs the real close, then reports an injected `OSError`. Both subcases correctly report errors, but both listening sockets and both server threads remain live before the probe's independent rescue. `/tmp/ignovate-test-boundaries-review-cleanup.log` records the result and confirms every probe-owned server was subsequently closed and joined.
- Suggested fix: place SDK cleanup inside a nested `try/finally` whose outer cleanup always shuts down, closes, and joins the loopback server. Preserve the close exception as a test failure.
- Severity: nonblocking fixture robustness. Current unmodified SDK behavior and all 14 relevant tests pass, and production bytes are unchanged. This is not a present product regression or a reason to reopen the prior production review.

## Validation

Independently executed, using Python 3.11.15 with `-B` and `PYTHONDONTWRITEBYTECODE=1`:

1. `PYTHONPATH=.:tests .venv/bin/python -B -m unittest test_model_http_backends test_mcp_closing_receipt -v`: **14 tests passed in 6.332s**. Log: `/tmp/ignovate-test-boundaries-review-tests.log`.
2. The supplied finite diagnostic-failure probe against the current fixture: failure reported, watcher/owner/sockets finished, **FD 7 → 7**. Log: `/tmp/ignovate-test-boundaries-review-fixture.log`. Reviewed the probe's RED-only gate rescue; it releases only its scheduling gate after the timeout and then reports failure.
3. The three new backend methods with production imported from a disposable extraction of accepted phase24 wheel SHA256 `c24d068c01ba9023aff237678e916c807e563247b7fefc8ea2a125ff8d2ed86a`: expected **3 methods / 4 errors / 2 failures**. Import origin was checked outside the checkout. Log: `/tmp/ignovate-test-boundaries-review-negative.log`.
4. The bounded close-error cleanup probe above: confirmed M1 and rescued every owned server. No resources intentionally left behind.
5. Exact production manifest/byte comparison, baseline reconstruction, and frozen-delta comparison described above.

Read the supplied diagnostic RED/GREEN logs and 107-test targeted result (**64.517s, 1 skip**). Also inspected the completed executor matrix results: Python 3.11 **2164 tests, 380.328s, 48 skips**; Python 3.13 **2164 tests, 385.744s, 49 skips**; actual installed-wheel runs **719 tests, 229.207s / 233.319s, 5 skips each**, with 158 Python-byte and module-origin attestations. These are executor results, not independently repeated reviewer runs. CLI/JavaScript final acceptance remains the executor's gate.

The local reviewer SDK versions were OpenAI 3.26.1, HTTPX 0.28.1, HTTPX2 2.13.1, HTTPcore 1.0.9, and HTTPcore2 2.13.1. Supporting source read covered `model_http.py`, relevant resource-owner admission/close paths, `StdioOwnership`, and the loopback handler/server definitions. No whole-project recertification is implied.

## Recommendations

Accept this test-only increment and record an explicit ruling on M1. Both previously deferred Minor objectives are met. No production changes or Critical/Important correction pass is requested. Keep the executor's complete acceptance evidence associated with the unchanged accepted wheel and these exact test bytes.

## Declined to judge / considered and set aside

Each line is separate for executor ruling.

- **D1 — Earlier production lifecycle correctness beyond these test boundaries:** production Python bytes are unchanged and this review was explicitly scoped to two deferred test-maintenance items; previous cancellation, retry, TLS, ownership, and long-task conclusions are not being reopened or recertified.
- **D2 — Native receipt/retry guarantees for explicit AnyIO or other custom backends:** the promised behavior is preservation of their SDK contract; a normal real socket close does not establish the default native-owner guarantees, and the new tests do not claim it does.
- **D3 — Unexecuted operating systems, alternate event-loop implementations, and future dependency versions:** independent execution was on the current macOS/Python 3.11 environment; executor Python 3.13 logs are additional evidence, not native coverage of those other environments.
- **D4 — Unrelated dirty files, frontend assets, packaging metadata, dependency changes, and HEAD history:** the accepted wheel plus reconstructed test artifacts define this bounded comparison; the Python byte check cannot attest every unrelated workspace item or reproducible-build provenance.
- **D5 — Persisting the diagnostic-failure injection probe as another repository test:** the required gate fix is present and its actual SDK failure behavior was independently exercised; this task did not require adding a permanent fault-injection method. The `/tmp` probe is evidence, not durable suite coverage.
- **D6 — Requiring `async def` for the asynchronous subclass override:** the ordinary method returns the public async backend's coroutine unchanged, which the real transport awaits; actual SDK requests pass. This is a valid protocol implementation, not a compatibility defect.
- **D7 — Treating the socket-close checks as empty-list or fake-success assertions:** the AnyIO list is explicitly nonempty and comes from a real successful keep-alive connection; current socket state is inspected directly. Public mock streams in the subclass scenarios intentionally test the custom non-native contract rather than native descriptors.

## Assessment

**Ready to merge? Yes.**

The two intended maintenance changes are correctly bounded, preserve existing failure assertions, and independently pass their real SDK regressions while detecting the earlier backend bug. M1 is a nonblocking failure-path cleanup improvement for the new fixture; this verdict covers only the frozen phase27 test increment and does not mark the broader project goal complete.
