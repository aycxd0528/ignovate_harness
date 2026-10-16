# Ignovate 六项 Harness 优化设计

日期：2026-10-16（Asia/Shanghai）。状态：待用户审阅，尚未实施。

本地基线：`097be863f757b104d38962826d4e0d728f4798b7`，Ignovate 1.2.1。
参考基线：DeerFlow `ec07de9618b4194533fea3d8bf8a2e4fbcca5385`。

## 1. 目标与已确认范围

用户已确认开始优化上一轮提出的六点：MCP schema 按需暴露、子代理证据回报、结构化工具摘要、模型工厂及请求准入、持久产物引用、可替换执行后端。

目标是增强当前本地桌面与 CLI 编码助手，减少无关请求上下文，使委派结果和产物来源可核对，并建立共享的模型及执行入口。CLI、GUI、无头模式继续使用同一 AgentService。

成功标准：六项均接入真实运行路径，有兼容行为和针对性回归；不以新增接口、测试替身或静态文档单独代替接入。后端抽象的首个交付是已接入的本机实现，不在本轮提供容器、远程沙箱或可写子代理。

本轮不更换 LangGraph、不重做地图、不引入服务端多租户和定时调度、不自动提取并确认长期记忆、不默认发送真实模型请求。现有用户改动和凭据不进入提交或验证夹具。

## 2. 现状与方案选择

已核对的实现：

| 领域 | 当前入口 | 需要补足的行为 |
| --- | --- | --- |
| MCP | `tools.py`、`nailong/mcp/tools.py`、`agent.py` | 已连接且允许的工具均构建完整 schema；缺少模型绑定前的延迟目录与加载状态 |
| 子代理 | `nailong/tools/agents.py`、`agent.py`、`nailong/tools/registry.py` | 并发和预算已有；工具主要返回文本，部分失败依赖字符串识别 |
| 大结果 | `nailong/tools/results.py` | 已有有界归档、引用及首尾预览；缺少内容结构摘要 |
| 模型 | `agent.py`、`nailong/core/model.py`、`reasoning.py`、`generation.py` | 主代理和子代理分别组装 DeepSeek；缺少共享的协议工厂和请求节流 |
| 产物 | `nailong/gui/presentation.py`、`controller.py` | 已有文件、命令、验证卡片；文件预览目前明确展示当前工作区内容 |
| 执行 | `local_tools.py`、`nailong/core/process_io.py`、`processes.py` | 本机命令执行、进程树清理和取消已有；缺少可替换的项目执行提供者 |

候选方案：

1. **在现有执行链上增量接入（采用）。** 复用权限门、预算、事件、归档和 GUI 投影；每项可单独验证和关闭。
2. 引入第二套 DeerFlow 运行时。会形成两套权限、会话及预算语义，迁移成本高，不采用。
3. 先扩展容器和可写代理，再整合上层。会同时增加进程、目录、审批与合并语义，不适合当前交付范围。

## 3. 所有模块共同遵守的约束

- 运行时生成的项目、线程、回合、工具结果身份是来源依据；模型参数、MCP 内容及历史文本不能提供权威身份。
- 发现工具不等于获准执行；工具调用仍经过 ToolExecutionContext、参数检查、模式检查及原生审批。
- 子代理继续只读，不能 shell、修改、审批或再委派；最多并行三项，沿用每轮共享 token 预算。
- 状态分别表达“执行是否结束”“引用是否匹配”“验收是否满足”；不将任何一项自动提升为任务完成。
- 新投影和摘要有输入、输出及条目数限制，超过限制明确返回部分或未知，不能裁剪后伪装完整。
- 取消、重启、分叉、连接更换和模型切换不会自动重放副作用。
- 配置沿用已有优先级和原子写入方式；不把密钥写入项目配置、产物索引或事件。

## 4. MCP 工具按需暴露

### 4.1 接入方式

新增 `nailong/mcp/discovery.py`，由运行时工厂在工具过滤之后构造不可变目录，并在 create_agent 模型请求路径安装发现中间件。

ToolNode 保留允许的工具路由；模型只绑定内置工具、`tool_search` 和当前已加载 MCP schema。未加载工具即使被模型猜中名称，也在执行前返回 `tool_not_loaded`，不能进入审批或远程调用。

新增配置 `mcp_discovery.enabled`，默认 true；false 恢复现有完整绑定行为。低层 `build_tools()` 继续返回完整内部工具集合，避免改变非模型调用者的接口。

### 4.2 目录和加载状态

- 目录包含真实命名空间名称、简短描述和服务来源；全文 schema 仅按需返回。
- `tool_search(query, offset=0, limit=5)` 支持有界字面关键词搜索；limit 为 1–10。空查询用于分页浏览，不执行正则表达式。
- 初始目录提示最多 4,096 字符，未显示部分说明可搜索；不把所有 schema 再编码进提示词。
- 加载状态保存在线程的原生 graph state，并绑定目录摘要。目录摘要涵盖名称、描述和 schema；执行时还核对现有 MCP connection identity。
- 加载与搜索受当前 allowed_tools 和 profile 限制；历史加载状态不能扩大当前权限。无 MCP 时不增加空目录和发现工具。
- 搜索结果返回完整 schema；不能通过截断 schema 适应窗口。单个 schema 无法容纳时明确失败，且不标记已加载。
- 同批次搜索与调用不依赖工具调度先后：未在请求开始前加载的工具，必须等下一次模型请求后调用。

### 4.3 上下文与兼容

最终上下文核算和预算预留使用本次实际绑定的 schema，加上目录提示及发现结果。不得只改变模型绑定而仍将隐藏 schema 计入硬窗口；也不得漏计发现工具返回的完整 schema。

更新现有模型/MCP 集成夹具，真实经过发现、审批和调用。恢复和分叉继续核对当前目录，不复活旧连接。

### 4.4 验收

实际模型绑定未含未加载 schema；搜索后下一次请求出现 schema；猜测调用不产生远程副作用；重连和 schema 漂移使旧加载失效；已有拒绝审批、批量审批、只读模式、恢复及取消回归继续成立。

## 5. 子代理报告与原生证据

### 5.1 类型与兼容

新增 `nailong/core/subagent_reports.py`。扩展 ChildTaskResult，增加原生 `child_run_id`、执行状态、受限证据列表、报告引用检查结果和未解决事项，同时保留 text、usage、usage_complete、usage_records、budget_failure。

ReadOnlyTaskRunner 新增结构化结果入口，原有 `run()` 保留文本兼容包装；模型可见的 `task` 改用结构化入口，不再通过回答前缀认定原生失败。旧测试 worker 返回 str 时正常包装，但其执行证据标记为 unavailable。

### 5.2 证据采集和引用

- 在 ToolExecutionContext 规范化结果并生成 tool_result_id 后，将白名单字段交给当前 child_run_id 所属的内部采集器。
- 采集器通过运行时上下文绑定，不能临时替换共享 observer。并行子代理各自隔离，退出时清理，并保留原有 hook/observer 行为。
- 证据字段包括工具名、原生结果 ID、成功/拒绝/中断状态、路径、版本、实际读取范围、分页/截断及归档引用；不持久化未脱敏正文和完整参数。
- 每个子任务最多保留 64 条证据；超过后标记证据目录不完整，不声明无引用结果不存在。
- 提示子代理使用 `[evidence:<id>]` 引用自己的执行记录。父侧只在本次子任务的原生目录内匹配，不接受父代理、其他子代理或旧轮次的证据 ID。
- 匹配证明记录与声明关联的工具存在；不证明自然语言结论正确。没有引用的报告保持 unverified，不能解析“已经完成”等字样为成功。
- 读取片段、搜索结果和完整文件分别表达覆盖；文件变化后保留历史观察版本，不表示当前版本已经核对。

### 5.3 状态与持久化

执行状态使用 completed/failed/cancelled/budget_exhausted；报告引用检查使用 corroborated/partial/unverified。状态来源由运行时填写，模型只能提供文本与引用。

通过原有 session events 保存有界报告元数据，绑定父回合和 child_run_id。复用工具回执身份，不新建一套项目验收或反馈状态机。取消仍等待预算结算完成，未返回的报告不能被记录为完成。

### 5.4 验收

无读取证据的“已读完整文件”不会被认可；伪造、跨子任务及旧轮次引用被拒；并行任务不串证据；预算与取消各结算一次；历史和分叉不会把旧回执认成本次执行；主代理能看到未核实范围。

## 6. 超长工具结果结构摘要

新增纯函数模块 `nailong/tools/synopsis.py`，在 ResultArchive.bound 生成最终有界响应时调用；不新增模型请求，不替换现有归档格式和 read_tool_result 分页语义。

首批支持 JSON、CSV/TSV、普通文本。摘要返回 `kind`、结构、数据规模、少量样本和 `summary_truncated`。JSON 给出有界字段名、容器路径和数组长度；CSV 用 csv.reader 识别逻辑记录，正确处理带换行的引号字段；文本保留已有首尾样本。

摘要输入上限为 1 MiB UTF-8；超限直接回退，不加载巨量归档。JSON 结构遍历深度最多 4，路径条目最多 24，字段名最多 80 字符。解析失败、过深嵌套和字段超限都回退，并明确说明结构未知。

摘要最多 2,000 字符，计入现有结果上限；保留 ok、错误、退出码、是否启动、分页、版本、完整性、结果身份及归档引用。摘要不能制造新的测试通过结论；解析字段中的 error/status 仅为数据样本。

归档写入失败时保留已有失败信息，不宣传可读取的完整文件。多次 bound 必须幂等，不将摘要再摘要，或重复保存同一结果。

验收包含中间字段可发现、引号内换行、畸形输入、长键、超限、密钥脱敏、归档失败、结果整体上限和原有完整性字段不丢失。

## 7. 共享模型工厂与请求准入

### 7.1 模型工厂

新增 `nailong/core/model_factory.py`，主代理初始化、模型/推理切换与只读子代理均使用同一创建函数。保留 HarnessChatDeepSeek 对 reasoning_content 的序列化修正。

首批适配器为 deepseek 和 openai_compatible Chat Completions。新增非密钥配置 `model_runtime.adapter`，缺省为 deepseek，保持现有连接行为；显式选择 openai_compatible 时使用其协议适配器。不在本轮宣称支持原生 Anthropic、Responses 或任意 CLI 模型。

模型描述包含 adapter、请求模型、endpoint、上下文/输出上限及已确认推理能力。能力仍只来自已有内置表或显式配置；未知能力保持未知，模型 ID 不自动证明提供商身份。适配器不支持当前非默认推理配置时，在请求前报配置错误。

Fake 模型注入和现有测试替换入口继续可用。涉及新增直接依赖时同步 requirements、锁定安装依赖及打包验证。

### 7.2 请求准入

新增 `nailong/core/request_admission.py`。配置 `model_runtime.request_admission`，默认关闭；启用后要求显式正数 requests_per_minute，max_queue_size 默认 32，max_wait_seconds 默认 30。

同一运行时工厂的主代理和子代理共用节流器；不同工厂只有显式同组且连接指纹与策略一致时才共享。连接指纹不包含可回显的密钥，不跨不同认证连接混用。该限制仅在当前进程内有效，不宣称跨 CLI/GUI 进程全局限流。

按 FIFO 均匀放行，不积累空闲突发额度。排队可取消；队满与等待超时有明确错误码。使用可注入单调时钟验证，不依赖长时间真实 sleep。

ModelAccountingMiddleware 在实际预算预留和模型轮次准入之前等待限流；等待取消不消耗模型调用次数、不长期持有 token/cost 预留。放行后若任务已更换、暂停或预算不足，继续由现有核对拒绝请求。

### 7.3 重试与账务

准入启用时关闭 SDK 隐式重试；现有预算控制也继续强制单次 SDK 尝试。新增显式瞬时重试配置，默认 0，最多额外 1 次；每次均重新经过准入、轮次及预算核算。

首批仅重试可证明响应尚未流出正文或工具片段的 HTTP 429/503；认证、余额不足、畸形请求和已输出片段不自动重试。Retry-After 超出剩余等待/目标预算时立即结束。网络错误无法确认是否计费时保留 usage 不完整，不能按零结算。

现有截断续写继续由 GenerationRecoveryMiddleware 负责，不形成第二套递归重试。

### 7.4 验收

主/子请求适配一致；DeepSeek reasoning_content wire 回归保持；通用适配器不收到专有推理参数；FIFO、取消、队满、超时可复现；排队不消耗调用次数；重试逐次结算；流式部分输出不重放；切换连接后不复用旧策略。

## 8. 持久产物引用与 GUI

### 8.1 注册表

新增 `nailong/core/artifacts.py`，项目私有会话目录中使用 `artifacts.sqlite` 保存版本化索引。句柄由原生项目、来源回合、tool_result_id、结果内序号和内容版本确定；同一原生结果重投影不重复注册。

记录 handle、kind、来源线程/回合/工具结果、task_id、显示名、MIME、引用类别、文件观察版本、快照状态和有界内容引用。旧事件没有权威来源时不补造身份。

### 8.2 发现来源

- 原生 write_file/edit_file 的成功结果可登记文件观察；已有 diff 和历史事件仍保留。
- MCP structured_content 和 resource links 中的 URI 登记为外部引用；远程 `/path` 不能被解释成本机文件，也不自动获取 URL。
- 普通命令 stdout 不用正则扫描所有路径来推断生成文件。新增 `register_artifact(path, title)` 以已有 FileSession 和读权限核对本地文件；来源标签为“显式登记”，不能表示已经证明该命令生成了它。
- 结构化数据和已有归档结果可登记为 data/result 类型，不递归无限展开。

新增 `read_artifact(handle, offset, limit)` 通过所属线程或可核对的分叉祖先解析句柄。句柄不授予项目外访问或外部服务权限；所有读取继续核对当前权限。其他项目或无祖先关系的线程不能仅凭句柄获得读取能力。

### 8.3 历史与当前内容

本地文本首次登记时可保存最多 256 KiB 的有界脱敏快照；大文件仅登记元数据和当前读取入口，明确历史快照不可用。快照具有 digest、完整性和来源版本；不得把截断快照称作完整文件。

索引发布必须在快照成功持久化之后；失败保留 snapshot_unavailable，不能发布失效快照引用。重试使用相同来源身份，避免重复记录。快照在文件后来变化或删除后仍是历史观察；读取前仍复核记录路径对应的当前访问策略。

GUI 复用现有产物卡片和 inspector：有快照时显示“历史快照”，现有 file 路径显示“当前文件”，两者不能混用。URL 只作为外部链接呈现，不嵌入脚本或主动执行 HTML。首批支持文本、JSON 和外部链接，不新增通用二进制媒体播放器。

CLI/无头工具结果同时返回句柄和可读摘要，GUI 不成为唯一入口。

### 8.4 验收

重启可读、同源幂等、跨项目拒绝、分叉祖先准确继承、来源删除/文件改动不冒充原版本、快照失败不发布假引用、显式登记与生成声明区分、MCP 远程路径不触发本地读取、HTML/脚本内容安全呈现。

## 9. 可替换执行后端

新增 `nailong/core/execution_provider.py`，声明执行能力、项目绑定、租约获取/释放、同步/异步命令执行和验证进程观察接口；提供 LocalExecutionProvider，实际委托现有进程所有权、采集、超时和取消清理实现。

local_tools.run_command/run_command_async 与配置验证流程均接入提供者，避免普通工具与 /verify 出现两套不同执行后端。ToolExecutionContext 仍拥有审批、输入校验和结果身份；提供者不能直接改变这些策略。

本机提供者必须声明 `sandboxed=false`、`filesystem_isolation=false`、`network_isolation=false`。Electron 渲染器 sandbox 状态与命令执行能力分开表达。网络可达性和系统账户权限沿用当前主机行为。

首批不改变文件工具的 FileSession 实现。提供者契约明确声明是否具备文件系统映射；未来远程提供者在命令与文件工具无法保持一致时不能启用。未知或不可用提供者在执行前明确失败，不能悄悄回退本机。

租约绑定项目和运行时 owner，取消后等待现有进程树清理完成再释放；项目切换/关闭后旧租约不可调用。不同回合不继承旧审批或进程。后端通过受信代码注入，不从模型参数动态 import。

本轮保留项目跨进程执行锁，不引入并行写入，不把 worktree 表述为系统沙箱。已有调用方直接使用 local_tools 函数的兼容入口继续有效。

验收使用本机集成和可替换假提供者：普通命令及 /verify 都进入同一接口；拒绝审批不 acquire/execute；shell 与 argv 语义、Windows 进程清理、取消/超时、退出码、归档和项目锁保持；关闭释放仅一次；后端失效不回退主机。

## 10. 数据兼容、开关与交付顺序

现有 bootstrap 密钥配置和 GUI providers v1 记录保持兼容；本轮模型协议设置进入非密钥运行配置，不直接把新字段写进严格校验的旧 vault。旧会话、工具结果、产物卡片继续可读，新增字段仅在有原生来源时提供。

执行顺序建议：结构摘要 → MCP 发现 → 子代理报告 → 模型工厂与准入 → 持久产物 → 执行后端 → 整体回归。前三项独立验证后再整合模型工厂；子代理报告和产物引用复用相同来源身份，避免互相发明标识。

每一项有真实集成入口和针对性测试。模型准入默认关闭以保持现有速率；新后端默认 local。MCP 发现可通过配置关闭，结构摘要失败回退现有预览，旧数据不做破坏性迁移。

## 11. 验证与效果判定

行为验证以离线 Fake 模型、本地 MCP fixture 和临时项目为默认，先写针对性失败用例，再实施。新增测试不只镜像内部函数：覆盖实际模型绑定、原生执行门、预算结算、重启/分叉和 GUI 文件版本。

重点回归：test_mcp_tools、test_mcp_batch_approval、test_subagent_budgets、test_generation_wire、test_native_budget_bound、test_command_argv、test_approval_cancellation、test_verification_cancellation、test_gui_branches、test_gui_native，以及新增对应集成用例。

整体运行 scripts/run_native_tests.py；GUI 涉及产物卡片时运行已有 conversation/文件变更流程及新增产物浏览器场景；构建 wheel 并检查新增 Python 模块和静态资源进入包。修改依赖时验证受影响安装入口。

效果夹具固定模型请求形状和数据，比较完整 MCP schema 绑定与按需绑定的输入估算、摘要可发现字段、报告引用核对结果和排队调用数。记录真实 provider usage 与本地估算的区别；静态节省不能冒充真实任务成功率或账单收益。

真实模型效果评测为显式可选，不自动调用现有用户密钥。准备可重现命令、版本、任务、预算、结果和未知用量字段；没有运行就明确标为未运行。

## 12. 参考实现

- [DeerFlow 工具发现](https://github.com/bytedance/deer-flow/blob/ec07de9618b4194533fea3d8bf8a2e4fbcca5385/backend/packages/harness/deerflow/tools/builtins/tool_search.py)
- [延迟 schema 过滤](https://github.com/bytedance/deer-flow/blob/ec07de9618b4194533fea3d8bf8a2e4fbcca5385/backend/packages/harness/deerflow/agents/middlewares/deferred_tool_filter_middleware.py)
- [子代理报告契约](https://github.com/bytedance/deer-flow/blob/ec07de9618b4194533fea3d8bf8a2e4fbcca5385/backend/packages/harness/deerflow/subagents/report_contract.py)
- [结构摘要](https://github.com/bytedance/deer-flow/blob/ec07de9618b4194533fea3d8bf8a2e4fbcca5385/backend/packages/harness/deerflow/agents/middlewares/tool_output_synopsis.py)
- [请求准入](https://github.com/bytedance/deer-flow/blob/ec07de9618b4194533fea3d8bf8a2e4fbcca5385/backend/packages/harness/deerflow/models/request_admission.py)
- [产物注册表](https://github.com/bytedance/deer-flow/blob/ec07de9618b4194533fea3d8bf8a2e4fbcca5385/backend/packages/harness/deerflow/tools/artifact_registry.py)
- [沙箱提供者](https://github.com/bytedance/deer-flow/blob/ec07de9618b4194533fea3d8bf8a2e4fbcca5385/backend/packages/harness/deerflow/sandbox/sandbox_provider.py)

本文借鉴接口与机制，不复制上游实现；若实施中引入第三方代码，需保留相应许可证与来源。
