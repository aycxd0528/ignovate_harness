# Task 2 当前任务上下文交接

当前状态（2026-10-04）：测试授权已解除，专属 28 项测试和包含它们的 139 项关联回归通过。已验证实际同步/异步图、当步记忆刷新及 token/费用预算接线；没有调用真实 API。扩大范围发现记忆所有者的 3 项测试失败，已独立复现并完整记录，不能宣称整体项目通过。详见 `docs/reviews/2026-10-04-runtime-context-verification.md`。

以下为 2026-10-03 静态阶段的历史交接，其中“不运行测试”描述只适用于当时阶段；2026-10-04 的更新见文末。保留全部既有文件与改动，无提交、推送或工作树移动。

## 实际修改

- 新增 `nailong/core/task_context.py`：`render_task_context(snapshot: dict | None, max_chars: int = 12000) -> str`；结构无效抛 `TaskContextError`，保护字段过长抛 `TaskContextTooLargeError`。
- 修改 `nailong/core/context_runtime.py`：新增可选同步 provider、请求尾部投影、身份/版本/工具配对保护及请求快照元数据；新增可选 `memory_request_context` 的当步刷新与精确替换。不直接依赖 TaskStore 或 memory 模块。
- 修改 `nailong/core/context.py`：实际有投影时新增互斥分类 `task_context`，没有投影时保留旧分类集合与默认行为。
- 未修改 `agent.py`、`agent_service.py`、`memory*`、工具注册表、UI、TaskStore 或任何测试文件；`compact.py` 和 `history_archive.py` 保持本轮原状。

## 渲染契约

消费 Task snapshot v1 及总控明确追加的 `request_history`、`profile`、`input_fingerprint`。只复制所消费的字段，不改变 provider 的对象。`progress_observations`、`progress_repeats`、时间及未约定的随机计数不进入正文或需求签名。

完整保留目标、项目内范围、约束、全部要求历史、最新用户要求、生命周期/阶段、未完成步骤、所有验收项、修改路径、待验证与阻塞，以及全部证据的 ID、来源、状态、版本、路径、覆盖、指纹和引用。目标或最新要求与历史中的原文完全相同时，使用从 0 开始的索引引用同一正文，避免重复文本；不是截断。已完成步骤只列 ID 并注明不等于验收通过；进展/证据摘要为明确标注的节选，可不注入，完整内容仍在任务记录/归档。

保护字段不能放进字符上限时明确失败，不截断约束、要求索引、验收或证据状态。本实现没有自动生成大任务历史的恢复引用，因此大任务超过 12,000 字符时会停止投影；总控可以显式提高 `task_context_max_chars`，但完整窗口预算仍会拒绝过大输入。不能单纯截短 `request_history` 绕过保护。

旧 v1 没有 `request_history` 时仅保留已知目标和当前要求，并明确标记无法确认中间要求；不会伪称历史完整。新 TaskStore 应始终提供完整历史。

Evidence 的 model 来源、非成功状态、缺失关联、未知/部分覆盖及任务版本不匹配均可见。Evidence 指纹与 task.input_fingerprint 不同时标记旧证据；相同时只说与任务记录一致，不能冒充已检查当前磁盘。`verification:<run_id>` 原样保留为事件引用，不当作 `hist_...` 归档参数；配置流程通过和用户功能覆盖是不同事实，最终交付判定交由 delivery。

## 必需接线

新增参数均在原构造签名末尾，默认不启用：

```python
ContextManagerMiddleware(
    ...,
    task_snapshot_provider=None,     # Callable[[str], dict | None]，必须同步
    task_context_max_chars=12000,
    memory_request_context=None,     # 同步 MemoryReadContext，支持 refresh_for_request/filter_messages
)
```

总控可直接传绑定方法，不需要闭包或把 TaskStore 导入 context：

```python
context = ContextManagerMiddleware(
    self.settings, tools, system_prompt, profile, thread_id,
    self.session_store, self.history_archive,
    self.context_reports, self.context_calibration,
    parts=self._prompt_parts(profile, target_path, tool_names, thread_id),
    goal_store=self.goal_store,
    request_filter=memory_context.filter_messages,
    requested_output=getattr(self.model, 'max_tokens', None),
    memory_budget=snapshot.report(),
    task_snapshot_provider=self.task_store.snapshot if thread_id else None,
    memory_request_context=memory_context,
)
```

provider 必须按调用会话返回当前任务；普通问答不应把无关旧任务交给它。当前实现对快照的 thread_id 和规范项目根目录进行绑定检查；不能把父任务 provider 传给未绑定的只读子代理。状态更新、磁盘指纹刷新及 request_history 的完整性由总控在调用前保证。

**费用/token 预留的中间件顺序必须调整。** 现有 `MemoryContext → ModelAccounting → ContextManager` 会使 accounting 在任务投影加入前预留，漏算任务内容。由于本模块在 ContextManager.wrap_model_call 投影，推荐总控在启用 provider 的运行时使用：

```python
middleware = [
    MemoryContextMiddleware(memory_context),
    context,
    ModelAccountingMiddleware(self.settings),
    # 原有 HITL（适用时）与 SecretRedactionMiddleware，保持后者最后注册
]
```

这样 memory 先裁剪，context 添加投影并检查窗口，accounting 对完整输入预留并可进一步缩小输出。此顺序变化下 `/context` 的输出预留是窗口层的上界，accounting 可能在内层再收窄；预算层拒绝也会记录一次准备请求失败，不能将该记录解释为已经向提供商发出网络请求。若需要保留内层预算后的精确输出 cap 和严格发送统计，总控应另加观测点，或设计先投影后 accounting 再统计的包装器；不能只改 provider 参数而保持旧顺序。

UI 分类标签由总控补 `task_context: 当前任务`。实际请求报告新增 `task_projection`，仅含 task_id/revision/phase/lifecycle/characters/projected；不把完整要求正文放入事件日志。没有当前任务时 `projected=false`，未启用 provider 时保留旧报表行为。

## 请求、压缩与缓存边界

- before_model 将同一轮的任务投影计入每次压缩后的完整估算，新增投影本身不写入 checkpoint。wrap 时重新获取快照并检查实际请求预算，避免工具运行后仍使用旧阶段/证据。
- 投影仅在工具交换完整后追加为有标记的 HumanMessage；未完成并行调用、孤立结果或旧式函数配对无效会明确停止。不会伪造 ToolMessage，也不会隐性修改历史来修复配对。
- 用固定保留 ID、名称和内部标记替换同一次请求中的旧投影，重复包装不累积多份快照。只改变 request.override(messages=...) 的视图；压缩状态更新仍只包含原历史的压缩结果。
- memory filter 与 task projection 独立：先按原有记忆预算裁剪记忆工具，再按完整窗口预算处理包含任务尾部的请求。未改变 MemoryContextMiddleware 及其持久历史策略；提供 `memory_request_context` 后，context 会在刷新预算后再过滤实际请求，避免外层旧预算视图与本次系统记忆不一致。
- 同任务缓存的需求 revision 不能倒退；同 revision 的目标、范围、约束和完整历史不能变化。普通控制输入（与 TaskStore.begin 相同的“继续/请继续/开始/开始修改/继续修改/continue”）允许不增加 revision 更新 latest_request；其他新要求必须提高版本。执行阶段、步骤与证据状态可在同 revision 更新。
- 版本缓存只在当前 middleware 实例内有效，重新编译后不能代替 TaskStore 的跨进程版本保护。provider 可能在 preflight 和 wrap 之间改变；wrap 再检查，不能容纳时直接停止，不偷偷使用旧快照。单实例没有新增并发同步，调用方应继续串行执行同一会话。
- 系统与工具前缀不加动态任务正文；任务稳定时尾部文字稳定。上下文图不保留上轮投影，因此不会造成滚动摘要重复吸收任务快照；但当前用户文本可能同时存在于真实历史和快照的完整要求索引，预算会如实计入两处。

## 静态自审与待验证

已读取并逐项对照共享 spec/plan、TaskStore.begin/snapshot 的最新契约、ModelAccounting 的预留顺序、原有 memory request_filter 接口和 context 的分类路径。确认代码没有测试修改、网络调用、原始 shell 参数读取或 .env 访问。

实际执行的静态检查：三个改动源码用 `ast.parse` 解析，`git diff --check` 无输出。前者不导入或执行应用；后者不覆盖未跟踪文件，因此额外静态检查了这三个文件的行尾空白。没有运行自动测试、模型、CLI 冒烟或运行时动态复现。

待总控完成：agent/provider 与中间件顺序接线、UI 标签、普通问答 gating、恢复时当前磁盘证据核对及 delivery 汇总。待未来授权测试后核对：None/旧 v1、超长保护字段、多次压缩与工具循环、同/旧 revision、新要求历史、model/unknown/stale 证据、同步/异步、预算拒绝、并行工具配对、memory 共用请求视图、checkpoint 无任务投影、费用/token 预留确实包含任务正文、重启及跨项目/会话拒绝。

## 补充：动态记忆接口（总控要求）

`memory_request_context` 默认 None，与现有默认行为兼容。总控应传给外层 MemoryContextMiddleware、工具注册表和 ContextManagerMiddleware **同一个** MemoryReadContext 对象。启用后，context 优先使用该对象的 `filter_messages`，不使用另一个 request_filter 回调来构造不同的预算视图。

调用路径：

1. before_model 调用 `refresh_for_request()` 一次，消费 `initial_context/context/report`；从编译时的固定槽位重建 self.system_prompt，更新 parts.fixed_memory 与深复制的 memory_budget。L1/L2 的每次估算复用该轮刷新结果，不在压缩循环里重新渲染。
2. wrap_model_call/awrap_model_call 通过同一 `_prepare_request` 再刷新一次；以编译时 initial_context 精确定位 request.system_message 的区域，替换为最新 context，再用同一对象过滤工具消息、添加任务投影并检查完整预算。工具步骤后会走新的 before_model/wrap，避免继续使用原来的 observed 元数据。
3. memory_budget 报告深复制，后续刷新不会修改已记录请求的报告对象。系统消息用 model_copy/request.override 创建，只改变实际请求，checkpoint 和编译初始模板不被改写。

首次定位优先使用编译的 `base_system + fixed_memory + skill_catalog` 布局；没有完整布局时，非空 initial_context 仅在编译提示中唯一出现才可替换。**空 initial_context 不调用 replace('', context)**：完整布局使用 base/catalog 之间的零长度槽位，没有组装信息则使用编译提示尾部的单个零长度槽位。部分或不一致的组装信息会拒绝，不能猜测替换区域。

投影系统消息附带内部标记，绑定编译模板、槽位、实际区域起点/长度与正文哈希。重复包装先核对标记后替换该区域，所以初始为空、最新正文为空或再次变长均不累积多份正文。没有标记时接受原编译提示、当前/前一步的已知提示，或唯一的非空初始片段；空初始记忆且提示被其他中间件改成未知布局时明确停止，不盲目追加。调用方应保留 SystemMessage 的该内部元数据，或重新从编译模板生成请求。

MemoryReadContext 刷新钩子不重绑正文版本，本模块也不刷新分页版本绑定或提升候选知识。刷新元数据/预算与过滤使用同一个对象，但不是跨文件的原子文件系统事务；SourceValidator 在过滤时仍可重新读取来源，由 memory 所有者的版本和有效性规则处理变化。外层先缩短的结果在内层不会凭空恢复更多字符，完整正文仍在持久历史/归档；新的视图至少再次满足当步预算。

新增接口的静态自审已对照实际 `MemoryReadContext.refresh_for_request` 返回字段及 `filter_messages` 的消息复制行为，确认同步/异步调用共用准备路径、初始模板保留、每步刷新、报告复制、无图状态记忆投影。再次完成 AST/行尾空白静态检查；未执行应用、测试、CLI 或真实 API。待后续授权的专项验证还包括：最初零记忆后发现记忆、空/非空往返、同请求重复包装、编译片段重复、其他中间件改变系统布局、来源写入后 observed 失效、报表快照不可被后续刷新改变。

## 补充：用户验收与配置验证绑定

acceptance 中可选 `verification_binding={step: str, paths: list[str], source: 'user'}` 已纳入结构校验与保护投影。提供时必须为对象、step 为非空文本、paths 为项目内相对路径列表、source 严格为 user；未提供时不生成映射。字段与其验收项一起完整渲染，超过字符预算明确失败，不按描述猜测步骤或覆盖范围，也不将绑定本身当作通过证据。绑定改变应由总控提高需求 revision 并重新核对证据；原中间件版本保护继续适用。本补充仅做代码自审、AST 与空白静态检查，未运行测试或 API。

## 2026-10-04 运行验证更新

新增专属 `tests/test_managed_context.py`，执行了真实同步/异步 AgentRuntimeFactory 图、SQLite checkpoint、记忆来源变更后的工具循环及两种预算预留/结算；模型本身均为离线响应。专项 28 项、关联回归 139 项通过，完整命令和日志见验证记录。

根据失败测试修复 context_runtime.py：提高需求版本仍不可删除或改写已见要求历史；同版本的验收定义和用户绑定纳入要求签名；显式无系统消息按实际请求统计，只有省略参数的 preflight 才采用编译提示。版本保护不冻结执行阶段、验收状态或证据关联。

agent/provider 与中间件顺序已由总控接线并通过局部真实图验证。跨进程 TaskStore 恢复、整项目验收、CLI/TUI 与真实 API 未在本分工执行。扩大回归的三个记忆模块失败已逐项列入验证记录，所有权仍由记忆分工保持；不以 139 项通过替代它们的修复和复验。
