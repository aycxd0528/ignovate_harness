# Task 4：工具执行边界交接

状态：2026-10-03 阶段完成实现与静态自审，当时未获准行为验证。2026-10-04 新授权后的本地验证、实际 async 入口修复和旧测试迁移，见 [运行验证交接](2026-10-04-runtime-tools-verification.md)：最新三个授权旧测试文件与 managed tools 专项合计 80/80 通过，全仓未经本轮重跑，不代表目标验收。

## 总控接线接口

新增 `nailong/tools/execution.py`：

```python
ToolExecutionContext(
    project_root,
    *,
    permission_engine=None,
    permission_mode="default",
    approval_handler=None,
    pre_tool_hook=None,
    post_tool_hook=None,
    observer=None,
    api_key="",
    artifact_directory=None,
)
```

`build_tools(..., execution_context=None)` 为新可选参数；旧构建入口不需要新增必填参数。默认创建当前项目的 PermissionEngine，deny 始终在 handler 前拒绝；ask 没有 approval_handler 时返回结构化审批未完成错误，零执行。构建默认可用不代表默认授予写权限。

回调参数及返回：

- `approval_handler(action: dict, permission: PermissionResult)`：返回 `ApprovalDecision`、`{"type":"approve"|"reject","message":...}` 或相同含义的字符串；异步执行支持 awaitable，同步执行不隐式启动事件循环。无答案、未知决定按拒绝处理。
- `pre_tool_hook(action: dict)`：返回 None 或 `{blocked: bool, reason: str}`；也支持既有 HookResult。async 支持 awaitable；阻止则 handler 零执行。
- `post_tool_hook(action: dict, result: dict)`：操作结束后的观察钩子；失败不重放已执行操作。
- `observer(observation: dict)`：接收脱敏、有界执行观察，包含工具名、call_id、thread_id（均来自 config）、status、paths、coverage、summary、artifact_ref、input_fingerprint、started。`thread_id` 取 `config.configurable.thread_id`，缺失为 `""`。`started` 由执行入口标记，handler 前的拒绝/前置异常为 False，不采用 handler 自报值。观察回调失败只标记 `observation_incomplete`，不重放副作用，也不提升验证状态。

总控目前只将 interrupted 观察写入 Evidence v1；其余终态由 ToolMessage 统一记录，避免双记录。将观察或 ToolMessage 转为证据时仍需关联当前 task revision，不把普通工具成功提升为用户验收。

`action` 明确包含 **`profile`**，来自已注册工具适配器，不接收模型覆盖；审批和 pre/post 回调之前已经存在。plan/review/subagent 的 shell 生命周期钩子应由总控按此字段跳过。子代理 ASK 在调用 approval_handler 之前返回 `approval_required`，不会自行请求审批；候选搜索文件的 ASK 也跳过并标记覆盖不完整，不能以目录授权代替单文件授权。

用 LangGraph interrupt 桥接审批：

```python
from langgraph.types import interrupt

def approve_tool(action, permission):
    resumed = interrupt({
        "action_requests": [action],
        "review_configs": [{"action_name": action["name"],
                            "allowed_decisions": ["approve", "reject"]}],
    })
    return (resumed.get("decisions") or [{}])[0]

execution = ToolExecutionContext(
    root, permission_engine=service.permission_engine,
    permission_mode=service.permission_mode,
    approval_handler=approve_tool,
    api_key=settings.api_key,
    artifact_directory=session_store.root / "tool-artifacts",
)
tools = build_tools(..., execution_context=execution)
```

**重要接线要求：**启用本执行入口后，`agent.py` 不再为这些工具注册旧 HumanInTheLoopMiddleware，避免双重审批。AgentService 已有 action_requests/decisions 恢复协议可保留；不要在仅用于 UI 的 tool_start 事件中再次执行 handler。向所有 sync/async Agent 及只读子代理传递相同权限引擎；子代理允许规则仍受只读 profile 硬限制。执行上下文/协调器按项目或会话长期复用，不按每轮重建。

执行入口会缓存待审批文件版本，按 thread_id、tool_call_id、工具名和参数摘要绑定；GraphInterrupt 恢复不得重建执行上下文。工具适配器将实际 ToolCall ID 放入 config.metadata；如果外部调用器丢弃 config/ID，异步原地等待仍绑定局部版本，但无法在重建节点时沿用这份缓存。进程重启后的已开始命令必须由总控恢复策略核对，不能因为重新构图而自动重放；本模块没有伪装成已实现持久命令幂等账本。

### 动态工具元数据

旧 PermissionEngine 使用 chat 的静态工具快照；可选注册的 `read_history_result`、`read_memory` 可能不在该快照。建议总控为 `decide_action` 增加可选 `tool_spec=None` 并用 `spec = tool_spec or _TOOL_SPECS.get(name)` 判断实际 read_only 元数据，保留 deny/ask 与硬边界优先级。本执行入口已检测并自动传入这个可选参数；旧签名仍能调用，但动态只读工具会安全拒绝或要求审批，不会越权执行。

### 子代理配置与观察归属

新增 `current_tool_execution: ContextVar[dict | None]`（execution.py），在 handler 执行期间返回 `{context, profile, thread_id}`。总控可在 `_run_readonly_subagent` 读取它，继承同一执行上下文，并在子图 config.configurable 中传入父 thread_id，保证观察归属；不能把父 profile 传给子工具，`build_tools(profile='subagent')` 仍固定只读边界。

## 文件与变更协调

`FileSession` 负责已有文件完整读取区间、摘要和原子覆盖；`write_file` 通过它执行，不再直接覆盖底层文件。审批请求中绑定文件版本；等待期间变化返回冲突，不自动重放。同步/异步命令、edit/write 使用同一个变更协调器，异步等待不持有线程 RLock。

协调器按实际项目根目录复用，覆盖不同 FileSession/会话；同步入口可重入，异步入口用不阻塞事件循环的可取消等待。异步取消不能取消已在工作线程开始的写入，因此执行入口会等待该线程结束并报告 interrupted/需要核对，不假装零副作用。命令继续复用 core.processes.execute_process 的取消及进程组清理。

## 修改文件与其他契约

- tools.py：ExecutionTool；sync/async 同一执行入口；ToolException 和参数错误产生 error 状态，保留旧 JSON 对象字段。
- execution.py：权限、审批、前置规则复核、待审批版本、回调和脱敏观察；子代理审批零执行拒绝。
- coordination.py：跨线程/协程的项目变更协调及执行期间的版本/候选文件权限上下文。
- schemas.py：注册表与 LangChain 共用 Pydantic 参数模型；严格类型、额外字段拒绝、枚举/范围/长度限制。保留现有模型输入默认值；超界不再静默夹紧。
- files.py：完整读取区间合并、摘要/路径/inode 冲突检查、原子新建和覆盖；候选文件逐项权限过滤；搜索后端及跳过/截断信息；grep 上下文行最多 500 字符，rg 捕获最多 2 MB。
- registry.py：上述能力接线；目标证据记录与命令执行处于同一变更临界区；task 返回对象及明确失败；新增 `read_tool_result`，仅按当前上下文的结果引用分页读取。
- results.py：完整 JSON 返回上限 16,000 字符；大结果受限归档和分页。单条归档最多 1,000,000 字符，每上下文最多 128 条/20,000,000 字符；归档不完整或不可用会显式标记；文件 0600、目录 0700。分页的 coverage 仅在返回完整归档时为 complete；新增有界 `HeadTailBuffer` 可供命令服务复用。
- local_tools.py：项目根目录改用 ContextVar，避免不同项目线程改写全局根目录；完整覆盖必须传入已有读取会话；字面量搜索复用相同覆盖契约；同步命令捕获保留首尾日志，并显式标记中间截断。

模型读取输出若又被总 JSON 预算截断，会撤销该文件的完整读取记录；不能把未实际返回的文件内容当作已读取。结果归档是历史证据，不自动赋予当前文件的完整读取资格。

`read_file` 新增安全元字段 `content_offset`：取实际 `selected_start`，表示本页 `content` 在完整 UTF-8 解码字符序列中的从 0 开始的绝对字符位置，区别于首行内的 `char_offset`。空文件返回 0；非空文件未选择任何内容返回 None。旧输入、分页游标和其他输出字段保持不变。总控可按同一 `version` 下实际模型请求消费的 ToolMessage 页区间判断静态 review 覆盖，不能将 `read_complete` 或历史归档当作 reviewed。若 ToolMessage 被整体预算裁剪、摘要化或脱敏改变了内容，不应按其字符串长度推定完整原文区间；整体裁剪时此字段不会被当作可靠页元数据保留。

`grep` 新可选输入 `pattern_mode='regex'|'literal'` 默认 regex；没有 rg 时返回明确错误，不改变正则语义。search_text 保留字面量行为并报告 skipped_files/scanned_files/coverage。旧依赖缺少 rg 时字面量回退的测试预期需在未来获准验证时评估，不通过放宽断言掩盖保护。

## 静态自审与剩余集成

已对全部八个改动/新增 Python 文件做 AST 语法解析，并人工检查 import 依赖、sync/async 入口、拒绝和子代理审批分支、GraphBubbleUp 的传播、版本缓存与覆盖区间、取消后线程收尾、协调器释放、归档与 schema 的输入/输出边界。此为静态核对，不是行为验证。

总控已反馈：execution_context 已接入并移除旧 HITL 双重审批；按可信 action.profile 跳过 plan/review/subagent 的全部 shell 生命周期钩子；只存 interrupted 观察，避免和 ToolMessage 双记录。本执行层已补齐观察 thread_id，并确保只读子代理 ASK 不调用审批 handler。

需要总控完成/确认：动态 tool_spec 权限元数据；子代理 config.configurable 的父会话归属（仅共享 execution_context 不够）；Evidence v1 关联任务版本和完整项目验证指纹；重启时对已开始命令的恢复核对。本模块没有将单文件摘要提升为项目验证指纹，command coverage 仍可为 unknown。

命令时限仍为已声明的 30 秒，本轮没有开放后台进程或改动总控所有的 core.processes/VerificationService。同步 local_tools 已使用首尾缓冲；异步 core.processes 既有命令捕获仍只保留日志前缀。总控可复用 `nailong.tools.results.HeadTailBuffer` 或接入受限输出 sink。不要把 JSON 归档的 artifact_complete 解释为整个子进程日志完整。

文件写入在审批后、获取变更锁后、原子替换前检查内容和路径版本；新建使用无覆盖的原子硬链接。项目内工具已串行协调，但它不是操作系统文件沙箱：外部进程在最后检查与替换之间的极短竞态不能被当前契约完全消除，后续若要更强边界应使用目录描述符和操作系统隔离。

未新增测试，未运行测试、复现脚本、项目模块导入或真实 API。早先审查中的实测发生在本轮禁止测试前，不代表这轮实现已通过回归。没有提交/推送或改动共享文件。

## 范围与验证约束

只修改 tools.py、nailong/tools/registry.py、nailong/tools/files.py、local_tools.py 及新增工具参数/执行模块。本轮不新增/运行测试、不调用真实 API、不提交推送、不修改 agent/service/UI/memory/context/goal。代码自审和语法/接口静态核对不能宣称行为验证通过。

Ruling：本轮以用户及 spec 明确的验证限制替代 executing-plans/TDD 中运行测试和提交的步骤；交接文档作为 Task 4 的独立实施记录，不修改总控的共享 ledger。
