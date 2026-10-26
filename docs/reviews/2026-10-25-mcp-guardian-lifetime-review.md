# MCP guardian lifetime review — 2026-10-25

## Scope and review boundary

This is the sole fresh bounded review of the newly demonstrated MCP health-channel lifetime gap. The reviewed checkout is `/Users/aycxd/.codex/worktrees/9eec/LLM_project`, branch `codex/long-task-post-merge-audit`, with HEAD/base `bd8fa20b208482a340c4254f73ada27c753307ca`.

The authoritative frozen delta is `/tmp/ignovate-mcp-guardian-lifetime-frozen.diff`, SHA256 `77a746410c91de2400c19a2360fe5977b010ec43b21caf43e75c736491019d36`. It contains exactly:

- `nailong/core/posix_process.py`: add optional `keepalive_fds` and preserve them in the existing guardian's `pass_fds`.
- `nailong/core/_mcp_stdio_owner.py`: supply the authenticated health socket to that guardian.
- `tests/test_mcp_guardian_lifetime.py`: add one native, gated launcher-death regression.

The existing uncommitted direct-child reaping change in `_posix_process_child.py` and `tests/test_process_crash_ownership.py` was read only as the accepted baseline needed to understand cleanup ordering. Its previous review, `docs/reviews/2026-10-25-posix-direct-child-reaping-review.md`, is not reopened or superseded. No other production changes are reviewed. No production/test edits, test reruns, additional reviewers, or subagents were used; this requested report is the only reviewer write.

## Strengths and requirement alignment

The fix ties socket EOF to the existing independent guardian's lifetime using one inherited descriptor. It adds no relay, extra process, daemon, protocol parsing, or production retry timer. Authentication and the existing process topology are retained.

| Boundary | Inspected behavior |
| --- | --- |
| Health socket ownership | `_mcp_stdio_owner.py:28`–`37` authenticates/adopts the existing channel before giving its descriptor to `PosixProcessGuard`. `posix_process.py:63`–`66` includes it in the bootstrap `pass_fds`. Outer launcher death therefore closes its lifeline writer without closing the last health-socket reference. |
| Inner guardian lifetime | The existing child owns the command session and runs its cleanup on lifeline EOF. The new socket reference remains open until this process actually exits, including the cleanup interval. The inner guardian neither reads nor writes the health protocol. |
| Service non-inheritance | `_posix_process_child.py:38`–`39` calls `subprocess.Popen` without `pass_fds` or a `close_fds` override. The installed CPython 3.11 implementation defaults `close_fds=True`; the health descriptor is closed in the service exec path. A service cannot retain this inherited socket to suppress EOF or produce `C`. |
| Descriptor ownership in the caller | `keepalive_fds` are borrowed handles. The guard does not close the outer launcher's socket or add it to its own temporary descriptor cleanup list. The outer socket remains under the existing top-level `finally` in `_mcp_stdio_owner.py:82`–`83`. |
| Fork/exec and admission faults | If launcher death precedes bootstrap creation, there is no admitted service to wait for. Once the bootstrap inherits the socket, its exit closes that reference. Existing lifeline admission checks and constructor exception cleanup remain in place. Missing bootstrap/user executable paths still fail startup; preserving a socket does not admit user code or create a clean receipt. No new fault branch is introduced between process creation and descriptor preservation. |
| Clean versus unknown receipt | Only the outer launcher executes `sendall(b'C')` at `_mcp_stdio_owner.py:62`–`63`, after `guard.close()` returns with verified inner cleanup. SIGKILL of that launcher still produces an empty receipt and cleanup-unknown diagnostic. The change delays that unknown result until the surviving guardian exits; it does not relabel it clean. |
| Stdio and actual exit status | Existing release of monitor stdio, direct service protocol streams, actual signed exit messages, signal forwarding, and outer signal-exit reproduction are untouched. A health descriptor cannot keep service stdin/stdout open. |
| Default and platform behavior | `keepalive_fds=()` preserves the ordinary POSIX guard's original descriptor set. The launcher without a health channel also supplies an empty tuple. The MCP client selects this launcher only on POSIX; no Windows or HTTP transport implementation changes. |
| Cancellation | Existing ownership context exit awaits the health watcher after SDK teardown. Holding the peer in the guardian prevents early EOF in that same await; no new cancellation, timeout, or polling policy is added. Existing cancellation still propagates through the surrounding ownership code. |

The regression uses the actual launcher source and actual guard, substituting only a bootstrap wrapper that gates native lifeline EOF. After killing and waiting for the real outer launcher, it observes the gate and verifies the service still exists. The nonblocking native `MSG_PEEK` assertion at `tests/test_mcp_guardian_lifetime.py:70` proves that EOF is absent at that boundary; it is not an eventual-process-exit polling workaround. Releasing the gate then checks service absence, empty receipt, and cleanup-unknown reporting. Its five-second gate limit is a fixture escape bound, not production behavior.

## Findings

### Critical

None identified in the frozen delta.

### Important

None identified in the frozen delta.

### Minor

None identified. No speculative refactor or extra test request is raised.

## Evidence and limits

The review request supplies the new test's old-code RED result (`BlockingIOError not raised`) and a two-test GREEN result (0.627 seconds) covering the new native regression and unchanged SDK internal cancellation test. The reviewer inspected the regression mechanism and unchanged strict final `process_exists` assertion at `tests/test_mcp_cancellation.py:556`; these small RED/GREEN runs were not replayed, and no separate raw logs for them were supplied to this review.

The reviewer directly inspected the completed focused log `/tmp/ignovate-mcp-lifetime-focused311.log`, SHA256 `0d4ff9de7b1397efeb8c9ab21b769dec6721affe45cd76daeec8ec091743b088`: **167 tests in 78.388 seconds, OK, four skips**. Its verbose records explicitly pass the new lifetime test and unchanged SDK cancellation test. They also pass service health-socket non-inheritance, stdin/stdout close propagation, positive/signal exits, bootstrap/executable failure, descriptor release, pre-handshake launcher death, cross-connection isolation, clean/unknown receipts, and cancelled disconnect/close cases. The log includes SDK diagnostic output about killing its direct process and a startup-teardown termination fallback; the corresponding tests finish `ok`.

Additional completed author-run logs were present and their final summaries inspected without rerunning them:

| Log | Reported result | SHA256 |
| --- | --- | --- |
| `/tmp/ignovate-mcp-lifetime-full311.log` | 2233 tests, 388.183 seconds, OK, 54 skips | `87396ca36a90a4ff7cda4f161d7ad5e5057a96acbd61b77f26a8fae373555cff` |
| `/tmp/ignovate-mcp-lifetime-full313.log` | 2233 tests, 390.408 seconds, OK, 55 skips | `f62823426ef94e72322a86ed86d220be1d6d1578249a7f638720857186ecd862` |
| `/tmp/ignovate-mcp-lifetime-installed311.log` | 167 tests, 80.249 seconds, OK, four skips | `6316f09dab0c7961a870d0a447c5a6934fd11cdfd20c976eafbbdb56d5853e6d` |
| `/tmp/ignovate-mcp-lifetime-installed313.log` | 167 tests, 80.466 seconds, OK, four skips | `ad84afe5b8b9ab696dc8f2476323ae95ecb27b54259d268557b303bcb51c97e6` |

Both installed logs report 198 wheel Python/static files and ten installed origins attested, with wheel SHA256 `ee0250e546cb9a9bbf5fa4288d438c3d2fd1e842c27ade154d42b325a8c7f4c9`. These are inspected author-run evidence, not an independent reviewer execution or reconstruction of the wheel attestation.

The deterministic test establishes this distinct socket-lifetime gap. It does not prove the precise state of every historical intermittent PID-existence failure. The author reports that the current diagnostic observed `process_exists=True` but the process had vanished by a subsequent `ps` sample, with cleanup correctly reported unknown. That sampling history cannot retrospectively distinguish all earlier states. The new source-level causal argument and gated reproduction stand independently of that uncertainty.

Native execution evidence is macOS; Linux and Windows execution, hostile scheduling, a real kernel wait timeout, and arbitrary guardian destruction were not independently exercised by this review. EOF establishes that the remaining socket holder exited, not an independent universal proof about every descendant. Clean verification continues to depend on the pre-existing cleanup/receipt contract; unexpected guardian loss continues to report unknown. The fixture gates cleanup before its existing finalizer, so it specifically establishes the ordering boundary rather than simulating every kernel cleanup failure.

## Considered and declined alternatives and costs

- **Add an extra supervisor or health relay:** adds another lifecycle and failure surface when the existing independent guardian can hold the descriptor directly.
- **Have the guardian emit `C`:** would change the receipt authority and potentially turn launcher failure into a clean-looking shutdown. Retaining outer-only acknowledgement preserves unknown-loss semantics.
- **Keep the socket in the service:** would permit a long-running service or descendant to delay health EOF and expose the private channel to untrusted service code. Existing service `close_fds` avoids that cost.
- **Add a sleep, retry `process_exists`, or relax the final PID assertion:** masks the observable boundary and adds latency or weaker evidence. Native socket ownership supplies the required ordering directly.
- **Add a new guardian timeout/cancellation policy:** would introduce a new early-return boundary without proving cleanup. This delta follows the existing guardian lifetime and ownership-context semantics.
- **Generalize validation/ownership of arbitrary `keepalive_fds`:** the only new caller supplies its live authenticated socket; generic descriptor API design and handling arbitrary caller-owned invalid handles would widen this private fix. Existing Popen failure handling remains applicable.
- **Run duplicate suites or commission another review:** the completed affected suite and author-run broader evidence are available. Repetition would add review cost without a new demonstrated concern; this is the requested sole bounded review.

## Declined to judge

- The already reviewed direct-child reaping delta: accepted baseline context; reopening it would duplicate its sole fresh review.
- Forensic attribution of every historical intermittent SDK cancellation/PID observation: old evidence lacks sufficient process-state information. This report judges the newly demonstrated lifetime ordering defect.
- Containment of descendants that deliberately leave the process group, universal descendant reaping, or guaranteed cleanup after external destruction of the inner guardian: these exceed the existing ownership model and require a separate architecture decision. Unknown-loss reporting is preserved here.
- A strict shutdown wall-clock bound under a stopped guardian, uninterruptible kernel state, or arbitrary scheduler delays: socket lifetime intentionally follows the existing guardian; this change adds no time guarantee.
- Whole-project release, merge, or deployment authorization: the frozen delta is the review boundary; broader test summaries are evidence for the author's integration decision.

## Assessment

**Scoped verdict: acceptable; ready for the author's one disposition, with no actionable findings.** The inherited descriptor closes the demonstrated early-EOF gap while preserving service non-inheritance, direct stdio, signed exits, default paths, and the distinction between verified cleanup and unknown launcher loss.

The supplied affected run is green, and the inspected broader/installed summaries are green. This is one review of this new delta, requests no second review, and does not claim forensic certainty about all historical failures or whole-project release readiness.
