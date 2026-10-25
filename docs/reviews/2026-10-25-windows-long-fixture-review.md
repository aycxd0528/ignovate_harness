# Bounded review: Windows long scenario fixtures

## Scope and evidence

Reviewed once, read-only, at checkout `/Users/aycxd/.codex/worktrees/9eec/LLM_project`, HEAD/base `93cb259966d8a61e39313bd2d275d5078b25ab7a`. Frozen four-path diff `/tmp/ignovate-pr5-long-fixtures-review.diff` SHA256 verified as `1b694436a429b827674db849eddd3d79bbaf5652f861144df239d44816b9f2de`. Requirements: `docs/reviews/2026-10-25-windows-long-scenario-fixtures.md`, R1–R4. No checkout, index, HEAD, dependency, or environment mutation; no network, subagents, or test execution. This report is the only file written by the reviewer, outside the checkout.

Inspected both changed scenario helpers, new construction regression, the native workflow, existing `platform_fixtures.shell_join`, the narrow current shell transport interface, both existing long E2E modules and their meaningful assertions, native runner discovery/watchdogs, supplied RED/GREEN logs, and both supplied Windows push logs. No repeated review of historical producer changes.

## Strengths

- Both command sites now call the existing native helper with unchanged interpreter, `-B`, `-c`, and source body (`tests/long_task_scenario.py:25`, `tests/long_goal_driver_scenario.py:29`). The helper retains `shlex.join` on POSIX; on Windows it uses PowerShell invocation and UTF-8 hex source transport compatible with the existing PowerShell execution interface. Existing native deep Unicode shell coverage already exercises this helper path.
- The new regression captures real settings construction in temporary directories, verifies both helper calls and their Python argv/source markers, and stops before model/runtime initialization (`tests/test_long_scenario_commands.py:12`). The RED log contains two assertion failures at the captured command check, one for each scenario, rather than setup errors. Supplied GREEN evidence is 13 tests in 10.944 seconds, OK; reviewer did not duplicate it.
- The full E2Es still exercise repair and require successful verification, preserved original requirements, compaction/restart continuity, verified delivery, and invalidation after external artifact changes. The autonomous scenario still requires removal of partial output and exact report bytes. The richer `second` assertion changes diagnostics only; `status == 'passed'` remains mandatory (`tests/long_task_scenario.py:105`).
- CI adds the new regression and both complete long modules to the existing native preflight; original preflight modules, full discovery, 3.11/3.13 matrix, 35-minute job budget, and 120-second per-test watchdog remain (`.github/workflows/windows-native.yml:67`). No production path is in the reviewed diff.

## Issues

- Critical: none.
- Important: none.
- Minor: none.

## Initial requirement rulings and costs

| Requirement | Review ruling | Cost / limitation retained |
| --- | --- | --- |
| R1 | Satisfied by the two existing-helper substitutions; source program and real execution semantics are retained. | Windows command text changes to the established native transport. POSIX uses the same join semantics. Actual Windows execution of both complete scenarios remains required; local construction evidence cannot prove that execution. |
| R2 | Satisfied: all three named modules are added to the original preflight, with full native runtime discovery retained. No skip or assertion relaxation appears. | Both long scenarios and the projection tests in the continuation module run in preflight and again in the full suite. This adds finite runtime; actual Windows elapsed time must fit the existing job budget. No budget increase is requested or inferred. |
| R3 | Satisfied within its stated construction-only scope: both settings commands use the patched builder, with preserved interpreter/options and source markers. RED/GREEN evidence supports the regression. The richer failure assertion is safe. | The mocked builder and deliberate early exception do not test quoting/execution, complete source equivalence, models, restart, compaction, or delivery. Static diff inspection confirms unchanged source bodies; unchanged E2Es remain responsible for execution behavior. |
| R4 | Review portion satisfied by this sole fresh bounded review; no code fixes are requested. Author disposition and final validation remain the next steps. | No historical production re-review, extra reviewer, or suite duplication was performed. Actual Windows final source/installation checks and all required CI gates for the final current HEAD are still pending; merge must wait for those results and log cleanup checks. |

## Considered and declined to judge

- Final Windows success: declined to infer from local GREEN evidence. Cost: wait for the already required native 3.11/3.13 and other current-HEAD gates; do not merge on this review alone.
- Complete source equality in the new mocked regression: considered; the regression checks native-helper wiring and source markers, while the frozen diff proves the body is unchanged and full E2Es execute it. Cost: the construction test alone cannot catch every future source edit. No additional issue or duplicated execution is warranted for this increment.
- `patch.object(..., create=True)` can supply an otherwise absent helper attribute: considered; it makes the pre-fix behavior fail at the intended command assertion. Unchanged E2Es in the same preflight independently fail if an import/call cannot execute. Cost: construction coverage alone is not proof of a valid real helper import; no code change requested.
- Additional preflight execution time: considered within R2; full long modules are intentionally included, not selected single methods. Cost: repeated module execution and a requirement to observe actual Windows budget fit. Existing logged full-suite duration leaves headroom, but no final runtime claim is made.
- Historical producer code, old intermittent MCP assertions, and existing long Git directory limits: declined to reopen because this frozen increment only changes test/CI fixture wiring. Cost: their previously recorded dispositions/boundaries remain; this review supplies no new production certification.
- Wheel hash and exhaustive production/GUI byte identity: outside this four-path diff review. Cost: retain author/source/package verification gates; the reviewed paths themselves contain no production edits.
- PR-event results and any unprovided final runs: declined to infer from push logs. Cost: retrieve/check the required final runs separately.

## Failure evidence and assessment

Both supplied Windows push logs show preflight `Ran 64 tests` / `OK (skipped=3)` (3.11: 143.182s; 3.13: 152.275s), then exactly two full-suite failures out of 2228 tests with 99 skips (1094.761s / 1097.072s). The autonomous scenario is denied completion because successful verification is absent; the continuation scenario observes repaired verification still `failed`. These match a broken fixture command and retained completion enforcement. They support this targeted correction but do not establish its Windows success. Search of both supplied logs found no `branches.sqlite`, `WinError32`/`WinError 32`, or `Exception ignored` match.

**Code assessment:** approved for final validation; no C/I/M findings or requested changes in the bounded increment.

**Ready to merge?** Not yet: actual Windows final gates and final-current-HEAD CI remain pending under R4. Complete the existing gates and author disposition; this review does not request another review cycle.
