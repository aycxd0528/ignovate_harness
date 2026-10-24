# Phase26 fresh read-only MCP closing-receipt review

## Scope and provenance

This is the single final review of `/tmp/ignovate-mcp-closing-receipt-review.diff`, using `.agents/skills/requesting-code-review/code-reviewer.md` and the bounded design in `docs/reviews/2026-10-24-mcp-closing-receipt.md`. I did not dispatch another reviewer or modify source, tests, the index, HEAD, or branch state. No model APIs, installs, commits, or external services were used. The supplemental probes and this report are under `/tmp`.

I independently verified the accepted phase25 wheel SHA256 as `b37090a696f16a17ed3b934000acc860fdb54e6ec779ab9bc8b03cf1cd696894`. The wheel's `nailong/mcp/stdio_owner.py` versus the checkout, plus `tests/test_mcp_closing_receipt.py` versus empty, reproduces the supplied frozen diff exactly. I rechecked that equality before this report. HEAD was not treated as the baseline.

Reviewed file hashes:

- `nailong/mcp/stdio_owner.py`: `c1c51d947d2209f6919ab9247a3fd3225b3a344a976801fb9fd40916702329e5`
- `tests/test_mcp_closing_receipt.py`: `9171f2733a48de10a21c1ae756ee834f118f67db0bc18c75606ccd77a6f48ba9`
- Frozen diff: `10940fcb0500ff3697e179f6eab0efe2c52f520690af2874417d2b88396c2705`

The supporting read included the complete MCP manager, ownership channel, outer helper, process guard and inner helper; the installed MCP 1.30.0 stdio transport and POSIX termination code; AnyIO 4.15.1 process/task-group cleanup; the native service fixture; and relevant existing MCP cancellation/ownership tests. This is a bounded increment review, not a declaration that the ongoing project goal is complete.

## Strengths

- `nailong/mcp/stdio_owner.py:54-68` records the final observation before publishing it. Unknown cleanup now closes admission and survives an already-closing connection. An exact authenticated `C` followed by EOF preserves a clean explicit close and preserves an earlier specific error. A newly detected failure still wakes an unfinished connection attempt and cancels its owner once.
- `nailong/mcp/stdio_owner.py:70-82` joins the existing observer after the SDK context has unwound, before closing the channel. The manager's stack ordering supports this: session, stdio/process, then ownership. The normal helper owns the remote socket exclusively; process creation does not pass it to the service. Thus its direct-process exit supplies the remaining bytes/EOF without introducing a new task, protocol, deadline, or process.
- The fallback distinguishes an absent observation from an observed receipt. A cancelled or failed watcher cannot manufacture verified cleanup. A cancellation interrupting the owner's receipt join still propagates after the fallback and socket closure. Closing connections are not recancelled by reporting, preserving the established SDK-owner discipline.
- `tests/test_mcp_closing_receipt.py:19-84` uses the actual SDK, service and guardians. It delays observer scheduling, peeks at actual terminal bytes, and checks public status, withdrawn tools, rejection before admission, and completion of the connection owner. The healthy control prevents a blanket error-on-disconnect implementation from passing.
- The current client fallback retains this late unknown diagnostic if SDK teardown also raises. Independent probes confirmed that relationship and confirmed that a valid late `C` leaves an earlier specific diagnostic intact.

## Issues

### Critical (Must Fix)

None found.

### Important (Should Fix)

None found.

### Minor (Nice to Have)

**M1 — A diagnostic peek failure can strand the new regression fixture's scheduling gate.**

- File: `tests/test_mcp_closing_receipt.py:36-37`.
- The `stdio_finished.set()` call follows `peer.recv(..., MSG_PEEK)` without an enclosing `finally`. If that nonblocking inspection raises, the patched watcher remains inside `stdio_finished.wait()`. Production teardown then correctly waits for that watcher, while the test is still awaiting `manager.disconnect()`; the test's outer cleanup at lines 72-74 cannot run to release it.
- Effect: an exceptional diagnostic read can hang this test until a supervising timeout, obscuring the original failure. This is test-fixture robustness, not an observed production cleanup failure or evidence that normal native peeks are flaky.
- Evidence: `/tmp/ignovate-mcp-closing-receipt-review-fixture-probe.py` runs the actual added fixture and native SDK/helper, injecting one `BlockingIOError` only into the diagnostic `MSG_PEEK` call. After the exception, the gate, watcher, owner and test remained pending. Releasing that existing gate let cleanup finish and the test report an assertion failure; no live owner remained. Output is in the adjacent `.log` file.
- Suggested follow-up: put gate release in a `finally` around the diagnostic peek so an inspection error cannot block observer completion. This minor item can be deferred under the single-review contract; no C/I source fix pass is requested.

## Tests and evidence

Independent execution used CPython 3.11.15 with `-B`, finite owned native children, and local IPC only:

- `PYTHONPATH=tests .venv/bin/python -B -m unittest -v test_mcp_closing_receipt`: all 3 tests passed in 1.072 seconds.
- Five bounded native lifecycle cases passed: observer exception without receipt; late unknown plus a secondary exception after native SDK exit; cancellation of the owner during the final observer join; cancellation of the disconnect waiter during that join; and valid `C` preserving an existing specific diagnostic. All cases checked sealed admission, closed peer, completed observer and no live manager connection. The combined run ended with FD count 7 -> 7 and zero pending tasks. Probe/log: `/tmp/ignovate-mcp-closing-receipt-review-probe.py` and `/tmp/ignovate-mcp-closing-receipt-review-probe.log`. The process had a 30-second outer limit. The error/cancellation injections are explicit test seams; they do not claim those failures happened spontaneously.
- The separate bounded fixture probe reproduced M1 and then released its own gate to complete cleanup. It had a 20-second outer limit.

I inspected the author’s attested-old-wheel RED log (2 failures, normal control passing), current three-test 3.11/3.13 GREEN logs, 99-test relevant-suite summary with one skip, and global-cancellation probe outputs. Those are author evidence. I did not independently repeat the complete package, installed-wheel or cross-version matrices.

## Recommendations

Accept the bounded production fix after the parent's separately owned final package gates. Record an explicit disposition for M1; deferring this diagnostic-failure-path hardening does not undermine the reproduced late-receipt correction. Keep the original RED and current package provenance attached to acceptance. No production change is requested by this review.

## Declined to judge

- D1 — Earlier GUI changes and other dirty/untracked historical work: they are outside the frozen two-file delta and were not independently recertified here.
- D2 — Package-wide acceptance, all distribution contents, the complete Python 3.11/3.13 source and installed matrices, and CLI/JavaScript behavior: the parent owns those gates; this review did not duplicate them or infer their final result.
- D3 — Native Windows, Linux or other untested operating-system behavior, and future MCP/AnyIO releases: independent execution here was on the current macOS POSIX environment with the installed SDK versions; code inspection is not equivalent to those native runs.
- D4 — Containment of deliberately detached or privilege-changing descendants, or recovery after independently destroying the inner guardian: the inherited process-group boundary is unchanged. This delta improves truthful reporting of lost evidence; it does not establish stronger containment.
- D5 — A hostile same-user process stealing the private token or independently retaining/spoofing the authenticated peer: the handshake and local trust boundary are unchanged. I inspected normal descriptor ownership but did not perform an adversarial local IPC security audit.
- D6 — Kernel-resource-exhaustion scheduling during the grant-to-watcher handoff in unchanged `attach()`: the ordinary fresh-socket one-byte grant path has no intervening suspension after a successful immediate send, but I did not establish behavior under an abnormal blocked send followed by cancellation before watcher creation. This requires a separate startup/admission investigation, not an unsupported claim of a reproduced defect.
- D7 — Immediate active-connection revocation after independently cancelling or replacing only the private watcher while leaving its owner running: there is no public lifecycle operation that does that, and the producer's live-observer policy is unchanged. This review verifies conservative final-close handling of a missing observer; it does not certify a separate live-observer supervisor.
- D8 — Arbitrarily repeated raw cancellation of private SDK-owner tasks before native SDK teardown has finished, or an SDK/OS failure that leaves the direct process unreaped: the independent cancellation probe targets the new post-SDK join, and the author’s global-cancellation probe covers one concrete shutdown sequence. Neither proves every earlier SDK cleanup interruption or OS failure; no such failure was reproduced in this review.

## Assessment

**Ready to merge? Yes, for this bounded increment, subject to the parent's final package gates.**

**Reasoning:** The exact reviewed production delta closes the reproduced false-clean-disconnect window, preserves the SDK cleanup order and cancellation discipline, and passes independent native receipt/error/cancellation checks. There are no Critical or Important findings; one nonblocking test-fixture robustness issue is identified above. This verdict does not complete the broader project goal.
