# Sole fresh review: Windows actual cwd short-alias delta

Reviewed the frozen five-path diff `/tmp/ignovate-pr5-windows-shortdir-review.diff`, SHA-256 `da243a2302959b32b70c85753866c1b355b087dbfae1895a47d2f5f150fc8077`, against base `5070901ef3e396963858eae5d10553dcc678c6b9`. Read all of `docs/reviews/2026-10-25-windows-launch-cwd.md` (R1–R5), all changed code/tests and surrounding path validation, native pinning, process launch and verification outcome handling. This is one read-only review; no checkout/index/HEAD/dependency changes, network, subagents, or duplicate full matrix.

## Strengths

- `short_directory` checks the native size/result boundary, removes only the expected extended local prefix, counts the resulting cwd in UTF-16 units, and compares volume/file-index identity while both original and alias ancestor chains are pinned. It refuses an alias that remains too long rather than treating API success as proof of launchability.
- `OwnedProcess` retains safe absolute-path validation and the UTF-16 threshold, including relative expansion and astral Unicode cases, before selecting the native alias.
- The replacement regression now checks the meaningful native outcome: the command fails and original input/path remain intact with no downstream generated outputs. Existing real native long ASCII/emoji process tests remain enabled on Windows.
- The portable helper test exercises the actual extracted implementation for success, mismatched native identity and an insufficiently short returned path. Its limits are explicitly documented.

## Issues

### Critical (must fix)

None found.

### Important (should fix)

**I1. The new long-root non-repository shortcut can erase a real Git identity.**

- Location: `nailong/core/verification.py:74` (new early return); supporting predicate at lines 46 and 58.
- Trigger: Windows project root at least 260 UTF-16 units, Git available, valid bare repository whose object store is supplied through `GIT_OBJECT_DIRECTORY` rather than a local `objects` directory.
- Problem: `_non_repository` checks four Git environment variables but omits `GIT_OBJECT_DIRECTORY`; its bare-repository heuristic requires local `HEAD`, `objects`, and `refs`. The new call therefore returns `b''` before running installed Git for this valid repository. Git HEAD changes can disappear from the intended Git identity contribution to verification evidence. This is newly reachable with installed Git because of the added long-root shortcut.
- Evidence: in a temporary directory outside the checkout, initialized a bare repository, moved its object store outside the root, supplied `GIT_OBJECT_DIRECTORY`, and created a valid commit/HEAD through real Git. `git rev-parse HEAD` returned `9b257cad2fe45e2507955ab6aa79d5b5ef75eb0a`, while the unchanged predicate returned `_non_repository(root) == True`; the local `objects` directory did not exist. This is a portable predicate reproduction; it is not a claim that Windows execution was performed.
- Fix: conservatively return False whenever `GIT_OBJECT_DIRECTORY` is present (including empty), as for the other identity-affecting variables, and add a portable regression for this valid alternate-object bare repository or equivalently prevent this new shortcut from trusting that predicate in this case. The real Git path can then run from the verified alias or fail explicitly when none exists.
- Proposed ruling/cost: Accept and fix during the one author correction pass. Cost is a small predicate/test change and the required checks on the corrected candidate; no new review seat is needed.

### Minor (nice to have)

None found.

## R1–R5 disposition

- **R1:** Implemented as specified in the reviewed delta. Accept existing-alias-only support, native identity comparison, and explicit failure for no sufficiently short alias. Cost: volumes without useful aliases remain unsupported for over-MAX_PATH process cwd; this is not certification of such volumes.
- **R2:** The alias-based real-Git route and short-root safe anchor are implemented. **Needs I1 correction** before the non-repository shortcut meets its claimed proof requirement. Cost: conservative repository detection may require a usable alias or explicit refusal in more environment configurations.
- **R3:** Accept the changed native assertion. Surrounding service code assigns `failed` only after a started step fails, whereas omitted/unapproved execution becomes `unverified`; input/output assertions preserve the security expectation. Cost: this specifically models native pins blocking the first rename, not an input snapshot being changed and restored.
- **R4:** Accept preservation of real native ASCII/emoji launch cases and the added portable helper assertions. The final focused log independently read here reports 59 tests, 3 skipped, 26.407 seconds, OK. Cost: the skipped native cases and actual ctypes ABI/process behavior still require genuine Windows jobs.
- **R5:** This report is the one fresh bounded review of the new helper delta, not a repeat of the archived seven-path review. Accept author disposition/correction followed by all required native/full/install/CI gates before merge. Cost: no merge readiness claim until the corrected candidate's actual Windows results are green.

## Declined to judge

- **Actual Windows execution and ctypes ABI success for this candidate:** unavailable on this review host; neither the AST test nor the macOS focused run proves it. Proposed ruling: keep the real Windows gates mandatory and inspect their results on the final corrected candidate. Cost: wait for those jobs and address any real failure before merge.
- **Universal operation on Windows volumes without 8.3 aliases:** explicitly outside the accepted R1 guarantee, because the helper must refuse when the returned alias remains too long. Proposed ruling: accept explicit refusal, retain documentation of the limitation, and do not skip native failures to imply universal support. Cost: affected users cannot launch verification from that long cwd without changing to a supported location.
- **General process-cwd rename races outside the verification DirectoryGuard lifetime, unrelated historical issues, and the prior intermittent MCP exit assertion:** outside this frozen five-path delta; the helper's temporary selection pins do not redefine the lifetime of existing outer guards. Proposed ruling: preserve the prior recorded status and assess any broader lifetime hardening as separately scoped work. Cost: no new general sandbox or race-freedom guarantee from this patch.

## Assessment

**Ready to merge? With fixes, and only after actual Windows gates pass.**

The helper/process change is bounded and aligns with the revised platform model, but I1 breaks the stated requirement to return empty Git identity only for a proven non-repository. Correct it once, record the author disposition, then use real Windows/native/full/install/CI results for the final candidate as the merge proof. No final-candidate Windows success is claimed by this review.
