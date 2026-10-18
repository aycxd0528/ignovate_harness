# 空会话生命周期

用户希望没有任何对话内容的空窗口不再留在会话列表。

## 行为

- 连续点击新会话复用当前空白窗口；离开或重启后清理未使用的占位。
- 新工作区首次输入文字即保存草稿。保留聊天草稿、地图追问草稿、发送记录、任务、检查点、分叉与原始历史文件；损坏的非空日志不按空记录处理。
- 工作区正被执行任务占用时稍后自动重试；每次重试重新检查当前选择和草稿，保留启动时的清理意图。
- 新建前保存旧草稿时，继续输入的文字进入新会话，不会被清空。

## 验证

- 10 项真实控制器/存储回归通过，覆盖清理、复用、草稿/历史/任务/检查点保护、损坏日志和执行锁。
- 真实 GUIServer + Chromium 临时工作区回归通过：首次启动清理、忙碌后重试、重复新建、聊天/地图草稿、切换、重载、首次输入，以及延迟草稿保存时继续输入。
- 独立复审发现的输入竞争、非空损坏日志、忙碌重试及启动重试意图均有 RED→GREEN 回归，最终复审无阻塞问题。
- 隔离源码快照完整回归最近一轮 1637 passed、44 skipped、806 subtests passed；之后增加执行锁重试用例并通过定向检查。
- 当前主工作区 `python -m pytest tests -q --tb=short`：1637 passed、1 failed、44 skipped、806 subtests passed。失败名称为 `HarnessWelcomeUiTests.test_small_layout_keeps_fields_and_keyboard_help_visible`，预期步骤 1、实际步骤 2；该 CLI 欢迎页测试独立复测通过。该界面会读取已有环境密钥并允许空字段继续；整套运行可能受前置测试的环境状态影响，具体来源尚未完全确认。本次未改动其代码或测试。未将此次整套运行报告为全部通过。
- 主工作区直接从根目录运行 pytest 会收集 `artifacts/agent-framework-test-kit-2026-10-06/` 中的历史测试副本，发生 204 项 collection errors，包括多个 `test_baseline.py` 的 import file mismatch；原始日志位于 `/tmp/ignovate-empty-conversations-primary.log`。验证正式代码时使用 `tests/` 目录。
- JavaScript 语法检查、Git diff 空白检查通过；未发送真实模型请求。

## 当前源码窗口

操作前已用 SQLite backup 保存会话数据库到 `/tmp/ignovate-empty-conversations-backup/`。重启源码桌面后，49 个分支中的 9 个空占位已清理，余下 40 个；删除前逐一核对这 9 个记录均无保存草稿、节点或发送记录。所有原有节点 ID、运行 ID 完整保留，未重发历史请求。这里记录的是提交前源码验收；发布安装包尚未更新。

## 后续 GitHub 提交验收

通过 `DEEPSEEK_API_KEY=fixture-existing` 稳定复现上述 CLI 欢迎页失败。测试夹具现于构造界面时隔离该环境变量，不继承本机连接密钥；同样设置下 `tests/test_harness_setup.py` 的 16 项检查全部通过。头像、等待队列、7 项代码渲染及真实服务的空会话浏览器回归、14 项 JavaScript 单元测试、前端构建、桌面语法检查、wheel 构建和安装 smoke 均通过；未调用真实模型 API。

最终提交快照执行 `.venv/bin/python -m unittest discover -s tests -v`：1682 tests，OK，44 skipped。该快照不包含本地 `.env`、IDE 配置和临时工作副本。
