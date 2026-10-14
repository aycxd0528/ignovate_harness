# macOS desktop release and main integration

Goal: Save the current project to GitHub, merge it into main, and create an installable Apple Silicon macOS desktop release. Existing user data and local credentials are excluded from publication.

Design: Build an Ignovate.app using official Electron Packager, a relocatable Python 3.11 runtime and the validated wheel plus pinned dependencies. Packaged launch uses its embedded Python and asks the user to choose a workspace instead of opening the filesystem root. The DMG contains the app, an Applications shortcut and install instructions. No Developer ID identity is available, so local builds use ad hoc signing and do not claim notarization. Preserve prior v1.0.3 assets; this GUI release uses v1.1.0.

Execution: inline; read-only independent final review. User explicitly authorized commit/push, main merge and macOS release packaging. No model requests, real credential changes or destructive index cleanup. Existing staged IDE entries are saved separately and restored afterward.

- [x] Freeze and scan the project snapshot; full offline unittest suite: 1436 tests, 43 platform skips. Build and pure JS checks passed.
- [x] Commit snapshot, preserve main documentation and current platform details while resolving the merge conflict; push the existing feature branch.
- [x] Add packaged runtime/workspace resolution and focused tests, native macOS build script and version/release documentation.
- [ ] Build the app/DMG; verify relocation, installed package metadata, frontend files, launch/close, disk image and SHA-256 checksum.
- [ ] Independent review, final source tests, push and merge the current PR into main; ensure remote commit matches.
- [ ] Produce release assets from merged source and publish the macOS artifact with accurate signing/architecture notes.

Validation before integration: Python 3.11.15 and 3.13.13 each passed 1443 tests (44 platform/dependency skips). Three additional official-archive build-ID regressions passed after a witnessed failure. The installed wheel smoke and desktop runtime/syntax checks passed. A prototype DMG passed strict/deep codesign, hdiutil verification and relocation into Chinese/space paths, with no external Python/PATH/model credentials. Final DMG must be rebuilt with all license notices and the final source commit. Independent review found no blockers after specifying macOS13+ and fixing distribution notices and temporary-script paths.
