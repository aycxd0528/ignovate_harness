# Ignovate 六项 Harness 优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Ignovate 执行链中交付六项优化，并用真实接入路径的离线回归证明兼容性。

**Architecture:** 复用 AgentService、ToolExecutionContext、原生工具身份、预算、归档和 GUI 投影。以九个可独立验收的任务顺序交付；模型工厂与准入、产物内核与 GUI 分别验收。执行后端首批仅实现已接入的 LocalExecutionProvider。

**Tech Stack:** Python 3.11+、LangChain 1.x、LangGraph 1.x、SQLite、unittest、原生 JavaScript、Playwright。沿用现有依赖锁和构建方式。

**Spec:** `docs/superpowers/specs/2026-10-16-deerflow-harness-optimization-design.md`（2026-10-16 用户确认）。

**Status:** 实施计划待审阅；以下复选框均未执行，不代表测试已通过。

## Global Constraints

- CLI、GUI、无头模式继续使用同一 AgentService。
- 发现工具不等于获准执行；工具调用仍经过 ToolExecutionContext、参数检查、模式检查及原生审批。
- 子代理继续只读，不能 shell、修改、审批或再委派；最多并行三项，沿用每轮共享 token 预算。
- 状态分别表达“执行是否结束”“引用是否匹配”“验收是否满足”；不将任何一项自动提升为任务完成。
- 取消、重启、分叉、连接更换和模型切换不会自动重放副作用。
- 配置沿用已有优先级和原子写入方式；不把密钥写入项目配置、产物索引或事件。
- MCP 发现默认 true；初始目录提示最多 4,096 字符；tool_search 默认 limit=5，允许 1–10。
- 子代理证据每任务最多 64 条；报告引用格式为 `[evidence:<id>]`。
- 摘要输入上限为 1 MiB UTF-8；JSON 遍历深度最多 4，路径条目最多 24，字段名最多 80 字符；摘要最多 2,000 字符。
- model_runtime.adapter 缺省为 deepseek；首批只支持 deepseek 和 openai_compatible Chat Completions。
- 请求准入默认关闭；启用要求显式正数 requests_per_minute，max_queue_size 默认 32，max_wait_seconds 默认 30；瞬时重试默认 0，最多额外 1 次。
- 本地历史文本快照最多 256 KiB；更大的文件仅登记元数据和当前读取入口。
- 本机提供者必须声明 sandboxed=false、filesystem_isolation=false、network_isolation=false；不引入可写子代理或远程/容器执行。
- 不复制工作区已有 .idea、.superpowers、脚本改动或真实凭据进入实现提交；不自动调用真实模型。

## Review Focus

1. **并行发现与调用：** 搜索和 MCP 调用同批出现时不能因调度次序提前执行。Task 2 测试 `test_same_batch_search_does_not_authorize_sibling_call`。
2. **复用工具调用 ID：** 后续回合复用 call_id 时不能把旧子代理证据认成本次执行。Task 3 测试 `test_reused_call_id_cannot_import_old_child_evidence`。
3. **队列等待期间状态变化：** 用户暂停或替换任务后，即使限流放行也不能请求模型。Task 5 测试 `test_task_replaced_while_queued_never_dispatches`。
4. **文件发布竞态：** 快照读取期间源文件变化，不能发布完整历史快照。Task 6 测试 `test_file_changes_during_snapshot_publish_no_complete_snapshot`。
5. **取消与后端关闭竞态：** 重复取消或关闭同时发生时必须先完成进程树清理，再释放一次租约。Task 8 测试 `test_repeated_cancel_and_close_release_after_cleanup_once`。

---

## 文件职责与接口依赖

| 新文件 | 单一职责 | 任务 |
| --- | --- | --- |
| `nailong/tools/synopsis.py` | 确定性结构摘要 | 1 |
| `nailong/core/harness_options.py` | 新增非密钥配置的分层读取和校验 | 2、4、5、8 |
| `nailong/mcp/discovery.py` | 目录、graph state、搜索、请求过滤及调用准入 | 2 |
| `nailong/core/subagent_reports.py` | 原生子任务证据与报告引用核对 | 3 |
| `nailong/core/model_factory.py` | 主/子模型的共享协议与能力处理 | 4 |
| `nailong/core/request_admission.py` | 可取消的 FIFO 限流及连接分组 | 5 |
| `nailong/core/model_attempts.py` | 原生单次尝试、输出观察与有限重试 | 5 |
| `nailong/core/artifacts.py` | 项目私有产物索引与不可变内容存储 | 6 |
| `nailong/gui/artifacts.py` | 产物 API 的来源解析、权限及展示投影 | 7 |
| `nailong/core/execution_provider.py` | 提供者契约、租约和本机后端 | 8 |
| `scripts/benchmark_harness_optimizations.py` | 离线比较及显式可选的真实模型评测入口 | 9 |

已有大文件只增加接入代码，不做无关拆分。九个任务依赖为 `1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9`；共享 agent.py 和执行门的修改顺序固定，避免并行编辑。

## 执行准备（在计划批准后）

- [ ] 阅读 using-git-worktrees 技能，先检查当前聊天已有 worktree，复用适合的工作区；需要新建时从包含本设计和计划的本地提交创建隔离工作区。分支可命名为 `codex/deerflow-harness-optimizations`；原 checkout 的暂存内容保持原样。
- [ ] 在隔离工作区使用 Python 3.11–3.13，建立依赖环境；以下 `python` 均指该环境。仅使用临时 HOME/项目夹具，检查没有复制真实 .env 和 .nailong 私有配置。
- [ ] 先运行 `python scripts/run_native_tests.py`，记录基线退出码、失败/跳过和日志。基线失败先定位并记录，不把新增失败当作既有失败略过。
- [ ] 每任务按 RED → GREEN 执行并保存命令/输出。每个提交只 git add 本任务列出的具体文件；工作区若为 detached HEAD，不擅自改动用户主分支。

### Task 1: 结构摘要接入现有结果归档

**Files:** Create `nailong/tools/synopsis.py`, `tests/test_tool_synopsis.py`; Modify `nailong/tools/results.py:176`, `tests/test_result_archive.py`。

**Interfaces:**
- Produces: `build_synopsis(content: str) -> dict`，字段为 kind、structure、size、sample、summary_truncated、structure_unknown；未知规模使用 null。
- Preserves: `ResultArchive.bound(result: dict, *, limit=MAX_RESULT_CHARS) -> dict`、save/read 引用及完整性语义。

- [ ] **Step 1: 写失败测试。** `test_middle_json_field_is_visible_in_bounded_result` 用超过结果上限且关键字段在中间的 JSON；`test_csv_quoted_newline_counts_logical_rows` 用三条数据记录，其中一条带引号内换行；另测畸形、过深、长键、1 MiB+1 byte、脱敏、归档失败、重复 bound。
  核心断言：`assert synopsis['size']['rows'] == 3`；`assert len(json.dumps(result, ensure_ascii=False)) <= MAX_RESULT_CHARS`；`assert archive.bound(result) == result`；失败归档不得出现可用 reference。
- [ ] **Step 2: 验证 RED。** Run `python -m unittest discover -s tests -p 'test_tool_synopsis.py' -v`；应因摘要功能缺失失败，不能因 fixture 本身失效而失败。
- [ ] **Step 3: 实现纯函数。** 使用 json、csv、io；先检查 UTF-8 字节上限，再做有界结构遍历。对解析异常、过深和字段超限返回 structure_unknown，不将裁剪字段当准确键名。所有摘要总字符不超过 2,000。
- [ ] **Step 4: 接入 bound。** 在已脱敏内容上生成摘要，计入原有结果上限；优先保留错误、退出码、是否启动、结果身份、版本、分页、完整性和归档引用。只对首次超限结果摘要，避免重复归档。
- [ ] **Step 5: 验证 GREEN。** 运行新测试及 `python -m unittest discover -s tests -p 'test_result_archive.py' -v`、`test_command_argv.py`、`test_managed_tools.py` 同形式命令；全部无新增失败。
- [ ] **Step 6: 提交。** 只提交本任务四个文件；message: `feat: summarize oversized tool results without model calls`。

### Task 2: MCP 延迟目录、schema 加载和请求预算

**Files:** Create `nailong/core/harness_options.py`, `nailong/mcp/discovery.py`, `tests/test_harness_options.py`, `tests/test_mcp_discovery.py`, `tests/test_mcp_discovery_runtime.py`; Modify `agent.py:607`, `nailong/core/context_runtime.py:344`, `tools.py:119`, `tests/test_mcp_tools.py`。

**Interfaces:**
- Produces: `load_harness_options(settings: Settings, *, user_path: Path | None = None) -> HarnessOptions`；初始 HarnessOptions 包含 `mcp_discovery: DiscoveryOptions(enabled: bool = True)`。按默认 → 用户 preferences → 项目 settings → local → CLI 非空显式值合并；新字段只读，不修改旧凭据文件。
- Produces: `DeferredCatalog(tools: Sequence[BaseTool])`，具有 digest、names、`search(query: str, offset: int = 0, limit: int = 5) -> dict`、`visible_tools(state: dict) -> list[BaseTool]`。
- Produces: `MCPDiscoveryMiddleware(catalog: DeferredCatalog, *, input_fits: Callable[[ModelRequest], bool])`、`build_tool_search(catalog: DeferredCatalog, *, execution: ToolExecutionContext, file_session: FileSession) -> BaseTool`；中间件声明原生 DiscoveryState，字段 `mcp_promotions={catalog_digest, names}`，并提供按同目录合并的 reducer。
- Consumes: 现有 ToolExecutionContext、MCP connection identity 和 ContextManagerMiddleware 的真实请求核算；不改变 build_tools 返回的内部路由集合。

- [ ] **Step 1: 写失败测试。** 用真实本地 MCP fixture 和记录 bind_tools 参数的 Fake 模型，覆盖搜索→下一请求加载→审批→调用、关闭开关、无 MCP、目录分页及配置优先级。
  断言 `assert hidden_name not in first_bound_names`、`assert hidden_name in next_bound_names`；拒绝审批时 `assert remote_effect_count == 0`。
- [ ] **Step 2: 加并发和恢复用例。** `test_same_batch_search_does_not_authorize_sibling_call` 交换工具调度顺序，两种次序均断言 `error_code == 'tool_not_loaded'`；另测重连、schema 改变、checkpoint 恢复/分叉及 allowed_tools 缩小后旧加载无效。
- [ ] **Step 3: 验证 RED。** 分别运行 `python -m unittest discover -s tests -p 'test_mcp_discovery*.py' -v` 与 `test_harness_options.py`；缺少目录/过滤机制导致失败。
- [ ] **Step 4: 实现目录和搜索工具。** 初始提示最多 4,096 字符，超出显示省略与搜索指引；字面关键词搜索，不执行 regex。搜索返回完整 schema；input_fits 失败只返回 `tool_schema_too_large` 错误、不输出超限 schema 且不加载。并行搜索通过 reducer 合并，不丢失兄弟更新。
- [ ] **Step 5: 接入模型与执行路径。** 每次模型请求冻结可调用的已加载集合；同批 sibling 不能读取搜索刚产生的授权。搜索及真实调用均复用原生工具执行门；模式、schema 和审批检查仍在。MCP 重连由原连接身份校验拒绝。
- [ ] **Step 6: 对齐上下文。** 给 ContextManagerMiddleware 增加可选 `request_tool_selector: Callable[[dict], list] | None`；before_model 预检、压缩决策及 wrap_model_call 最终请求都使用相同选择函数。目录提示通过 parts 参与核算；发现结果保留完整 schema，绝不漏计或仍按隐藏工具全量计费。
- [ ] **Step 7: 验证 GREEN。** 新测试、test_mcp_tools、test_mcp_batch_approval、test_context_budget、test_task_projection_efficiency、test_approval_cancellation 全部运行。小窗口夹具断言隐藏 schema 不会造成虚假硬窗口失败，已加载 schema 必须被计入。
- [ ] **Step 8: 提交。** 按 Files 列表提交；message: `feat: discover MCP schemas on demand through native runtime`。

### Task 3: 子代理结构化回报与证据核对

**Files:** Create `nailong/core/subagent_reports.py`, `tests/test_subagent_reports.py`, `tests/test_subagent_report_runtime.py`; Modify `nailong/tools/agents.py:131`, `nailong/tools/execution.py:284`, `nailong/tools/registry.py:310`, `agent.py:468`, `tests/test_subagent_budgets.py`, `tests/test_subagent_prompt_policy.py`。

**Interfaces:**
- Produces: `ChildEvidenceCollector(child_run_id: str, parent_turn_id: str | None)`，`record(observation: dict) -> None`、`snapshot() -> dict`；内部 ContextVar 仅接受运行时创建的实例。
- Produces: `check_report(text: str, evidence: dict) -> dict`，返回 verification（corroborated/partial/unverified）、matched_refs、unknown_refs、evidence_complete。
- Extends: ChildTaskResult 保留原有字段，新增 child_run_id、status、evidence、report_check、unresolved；`ReadOnlyTaskRunner.run_result(thread_id: str, prompt: str) -> ChildTaskResult` 为唯一原生执行/结算入口，`run(...) -> str` 只包装返回 text。

- [ ] **Step 1: 写失败测试。** `test_uncited_claim_is_unverified`、`test_foreign_and_fabricated_refs_are_rejected`、`test_parallel_children_do_not_share_evidence`、`test_reused_call_id_cannot_import_old_child_evidence`；断言 `assert report_check['verification'] == 'unverified'`，不能把文本中的“执行成功”当 status 来源。
- [ ] **Step 2: 验证 RED。** Run `python -m unittest discover -s tests -p 'test_subagent_report*.py' -v`；确认没有原生结果契约时这些断言失败。
- [ ] **Step 3: 实现采集器。** _finish 生成 tool_result_id 后记录白名单字段和读取范围；最多 64 条，超出标记不完整。证据来源必须是该次执行门，不接受 handler 自填身份。保留原 observer 和 hook；不修改共享 observer 以连接采集器。
- [ ] **Step 4: 接入只读子代理。** 生成独立 child_run_id，绑定采集上下文，向模型展示当前有界证据引用目录并计入输入预算；final-answer 预算回退同样保留可引用证据。父侧原生核对后才包装报告。引用匹配不能授予测试通过或当前文件完整覆盖。
- [ ] **Step 5: 接入 task 和事件。** task 返回运行时状态、text、报告检查及受限证据；预算不足/异常使用类型和原生状态分类，删除原生路径的回答前缀判断。取消保留 cancelled 事件并继续抛取消，预算结算一次；字符串 worker 兼容为证据 unavailable。
- [ ] **Step 6: 验证 GREEN。** 新测试及 test_subagent_budgets、test_subagent_prompt_policy、test_child_batch_approval、test_tool_event_replay、test_native_budget_bound 运行通过；增加 65 条观察断言 evidence_complete=false，并验证旧证据经恢复/分叉不会变成本次证据。
- [ ] **Step 7: 提交。** 按 Files 列表提交；message: `feat: return child reports with native evidence references`。

### Task 4: 主代理与子代理共享模型工厂

**Files:** Create `nailong/core/model_factory.py`, `tests/test_model_factory.py`; Modify `agent.py:298`, `agent.py:414`, `agent.py:459`, `agent.py:468`, `nailong/core/harness_options.py`, `nailong/core/budgets.py:85`, `requirements.txt`, `uv.lock`, `requirements-release.lock`；按需更新 `tests/test_generation_wire.py`、`tests/test_harness_settings.py`。

**Interfaces:**
- Extends HarnessOptions: `model_runtime.adapter: Literal['deepseek','openai_compatible'] = 'deepseek'`。
- Produces: `create_harness_model(settings: Settings, *, output_tokens: int, max_retries: int = 2, constructor: Callable | None = None) -> BaseChatModel`；constructor 为保留测试替换入口的显式注入。
- Produces: `describe_model(settings: Settings) -> dict`（不含密钥）、`wire_input_bound(request: ModelRequest) -> int`，供预算计算复用实际适配器的消息/schema 序列化。

- [ ] **Step 1: 写失败测试。** 主模型、set_model、set_reasoning、只读子代理分别记录构造参数，断言共用工厂；generic 适配器断言 `'reasoning_effort' not in payload`、`'thinking' not in payload`；未知适配器和不支持的非默认推理请求在发送前失败。
- [ ] **Step 2: 验证 RED。** Run `python -m unittest discover -s tests -p 'test_model_factory.py' -v`；确认当前重复构造路径和未提供的工厂导致失败。
- [ ] **Step 3: 实现协议工厂。** deepseek 保留 HarnessChatDeepSeek；generic 使用 ChatOpenAI。输出上限由现有 generation policy 给定，模型能力只取已有已确认元数据。主/子及切换调用工厂，Fake 注入不强制变成真实 SDK 对象。
- [ ] **Step 4: 对齐预算与依赖。** _input_bound 调用工厂的有界序列化函数；保留 DeepSeek reasoning_content。显式声明 `langchain-openai>=1.6.7,<2.0`。执行 `uv lock`，再按 docs/INSTALL.md 的既有命令 `uv pip compile requirements.txt --python-version 3.11 --universal --generate-hashes --no-annotate --no-header -o requirements-release.lock` 更新发布锁；不手工填写哈希。
- [ ] **Step 5: 验证 GREEN。** 新测试及 test_generation_wire、test_harness_settings、test_native_budget_bound、test_reasoning_context_estimation、test_model_configuration、test_gui_settings_integration；构造和切换失败不能先覆盖当前可用模型。
- [ ] **Step 6: 提交。** 仅实际修改的 Files；message: `refactor: share model adapters across parent and child agents`。

### Task 5: 有界请求准入与逐次重试账务

**Files:** Create `nailong/core/request_admission.py`, `nailong/core/model_attempts.py`, `tests/test_request_admission.py`, `tests/test_model_attempts.py`, `tests/test_request_admission_runtime.py`; Modify `agent.py:111`, `nailong/core/harness_options.py`, `nailong/core/model_factory.py`, `tests/test_generation_wire.py`。

**Interfaces:**
- Extends HarnessOptions: RequestAdmissionOptions(enabled=false, requests_per_minute=None, max_queue_size=32, max_wait_seconds=30, group=None, max_transient_retries=0)；数值拒绝 bool、NaN、无穷及越界；最大重试 1。
- Produces: `RequestAdmission(options, *, clock=time.monotonic, sleep=asyncio.sleep, sync_sleep=time.sleep)`，`async acquire(*, deadline: float | None = None) -> None` 与 `acquire_sync(*, deadline: float | None = None) -> None`；错误码 admission_queue_full/admission_timeout。三个时钟/等待函数均可在测试中注入。
- Produces: `get_request_admission(connection_key: str, options: RequestAdmissionOptions) -> RequestAdmission | None`；connection_key 是进程私有认证连接身份，不能以明文密钥或其可见摘要进入事件。
- Produces: `AttemptObservation(emitted_content: bool = False, emitted_tool_fragment: bool = False)`、`observe_attempt() -> ContextManager[AttemptObservation]`、`retry_delay(error: Exception, observation: AttemptObservation, *, remaining_seconds: float) -> float | None`。
- Extends: `ModelAccountingMiddleware(settings=None, *, main_agent=False, record=None, admission: RequestAdmission | None = None, lifecycle_check: Callable[[], None] | None = None)`；lifecycle_check 绑定原运行的任务身份及修订，不是重新认领更新后的任务。

- [ ] **Step 1: 写 FIFO 与取消失败测试。** 注入单调时钟和可控 sleep；断言放行时间间隔为 `60 / requests_per_minute`，等待取消/队满/超时不调用模型且不新增模型尝试预算预留；同组不同凭据不共享，策略冲突明确失败。子任务已存在的准入/最终回答保护由原生命周期管理，测试比较排队前后的预留增量，并在取消及 close 后确认归零，不能挪用最终回答保护额度来通过断言。
- [ ] **Step 2: 写原生集成失败测试。** `test_task_replaced_while_queued_never_dispatches` 中暂停/更新 TaskStore 后放行，断言 `assert provider_calls == 0`、`assert turn_budget.calls == 0`。HTTP MockTransport 验证 429/503 逐次重试，401/余额不足/部分流输出不重试。
- [ ] **Step 3: 验证 RED。** 分别运行 test_request_admission*.py 和 test_model_attempts.py；缺少准入和原生尝试观察时失败。
- [ ] **Step 4: 实现 FIFO。** 有界队列和无突发均匀节流；取消 finally 移除本等待者；不持有长寿命 executor worker。同步非预算调用通过同一策略兼容入口等待，不在异步事件循环里阻塞。
- [ ] **Step 5: 接入账务。** ModelAccountingMiddleware 先等准入，再调用生命周期复查，之后才 reserve/admit/标记 provider_started。每次瞬时重试重新进入这条路径；活跃 token/cost/session 预算的释放与 settlement 逻辑沿用现有 shield 清理。子代理已有最终回答保护保持独立，不因限流等待被挪用；取消结束后按原生清理释放。
- [ ] **Step 6: 实现有限重试。** 准入启用或预算活跃时 SDK max_retries=0；AttemptObservation 从原生流式 callback 记录已输出正文/工具片段。无法证明未输出时不重试。Retry-After 只在剩余准入/目标时间内等待；未知是否计费的失败保持 usage incomplete，不能制造零成本记录。GenerationRecovery 不额外重复瞬时重试。
- [ ] **Step 7: 验证 GREEN。** 新测试及 test_generation_wire、test_session_budget_integration、test_cumulative_budget、test_subagent_budgets、test_goal_control。断言取消后 reserved/final_reserved 不泄漏，重试次数进入主模型调用上限。
- [ ] **Step 8: 提交。** 按 Files 列表提交；message: `feat: pace model attempts with cancellable budget-aware admission`。

### Task 6: 持久产物索引与 CLI/无头工具接入

**Files:** Create `nailong/core/artifacts.py`, `tests/test_artifact_registry.py`, `tests/test_artifact_tools.py`; Modify `nailong/tools/execution.py:284`, `nailong/tools/registry.py:124`, `tools.py:119`, `agent.py:298`, `nailong/core/branches.py:80`, `nailong/core/sessions.py`, `nailong/mcp/client.py`（仅必要的资源链接保留）、`nailong/tools/results.py:176`。

**Interfaces:**
- Produces: `ArtifactRegistry(session_root: Path, project_root: Path)`；`register(*, origin: dict, kind: str, display_name: str, reference: dict, snapshot: bytes | None = None) -> dict`、`get(handle: str, *, visible_origins: set[tuple[str,str]]) -> dict`、`close() -> None`。
- Produces: `capture_artifact_snapshot(session: FileSession, path: str, *, api_key: str) -> dict`；最多读 256 KiB+1，完整快照有 digest/version，超限不保存部分内容冒充历史完整快照。
- Produces: `register_artifact(path: str, title: str = '') -> dict`、`read_artifact(handle: str, offset: int = 0, limit: int = 6000) -> dict`；后者 limit 1–6000 字符。
- Consumes: 原生 tool_result_id、父回合身份、TaskStore 当前 task_id；分叉可见来源由原生 checkpoint/不可变 node_history 边界取得，不由模型传入任意祖先线程列表。

- [ ] **Step 1: 写失败测试。** 重开数据库后句柄可读、同原生结果重复发布不重复、不同结果复用 call_id 不冲突；跨项目、无祖先线程、分叉点之后的父分支产物均拒绝。明确登记文本但不证明生成命令。
- [ ] **Step 2: 加发布竞态测试。** `test_file_changes_during_snapshot_publish_no_complete_snapshot` 在读取期间改文件，断言 `snapshot_status != 'complete'`；另测快照写失败、256 KiB+1、文件删除后历史读取、当前读权限撤销、远程 `/path` 不被读取。
- [ ] **Step 3: 验证 RED。** Run `python -m unittest discover -s tests -p 'test_artifact_*.py' -v`；缺少持久索引和模型工具时失败。
- [ ] **Step 4: 实现原子发布。** 私有 artifacts.sqlite 使用版本化表和唯一来源键；快照先原子落盘，再发布索引，失败返回 snapshot_unavailable。使用现有安全文件辅助，防止链接/Windows 重解析重定向；读取前复核记录来源对应的当前权限。关闭幂等，不后台删除活跃快照。
- [ ] **Step 5: 接入执行门和工具。** 原生成功写入自动登记；MCP 资源链接仅作外部元数据，structured_content 遍历深度最多 4，每结果最多登记 16 个引用，超过明确标记不完整；命令 stdout 不扫描路径。register_artifact 使用 FileSession 和当前读权限；read_artifact 在审批/模式与来源可见性通过后读取。新句柄作为 transport 元数据，不污染稳定业务结果 digest。
- [ ] **Step 6: 接入恢复、分叉与无头结果。** 不从旧无来源日志补造产物；只保留可核对的祖先原生来源；模型工具结果包含句柄及摘要，原有 /export 与历史查询继续工作。文件快照记录观察版本，后来文件改变不能改写原快照。
- [ ] **Step 7: 验证 GREEN。** 新测试及 test_tool_event_replay、test_gui_branches、test_history_archive、test_mcp_tools、test_headless、test_unrestricted_files；结果整体仍受归档上限，密钥不会进入快照/元数据。
- [ ] **Step 8: 提交。** 按实际 Files 提交；message: `feat: persist scoped artifact handles and immutable text snapshots`。

### Task 7: GUI 区分历史快照与当前文件

**Files:** Create `nailong/gui/artifacts.py`, `tests/test_gui_artifacts.py`, `tests/gui-artifacts.spec.cjs`; Modify `nailong/gui/controller.py:310`, `nailong/gui/presentation.py`, `nailong/gui/static/conversation.js:116`, `nailong/gui/static/app.js:504`, `tests/gui_browser_fixture.py`, `tests/gui-conversation.spec.cjs`, `docs/GUI.md`。

**Interfaces:**
- Produces: `artifact_view(controller, payload: dict) -> dict`，payload 只接受当前 project_id、node_id、handle、offset、limit、view（snapshot/current）；来源范围在服务端从 node 取得。
- Extends: GUI command `artifact`；artifact_cards(node) 保留旧卡片，新增 handle/kind/snapshot_status/source。没有句柄的旧文件卡片继续用原 file 命令。
- Consumes: Task 6 ArtifactRegistry.get 与原生祖先来源；不向前端下发密钥、任意可读路径列表或完整未脱敏输出。

- [ ] **Step 1: 写失败测试。** 原文件 A 生成快照后改为 B，历史视图断言 `assert content == 'A'`，当前视图断言 `assert content == 'B'`；删除源文件后历史仍明确是快照。切换项目/节点后迟到请求不能覆盖新 inspector。
- [ ] **Step 2: 验证 RED。** Run `python -m unittest discover -s tests -p 'test_gui_artifacts.py' -v`；现有 GUI 只读取当前文件，历史断言应失败。
- [ ] **Step 3: 接入 GUI。** 标题明确为“历史快照”或“当前文件”；snapshot_unavailable 不提供假历史按钮。文本/JSON 使用文本节点或现有安全渲染，不插入可执行 HTML；外部链接不自动 fetch，拒绝 javascript/data/file 等非允许协议。
- [ ] **Step 4: 加浏览器验收。** 新 spec 通过原生 Fake 模型与临时项目生成、登记、改写、重载并分叉，验证旧/新预览、来源边界、HTML 字符原样显示及旧卡片兼容；使用 GUI_TEST_ORIGIN，不访问真实项目。
- [ ] **Step 5: 验证 GREEN。** 新 Python 测试及 test_gui_controller、test_gui_native、test_gui_diffs；每组启动新的 `PYTHONPATH=. python tests/gui_browser_fixture.py` 后，分别运行 `node tests/gui-conversation.spec.cjs`、`node tests/gui-artifacts.spec.cjs`、`node tests/gui-map-live.spec.cjs`、`node tests/gui-trace.spec.cjs`。浏览器依赖按 docs/GUI.md 配置，每组结束关闭自己的 fixture。
- [ ] **Step 6: 提交。** 按 Files 列表提交；message: `feat: distinguish historical artifacts from current workspace files`。

### Task 8: 执行提供者接入命令和配置验证

**Files:** Create `nailong/core/execution_provider.py`, `tests/test_execution_provider.py`, `tests/test_execution_provider_runtime.py`; Modify `local_tools.py:384`, `nailong/core/verification.py:91`, `nailong/core/verification.py:280`, `nailong/tools/registry.py:277`, `nailong/tools/execution.py`, `agent.py:298`, `agent.py:750`, `ui/actions.py:244`, `nailong/gui/controller.py:752`, `nailong/core/harness_options.py`, `nailong/core/diagnostics.py`。

**Interfaces:**
- Produces: `ExecutionCapabilities(sandboxed: bool, filesystem_isolation: bool, network_isolation: bool, filesystem_mapping: bool)`、`ExecutionLease(project_root: Path, owner_id: str)`。
- Produces: `ExecutionProvider` 协议与 `LocalExecutionProvider(project_root: Path)`，方法为 `acquire(owner_id: str) -> ExecutionLease`、`execute(lease: ExecutionLease, invocation: str | list[str], *, timeout: float, expected_exit_code: int = 0) -> dict`、`async aexecute(lease: ExecutionLease, invocation: str | list[str], *, timeout: float, expected_exit_code: int = 0) -> dict`、`async observe(lease: ExecutionLease, command: str, *, timeout: float, stdout_contains: str | None = None, http_url: str | None = None, expected_status: int | None = None) -> dict`、`release(lease: ExecutionLease) -> None`、`async aclose() -> None`。
- Extends: `VerificationService.__init__(project_root, session_store, goal_store=None, *, api_key='', task_store=None, execution_provider: ExecutionProvider | None = None)`；缺省本机实例只在步骤执行时获取租约，list_steps 不获取租约。
- Produces: `use_execution_provider(provider, lease) -> ContextManager`；仅原生运行时绑定。无原生绑定的低层 local_tools 调用创建短期 local 租约，保持兼容。
- Extends HarnessOptions: `execution.provider='local'`，未知值立即报错；可替换提供者只通过受信构造参数注入，不能由模型提供 Python import 路径。

- [ ] **Step 1: 写失败测试。** 注入记录 acquire/execute/observe/release 的假提供者，验证 run_command 与 /verify 使用同一实例；`test_cli_gui_goal_verify_share_factory_provider` 分别走 ui/actions、GUI verify 和 verify_goal_if_configured 三个执行入口，不能只测构造器。拒绝审批时所有调用数为零，关闭后旧 lease 不可使用。
- [ ] **Step 2: 加清理竞态用例。** `test_repeated_cancel_and_close_release_after_cleanup_once` 使用受控 cleanup 事件，先断言 `assert release_count == 0`，完成清理后断言 `assert release_count == 1`；另测取消、超时、未知后端不能回退主机以及项目切换。
- [ ] **Step 3: 验证 RED。** Run `python -m unittest discover -s tests -p 'test_execution_provider*.py' -v`；现有命令和 verify 直接执行，提供者记录应未被调用。
- [ ] **Step 4: 抽取本机委托路径。** 现有 local_tools 内部低层 capture 函数保留为不回调 provider 的叶子函数；LocalExecutionProvider 委托它们及 processes.execute_process，避免 wrapper 递归。保留 shell/argv、输出归档、Windows OwnedProcess 和取消清理的现有语义。
- [ ] **Step 5: 接入所有执行生命周期。** 命令完成审批/复查后 acquire；VerificationService 新增 execution_provider 注入，ui/actions、GUIController 和 verify_goal_if_configured 三个执行入口都传递工厂拥有的同一提供者。只读 list_steps 调用无需注入或获取租约。普通步骤、运行观察与中断都清理后 release；项目锁仍在外层。release 只能移除已完成进程的租约，aclose 先 join 活跃清理再幂等结束。
- [ ] **Step 6: 展示真实能力。** 默认 LocalExecutionProvider 的前三项隔离能力均 false；diagnostics 与运行元数据展示命令执行能力，不能把 Electron sandbox 当作命令隔离。没有 filesystem_mapping 的远程注入提供者在文件工具与 cwd 不一致时拒绝启用。
- [ ] **Step 7: 验证 GREEN。** 新测试及 test_command_argv、test_argv_tool_integration、test_verification_cancellation、test_native_processes、test_gui_shutdown、test_approval_cancellation、test_stage_constraints；平台相关测试在对应平台运行，跳过不能声称该平台通过。
- [ ] **Step 8: 提交。** 按实际 Files 提交；message: `refactor: route commands and verification through execution providers`。

### Task 9: 整体回归、可重复比较与交付说明

**Files:** Create `scripts/benchmark_harness_optimizations.py`, `tests/test_harness_optimization_benchmark.py`, `docs/reviews/2026-10-16-deerflow-harness-acceptance.md`; Modify `README.md`, `docs/PROJECT.md`, `docs/GUI.md`、本计划的执行状态。

**Interfaces:**
- Produces: 离线命令 `python scripts/benchmark_harness_optimizations.py --output <new-directory>`，输出 manifest.json、cases.jsonl、summary.json；输出目录必须新建，已有目录拒绝覆盖。
- Produces: 显式真实模式 `--live --model <id> --base-url <url> --api-key-env IGNOVATE_BENCHMARK_API_KEY --max-cost-usd <positive-budget>`。仅读取指定环境变量；未提供实时计价/预算保护时请求前失败，不继承 bootstrap 或 .env 密钥。该模式本轮默认不运行。

- [ ] **Step 1: 写失败测试。** 默认模式禁止网络，固定 0/1/20/100 个 MCP 工具、含中间字段的大 JSON、带换行 CSV、64/65 条证据、多个排队子请求。断言报告包含源码 revision/dirty、依赖版本、配置、固定 seed、请求数和完整性，未知费用/usage 用 null，不能用零替代。
- [ ] **Step 2: 验证 RED。** Run `python -m unittest discover -s tests -p 'test_harness_optimization_benchmark.py' -v`；缺少评测入口及报告契约时失败。
- [ ] **Step 3: 实现离线比较。** 比较完整 schema 与按需 schema 的真实构造请求估算；检查摘要可发现内容和子代理原生引用核对；记录排队取消后的实际请求数。使用本次生产路径而非复制一套简化算法。真实模式重用原生工厂/预算，并显式标注未经真实运行的结果。
- [ ] **Step 4: 验证 GREEN 与全量回归。** 新测试通过后，在隔离工作区执行 `python scripts/run_native_tests.py`，保存退出码和完整日志；运行 `node --test tests/gui-map.test.mjs tests/gui-control-map.test.mjs tests/gui-diff.test.mjs` 以及 Task 7 浏览器验收。任何新修复后重跑覆盖修复的测试，再确认全量结果仍对应最终源码。
- [ ] **Step 5: 构建交付检查。** 执行 `python -m pip wheel --no-deps --no-build-isolation . --wheel-dir /tmp/ignovate-deerflow-wheel`；用 zipfile 检查文件职责表中的十个新增生产模块及 GUI 资源进入 wheel，在源码目录外临时环境安装并运行 `ignovate --help` 与 `ignovate doctor --output-format json`。doctor 未配置连接时退出 1 属于预期，不能误报安装失败。
- [ ] **Step 6: 记录限制与操作。** 文档给出 MCP 发现关闭方法、generic adapter 示例、准入限流示例、产物显式登记/读取、local 能力；验收报告逐项列实现与证据、跳过/未运行平台和真实模型评测。不能声称已有容器隔离、可写代理或已证明真实成功率/账单下降。
- [ ] **Step 7: 整体独立审查与修复。** 按 requesting-code-review 技能提供设计、计划、准确 diff 和测试结果；审查重点包括 Review Focus 五类、身份边界、模型账务和跨阶段接口。修复实质问题后运行对应回归；不把审查文本代替测试。
- [ ] **Step 8: 提交及交付。** 按实际 Files 提交；message: `test: verify six harness improvements and document operating limits`。报告六项交付、测试命令/结果、未运行项目及最终 commit；集成回主工作区按 finishing-a-development-branch 流程处理，不覆盖原先用户改动。

## 规格覆盖与计划自审

| 规格章节 | 实施任务 | 完成所需证据 |
| --- | --- | --- |
| §3 共享约束 | 2、3、5、6、8、9 | 权限/身份/预算/取消和兼容回归 |
| §4 MCP 发现 | 2 | 实际 bind_tools、审批、恢复/分叉及窗口核算 |
| §5 子代理证据 | 3 | 并行原生证据、引用核对、预算结算 |
| §6 结构摘要 | 1 | JSON/CSV、上限、回退与归档完整性 |
| §7.1 模型工厂 | 4 | 主/子/切换共用入口及 wire 请求 |
| §7.2–7.3 准入与重试 | 5 | FIFO、状态重核、逐次结算及流式不重放 |
| §8 产物 | 6、7 | 原生来源、重启/分叉、快照与当前视图 |
| §9 执行后端 | 8 | 普通命令与 /verify 真正接入、清理与能力声明 |
| §10–11 兼容和验证 | 所有任务、9 | 离线全量、浏览器、wheel 和受限评测报告 |

自审结论：所有六项均有生产接入和针对性回归，未将提供者接口或 Fake 测试单独视为完成。跨任务名称采用上述 Interfaces；Task 5/8 只扩展 Task 2 的配置结构。实施期间发现不能满足规格的库接口差异时先记录具体冲突，不默默缩减验收条件。

执行方式建议：Native。本计划多处顺序修改 agent.py、ToolExecutionContext 和上下文/账务链，同一实现者持续维护接口最直接；完成后安排一次独立整体审查。若选择子代理执行，各任务仍按依赖顺序实施并逐项审查，不能并行编辑共享文件。
