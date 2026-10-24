# Fresh bounded review: model backend compatibility

Reviewed once, read-only, against `docs/reviews/2026-10-24-model-backend-compatibility.md` and `/tmp/ignovate-model-backend-review.diff`. The production baseline is the accepted wheel `/tmp/ignovate-mcp-diagnostic-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`, not HEAD. Independently verified SHA256 `c24d068c01ba9023aff237678e916c807e563247b7fefc8ea2a125ff8d2ed86a` and exact equality between its `model_http.py` → checkout delta and the frozen production diff. The new test file also matches the frozen delta exactly. No checkout, index, HEAD, or branch changes; no subagents or real model APIs.

## Strengths

- `nailong/core/model_http.py:184–190` compares actual default class identity. Public HTTPcore MockBackends, default-class subclasses, and explicitly selected AnyIO implementations cannot acquire native socket obligations merely because their package or class name resembles a default. The import failure path safely keeps the existing SDK contract.
- `nailong/core/model_http.py:206–216` completes fresh-pool eligibility checks before inspecting used pools or decorating anything. This fixes the otherwise-valid warmed-default/custom-mount case without weakening rejection of already-used, entirely default clients.
- `tests/test_model_http_backends.py:17–132` exercises actual OpenAI SDK calls and real HTTPX/HTTPX2 response parsing. It checks the selected offline transport before issuing the invalid-host request, checks backend identity after adoption, verifies warm native sockets close, and checks that a rejected default client remains open for its caller.
- The implementation is tightly bounded: stream receipt bookkeeping, close retry behavior, cancellation handling, TLS, and request deadlines are unchanged. No global SDK alteration or schema migration is involved.

## Issues

### Critical (Must Fix)

None found.

### Important (Should Fix)

None found.

### Minor (Nice to Have)

**M1 — Keep regression coverage for the exact identity boundary.**

- File: `tests/test_model_http_backends.py:21`, alongside `nailong/core/model_http.py:184`.
- All persisted custom-backend scenarios currently use `MockBackend`/`AsyncMockBackend`. They catch the original package-prefix bug, but do not directly preserve the separately stated subclass and explicitly selected other-native-backend promises.
- The current implementation behaves correctly: supplemental reviewer checks passed for same-module/name `SyncBackend`/`AutoBackend` subclasses with actual SDK request/close and for explicitly installed `AnyIOBackend` objects in both HTTPX families. This is a future regression risk, not a present failure.
- Add a small durable check for a default subclass and explicit AnyIOBackend when the next authorized test expansion is made. A subclass deliberately using the default class's module/name ensures the test protects identity rather than only a name/prefix rule.

## Validation

- Independent Python 3.11.15 run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -p test_model_http_backends.py -v` — **8 methods / 16 subcases passed in 4.389s**. One asyncio debug slow-task notice appeared during fixture initialization; no test failure resulted.
- Supplemental in-memory probe — **6 cases passed**: both HTTPX families × sync/async same-module/name default subclasses, using actual OpenAI requests over public MockBackend response bytes; both families' explicit AnyIOBackend admission and empty-client SDK close. Exact unmodified defaults were also positively checked. The explicit AnyIO cases did not exercise network I/O. These supplemental probes made no network requests and disabled bytecode writes.
- Read the source of the existing 31 native ownership tests, the fixture setup, and the runtime ownership/close integration. Read the supplied 72-test GREEN log (`35.053s`), the original 8-error RED log, and the warmed-mixed 4-error RED log. The reviewer did not independently rerun the entire native or package matrix; the executor owns the final Python 3.11/3.13, installed wheel, CLI, and JavaScript gates.
- Local review environment: OpenAI 3.26.1, HTTPX 0.28.1, HTTPX2 2.13.1, HTTPcore 1.0.9, HTTPcore2 2.13.1. The repository lock records OpenAI 3.26.0; locked/installed acceptance remains with the executor's matrix.

## Recommendations

Record M1 as nonblocking follow-up and retain the planned full acceptance gates. No production revision is requested by this review.

## Declined to judge

Each line is an explicit item for the executor to rule on; none is silently included in this approval.

1. **Other dirty production changes and historical review outcomes:** excluded because this is the frozen two-file compatibility delta against the accepted wheel, not a review of HEAD or earlier completed changes.
2. **Changing mounts/backends after a client has already received `_Backend` wrappers:** the existing early return of an established owner can bypass later pool checks; this behavior predates this delta and the brief expressly leaves post-install mutation unsupported.
3. **Sharing a client/transport between independent factories:** existing wrapper-owner reuse does not establish separate lifetimes; this is an expressly retained boundary, not a new sharing guarantee.
4. **Racing a request against initial adoption:** the used-pool check and wrapping are not one lock-protected transaction. Product construction adopts fresh clients before requests, and this delta does not add concurrent external-client adoption support.
5. **Monkeypatching an exact default backend instance/class, or privately replacing an AutoBackend's internal delegate:** exact class identity cannot prove the implementation behind that identity is unmodified. The approved predicate deliberately identifies default classes; support for arbitrary internal monkeypatches was not assessed.
6. **Direct use of a custom async SDK followed by synchronous `factory.close()`:** fallback clients have no native owner's `started` signal, so this broader usage cannot claim the default native activity guard. The existing documented contract requires `aclose()` after async use; extending that guard to custom clients is outside this delta. The added tests correctly use `aclose()` for async SDKs.
7. **Native receipt/retry guarantees for mixed or explicit custom-backend clients:** the approved whole-client fallback deliberately delegates their lifetime to the SDK. Normal warm socket closure is tested; stronger native settlement guarantees for those clients were not assessed or promised.
8. **Future dependency layouts, Trio execution, and other platform matrices:** current class paths and installed defaults were inspected, but future private-module moves and additional event-loop/platform behavior were not executed. Missing expected class paths fall back to SDK ownership; these broader support claims need separate evidence.
9. **Full-package and installed-artifact acceptance:** the supplied native/Factory GREEN evidence was reviewed, while final Python 3.11/3.13, actual installed-wheel provenance/bytes, CLI, and JavaScript checks remain assigned to the executor and are not independently certified here.

## Assessment

**Ready to merge? Yes, subject to the already planned final acceptance gates.**

The frozen change correctly narrows native wrapping to the actual default classes and fixes warm mixed-client eligibility without partially decorating a client or taking over a rejected caller-owned client. No Critical or Important issue was found; M1 concerns durable coverage for behavior that passed supplemental checks.
