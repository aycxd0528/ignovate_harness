# Tool use 审查：契约、执行与反馈

日期：2026-10-03（Asia/Shanghai）

结论：优先修复只读工具权限未执行、整文件覆盖缺少快照保护、异步命令与文件写入可交叠这三个 P1 问题；随后完善 schema、错误结果、输出预算、搜索覆盖信息和长命令生命周期。本次审查新增本文档，没有实施运行时代码修复。

## 来源与审查方法

参考用户提供的 [CLAUDE-OPUS-5.5.md](https://github.com/elder-plinius/CL4R1T4S/blob/f0052f05837c6dcfec5ed70179bb761f2593146a/ANTHROPIC/CLAUDE-OPUS-5.5.md)。GitHub API 核实该文件最近一次提交为 `f0052f05837c6dcfec5ed70179bb761f2593146a`。这是第三方整理的材料，本次没有确认它是 Anthropic 官方原始提示词。

定向读取其工具契约及 `how_a_task_runs`，与本项目的真实注册工具和调用链对照：

| 参考内容 | 审查准则 |
| --- | --- |
| `Read`、`Glob`、`Grep` | 先定位、定向读取，工具返回足够的定位和覆盖信息 |
| `Edit`、`Write` | 已有文件先读后改；局部替换与整文件覆盖有不同前置条件 |
| 工具参数 schema | 参数含义、有效取值和限制能被调用方识别 |
| `Bash` | 命令时限与输出可用性应匹配实际验证任务 |
| `Agent` | 只在独立调研有收益时委派，避免重复工作，主代理负责整合 |
| `AskUserQuestion`、`TaskCreate/Update`、`how_a_task_runs` | 关键决策有等待机制，多阶段工作有可观察的进度和验证状态 |

另外核对了 [Anthropic 官方工具定义文档](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)，其中的参数说明、枚举和高信息密度返回原则可作为工具契约设计依据。项目使用 `ChatDeepSeek` 与 LangChain/LangGraph；下文建议针对该实现，不假定 Anthropic 专用 API 字段可以直接使用。

静态审查范围：`agent.py`、`agent_service.py`、`tools.py`、`local_tools.py`、工具注册/文件/子代理模块，以及权限、进程和验证执行链。实测使用临时项目、离线假模型及模拟进程；没有调用付费模型 API，没有读取凭据内容。

## 已有设计值得保留

- 工具按 chat/init/review/plan/subagent 模式过滤，提示词根据实际工具组装。
- 工具说明已涵盖使用场景、参数、截断和失败处理；无需再全面重写文案。
- `read_file` 提供行/字符分页；`edit_file` 检查读取快照、匹配唯一性，并采用原子写入。
- 文件工具拒绝受保护路径和项目外路径；修改工具有审批，命令可取消并清理进程组。
- 子代理是只读模式，具备并发上限和共享 token 预算。

## 1. [P1] 对只读工具真正执行权限决定

代码位置：[运行时审批注册](/Users/aycxd/Desktop/LLM_project/agent.py:471)、[工具开始事件](/Users/aycxd/Desktop/LLM_project/agent_service.py:514)、[拒绝规则解析](/Users/aycxd/Desktop/LLM_project/nailong/core/permissions.py:208)。

触发：配置 `deny: ["Read(blocked.txt)"]`，模型请求 `read_file("blocked.txt")`。

实测：`PermissionEngine` 返回 `deny`，实际 AgentService/LangGraph 调用仍返回文件内容，`ok=true`。这里验证的是普通项目文件；`.env` 等硬路径边界仍由文件工具检查，不能据此推断硬边界失效。

根因：HITL 只注册 `edit_file`、`write_file`、`run_command`。其他工具在 `_tool_start_data` 中计算权限，用于事件的说明文字，但没有在执行入口应用该决定。只读工具的 `ask` 规则也缺少等待入口；`PreToolUse` 当前位于变更工具的审批恢复流程中。

建议：在所有工具的同步/异步执行入口统一应用权限与前置钩子；deny 必须在 handler 前终止，ask 必须先等待有效决定。把对应权限上下文传入只读子代理，避免不同执行路径获得不同结果。

验收：真实 Agent 图测试 deny 时 handler 调用次数为 0；ask 时批准前不调用；覆盖只读工具、变更工具、子代理及同步/异步路径。仅测试 PermissionEngine 的返回值不够。

## 2. [P1] 对已有文件的完整覆盖增加读取、过期与原子写入保护

代码位置：[write_file 适配](/Users/aycxd/Desktop/LLM_project/nailong/tools/registry.py:252)、[底层写入](/Users/aycxd/Desktop/LLM_project/local_tools.py:220)、[现有 edit_file 快照检查](/Users/aycxd/Desktop/LLM_project/nailong/tools/files.py:254)。

参考中的 `Write` 明确要求已有文件先读取。当前项目的提示词也要求完整读取，但 handler 直接调用 `Path.write_text`，没有检查读取快照。

实测：未读取已有文件就覆盖，返回 `ok=true`；读取后由用户修改文件，再调用旧内容的完整覆盖，仍返回 `ok=true`，用户新修改丢失。这是已授权覆盖操作执行时缺少内容前置条件，不是绕过写入审批。

建议：新建文件保持直接创建；已有文件覆盖要求完整读取记录、预期版本/摘要，并在执行前验证文件仍为该版本。审批预览与执行绑定同一摘要；变更后重新读取和审批。使用与 `edit_file` 一致的原子替换，避免直接截断文件后写入。

注意：当前 `read_file` 即使只返回一页也会计算全文件摘要；因此“存在 snapshot”不能证明模型已经完整读取。需要额外记录已返回的行/字符区间，或设计明确的完整读取凭据。

验收：未读、只读一页、读取后外部修改、审批等待期间变化都拒绝完整覆盖；新建和完整读取后的合法覆盖成功，失败时保留原内容。

## 3. [P1] 让并发安全声明落实到异步执行

代码位置：[并发元数据](/Users/aycxd/Desktop/LLM_project/nailong/tools/registry.py:105)、[异步命令锁](/Users/aycxd/Desktop/LLM_project/nailong/tools/registry.py:279)、[LangChain 工具适配](/Users/aycxd/Desktop/LLM_project/tools.py:59)、[现有同步串行测试](/Users/aycxd/Desktop/LLM_project/tests/test_tool_registry.py:66)。

实测：让异步 `run_command` 的模拟进程保持运行，此时 `write_file.ainvoke` 仍能成功完成。真实模型若在同批调用中提交命令与编辑，测试/构建可能观察到正在变动的代码。

根因：同步 edit/write/command 使用 `FileSession.mutation_lock`；异步命令只使用单独的 `asyncio.Lock`。`ToolSpec.concurrency_safe` 没有传入工具执行调度。现有串行用例只覆盖同步 handlers。

建议：为同一会话设置统一的变更执行队列或异步协调器，让 edit/write/command 共用它；跨轮重建工具包仍复用协调器。独立只读调用可并发，存在前置依赖的调用分批执行。避免在跨 `await` 的临界区直接依赖线程 RLock 实现协程互斥。

验收：真实异步图或工具测试中，命令与文件变更最大同时执行数为 1；取消和异常均释放执行权。增加测试确认独立读取仍可并发。

## 4. [P2] 将参数约束写入唯一的 schema

代码位置：[简化 schema 生成](/Users/aycxd/Desktop/LLM_project/nailong/tools/registry.py:119)、[StructuredTool 自行推断参数](/Users/aycxd/Desktop/LLM_project/tools.py:86)。

实测：模型工具 schema 中，`grep.output_mode` 和 `update_goal.state` 只是字符串；`read_file.offset`、`run_command.timeout_seconds` 只是整数，缺少枚举和范围；没有明确禁止额外参数。`ToolSpec.input_schema` 与 LangChain 参数模型是两个分别生成的契约，现有测试只比较参数名与 required。

建议：使用同一 Pydantic 参数模型生成注册表 schema，并作为 `StructuredTool.args_schema`。以 `Literal` 定义 output_mode/state，以字段约束定义分页范围、内容长度、命令长度和时限，并给关键参数补充说明；配置额外字段拒绝。范围变化需同步修改描述和 handler，不要只改 schema。

验收：非法枚举、错误类型、未知参数和超界值在 handler 前产生可恢复的参数错误；模型看到的 schema 与运行时校验一致。

## 5. [P2] 统一失败结果、工具状态和 UI 摘要

代码位置：[结果 JSON 序列化](/Users/aycxd/Desktop/LLM_project/tools.py:55)、[task 返回文本](/Users/aycxd/Desktop/LLM_project/nailong/tools/registry.py:302)、[子代理异常转文字](/Users/aycxd/Desktop/LLM_project/nailong/tools/agents.py:73)、[UI 结果摘要](/Users/aycxd/Desktop/LLM_project/agent_service.py:540)。

实测：读取不存在的文件，payload 为 `ok=false`，LangChain `ToolMessage.status` 却为 `success`。子代理抛出异常后，task 返回 JSON 字符串，仍为 success；UI 只显示“返回 49 个字符”，没有失败状态。

普通文件工具的 UI 已解析 `ok`，不应声称所有错误都被隐藏。缺口集中于传输状态与业务状态不一致，以及 task 的非对象返回。

建议：所有工具使用统一结果对象，包含 `ok`、`data` 或 `error`；错误提供稳定 code、必要 details，以及重试/重新读取提示。适配层同步设置 ToolMessage 状态，UI 显示失败原因。参数、权限、文件冲突、环境、超时分别分类，避免相同失败机械重试。

DeepSeek 接口是否接收状态字段取决于适配器；不能只设置 LangChain 内部 status，就认为模型已获得完整错误语义。模型可见内容也必须包含明确失败信息。

验收：缺文件、冲突、拒绝、命令失败、子代理异常和预算耗尽，均在模型返回、事件日志、UI 上表达一致状态。

## 6. [P2] 为完整工具返回设置总输出预算

代码位置：[grep 上下文输出](/Users/aycxd/Desktop/LLM_project/nailong/tools/files.py:442)、[edit_file 完整 diff](/Users/aycxd/Desktop/LLM_project/nailong/tools/files.py:310)、[命令输出只保留前缀](/Users/aycxd/Desktop/LLM_project/nailong/core/processes.py:66)。

实测：仅 3 条 grep 匹配、`context=1`，周围长行使 JSON 返回达到 60,384 字符，仍 `truncated=false`。每条匹配正文限制 500 字符，但 context_before/after 没有字符限制。edit_file 的 diff 也没有独立总预算。

另一个实测：命令输出超过 12,000 字符后，尾部 `TAIL_MARKER` 被丢弃，尽管命令成功；返回只标记截断。测试汇总和最终错误常在日志尾部，单纯补充输出说明不能恢复这些信息。

建议：按序列化后的完整结果执行字符/token 预算，限制上下文行并合并重叠片段；保留定位、摘要、截断原因和可继续读取的游标。完整 diff/log 存在会话管理的有界本地 artifact 中，提供范围读取；命令实时摘要保留首尾，尤其是最后的失败或测试汇总。

验收：长行、大量匹配、大 diff 和大命令日志均符合总预算；截断后的内容可定位和补取，而不是必须重新执行命令。

## 7. [P2] 搜索结果明确报告未覆盖部分，并稳定正则语义

代码位置：[search_text 跳过大文件](/Users/aycxd/Desktop/LLM_project/nailong/tools/files.py:479)、[grep 后端回退](/Users/aycxd/Desktop/LLM_project/nailong/tools/files.py:409)。

实测：文件大于 200,000 字节，第一行包含 `needle`；调用 search_text 返回 `ok=true, matches=[], truncated=false`。大文件被跳过，却没有进入覆盖信息，零匹配容易被误读成不存在。

grep 已标记超大文件，但缺少 ripgrep 时会把正则 pattern 作为字面量；该差异已在工具说明中披露，仍增加模型跨环境调用成本。

建议：统一返回 backend、pattern_mode、scanned_files、skipped_files/skipped_reasons、coverage_complete。regex/literal 模式显式区分；请求 regex 而后端不支持时返回明确错误，不静默改变语义。大文件优先支持有界流式搜索，仍跳过时明确显示覆盖不足。

验收：超大文件、无权限/不可解码文件、文件数上限、匹配上限、rg 缺失等场景，都不能被报告为完整零匹配；regex 不因部署环境改变含义。

## 8. [P2] 打通 Agent 可调用的长验证命令

代码位置：[run_command 30 秒上限](/Users/aycxd/Desktop/LLM_project/nailong/tools/registry.py:288)、[已有验证服务的 300 秒范围](/Users/aycxd/Desktop/LLM_project/nailong/core/verification.py:96)。

实测：`run_command(timeout_seconds=600)` 被静默压到 30 秒。这个限制符合当前工具说明，不是实现违约；它限制了 Agent 自主完成较慢测试和构建。项目的 `/verify` 执行服务已经允许配置 1–300 秒，但当前工具清单没有调用该服务的入口。

建议：优先提供受权限控制的验证工具，调用现有 VerificationService 并返回逐项结果和真实验证证据；如开放普通命令时限，则保持默认值并明确允许范围，超界返回参数错误。需要开发服务器等持续进程时，再引入 start/poll/cancel 生命周期与增量日志，而不是无限延长阻塞调用。

验收：超过 30 秒的正常配置验证可通过 Agent 工具完成；超时、取消和验证失败均有准确状态，进程组仍会清理，目标完成证据不能被普通文字替代。

## 后续能力建议：与缺陷修复分开评估

- **review/init 的范围内搜索**：目前这些模式只有 list/read，review 另有 20 文件限制。可增加严格遵守 review_paths 和读取限额的定位工具，并返回审查覆盖统计。应先修复统一权限入口，再扩充工具。
- **结构化需求澄清**：用户必须决定的选项可通过 interrupt 等待输入，而不是仅在模型文本中提问；普通实现细节继续采用合理默认值。沿用当前用户输入通道，不必复制参考的工具名和参数。
- **任务进度**：已有计划/目标机制可增加步骤状态和验证结果，避免新造一套平行任务系统。简单问答不创建清单。
- **检索与委派评估**：补充只读子代理的证据格式、失败码和重复任务指标。当前工具规模较小，工具延迟加载应等到实测 schema 成本或工具数量成为瓶颈再考虑。

## 验证记录与建议实施顺序

首次运行以下现有离线用例，全部通过，共 83 个：

| 命令（工作目录为项目根目录） | 用例数 |
| --- | ---: |
| `.venv/bin/python -m unittest discover -s tests -p 'test_tool*.py' -q` | 14 |
| `.venv/bin/python -m unittest discover -s tests -p 'test_nailong_files.py' -q` | 18 |
| `.venv/bin/python -m unittest discover -s tests -p 'test_agent.py' -q` | 14 |
| `.venv/bin/python -m unittest discover -s tests -p 'test_permissions.py' -q` | 15 |
| `.venv/bin/python -m unittest discover -s tests -p 'test_agent_service.py' -q` | 22 |

审查期间工作区出现并行更新，涉及记忆工具、提示词组装及测试。对更新后的工作区再次组合运行上述五组用例，结果为 **84 个用例、83 通过、1 失败**。失败项为 `test_tool_registry.ToolRegistryTests.test_registry_order_is_stable_and_includes_p0_tools`：测试预期包含 `memory_list`、`memory_read`，当前默认注册工具列表不包含它们。此处记录复核时的集成状态，不把尚在变动的工具命名方案当作最终设计；本次没有修改其他工作的代码或测试。

并行更新后重新执行临时目录复现，前三个 P1 问题及 schema、错误状态、grep 输出、搜索覆盖和命令时限结果仍相同，并重新核对了本文代码位置。上述用例的通过部分不代表本报告的问题已经修复。运行时代码未由本次审查改动，因此没有修复后的回归结果，也没有真实模型行为对比；本次未运行全项目测试套件。

推荐顺序：先补齐权限入口、覆盖前置条件和共享变更调度；其次统一 schema、结果与输出预算；再处理搜索覆盖和验证工具入口。每一项先把本文对应触发条件固化为行为回归，再实现修复。
