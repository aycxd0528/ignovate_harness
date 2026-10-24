# Bounded Windows shell location review

## Result and scope

No Critical, Important, or Minor defect found in the frozen two-path increment. Code review passes conditionally; this is **not merge readiness**. Actual Windows proof and the author's final source, installed-package, and CI gates are still required. At review completion, only the related macOS suite result was available to this reviewer.

Reviewed base `e91ec4d4df70caca0d0d63afbe1639ee17f4719a` against `/tmp/ignovate-pr5-shell-location-review.diff`, whose SHA256 was independently read as `b200fe5d70ab6fbbb4184c67cb0b8e6912e7f9b6473aa0411f2fe0c55417cea8`. A read-only `git diff` for the two paths matched the frozen text. The author reports the unchanged source was pushed as `b4542931bba9ac736b2b3f8c025c909e911db5dd` to obtain native proof. Paths: `nailong/core/process_io.py` and `tests/test_windows_verification_compatibility.py`.

Relevant context inspected: shell/process callers in `processes.py`, `process_io.py`, `plan.py`; existing native process regressions and platform command fixtures; the existing two-run deep Unicode ownership test; and the WindowsJob assignment/cleanup boundary. The earlier short-directory helper review was not repeated. No repository, index, HEAD, dependency, or environment mutation; no network, spawned agents, or test execution. The sole output write is this report.

## Findings

- Critical: none.
- Important: none.
- Minor: none.

The original failure is real and remains preserved in `/tmp/ignovate-pr5-e91-native311.log`: lines 613–624 identify the deep Unicode ownership test failing with a started PowerShell process, exit code 1, and `Program 'python.exe' failed to run: The directory name is invalid`. Lines 627–629 show 60 tests in 219.135 seconds, one failure and three skips. This supports the bounded diagnosis of the shell's native child location, rather than treating a successfully created shell as proof its Python child can run.

## Requirement dispositions and costs

### R1 — Accepted in code, native confirmation pending

`process_io.py:78–88` completes absolute-path/UTF-16 length selection and obtains the verified short alias before shell construction. That exact returned value is assigned both to the Popen cwd and the shell-location argument; it is not recalculated from another spelling. `shell_command` at lines 33–39 prepends `Set-Location -LiteralPath`, doubles single quotes, uses `-ErrorAction Stop`, and transports the complete script through the existing UTF-16LE Base64 mechanism. Literal single-quoted PowerShell text avoids interpolation; `-LiteralPath` avoids wildcard interpretation. The existing user command and exit-status suffix are unchanged.

Cost accepted: the internal location setup can now explicitly fail before the user command when the chosen directory cannot be selected. No global chdir or alias creation is added. The extra script length is bounded by the already short alias. Whether Windows PowerShell actually retains a usable short provider location must be established by the native gate, not inferred from encoding tests.

### R2 — Accepted

Short Windows cwd values never set `shell_cwd`, so their generated script stays identical. Non-Windows `shell_command` still immediately returns the original command and `True`; its POSIX guard inputs retain their prior values. Direct argv launches keep the existing cwd adaptation without receiving a shell preamble. Job construction now occurs after shell encoding (`process_io.py:88–92`), so path selection or encoding errors do not allocate a native job. The existing CREATE_SUSPENDED -> Popen -> assign-and-resume ordering and failure cleanup at lines 98–112 remain intact. Termination, process-handle waiting, and close behavior are untouched.

Cost accepted: the command observes the verified short spelling through PowerShell's current location for long initial cwd values. A user command that subsequently selects another overlong directory, or a third-party program with its own path restrictions, is outside this bounded fix. This review does not claim arbitrary tool long-path compatibility.

### R3 — Accepted coverage design; native gate pending

The new portable tests verify (1) `OwnedProcess` passes one identical verified alias into shell construction and Popen, (2) shell=False after encoded construction, and (3) the decoded UTF-16LE script contains the doubled quote and terminating-error preamble before the user command. The new actual-Windows test (`test_windows_verification_compatibility.py:171`) runs the normal `execute_process` -> `PipeCapture` -> `OwnedProcess` shell route in the same repeated Chinese directory shape and requires both success and the relative output file at the original root. Its `python_command` fixture uses the existing PowerShell-safe Python-source transport, so it targets cwd failure rather than reintroducing native argument quoting ambiguity.

The existing deep Unicode ownership regression remains strict: `test_output_provenance.py:30–48` requires two passed verification runs and output-authority generation 2. No skip or assertion relaxation was introduced. Existing short-path Unicode capture, exit-code, timeout, cancellation, hook, and cleanup tests remain relevant context.

Observed evidence: `/tmp/ignovate-pr5-shell-related.log` ends with `Ran 100 tests in 47.101s` and `OK (skipped=6)`. This is useful portable/related regression evidence only. The four native-directory tests are Windows-only and a macOS skip cannot establish Windows correctness.

Cost accepted: a native runner is necessary for the behavioral claim; the existing two-run ownership test and new child-output test add real-process execution time. No duplicate local focused run was performed by the reviewer.

### R4 — Accepted review process; final readiness outstanding

This is the single fresh bounded read-only review of the two-path shell increment relative to e91ec4d. No actionable C/I/M item requires an author source change. Author disposition may accept this conditional result and proceed with the already running actual Windows and final gates. Readiness requires those gates to pass against the unchanged reviewed source, including the deep Unicode two-run ownership behavior; a green portable result alone is insufficient.

Cost accepted: the author must await actual native and final results before claiming completion or merge readiness. This report does not supersede failed evidence or the earlier helper review, and it does not call for another historical-scope review.

## Explicitly considered and declined changes

- Broadening the preamble to all Windows cwd values: declined because the requirement preserves ordinary cwd semantics, and the frozen change deliberately sets it only after long-path adaptation. Cost: ordinary cwd behavior remains governed by existing PowerShell startup behavior.
- Encoding/escaping by interpolating raw command-line path text, using `Set-Location -Path`, or selecting another alias in the shell: declined because the current literal quoted value inside EncodedCommand already preserves the verified spelling without wildcard or expansion semantics. Cost: a small generated preamble and a quoted alias regression are maintained.
- Creating aliases, changing process-global cwd, changing directories on disk, or swallowing location failure: declined because none is needed to pass the verified alias to both launch layers, and explicit failure protects the requested working-directory behavior. Cost: environments without a valid usable short alias still fail explicitly under the existing helper contract.
- Moving job creation ahead of path/script preparation: declined because it would add a resource lifetime during operations that can fail without a child. Cost: job construction occurs slightly later, while still preceding suspended process creation.
- Adding another broad test matrix, rerunning the author's focused suite, or treating skips as native proof: declined to preserve the requested bounded review and avoid duplicate cost. Cost: readiness depends on the author's actual final gates and their recorded outputs.
- Re-reviewing the short-directory helper, unrelated historical verification changes, generic arbitrary-tool path support, and commands that deliberately change their own cwd: declined as outside the new two-path increment and its stated requirements. Cost: this is an incremental shell-location opinion, not a replacement for the prior helper/full-PR reviews.
- Requesting style-only rewrites or additional tests that mirror source ordering without new behavior: declined; the small change is readable, and the present portable assertions plus native output and unchanged ownership regressions address the failure mechanism. Cost: some lifecycle/short-cwd preservation conclusions rest on unchanged code and the existing regression suite rather than new duplicate assertions.

Final assessment: no review finding blocks the frozen code. **Await actual Windows proof and all final gates before declaring ready.**
