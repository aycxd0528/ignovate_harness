# PR5 Windows CI repair — sole fresh final read-only review

Reviewed worktree `/Users/aycxd/.codex/worktrees/9eec/LLM_project`, branch `codex/long-task-reliability`, base `91315a0961f21d7d168b5fdfb5ea81c1db494974`. Scope is the exact seven-path `/tmp/ignovate-pr5-windows-review.diff`, SHA-256 `795f826b68c32c5794e97242a9078a293a28c2505264bbde1a54150c08559c83`. All seven current file SHA-256 values matched `/tmp/ignovate-pr5-windows-review-files.json` during this review. Read the full Windows repair plan and reviewer instructions. No checkout, index, HEAD, dependency or environment mutation; no subagents; no full matrix, real network or Windows execution. Bounded probes used owned temporary directories and Python `-B`.

## Strengths

- Manifest parsing accepts the two canonical Windows spellings while rejecting anchored, drive-relative, root-relative, dot-normalized and parent traversal spellings. SQLite membership still operates on native canonical `Path` values; this does not reinterpret POSIX backslash filenames.
- Windows mutable directory admission reuses actual strong pins, with each existing directory and every ancestor held without write/delete sharing. `LIST_DIRECTORY` makes the pin participate in sharing rules; metadata-only handles would not. Rename/delete and the write handle needed for an in-place reparse change cannot coexist with those pins. Missing paths are not added to `protected`, and exception cleanup unwinds earlier pins. HANDLE checks correctly use Win32 information rather than `fstat`.
- Missing Git is narrowly optional for demonstrable non-repositories, and Git present still supplies real HEAD/index state. Windows Git gets an explicit project `-C` from a short drive cwd. The new tests exercise acceptance as well as refusals and own their native process cleanup.
- The workflow increase retains the real per-test 120-second watchdog; it does not remove tests or weaken product timeouts.

## Issues

### Critical (Must Fix)

None identified in this scoped patch.

### Important (Should Fix)

**I1 — Long Unicode cwd detection counts Python characters instead of Win32 UTF-16 units.**

- Location: `nailong/core/process_io.py:77`; associated coverage `tests/test_windows_verification_compatibility.py:62`.
- The new extended-prefix branch runs only when `len(os.fspath(cwd)) >= 260`. Windows wide paths count UTF-16 code units. A valid absolute path with three components of 50 emoji each has 163 Python characters and 313 UTF-16 units, with every component only 100 UTF-16 units. It bypasses the new branch and passes the ordinary over-limit cwd to `Popen`, retaining the same native startup failure this patch repairs. This affects a user opening an ordinary project under non-BMP Unicode directory names. The ASCII native test cannot detect it.
- Portable probe with the exact current `OwnedProcess`, a stub job and captured `Popen`: `python code points 163`, `utf16 units 313`, `Popen cwd passed through unprefixed True`, maximum component `100`. This proves incorrect dispatch; no native Windows execution is claimed.
- Fix: apply the threshold to the validated absolute Windows path's UTF-16 units, or consistently pass the validated native extended path where compatible. Add a portable branch regression and a real Windows valid-component path with Python length below 260 and UTF-16 length above the limit; ensure it executes a child and writes the expected output. Account for relative cwd becoming longer after absolute resolution if retaining the generic cwd interface.

### Minor (Nice to Have)

**M1 — Explicit empty Git environment values are silently classified as absent.**

- Location: `nailong/core/verification.py:46`; coverage `tests/test_windows_verification_compatibility.py:24`.
- `any(os.environ.get(key) ...)` overlooks a present key whose value is `''`. With each of `GIT_DIR`, `GIT_COMMON_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` separately set empty, an empty owned project and Git unavailable, exact helper probes returned `_non_repository=True`, `_git(...)=b''`. Installed Git with `GIT_DIR=''` instead reports `fatal: not a git repository: ''`.
- This diverges from R3's promise that explicit identity makes state unknown. I found no false existing repository authorization from this alone: actual `.git`/bare markers still refuse fallback, and the established Git-present code also treats a not-a-repository diagnostic as empty identity. Therefore this is a guard-contract/test gap, not a demonstrated security bypass or merge blocker by itself.
- Suggested small fix: use `key in os.environ` for all four keys and test both empty and nonempty values. Cost: users with deliberately exported empty Git keys must unset them in a Git-less non-repository environment to verify.

## Plan coverage — initial R1–R7

- **R1:** Considered and accepted: canonical native/POSIX manifest forms and anchor rejection maintain authority boundaries. Cost remains rejection of aliases and no migration/reinterpretation of POSIX names.
- **R2:** Considered and accepted for directory identity/namespace protection, subject to actual Windows gates. Strong sharing pins justify masking directory size/time versions for output creation; files and unmasked directory modes/identity remain checked. The guard does not claim a general Windows ACL mutation audit. Cost: legitimate ancestor moves/reparse changes cannot occur while verification owns the pins, and cancellation must release them before returning.
- **R3:** Considered; narrow no-Git fallback is appropriate with M1 noted. Ancestor marker checks fail closed on access errors, and real Git state remains consulted when installed. Cost: ancestry traversal, and an external writer's changes between discrete checks are not universally prevented.
- **R4:** Considered; Git short cwd/explicit `-C` is reasonable. OwnedProcess has I1 and needs correction. Actual native Git, PowerShell and child cwd behavior remain Windows gates; mac probes cannot certify them.
- **R5:** Considered; original RED log shows real product refusals/anchor acceptance failures (3 failures, 2 errors, 2 native skips), current portable log has 6 methods, 3 native skips. Related log records 114 tests/29 skips in 31.615 seconds. No native passing evidence inferred. Add I1 boundary coverage and optionally M1.
- **R6:** Considered and accepted: runtime job 20 to 35 minutes is justified by the old suite cancellation. The individual watchdog remains 120 seconds in `scripts/run_native_tests.py`. Cost is longer total failed workflow wall time; completed actual runs still required.
- **R7:** This is the sole fresh scoped review. One author C/I disposition pass follows, with source/wheel identity and actual CI/merge gates separately pending. No second review was dispatched. Cost: later corrections need a new final identity record, and this report does not certify unreviewed base changes.

## Declined to judge — explicit proposed rulings and costs

1. **All earlier PR5/base implementation outside these seven paths:** proposed ruling: retain existing reviews and final source/installed regression gates, without expanding this review into a repeated whole-project audit. Reason: assigned frozen delta is the review authority. Cost: no new full-history certification; independently demonstrated adjacent bugs must still be addressed when found.
2. **Actual Windows success, including PowerShell acceptance of extended cwd and Git long-path behavior:** proposed ruling: require the actual Windows installer/runtime 3.11/runtime 3.13 and desktop jobs, plus Linux jobs, on the final commit before merge. Reason: this reviewer runs on macOS. Cost: platform behavior is unproved until CI completes; the old failures/cancellation are not cleared by portable GREEN.
3. **Windows transient ACL/security descriptor or metadata-only attribute changes that restore their final value:** proposed ruling: keep R2's authority claim limited to pinned namespace/directory identity and existing mode/content checks; do not describe no-write sharing as a universal ACL/attribute freeze. Reason: sharing prevents replacement/reparse writer handles, while metadata/security operations have distinct rights; no concrete source-content substitution through such an operation was demonstrated here. Existing POSIX chmod-and-restore test explicitly skips Windows. Cost: this review provides no native ACL-history guarantee; if that guarantee is intended, add a separate Windows contract and observer rather than assuming sharing covers it.
4. **Old runtime311 interpreter-exit `branches.sqlite` sharing violations at clean-log lines 4634–4695:** proposed ruling: inspect final Windows run logs as well as job status; if these warnings recur, diagnose ownership/cleanup rather than declaring all failures repaired. Reason: actual old evidence exists, but this seven-path patch does not touch SQLite lifetimes and the errors occur after the failed test summary; I cannot establish their cause or persistence locally. Cost: potential pre-existing cleanup defects are not certified fixed by this review or by an otherwise green status.
5. **Adversarial simultaneous external repository creation between the two Git-state observations:** proposed ruling: retain existing before/after fail-closed snapshot observation without expanding to a global external-writer lock. Reason: the fallback does not authorize new exclusions, and there is no current exact reproducer that defeats both Git state and complete input inventory checks. Cost: this is discrete observation, not a claim to prevent every external write at every instant.

## Recommendations and assessment

Author should resolve I1, explicitly rule on M1 and each declined behavior, then record the updated final diff/commit identity and rerun the relevant bounded and final gates. Do not merge on this review alone or infer that every old Windows failure is cured.

**Ready to merge: With fixes, and only after final gates.** One Important Unicode cwd boundary defect remains. No Critical issue was found; Windows native passing evidence and full final source/installed/CI checks are separate pending requirements.
