# Settings Center Implementation Plan

> For agentic workers: use subagent-driven-development for isolated native/UI units and requesting-code-review for final integration. No staging or commits in this shared dirty checkout.

**Goal:** Replace the model-only settings dialog with a usable DSH-inspired settings center.

**Architecture:** Separate settings dialog with left navigation (general, models, built-in plugins). General/native defaults persist per project, existing browser appearance is preserved until explicitly changed. Provider secrets live only in native user configuration; provider catalog exposes metadata and configured status, never keys. Current connection activation reuses the existing native configuration path.

**Tech Stack:** Existing ES modules/DOM, CSS theme variables, Python GUIController, atomic_json/read_config and native BootstrapStore/PreferenceStore; no new dependencies.

**Spec:** User's two DSH screenshots and request in this conversation. Follow sidebar/scrollable settings rows, current system font, neutral theme surfaces, no ornamental dashboard cards.

## Constraints and rulings

- Continue the authorized existing checkout and preserve unrelated work, model/provider preferences and real conversation data during verification.
- User steering: omit Agent presets entirely. Existing task/profile/goal controls remain as before.
- Functional general controls: new-chat default permission, theme system/light/dark, chat font 12–22, compact/standard/detailed tool records, file change entry toggle, busy chat send queue/draft, editable Mod shortcuts N/K/F.
- No fake language or embedded-browser switches: those do not currently exist in the kernel/UI. The screenshot is a design reference, not an instruction to pretend unsupported behavior exists.
- Skills/MCP tab reuses real management module. Provider settings support the existing OpenAI-compatible API transport and arbitrary model ID, without claiming native Anthropic transport support.
- Appearance migration: absent native saved keys inherit existing browser theme/settings. New defaults apply only to newly created conversations; existing branches and running/queued captured permissions stay unchanged. Forks retain existing default-permission isolation.
- Ruling: split independent new native module and new settings UI module; root handles existing app/controller integration. Skills explicitly request implementer subagents; no parallel edits to shared existing files.

## Task 1 — Native settings and provider metadata

- [x] Implement/test `nailong/gui/settings.py` with `GUISettings(controller)`, `catalog()`, `preferences()`, `update(changes)`, async `provider(payload)`. Owned tests `tests/test_gui_settings.py`.
- [x] General file `.nailong/gui-settings.json`: preferences + saved_keys; reject unknown fields/ranges/shortcut duplicates/redirected files.
- [x] User provider vault beside BootstrapStore config: id/name/api_base/model/api_key; public metadata id/name/api_base/model/has_key/active. Existing current connection appears as id=current without automatic migration/write.
- [x] Provider actions save (optional activate), activate, remove inactive. Blank edit key retains native stored key; no key export. Busy connection changes rejected before mutation. Activation uses `_command_unlocked('configuration', ...)`, with intentional rollback handling on storage failures.
- [x] Final review considers secret leakage, corrupt/symlink state, active factory lifetime, queued tasks, project switches.

## Task 2 — DSH settings dialog

- [x] Implement `static/settings.js`, `static/settings.css`; own these files only. Root integrates callbacks/endpoints.
- [x] `showSettings({section,read,savePreferences,provider,plugins,onChanged,isCurrent,isBusy,openConfig})`. Modal separate from global child-form dialog, left tab navigation/right scroll rows, keyboard Escape/focus and responsive layout.
- [x] General controls use catalog preferences; provider rows edit/add/select/remove with current-vs-configured labels; API fields blank and never receive stored keys. Plugin tab uses createPlugins with provided read/mutate callbacks and current/busy guards. No Agent presets tab or preset/output-style defaults.
- [x] Child provider forms keep the settings center mounted; first-run settings opens Models and offers 保存并使用.

## Task 3 — Integrate and verify

- [x] Controller adds settings/settings_update/provider adapters, includes gui_settings in snapshot, persists seeded branch permissions for future conversations only;
- [x] App opens general settings from sidebar, models from model capsule/onboarding; applies appearance/tool preferences, edited shortcuts and busy-send behavior. Existing themes migrate without writing actual prefs during verification.
- [x] Conversation tool display modes preserve expansion per mode/key; change review entry toggle affects presentation only.
- [x] Static resource whitelist/CSS linking; config-location dialog shows known paths without reading key contents.
- [x] Failing-first native/browser settings tests, existing regression flow, independent review, appropriate full native checks and wheel/Electron checks. Refresh idle preview and capture actual screenshots.

Ruling: User explicitly removed Agent presets before implementation; native/UI contracts omit default_profile and output_style.
