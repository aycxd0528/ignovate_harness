# 上下文管理 1–7 项设计

用户已要求开始落实上一轮全部七项建议。本设计保留 Python 3.11、现有 LangChain/LangGraph、权限审批、项目边界和已发生费用记录；不增加在线服务或模型调用依赖。

## 目标与验收

1. 每次主/子模型请求（同步和异步、所有运行模式、工具循环）检查完整输入，包含 system、工具 schema、调用参数与当前输入。软阈值默认 150,000；已知窗口硬限制为窗口减输出预留及安全余量。未知窗口明确显示未知，仍执行软阈值。无法安全缩减时在提供商调用之前停止，不删用户当前输入或固定约束。输出上限随剩余窗口收窄。
2. L1 独立压缩已完成的旧工具正文，不依赖用户轮数；保留最新工具交换、完整调用配对和审批中的待完成调用。L2 在完整回合边界生成任务状态摘要，默认保留最近四轮。硬限制允许越过 20% 收益门槛，并逐步减少旧回合；固定消息仍保留。
3. 摘要显式包含当前目标、完整用户要求索引、有效约束、完成/待办、验证/阻塞、证据和 Skill 加载状态。原始用户要求不因固定八条队列淘汰；超出摘要预算的要求保存为可检索引用并注明截断。不推测任务已完成，不把工具资料升格为用户指令。
4. 会话私有归档保存脱敏后的原始消息/工具输出和参数，摘要含引用、状态、错误、退出码、超时/截断、文件行范围和内容版本。read_history_result 只能查询绑定会话，不接受路径；分页最多 6,000 字符，完整内容上限有明确截断标记。文件证据注明为历史版本。归档不能通过符号链接或 ID 越界访问。
5. 目标和计划使用稳定固定消息 ID。同类新版本替换旧版本；活动目标结束后解除 goal 固定状态，目标规则只注入绑定会话。普通用户输入不自动被固定或吞掉。
6. 每层固定记忆只注入简短核心约定，其余以 scope、标题和版本索引呈现；read_memory 仅能读取现有三层记忆，按主题/片段分页。旧接口小文件行为兼容。Skill 摘要保留 name/path/version 与“之前加载”状态，明确不重复一次性初始化；显式选择 Skill 继续强制读取最新版本。
7. /context 优先报告最近一次实际请求的模式、工具集合、互斥分类、估算/实际输入、输出预留、窗口余量、压缩原因和收益。字符估算按中文等非 ASCII 内容单独计量，并用同模型同提供商实际 usage 保守校准；稳定系统/工具前缀保持顺序。费用预算继续用既有保守 UTF-8 上界，不能把估算当实际账单。

## 方案

采用本地确定性分层处理，避免为每次压缩增加模型费用。与只调小阈值相比，它可以恢复证据并保护任务状态；与引入向量数据库/模型摘要相比，它保留现有部署方式，便于离线验证。未来可替换估算器，但本次不承诺 token 的精确预测。

`context.py` 负责分类、估算、窗口/输出预算及校准；`compact.py` 负责 L1/L2 和固定消息生命周期；新 `context_runtime.py` 在每次模型请求前执行与记录处理，并将变更持久化到图状态。无法容纳固定上下文时抛出可解释的 ContextLimitExceeded。

新 `history_archive.py` 提供会话私有原始结果归档；`memory.py` 提供核心+索引及分页读取。工具接线、权限和模式过滤仍集中在 registry.py/tools.py。AgentService 只保留手动 /compact 以及请求报告入口，避免两个自动策略互相竞争。

## 模块接口

- HistoryArchive(root, api_key='').save(thread_id, message, arguments=None) -> str；read(thread_id, reference, offset=0, max_chars=6000) -> dict。root 在 ProjectSessionStore.root 下，save 幂等、权限 0600、引用为不可遍历 ID。
- MemoryStore.catalog() -> dict，兼容并行记忆工作新增的受限主题目录；read_section(scope, section='', offset=0, max_chars=6000) -> dict 仅读取三层约定文件。select_core_memory 默认每层 2048 字符，再包装为 MemorySnapshot；rendered_context、目录和读取结果共用 MemoryReadContext 的预算（默认 4000 保守估算 tokens），不追加预算之外的索引。memory_list/memory_read 支持已登记的主题文档，read_memory 保留固定三层按标题读取。
- compact_messages(messages, keep_turns=4, min_gain=.20, *, level='all', archive=None, thread_id=None, hard=False) -> CompactResult；增加 level/summary_state 元数据，保留 removed_ids 兼容调用方。实际 checkpoint 用 REMOVE_ALL_MESSAGES 和完整结果序列替换，保证摘要早于保留回合。
- ContextManagerMiddleware 包含完整请求分类、calibration、request_filter 和 memory_budget 快照；主/子、同步/异步路径一致。预检查与实际 wrap 使用相同的记忆预算视图，持久原文保留；快照落到 store 的 context_request/context_result 事件。

## 验证

离线模型记录真实输入，检查多轮工具循环、同步/异步、受限工具/审查模式、小窗口、系统/schema 单独超限、中文估算、待完成工具与多工具配对。测试长历史约束、多次压缩、命令错误恢复、归档隔离/脱敏/符号链接、旧 checkpoint、目标结束和记忆/Skill 版本更新。运行完整 unittest 套件并核对真实 CLI /context 输出；不使用 API 密钥调用外部模型。

## 当前基线与执行决策

当前分支 codex/nailong-v1 有大量用户未提交和未跟踪文件，保留当前工作区，不移动或提交已有内容。基线为 374 测试，已有 test_unbound_goal_does_not_activate_named_chat_until_attached 失败，属第 5 项会话归属范围，实施时修复。

并行接口协调：用户另一个记忆会话实现 MemorySnapshot、MemoryReadContext、主题目录及命令诊断。本次保留它的接线和统一预算，核心选择器返回值必须再包装，不能覆盖为裸列表。任务索引归档使用简短要求摘要与独立原文引用，避免累计要求正文使索引达到单条归档上限；索引自身仍超限时拒绝销毁旧回合。
