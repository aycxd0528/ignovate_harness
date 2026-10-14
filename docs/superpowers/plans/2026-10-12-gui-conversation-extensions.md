# GUI Conversation and Extensions Implementation Plan

> **For agentic workers:** Use `executing-plans` to implement inline. Follow RED→GREEN per task and use one independent review after implementation.

**Goal:** 完成本轮 9 项 GUI 要求，已实现功能保留并按截图细化。

**Architecture:** 原生事件与分支记录提供标题、工具状态和时间；GUI 只投影展示。插件页通过一个独立适配器管理原生 Skills/MCP，不增加第二套代理运行循环。

**Tech Stack:** Python/SQLite/native Harness；无前端新依赖的 JavaScript/CSS/SVG；本地 Playwright 验收。

**Spec:** `docs/superpowers/specs/2026-10-12-gui-conversation-extensions.md`

## Global Constraints

- 蓝色强调色，其余黑白灰；保留 SVG 名称提示与减少动画设置。
- 原生权限与项目隔离；列出插件不启动程序或模型请求。
- 不覆盖手动标题；不猜测未记录产物；不重复模型回答。
- 保留当前工作区用户变更；不自动提交、推送或安装第三方扩展。

## Review Focus

- 快速入队/重试首条请求时，标题只生成一次，手动改名始终优先。
- 审批/停止/重载后，工具状态和耗时保持，排队时间不被冒充执行时间。
- 历史产物路径或敏感文件变更后，预览仍走当前工作区权限校验。
- 插件配置失败、切换项目和长时间连接响应不应写到其他项目。
- 本地 Skill 安装的符号链接/超大文件和 MCP 凭据声明不能突破原生边界。

### Task 1: Conversation metadata and execution projection

**Files:** Modify `nailong/core/branches.py`, `nailong/gui/controller.py`; create `nailong/gui/presentation.py`; test `tests/test_gui_branches.py`, `tests/test_gui_controller.py`.

**Interfaces:** `project_turn` adds `timing`; branches persist title source. Public runs expose started/finished/elapsed, activity and tools. `artifact_cards(node)` returns actual file/command/verification metadata.

- [x] Add failing tests for first-user title, manual/fork title preservation, native event duration/artifacts, in-flight tools and frozen elapsed after completion/reload.
- [x] Run named tests and observe failures for missing behavior.
- [x] Implement projections using recorded events and monotonic execution time; preserve legacy unknown states.
- [x] Run branch/controller/native regressions; record results here.

### Task 2: Chat, composer and artifact inspector

**Files:** Create `nailong/gui/static/conversation.js`; modify `app.js`, `ui.js`, `icons.js`, `style.css`, `server.py`; extend browser acceptance.

**Interfaces:** `renderTurn(node, callbacks)` supplies user/assistant layout; `renderLive(run, text, callbacks)` displays native runtime rows; callbacks preserve existing actions. Inspector consumes `node.artifacts` and native `file` preview.

- [x] Add failing browser checks for bubble alignment, title, status command/time, artifact-only inspector and quote from main chat; run RED.
- [x] Implement message layout, live timer updates, actual tool previews and composer/slider reference layout.
- [x] Adapt original quote test to main-chat selection; run new and previous browser/scale tests in fresh fixtures.
- [x] Record verification.

### Task 3: Native extension management

**Files:** Create `nailong/gui/plugins.py`, `nailong/gui/static/plugins.js`, `tests/test_gui_plugins.py`; extend `nailong/mcp/config.py`, GUI command routing/static whitelist and browser acceptance.

**Interfaces:** `GUIPlugins.catalog()` returns skill/MCP metadata. Plugin commands validate operations, use native registry/store/manager, return refreshed catalog. `renderPlugins` receives guarded command callback and current project identity.

- [x] Add failing isolated tests for list-without-execution, skill installation/enable, MCP add/update/connect/disconnect, invalid declarations retaining old values, busy/stale-project rejection and package boundaries.
- [x] Implement native-backed adapter and themed list/configuration dialogs; install only the user-selected local package; keep MCP connection explicit.
- [x] Run native plugin tests and browser flow against local fixtures; record results.

### Task 4: Review and delivery

- [x] Run full Python suite, native/browser/Electron checks and wheel resource verification.
- [x] Request one independent read-only review of implementation and scope; fix important findings with regression tests.
- [x] Refresh the existing 60303 preview after verifying no active execution; preserve user data/drafts.
- [x] Update `docs/GUI.md`, capture actual preview screenshots and report verified results.

## Execution notes

Native inline execution is already authorized by the user's request. Skill commit/handoff steps are adapted to the ongoing dirty shared workspace: preserve files and keep a verification record here instead of making mixed commits. Tasks 1→2 share telemetry/artifact metadata; task 3 independently uses the existing project/command guards; task 4 checks all three.

Task 1 complete: six new regression tests observed RED; branch/controller/native tests then passed (34). Timing persists as GUI execution metadata in native event sources; imported logs use recorded timestamps. No history reset or user rename migration.

Task 2 complete: new conversation browser acceptance observed RED then GREEN; original quote acceptance adapted to select main chat. Original browser/polish/composer, conversation/scale/layout, file entry and Electron all passed in separate fresh offline fixtures. Controls assertion updated to the user-requested attachment-first composer ordering.
Task 3 complete: 5 failing native tests observed RED; final plugin/server native tests passed (14 plus 10 subtests before timeout fix). Browser Skills install/toggle and actual local MCP stdio add/connect/disconnect/config/error-retention passed. All browser model, user preferences and configuration paths are isolated.
Task 4 complete: full Python suite 1277 passed, 43 skipped, 634 subtests passed; native static wheel modules, Electron sandbox, browser flows and map JS tests verified. Actual 60303 preview and owned desktop refreshed only after read-only active run check returned zero; user state and drafts preserved.
Final review: fresh reviewer began read-only review and reported native MCP 120-second handshake vs GUI HTTP 60-second timeout; reviewer then hit usage limit before full verdict. Implementer completed remaining review checklist; no claim of a complete independent review. Timeout regression observed RED (60 vs 150) then GREEN (10 plugin/server tests), followed by green full suite. No second reviewer dispatched.
Ruling: preserve pre-existing dirty shared working tree and deliver current running preview without mixed commits — this matches the authorized ongoing GUI work; changes remain uncommitted for user review.
Ruling: preserve prior manual/imported titles rather than bulk rewriting history — only new unnamed sessions use the first user input; existing names remain available.
Final deliverables: docs/previews/gui-conversation-2026-10-12.png and gui-plugins-2026-10-12.png captured from the real refreshed 60303 preview; tab kept open. Plugin browser fixture is self-contained and passed again after test isolation improvements.
