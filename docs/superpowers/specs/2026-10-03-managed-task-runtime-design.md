# 奶龙：任务连续性、证据与停止条件

状态：实施中。用户已要求按前述分析调度现有四个对话完成任务；此前授权后续计划自审后直接执行。使用现有共享工作区，保留全部已有修改，不提交或推送。

## 目标与本轮范围

在现有 LangGraph、ProjectSessionStore、GoalStore、ContextManagerMiddleware 和 MemoryStore 上增加可用的任务闭环。工程任务的状态独立保存；任务快照进入模型请求；工具执行满足权限和版本条件；交付状态来自实际证据；持续重复且无新结果时纠偏并有界停止。

本轮完成任务状态及恢复、上下文投影、记忆来源与有效性、三个已确认的工具 P1 及必要契约修复、结构化交付状态。普通问答继续直接回答。自动知识晋升、向量索引及复杂多 Agent 编排留给后续独立评估。

## 唯一共享接口

各组件消费普通 JSON-compatible dict，不能假定新的 ORM 或第三方依赖。

### Task snapshot v1

- `schema_version`: 1。
- `task_id`, `thread_id`, `project_root`: 身份；跨项目或会话拒绝混用。
- `objective`: 目标；`scope`: 项目内相对路径列表；`constraints`: 用户约束列表。
- `revision`: >=1 的需求版本；`latest_request`: 当前用户输入。新要求不能被旧快照覆盖。
- `lifecycle`: `active|paused|blocked|completed`。
- `phase`: `investigate|implement|verify|deliver`，与生命周期分开。
- `steps`: `{id,title,state,dependencies}` 列表；state 为 `todo|doing|done|blocked`。
- `acceptance`: `{id,description,kind,required,status,evidence_ids}` 列表；kind 为 `static|test|build|run|review|manual`；status 为 `pending|passed|failed|stale|waived`。waived 必须记录用户决定，不能由模型自行产生。
- `changed_paths`: 修改路径；`pending_verification`: 待验证说明列表。
- `evidence`: 以下 Evidence v1 列表。
- `progress`: 简短进展；`blockers`: 阻塞列表。

投影可以选择有界字段，但不得静默丢弃有效约束或验收项；超过保护预算必须明确报告或停止。显示时间、随机请求 ID、计数抖动不进入模型快照正文。

### Evidence v1

`{id,kind,source,status,task_revision,paths,input_fingerprint,coverage,summary,artifact_ref}`。

- kind 为 `read|edit|test|build|run|review|manual`。
- source 为 `runtime|user|model`。model 来源不能作为已验证成功证据。
- status 为 `passed|failed|denied|interrupted|unknown`。
- coverage 为 `complete|partial|unknown`，范围不能从退出码推断。
- input_fingerprint 及任务版本过期的证据不可用于宣称当前结果已验证。
- 不保存原始 shell 参数、密钥或完整工具正文；正文使用现有受限归档引用。

### 接口归属

总控新增 `nailong/core/task_state.py` 和 `nailong/core/progress.py`，提供 `TaskStore.snapshot(thread_id) -> dict | None`；存储位于现有项目会话数据目录，私有原子写入，带版本校验和恢复验证。

context 提供 `render_task_context(snapshot: dict | None, max_chars: int = 12000) -> str`，文件 `nailong/core/task_context.py`；可为 ContextManagerMiddleware 增加可选 `task_snapshot_provider`，默认 None。总控完成 agent.py 接线。

memory 保持 `MemoryStore.catalog/read_document` 和现有快照 API；在文档元数据与读取结果增加来源/有效性字段，不改变原有必需字段和无元数据文档的行为。接线建议写入交接文件，由总控整合共享注册表。

output 提供 `build_delivery_report(snapshot: dict | None, *, current_input_fingerprint: str | None = None) -> dict` 和 `render_delivery_report(report: dict) -> str`，文件 `nailong/core/delivery.py`。至少返回 `status, satisfied, pending, failed, stale, reasons`；status 为 `verified|unverified|failed|blocked|reviewed`。验证执行成功与验收覆盖分开；未知摘要不能当作匹配。总控负责服务及各界面接线。

tool use 拥有 tools.py、nailong/tools/registry.py、nailong/tools/files.py、local_tools.py 及新增工具执行/参数模块；若需 agent.py/service 接线，交付具体可选参数和接线片段，不直接改这些共享文件。

## 分工与共享文件纪律

1. context：context_runtime.py、task_context.py、compact.py、context.py、history_archive.py 及专属文档。
2. memory：memory.py、memory_context.py、memory_selection.py、新记忆模块及专属文档。
3. tool use：以上工具所有权；执行权限、已有文件完整覆盖快照及异步变更协调优先。
4. output：delivery.py、新交付模块及专属文档；允许只读检查其他组件，不修改 agent.py 或 ui/*。
5. 总控 Agent：TaskStore、ProgressMonitor、agent.py、agent_service.py、goal.py、runner.py、headless.py、ui/*、README 与集成文档。

同一文件由单一所有者编辑；接口变化写入交接文档，由总控协调，不要求其他对话自动回信。禁止 checkout/reset/stash/clean 用户工作区，禁止未经授权的提交/推送。不得读取或输出 .env 密钥。

## 行为边界

- 运行时事实决定状态；模型结论只作为提案，不提升为验证事实。
- 当前用户输入具有有效性；任务快照不能扩大授权或阻止用户改变要求。
- 拒绝后零执行；受保护路径不可由规则放行；命令审批仍不是操作系统沙箱。
- 审批等待期间输入变化必须复核；已启动的命令中断不能假装从未执行。
- 重启不盲目重放有副作用的操作；未知执行结果先核实。
- /rewind 保持对话回退语义，恢复时重新核对磁盘和证据。
- 重复信号按阶段解释；正常调查、分页、轮询和代码变化后的重新读取不可误停。
- 没有足够证据允许交付未验证结果；不能强制绕过用户拒绝的测试或工具调用。

## 本轮验收与验证约束

可观察场景：任务跨进程恢复；连续压缩后保持约束；更新需求后旧证据失效；普通聊天不串目标；只读 deny/ask 执行前生效；整文件覆盖拒绝部分读取和过期版本；同步/异步变更不交叠；记忆来源变化可见；失败/拒绝/截断一致显示；无证据不能提升 verified；重复相同工具和结果触发提示及有界停止。

2026-10-03 初始实施阶段只有代码自审和静态核对，没有新增或运行测试/API，也不能引用旧轮测试作本轮证据。2026-10-04 用户设置完整目标并明确要求按当前状态审查和验证，进入实际验证阶段，按模块增加本地回归并执行。保留全部既有测试，不因接口变化删除保护；真实 API 沿用此前用户明确冒烟授权，以预算和临时项目控制验证范围。

## 自审

范围与所有权明确；组件使用同一 snapshot/evidence 契约；已有入口默认参数兼容；当前模型、依赖和存储可复用；未知覆盖和过期信息不等于成功；未授权测试不执行。
