# Desktop App Icon Release Implementation Plan

> **For agentic workers:** Use the executing-plans skill inline; one fresh bounded whole-branch review before merging.

**Goal:** Publish Mac Apple Silicon and Windows x64 installers embedding the icon selected by the user.

**Architecture:** Convert the approved PNG into native multi-resolution ICNS/ICO resources. Point existing Electron packagers at those resources with module-relative absolute paths and fail early if absent. Retain existing window-icon fallback, installed-app acceptance and Release workflow.

**Tech Stack:** Electron Packager 20.1.1, macOS sips/iconutil, standard ICO container, GitHub Actions.

**Spec:** The user requested an icon matching the existing brand, selected the generated blue two-eye image, then asked to update the installers. Version v1.2.6 adds the icon to the v1.2.5 release tree.

## Global Constraints

- Preserve the selected PNG and its transparent outer corners; format/resolution conversion only.
- macOS 13+ Apple Silicon and Windows 11 x64, existing signing/support limits unchanged.
- Do not alter original user workspace or replace published v1.2.5 assets/tags.
- No dependency version changes. No new or manually executed test suites; existing push/PR/tag CI remains the publication gate (developer instruction).

## Review Focus

- Native container sizes/alpha: inspect ICNS and every ICO image frame.
- Arbitrary working directory and spaces: resolve icons from import.meta.url, not process.cwd().
- Missing asset: access must fail before Packager can silently retain an Electron default.
- Actual published icon: inspect the Mac bundle resource and Windows EXE PE icon resources from the public packages against committed resources.
- Release identity: matching versions, accepted merge tree, stable tag and public checksum coverage; old releases/workspace preserved.

## Tasks

- [x] Convert the existing PNG to ICNS (16–1024px representations) and ICO (16,24,32,48,64,128,256px); record provenance and reproduction commands in desktop/assets/README.md.
- [x] Update desktop/package-macos.mjs and package-windows.mjs to require and embed their native icon. No application runtime change is required: Electron uses the EXE icon for Windows windows when icon is omitted.
- [ ] Prepare v1.2.6 metadata/public docs/Release notes; inspect syntax, containers, version alignment and whitespace. Freeze one commit and obtain one bounded review.
- [ ] Push PR, require all current-head checks, merge with expected-head guard, verify tree and tag v1.2.6. Observe full Release workflow; inspect public packages/icons, digests/latest and preservation before final handoff.

## Execution rulings

- User already authorized updating the installers in the established publish workflow; execute inline without another approval gate.
- Follow the developer instruction on tests above; use static/container inspection and existing mandatory CI rather than adding or manually running tests.
- Native format conversion uses sips/iconutil; the standard-library ICO writer only assembles the resized PNG bytes into a container.
