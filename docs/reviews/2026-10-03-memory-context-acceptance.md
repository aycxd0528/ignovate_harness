# 记忆改进验收

日期：2026-10-03。工作区：`/Users/aycxd/Desktop/LLM_project`。

目标：先修会话隔离和截断提示，再实现记忆目录、按需读取和统一预算，最后评估自动提取与向量数据库。用户授权与 context 聊天协调接口；本次保留其上下文窗口、核心选择、归档和请求统计实现。

## 行为与证据

| 要求 | 已实现行为 | 主要验证 |
| --- | --- | --- |
| 会话隔离 | 只有明确绑定且匹配的会话收到目标规则，并可调用 update_goal 修改目标 | test_prompt_assembly 的绑定/匿名/普通会话用例；test_tools 的实际 StructuredTool 调用 |
| 截断与失败可见 | 各层记录 missing/empty/error/loaded/truncated、版本和字符数量；show 比较版本，不误报摘要为磁盘修改 | MemoryLoadingTests、MemoryCommandTests |
| 可发现的记忆目录 | 三层主题目录、可选 description；只返回元数据，主题正文不进入默认提示词；扫描上限可见 | MemoryCatalogTests、实际模型输入断言 |
| 完整分页 | memory_list/read 与旧 read_memory，保留 offset/next_offset/版本；运行中版本改变返回 changed | 完整重建、版本变化、首读绑定、目录超限和 Unicode 用例 |
| 统一预算 | 默认 4000，可配 512–16384；固定摘要、目录与工具 JSON 合计受限；历史、并发结果不会绕过限制 | MemoryBudgetTests；真实 LangChain 多步运行中的测试模型输入 |
| 低预算可用性 | 为最大合法 ID 与最小响应预留空间；过长目录元数据、诊断与 section 响应包装明确精简 | 最小 512、长 Unicode ID、未列出 ID、大标题和诊断用例 |
| 安全文件范围 | 拒绝穿越、符号链接、非普通文件、坏编码和超大文件；目录描述符绑定父路径，防止读取期间祖先链接交换 | 目录/文件链接、模拟并发交换、FIFO、UTF-8、160000 bytes 上限用例 |
| 请求边界刷新 | 核心与目录快照每轮刷新，保持用户文件边界；同一运行正文检查版本 | test_next_runtime_refreshes_memory_and_preserves_user_store_boundary |
| 工具范围 | chat/plan 可只读访问；init/review/subagent 保持各自工具范围和过滤 | 工具注册、实际 runtime profile/allowed_tools 测试 |
| 用户入口 | 共享 show/list/read/edit/reload；context 显示预算/状态；doctor 检查主题与配置且支持缺依赖离线诊断 | 命令测试、旧编辑审批、doctor -S 回归及 CLI JSON 检查 |

## 验证结果

- `.venv/bin/python -m unittest discover -s tests -v`：**497 tests，41.722s，OK**。日志 `/tmp/nailong-memory-acceptance-suite.log`。该数字包含工作区其他聊天的整合用例。
- 独立只读审查复核 **62 项相关测试通过**，没有剩余可行动发现；额外验证最大长度 Unicode ID 在快照建立后首次读取，512 预算下合计估算 457。
- 相关模块的 `compileall` 与 `git diff --check` 通过。
- 真实 CLI JSON：`/memory list project`、带空格文件名的 `/memory read`、`/memory show project`、`/context` 均成功；错误预算在 doctor 中显示 memory error，network_checked 为 false。
- 运行模型链的测试使用本地测试模型，不调用真实 DeepSeek API；已有全屏 UI/审批用例包含在全量回归中。独立审查没有单独操作原生 UI，也没有验证其他平台的文件系统回退路径。

## 审查修复与取舍

审查修复覆盖：最低预算的大 ID 和首次未列出 ID 读取、目录诊断占尽响应、大标题阻断第一页、祖先符号链接交换、普通聊天修改未绑定目标、过长 section 包装、标题截短未标记、CLI 续页文件名未引用。重要发现均先复现失败测试，再修复与复核。

保留当前工作区及既有未提交内容，没有提交、推送或迁移记忆文件。自动提取与向量索引没有开启；后续条件记录于 [评估文档](../design/2026-10-03-memory-storage-evaluation.md)。预算是明确的本地字符估算，实际 token 仍以提供商 usage 为准。
