# POSIX direct-child reaping review — 2026-10-25

## Review identity and scope

This is one fresh, bounded review of the frozen uncommitted change on `codex/long-task-post-merge-audit` in `/Users/aycxd/.codex/worktrees/9eec/LLM_project`.

- Base / checked HEAD: `bd8fa20b208482a340c4254f73ada27c753307ca`.
- Frozen diff: `/tmp/ignovate-posix-reap-frozen.diff`.
- Frozen diff SHA256: `a099bb70bb1e4f02c9305874a7f59e23fcd2f805a056f2ceb13f83477fc17742`.
- Reviewed modifications: `nailong/core/_posix_process_child.py` and `tests/test_process_crash_ownership.py` only.
- The live `git diff --binary` matched the frozen hash during review.

Read-only context included `nailong/core/posix_process.py`, `process_io.py`, `_mcp_stdio_owner.py`, `nailong/mcp/stdio_owner.py`, the relevant lifecycle portion of `nailong/mcp/client.py`, and related native/MCP tests. Local CPython 3.11.15 `subprocess.py` was inspected for POSIX `kill`, `poll`, and timed `wait` semantics. Production code, tests, index, HEAD, and branch state were not changed. This requested report is the sole authored artifact. No tests were run by the reviewer, no agents were dispatched, and no second review was requested.

## Strengths and requirement alignment

The change addresses the acknowledgement boundary directly and retains the existing subprocess architecture.

| Requirement | Evidence in the frozen change and context |
| --- | --- |
| Reap the direct user subprocess before clean acknowledgement | `_posix_process_child.py:72`–`78` calls `child.kill()` and `child.wait(timeout=4)` before writing `stopping`. A child already reaped by the normal `poll()` path has a cached return code; these calls do not signal a historical PID. |
| Failed kill or wait must not claim verified cleanup | Neither operation is swallowed. An exception skips `stopping`; the controller's existing `PosixProcessGuard.result()` rejects the missing acknowledgement for an admitted command. |
| Always attempt final own-group SIGKILL with a live leader | `_posix_process_child.py:80`–`83` nests the existing group kill in an unconditional `finally`. It executes on normal return, startup failure, kill failure, and wait failure. The isolated owner remains the live session/group leader when it obtains and signals its own group ID. |
| Preserve startup failures | `child=None` at line 21 makes pre-creation exits safe. Existing executable-start errors still use the same `error` message and filename/errno handling. |
| Preserve actual signed natural exit status | The existing successful `child.poll()` path still sends its actual `returncode` before cleanup; final cleanup does not replace that message with the owner's SIGKILL status. |
| Preserve stdio behavior | The descriptor inheritance, release policy, and transport code are unchanged. The owner still releases its MCP stdio copies before its loop; waiting for the direct subprocess adds no protocol intermediary. |
| Preserve SIGSTOP and hard-exit cleanup | Public STOP still targets only the direct child; the owner remains runnable to observe lifeline EOF. SIGKILL can terminate a stopped child before the bounded wait. Controller EOF and explicit close still converge on the same finalizer. |

The first added regression (`tests/test_process_crash_ownership.py:193`) observes native `os.waitpid(pid, os.WNOHANG)` exactly when the helper writes `stopping`. It distinguishes a running child, an exited but unreaped child, and an already reaped child. It retains native process creation and group termination rather than mocking their result.

The second added regression (`tests/test_process_crash_ownership.py:231`) injects only the direct-child wait failure. It checks that close reports cleanup as unverifiable, no acknowledgement is written, and both child and grandchild late writes are prevented by the remaining group cleanup. The injected wait fails immediately; it does not exercise the full four-second timeout duration.

## Findings

### Critical

None identified in the frozen diff.

### Important

None identified in the frozen diff. The existing MCP cancellation failure below is a validation limitation with an unknown cause, not a demonstrated defect introduced by these changed lines.

### Minor

None identified. No generic style or speculative test requests are raised.

## Test evidence and limitations

The author supplied the following RED/GREEN observations in the review request: the acknowledgement-boundary regression reported `running` against the old implementation and `already-reaped` against the fix; the failed-wait regression previously failed because `RuntimeError` was not raised, then passed with cleanup-unknown reporting and remaining group cleanup. The reviewer inspected the test mechanisms but did not independently replay these RED/GREEN runs or inspect separate raw RED/GREEN logs.

The reviewer directly inspected both supplied focused-run logs:

- `/tmp/ignovate-reap-focused311.log`, SHA256 `8ef989a2aa167afd3e1cd1169355eb2ff14f17a0cc385869e64c7b37f707bc53`: 58 tests in 27.757 seconds, five errors and two skips. `platform_fixtures` import failures made this an invalid complete validation run. The author reports the invocation omitted `PYTHONPATH=tests`.
- `/tmp/ignovate-reap-focused311-fixed-invocation.log`, SHA256 `bf58b621725a18058230999ce3247394a1dd0f33125ae226eea9a0a427e0558e`: 94 tests in 60.660 seconds, one failure and four skips. The sole reported failure is the existing `test_sdk_internal_cancellation_seals_admission_before_native_teardown`, at `tests/test_mcp_cancellation.py:556`, where the final `process_exists` assertion returns true. No new reaping regression failure is reported. This non-verbose aggregate log does not provide a per-test success record.

The corrected invocation fixes the import harness only; it is not an all-green run. The historical intermittent SDK cancellation failure still has an UNKNOWN root cause. Its occurrence with this fix does not establish that the fix caused it, and the bounded reaping change does not establish its resolution. The assertion alone does not distinguish a running process, a zombie, or another explanation for the observed PID check. This review makes no such attribution.

The review did not reproduce Linux behavior, Windows behavior, exotic scheduler delays, a real kernel wait timeout, or an actual permission-denied kill. The supplied run is native macOS CPython 3.11 evidence. The source-level nested `finally` covers kill/wait exceptions, while the new executable fault test specifically covers wait failure. Ordinary group-contained descendants remain the ownership model; comprehensive descendant reaping or containment of a descendant that deliberately leaves the group is not established by this change.

## Considered and declined alternatives and costs

- **Kill the entire group before waiting:** this would kill the owner itself before it can reap or acknowledge. A separate surviving supervisor would be required, increasing architecture and lifecycle complexity beyond the requested fix.
- **Wait without a timeout:** an uninterruptible direct child could keep the cleanup owner alive indefinitely. The four-second child wait preserves a bounded attempt within the controller's existing five-second owner wait; general OS scheduling still prevents a strict wall-clock guarantee.
- **Swallow kill/wait failures and send `stopping`:** this would turn absent reaping evidence into a clean receipt and violate the requested failure semantics.
- **Signal an old group ID from the controller after reaping its leader:** this would lose the current live-leader protection against PID/PGID reuse. Keeping the final signal inside the owner avoids that change.
- **Add a subreaper, double fork, or a separate descendant-reaping service:** these change the subprocess architecture and portability requirements. They are unnecessary for the scoped direct-child acknowledgement invariant.
- **Replace the native boundary test with polling `process_exists`:** a PID existence check does not prove that this owner has reaped its child. The native `waitpid` observation is stronger for this requirement.
- **Add another review or duplicate the author's suites:** this would repeat the authorized bounded review and consume additional time without resolving the distinct historical SDK failure. No repeat review or reviewer test run was performed.

## Declined to judge

- The root cause and repair of the historical SDK cancellation `process_exists` failure: outside this frozen two-file change and unsupported by current causal evidence. Its continued failure is explicitly retained above for the author's decision.
- Reaping grandchildren, group-escaping descendants, and behavior under external SIGKILL of the cleanup owner: the requirement is direct-child reaping while the owner survives to perform its own final group kill. A broader guarantee requires a separate architecture decision.
- Release or merge readiness of the whole project: the available focused run contains a failure, and this review intentionally does not audit unrelated work or substitute for the author's integration ruling.

## Assessment

**Scoped code verdict: acceptable; no actionable findings in the frozen diff.** The direct child is reaped before acknowledgement, cleanup failures omit that acknowledgement, and the final own-group SIGKILL remains unconditional while its leader is alive.

**Whole-project merge readiness: not established by this review.** The author must rule once on the scoped change and the unresolved focused-run failure. This report requests no further review pass and makes no claim that the historical MCP issue is fixed.
