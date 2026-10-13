# Settings center acceptance — 2026-10-13

User scope: DSH-inspired settings center; Agent presets explicitly omitted. Only 通用设置 / 模型 / 内置插件. No staging or commits in the shared dirty checkout.

Implemented:
- General project preferences: default permission for new root conversations, system/light/dark theme, chat font 12–22 px, compact/standard/detailed tool records, file-change entry visibility, busy chat queue/draft behavior, editable Mod shortcuts. Existing browser appearance and collapsed tool presentation are retained until explicitly saved.
- New-root permissions persist per branch in SQLite. Existing branches and queued payloads retain their captured mode. Forks retain default permission isolation; manual branch changes persist after restart.
- Native provider catalog adds/edit/activate/remove inactive saved OpenAI-compatible connections. Blank editing keys retain native values. Provider keys never appear in the public catalog; current/configured labels describe saved configuration, not a network test. Duplicate connection profiles preserve selected identity through native-only active_id metadata. Busy connection changes, corrupt/redirected paths and failed storage are guarded.
- Plugins reuse native Skills and MCP installation/configuration/connection management. Child provider/plugin forms keep the settings center mounted. Plugin reentry refetches after an async stale read. Header exposes configuration paths without reading key contents.
- Separate modal, tab keyboard navigation, focus restoration, responsive scroll layout and existing model-onboarding entry. Agent presets are absent.

Verification:
- Native settings adapters initially failed with unsupported commands (RED), then passed. Native settings/preferences/provider tests include 25 tests and 43 subtests. Targeted GUI suite: 71 passed + 43 subtests.
- Product Python suite from an isolated working directory: 1317 passed, 43 skipped, 677 subtests passed in 132.80 s. Actual project reasoning/provider files were not used as test defaults.
- New native-browser settings flow and mocked dialog lifecycle pass, including persistence, font/tool display, draft retention/no duplicate enqueue, native provider activation/blank-key editing, plugin management, shortcut editing, light/dark/mobile, stale callbacks and focus.
- Existing browser flows: chat-map-sync, map-live, browser, polish, composer, conversation, layout, scale, plugins, controls, reference, reference-lifecycle, creation-race, effort-stale, layout-stale; entry/Electron smoke pass. The prior polish test was updated from the removed model-only cancel dialog to 关闭设置.
- JS pure suites: 8 passed. Desktop syntax check passes. Built wheel contains settings.py and settings.js/settings.css/preferences.js with byte-equivalent current source. Build used local pip/no index/no dependencies after the local build/ directory shadowed python -m build.
- Independent reviewer was requested under the review skill, but its tool quota prevented execution; it did not return a code review. Implementer reviewed storage boundaries/rollback, selected-profile identity, lazy factory configuration, captured permissions/fork isolation, project guards, async dialog/save/plugin lifecycle and resource packaging. No independent-review pass is claimed.

Actual preview:
- Idle browser and owned desktop launcher refreshed. Settings center shows three tabs in the actual app; real model requests were not sent. Tests used disposable native configuration plus the local Fake model. No real provider or permission values were changed for verification.
- Screenshots: ../previews/gui-settings-live-2026-10-13.png and ../previews/gui-settings-models-live-2026-10-13.png. Isolated light/dark/narrow screenshots are under /tmp/ignovate-settings-*.png.

Completed: native module, UI module, integration and checks. Settings reference retains current system font and neutral theme surfaces. Existing task modes are unchanged.
