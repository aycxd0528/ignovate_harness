# 奶龙 Agent 分层架构自检

日期：2026-10-02

对照：用户提供的「KamaClaude: mini Claude Code Agent 分层模块图」。

本文保留修复前的自检记录；2026-10-02 的修复和新验证结果见文末「修复记录」。

范围：当前工作区，包括未跟踪源码；检查架构能力、现有回归用例和近期上下文、用量、命令控制器改动。生产代码未修改。所有运行探针均使用本地模拟模型和临时数据目录，没有访问真实模型 API。

## 结论

当前项目已具备本地编码 Agent 的主要能力：多种终端入口、模型与工具循环、审批、持久会话、计划与目标、上下文压缩、Skills 和只读子代理。但它是客户端与执行引擎同进程的程序，尚不是图中的独立 daemon 系统。

本轮发现 7 个可复现的行为问题，主要集中在回退、费用结算、预算和目标完成证据。现有回归尚未全部通过，不能据此宣布 v1 发布验收完成。

MCP、操作系统沙箱和可写的多角色子代理不属于已确定的 v1 交付；daemon、TCP/RPC 也不能仅因出现在参考图里就视为当前产品的必修缺陷。

## 七层对照

| 图中层次 | 当前状态 | 已有实现 | 与图片的差异 |
| --- | --- | --- | --- |
| 入口层 | 主体已覆盖 | `main.py` 的 CLI、plain/inline/Textual、`--print`，chat/slash/plan/goal | 没有独立 `trace` 命令与轨迹查看器；部分本地命令动作仍分布在各界面 |
| 协议层 | 部分覆盖 | 进程内 `TurnEvent` 事件流；headless 的 stdout 逐行 JSON | stdout NDJSON 不等于 TCP 传输；没有 JSON-RPC 2.0、socket 服务或 topic/glob 订阅 |
| 运行时核心层 | 部分覆盖 | `AgentRuntimeFactory` 组装运行时，`AgentService` 编排执行，`ProjectSessionStore`/`GoalStore` 持久化 | 没有独立 daemon、SocketServer 或持久 run 生命周期；客户端退出会终止当前进程执行。可恢复检查点不等于后台持续运行 |
| Agent 能力层 | 主体已覆盖 | LangGraph 模型/工具循环，DeepSeek 流式与重试，`ToolSpec` 注册表，读取/搜索/精确编辑/命令 | 模型提供商尚未抽象；提示缓存依赖提供商；`task` 是子代理调研工具，没有 `task_create/update/list` 任务树管理 |
| 治理与记忆层 | 部分覆盖，有缺陷 | StructuredTool/Pydantic 参数校验，项目边界、受保护路径、权限规则，SQLite 检查点，脱敏 JSONL，固定记忆、压缩 | JSONL 是脱敏事件流，并非完整消息回放；没有自主 `notes.md` 事实记忆；回退与预算存在下述问题 |
| 扩展生态层 | 部分覆盖 | 本地 Skills 按需加载、资源读取、Markdown 命令模板、hooks、最多 3 个并发只读子代理与隔离上下文 | Skill 本体未解析工具白名单；白名单目前属于命令模板。没有可写 planner/executor/reviewer 团队、MCP Client/Servers |
| 结果与证据层 | 部分覆盖 | 回答/文件、TUI token/tool/approval，脱敏事件、检查点、目标验证记录、headless 结果 | 没有持久 run_id 关联的事件目录和 `trace.ndjson` 时间线；费用记录会随回退删除，验证证据也缺少完整会话绑定 |

### 代码依据

- 入口：[main.py](/Users/aycxd/Desktop/LLM_project/main.py:573)。无头事件与结果：[headless.py](/Users/aycxd/Desktop/LLM_project/headless.py:150)。
- 执行引擎：[agent.py](/Users/aycxd/Desktop/LLM_project/agent.py:111)、[agent_service.py](/Users/aycxd/Desktop/LLM_project/agent_service.py:577)。
- 工具注册及模式收窄：[registry.py](/Users/aycxd/Desktop/LLM_project/nailong/tools/registry.py:220)。参数适配：[tools.py](/Users/aycxd/Desktop/LLM_project/tools.py:59)。
- 权限边界先于授权规则：[permissions.py](/Users/aycxd/Desktop/LLM_project/nailong/core/permissions.py:157)。
- SQLite 与会话事件：[sessions.py](/Users/aycxd/Desktop/LLM_project/nailong/core/sessions.py:62)。固定记忆：[memory.py](/Users/aycxd/Desktop/LLM_project/nailong/core/memory.py:16)。
- Skills：[skills.py](/Users/aycxd/Desktop/LLM_project/nailong/core/skills.py:31)。命令白名单：[commands.py](/Users/aycxd/Desktop/LLM_project/nailong/core/commands.py:80)。子代理：[agents.py](/Users/aycxd/Desktop/LLM_project/nailong/tools/agents.py:19)。
- 目前显示用的 run 标识：[presentation.py](/Users/aycxd/Desktop/LLM_project/ui/presentation.py:196)。持久事件记录只有 thread_id，见 `sessions.py:112`。

## 可复现问题

### 1. [P1] 回退边界超过 100 条消息时误删旧对话

位置：[sessions.py](/Users/aycxd/Desktop/LLM_project/nailong/core/sessions.py:242)、[agent_service.py](/Users/aycxd/Desktop/LLM_project/agent_service.py:961)。

`turn_start` 保存本轮开始前的全部 `message_ids`，但通用日志清洗将任何列表限制为 100 项。`rewind` 把截断后的列表当成完整保留集合，导致此前第 101 条及其后的消息也被删除。

本地真实工厂/检查点探针：51 轮产生 102 条历史消息，再进行第 52 轮。回退应只删除第 52 轮的两条消息，实际删除 4 条，只保留 100 条；上一轮的两条消息也丢失。

建议：回退边界采用完整状态标识或检查点标识；状态数据与展示日志使用不同的截断规则。

### 2. [P1] 目标在本轮暂停后漏记费用与轮数

位置：[goal.py](/Users/aycxd/Desktop/LLM_project/nailong/core/goal.py:305)。

模型通过 `update_goal(state="paused")` 改变目标状态后，`record_round` 因不接收 paused 状态直接返回。本轮已经发生的模型调用费用和轮数没有累计，恢复目标后仍可使用这部分预算。

真实 AgentRuntimeFactory + 本地模拟模型探针：模型暂停目标并返回最终回答，两次调用的事件费用合计 `$0.000084`；目标已暂停，但持久记录仍为 `round=0`、`spent_usd=0`。

建议：已开始轮次的结算独立于结束状态，允许对 paused 目标结算并保留暂停原因；使用轮次标识保证不会重复入账。

### 3. [P1] 取消目标绕过结算，目标仍显示 active

位置：[flows.py](/Users/aycxd/Desktop/LLM_project/ui/flows.py:173)、[headless.py](/Users/aycxd/Desktop/LLM_project/headless.py:219)。

轮次流程只捕获 `Exception`。Python 3.11 的 `asyncio.CancelledError` 属于 `BaseException`，取消会直接跳过后续轮次入账。界面退出、Runner 中断或任务取消都可能触发；取消中的子代理也可能丢失已经观察到的 usage。

真实工厂探针：在收到已完成模型调用的 usage 后取消，事件费用为 `$0.000042`，持久目标仍为 `active/round=0/spent_usd=0`。

建议：在取消向上传播前结算已发生的调用并暂停目标；子代理也在清理路径中提交已有用量。清理完成后继续传播取消，避免吞掉取消信号。

### 4. [P2] 子代理 token 预算只在结束后扣减

位置：[agents.py](/Users/aycxd/Desktop/LLM_project/nailong/tools/agents.py:44)、[agent.py](/Users/aycxd/Desktop/LLM_project/agent.py:172)。

入口仅按短描述长度和 4,000 输出 tokens 预留整项子任务。子代理可继续多轮模型与文件读取，期间没有每次调用的 token 额度检查，最后才扣实际 usage。目标模式另有美元预算，但普通聊天没有这个补充护栏。

真实工厂 + 模拟模型探针：两次只读文件调用后返回最终回答，累计 41,030 tokens；配置上限为 30,000，任务仍完整完成，剩余额度为 `-11,030`。

建议：对子代理的每次模型调用预留输入与输出额度，限制输出上限，返回后结算；不足时在下一次调用前终止。

### 5. [P2] `/rewind` 删除已经发生的用量记录

位置：[agent_service.py](/Users/aycxd/Desktop/LLM_project/agent_service.py:973)、[controller.py](/Users/aycxd/Desktop/LLM_project/ui/controller.py:102)。

回退截掉本轮所有事件，包括 `usage`；`/cost` 从剩下的事件累加，导致费用减少或归零。回退对话不能撤销已完成的模型调用，也不能撤销其费用。

上述 52 轮探针：回退前累计 5,720 tokens，回退后只统计 5,610 tokens；实际调用并未撤销。

建议：将用量、费用和真实操作证据放入不随对话回退的账本；对话显示事件单独回退。

### 6. [P2] 换会话恢复目标时沿用旧会话验证证据

位置：[goal.py](/Users/aycxd/Desktop/LLM_project/nailong/core/goal.py:154)、[goal.py](/Users/aycxd/Desktop/LLM_project/nailong/core/goal.py:173)。

目标重新绑定 thread_id 时保留 `verification_*`。当前工具与拒绝消息要求在当前目标会话实际验证，但新会话能够使用旧会话证据直接完成。

GoalStore 临时数据探针：A 会话写入模拟成功验证记录并暂停，B 会话恢复后没有验证，`update(state="complete")` 仍获准。

建议：为验证记录保存会话、执行轮次和被验证状态；重新绑定会话时清除或明确重新核验旧证据。如果产品允许跨会话沿用，必须相应改变当前约定，并确保暂停期间的变更使证据失效。

### 7. [P2] 已耗尽轮数的目标仍可恢复并继续调用

位置：[goal.py](/Users/aycxd/Desktop/LLM_project/nailong/core/goal.py:159)、[flows.py](/Users/aycxd/Desktop/LLM_project/ui/flows.py:129)。

`resume` 未检查已达到的轮数上限，流程又在轮次结束后才检查上限。每次恢复都可额外执行一轮。

模拟服务 + 真实 GoalStore 探针：`max_rounds=1` 的目标第一轮后因轮数上限暂停，恢复获准，继续执行后变成 `round=2`。

建议：恢复和每轮开始前检查剩余轮数；如需提高限额，应通过明确的预算变更操作完成。

## 验证结果与限制

### 现有回归

运行 `.venv/bin/python -m unittest discover -s tests -v`，发现 TUI 流式测试因旧替身未接受新参数而无限等待。中断后，使用不修改测试文件的 runner，仅给该用例加 10 秒超时，完成全部现有用例：

- 248 个测试，31.987 秒。
- 237 个通过，4 个失败、7 个报错，其中 1 个为等待超时。
- `test_agent`、`test_agent_service`、`test_cli`、`test_headless`、文件/权限/会话/Skills 等现有测试通过。
- 7 个 TUI 用例因旧 `FakeService`/`FakeStreamingService` 未接受新增 `allowed_tools/history_display` 参数而失败或等待。生产 `AgentService` 接口有这些参数，不能据此断言真实聊天无法运行。
- 2 个目标流程测试的价格替身只有 `estimate`，缺少新预算代码所需的 `available` 等接口。
- 1 个 TUI 目标测试仍 Mock `tui.CostEstimator`，但目标创建现已使用 `ui.controller.CostEstimator`；它使用的 `deepseek-chat` 未配置价格。
- 1 个 `/count` 测试仍断言旧的“已发起”语义，与现在“本次运行已完成”语义不一致。

这些测试需要同步新契约并保留原有行为断言；不能通过删除断言来获得全绿。新增的调用前预算、取消结算和长会话回退，目前没有足够回归覆盖。

日志：[本轮完整测试输出](/tmp/nailong-self-check-bounded-20261002.log)。

### 上下文压缩探针

真实 AgentService 连续六轮对话后压缩，估算上下文由 7,518 降至 5,554 tokens；再追加三轮并重复压缩，由 9,313 降至 5,753 tokens。两次均只保留一份摘要，摘要位于最近四轮之前，第二次共保留九条消息。

这里只确认摘要合并与排列行为。摘要是有截断的提取记录，并不能证明长任务中的全部约束、Skill 细节和模型行为都不会丢失。

### 未核验的范围

- 未调用真实提供商 API，不能据此证明实际账单、缓存命中或实时模型行为。
- 未测长时间真实终端运行和异常杀进程恢复。
- 没有修改生产代码或现有测试文件。

## 建议执行顺序

1. 修复回退边界与不回退的费用账本，避免会话和证据丢失。
2. 将轮次结算放入状态无关、取消可达的清理路径，并做重复结算保护。
3. 补齐子代理调用前额度、目标恢复上限与验证证据绑定。
4. 同步测试替身和断言，新增以上场景的回归，再完成发布验收。
5. 架构后续先补持久 run_id 与统一 trace；需要客户端退出后继续执行或多客户端时，再拆 daemon/RPC。MCP 仍按后续版本范围处理。

## 修复记录（2026-10-02）

上述 7 个行为问题已修复，原始发现与探针结果保留用于对照。

| 原始问题 | 修复后的行为与验证 |
| --- | --- |
| 回退截断 100 条消息边界 | 完整保存消息 ID 与数量；102 条旧消息回退后全部保留；可能已截断的旧边界拒绝回退 |
| 暂停后漏记轮次和费用 | 已开始轮次按独立 round_id 结算，重复结算不再计费；模型暂停后的两个调用仍记录 1 轮与 $0.000084 |
| 取消绕过结算 | UI 与 headless 显式关闭流，保存已观察用量、结算并暂停后继续传播取消；模型等待、事件显示和父任务取消均覆盖 |
| 子代理结束后才检查预算 | 每次模型调用前从共享 token 额度预留完整输入与输出上限；调用后按实际用量结算；缺少完整计量时保守收费并停止新调用 |
| 回退删除已发生用量 | 保留 usage/usage_missing 并标记已回退；重复回退不重复计费；回退用量不替换当前上下文样本 |
| 恢复沿用旧验证 | 验证记录绑定会话；恢复暂停目标或重新绑定会话后清除旧证据，要求重新实际验证 |
| 耗尽轮数后仍能恢复 | 恢复与每轮调用前均检查轮数和费用；耗尽额度的目标不会再调用模型 |

补充修复：父任务取消的集成测试发现 `task` 工具的异步 handler 被同步包装，导致协程转 JSON 报错。现已按 coroutine 注册，验证了真实 Agent 图中的子任务执行、取消清理，以及此前成功调用的 110 tokens 只落盘一次。独立审查指出结算后显示 goal_status 时取消会遗留 active 状态，新增失败用例后修复为仅暂停，不重复结算。

测试替身同步了真实服务的 allowed_tools/history_display 参数、目标价格接口和成功完成轮数语义；流式等待加 3 秒超时，保留原有功能断言。

- 完整命令：`.venv/bin/python -m unittest discover -s tests -v`。
- 272 个测试，24.418 秒，全部通过；本轮新增 24 个回归用例。
- 完整日志：[full-suite-final.log](/tmp/nailong-safety-fixes-20261002/full-suite-final.log)。
- 新用例：[test_safety_regressions.py](/Users/aycxd/Desktop/LLM_project/tests/test_safety_regressions.py)。
- 独立只读审查已完成，无遗留重要发现；审查者独立运行 24 个安全用例，并用临时探针核对并发预算、双预算拒绝时释放预留，以及旧目标数据兼容。
- 本轮使用模拟模型与临时数据目录，没有调用真实 API；费用仍为应用侧估算，未验证真实提供商账单。

本轮保留当前同进程架构。daemon/RPC、MCP、操作系统沙箱及完整 trace 不在本次修复中；这些架构范围与原自检结论一致。
