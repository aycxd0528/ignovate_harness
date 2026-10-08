# ignovate harness 项目说明

本文面向希望使用或维护编码代理的开发者，从实际开发痛点说明项目定位，再介绍结构、上下文压缩、工作流、工具调用、记忆与轨迹统计。安装与操作指引放在核心机制之后。适用版本为 1.0.1，维护日期为 2026-10-08。支持 macOS、Linux 和 Windows（通过 WSL2 运行）；Release 安装器会自动准备缺少的 Python 和应用依赖。

仓库：[aycxd0528/ignovate_harness](https://github.com/aycxd0528/ignovate_harness)。完整命令与界面细节见 [README](../README.md)，本次问题与验证记录见 [项目审查报告](reviews/2026-10-08-project-audit.md)。

## 项目定位：解决哪些开发痛点

用模型处理一个真实代码项目时，开发者经常需要反复补充项目背景，确认模型读了哪些文件，约束它能执行的操作，并检查“已经完成”是否有测试依据。任务一旦跨越多轮对话，需求变更、工具日志和历史结论又容易混在一起，增加接续工作和追查问题的成本。

ignovate harness 是本地终端中的编码代理运行时。模型负责理解问题、提出行动和生成代码；项目提供文件与命令工具，并管理执行权限、任务状态、上下文、记忆和验证证据。这里的 harness 指围绕模型建立的执行与管理机制，让编码任务能够持续推进、由人控制关键操作，并留下可核对的结果。

| 开发痛点 | 项目的处理方式 |
| --- | --- |
| 长对话和大量工具输出挤占窗口，当前要求容易被旧日志淹没 | 每次请求核算完整输入；分级压缩旧工具正文和历史回合，并重新提供当前任务快照 |
| 模型建议与实际执行混在一起，文件修改和 shell 命令难以控制 | 工具统一经过执行门，按模式限制能力，执行前审批并检查文件版本 |
| 多步骤任务中，计划、进度和验收条件容易散落在聊天里 | 将任务、计划和持续目标持久化，用绑定到当前输入的验证证据判断交付状态 |
| 新会话需要反复说明约定，长期记忆又容易变成过期结论 | 分层保存用户、项目和本地记忆；只加载有界核心与目录，正文按需读取，并标明来源与有效性 |
| 失败后难以知道停在哪一步，也不清楚工具是否真正执行 | 保存模型、工具、审批和错误事件，提供工具记录及可导出的运行轨迹 |
| 多轮循环和子代理调用的用量难以追踪 | 按实际模型调用收集 usage，汇总会话成本，并对目标和只读子代理设置预算护栏 |

典型场景包括理解陌生项目、审查指定改动、批准计划后实施，以及“修改 → 执行项目验证 → 检查验收覆盖”的开发任务。一次任务可以先用 `/review` 确认问题，再用 `/plan` 明确方案，执行后用 `/verify` 核对结果，通过 `/task`、`/cost` 和 `/trace` 查看交付依据与过程。

项目支持 Textual、inline、plain 和无头输出，共用核心执行能力。旧命令 `nailong`、Python 包名 `nailong`、配置目录 `.nailong` 与已有会话数据继续兼容。


## 项目结构与职责分层

项目将界面、回合编排、领域状态和工具执行分开。LangChain 提供模型与工具接口，LangGraph 组织模型和工具之间的循环，并通过 checkpoint（会话状态检查点）保存可接续的对话状态；项目自己的核心模块负责权限、预算、任务、记忆与验证。默认模型连接由 `HarnessChatDeepSeek` 适配。

```text
ignovate_harness/
├── main.py / nailong/cli.py       启动、参数与界面选择
├── config.py                     模型连接与配置校验
├── agent.py                      运行时工厂、图与中间件组装
├── agent_service.py              流式回合、审批、取消与事件
├── nailong/core/                 上下文、任务、记忆、会话和证据
├── nailong/tools/                工具契约、执行门与文件操作
├── nailong/mcp/                  外部工具连接与适配
├── tui.py / ui/                  交互界面与共享工作流
├── headless.py                   脚本使用的 JSON / 流式输出
└── tests/ / scripts/             回归、打包和真实 API 检查
```

下表中的 `core/` 和 `tools/` 均位于 `nailong/` 下，同一单元格内省略了重复的目录前缀。

| 层次 | 主要模块 | 负责的问题 |
| --- | --- | --- |
| 入口与展示 | `main.py`、`nailong/cli.py`、`tui.py`、`ui/`、`headless.py` | 如何选择项目、提交输入、展示进展、接收审批和输出结果 |
| 回合编排 | `agent.py`、`agent_service.py`、`core/runner.py` | 如何组装模型与工具、推进回合、排队、取消和恢复 |
| 请求上下文 | `core/context.py`、`context_runtime.py`、`compact.py`、`task_context.py` | 每次模型真正看到了什么，是否超过窗口，哪些历史可以压缩 |
| 任务与验收 | `core/task_state.py`、`goal.py`、`plan.py`、`verification.py`、`delivery.py` | 要完成什么、还缺哪些步骤、当前证据能支持什么结论 |
| 记忆与会话 | `core/memory*.py`、`sessions.py`、`history_archive.py` | 哪些信息跨会话保留，如何加载、恢复和检查版本 |
| 工具与权限 | `tools/registry.py`、`execution.py`、`files.py`、`core/permissions.py`、`local_tools.py` | 工具有哪些能力，调用能否执行，文件是否被其他进程修改 |
| 观测与统计 | `core/traces.py`、`usage.py`、`costs.py`、`ui/presentation.py` | 如何核对操作顺序、调用次数、用量、费用与交付状态 |

一次回合的主线是：输入与任务更新 → 组装请求上下文 → 模型提出回答或工具调用 → 工具执行门与审批 → 工具结果进入下一次请求 → 回合结束并汇总交付状态。配置验证由 `/verify` 或目标工作流执行，验证结果再进入任务证据；普通回答结束不会自动启动全部项目测试。

三种状态分别保存：对话历史用于接续交流，任务状态用于保留目标与验收，项目记忆用于复用长期约定。SQLite checkpoint 与脱敏 JSONL 事件按项目路径隔离，默认位于 `~/.nailong/projects/<项目路径摘要>/`。因此，压缩对话并不直接删除持久任务要求，创建新会话也不会删除记忆文件。

## 上下文管理与压缩机制

### 从完整请求核算预算

上下文管理在每次模型调用前执行，覆盖工具循环和只读子代理。核算范围包括系统规则、固定记忆、Skill 目录与已加载正文、工具 schema（名称、参数结构与说明）、工具调用参数、工具结果、历史消息、当前任务投影和消息封装。只计算可见聊天文字会低估实际输入，因此运行时还会在最终请求边界复查模型真正收到的内容。

已知模型窗口时，输入预算为“窗口容量 − 输出预留 − 安全余量”；输出预留随请求设置及窗口调整。默认软压缩阈值为 150,000 estimated tokens，可通过项目 `context.soft_threshold_tokens` 调整，实际触发阈值不会高于可用输入预算。未知模型需在 `context_windows` 中填写提供商支持的窗口容量；未配置时仍执行软阈值管理，无法据此保证提供商硬限制。

文中的 estimated tokens 指本地估算的模型计数单位，usage 指提供商返回的实际调用用量。估算对 ASCII 与非 ASCII 文本分别计数，并用实际输入 usage 按模型和提供商向上校准。它用于请求管理，不是精确 tokenizer 或费用账单。有界缓存复用未变化文本与工具 schema 的计数；内容、工具定义或策略变化后仍重新核对。

### 先清理工具正文，再压缩历史回合

1. **L1 工具正文清理**：将较早、已完成的读取、搜索、命令和技能工具长结果替换为简短记录，保留工具名、状态、版本、分页或截断标记、必要错误信息及归档引用。普通清理保护最近一次工具交换，未完成交换始终保留。
2. **L2 历史回合压缩**：按完整用户回合边界，将更早的要求、约束、行动、结论和证据整理为确定性的结构化摘要。常规软压缩保留最近四个用户回合，避免把工具调用与对应结果拆开。
3. **硬窗口处理**：仍超出输入预算时，可以进一步清理最近已完成结果，并尝试将近期保留窗口缩至两轮、一轮。当前输入、固定约束或未完成工具交换仍无法安全容纳时，停止提供商调用并给出调整原因。

这里的摘要由规则提取与归档索引生成，不额外调用模型做自由文本总结。常规压缩一般要求达到至少 20% 的消息估算收益，自动软压缩以触发阈值的 80% 为目标；同一输入压缩无收益时会跳过重复尝试，输入或策略变化后重新评估。

### 归档恢复与当前任务保护

替换正文前，运行时先保存脱敏历史快照。摘要中的 `read_history_result(reference=...)` 可按引用与偏移恢复已归档的要求或工具正文；归档、分页和超限截断状态会明确返回。它读取历史快照，不会重新执行原命令；历史文件内容也需要与当前版本核对。

每次请求还会从任务存储生成当前任务投影，包含目标、最新要求、范围、约束、步骤、验收和证据索引，并检查任务所属项目与需求版本。这份投影只用于当前请求，不反复追加到 checkpoint；长记录通过 `read_task_context` 按需恢复。如此既保留任务连续性，也避免每轮把完整任务历史重复发送。

用户可用 `/context` 查看分类占用、窗口、输出预留和最近主调用实际输入，用 `/compact` 主动整理历史。实现入口：[预算核算](../nailong/core/context.py)、[请求中间件](../nailong/core/context_runtime.py)、[压缩规则](../nailong/core/compact.py)、[任务投影](../nailong/core/task_context.py)。

## 工作流：从调查到可核对的交付

### 四种推进方式

| 方式 | 执行路径 | 适用场景 |
| --- | --- | --- |
| 普通任务 | 用户输入 → 读取或修改 → 汇总当前证据与待验证项 | 有明确范围的解释、调查和单轮修改 |
| `/review` | 确认文件或 Git 改动范围 → 分批只读检查 → 记录实际覆盖 → 报告问题和限制 | 先理解风险、核实缺陷与改动影响 |
| `/plan` | 只读调查 → 提交内存草稿 → 用户批准或编辑后再批准 → 保存计划 → 继续实施 | 多文件或需要先对齐方案的任务 |
| `/goal` | 持久目标 → 多轮推进 → 配置验证 → 检查用户验收覆盖 → 完成或暂停 | 需要持续执行并控制轮数与成本的任务 |

计划只有批准后才写入 `.nailong/plans/`；批准计划后，实际文件修改和命令仍按本次权限执行。只读审查按模型实际消费的文件分页收集覆盖证据，漏读、截断或版本变化会降低交付状态。一次静态审查的 `reviewed` 不代表测试通过或代码没有缺陷。

计划生成后，在确认界面选择批准、拒绝或编辑；编辑可通过 `$EDITOR` 完成，修改后的草稿仍需再次批准。用 `/goal status` 查看持续目标，`/goal pause` 暂停；补齐验收、审批或阻塞条件后，输入不带目标文本的 `/goal` 恢复。已达到轮数或成本上限的目标不能直接恢复，需要创建新的目标。

目标工作流默认最多 20 轮、估算成本上限 1 美元，可用 `/goal --max-rounds 10 --max-cost-usd 0.5 <目标>` 调整。轮数、成本、空转、缺少验收或等待审批都可能使目标暂停。相同阻塞原因需要连续三轮才满足阻塞判定；单项目驱动锁避免多个进程同时推进同一目标。

### 任务状态与验证证据

任务状态独立于聊天，持久保存目标、需求历史、范围、约束、步骤、验收条件、变更路径与证据。`/task` 用于查看和维护这些要求；“继续”会接续已有任务。主代理每轮最多 40 次模型调用，末次只汇总已有证据；内部图步骤另设 256 步保护，两者分别计数。

验收条件应描述可观察的结果，并绑定实际验证步骤及相关路径。例如修复 `calc.py` 中的加法问题：先按后文快速开始完成模型配置，用 `ignovate --project /你的项目路径` 启动，在该项目配置下文名为 `tests` 的验证步骤，然后提交“修复 calc.py 的加法并补充回归测试”。改动后登记并核对：

```text
/task accept add-correct test add(2, 3) 返回 5
/task bind add-correct tests calc.py tests/test_calc.py
/verify
/task status
```

`accept` 后依次是验收 ID（`add-correct`）、类型（`test`）和预期结果；`bind` 后依次是验收 ID、验证步骤名（`tests`）与覆盖路径。绑定表示用户声明该检查覆盖此验收，需确保测试确实断言了预期结果。`/task status` 会显示证据与交付状态，必要时补充检查后再次 `/verify`。

验证命令、时间、工作目录、退出码、观察结果和输入摘要由运行时记录；任务需求或输入版本变化后，旧证据可能变为 stale，需重新核对。模型回答中的“测试通过”不能代替实际命令证据，命令退出成功也不能自动证明全部用户要求已覆盖。

交付报告区分 `verified`、`reviewed`、`unverified`、`failed` 和 `blocked`，在回答后、`/task`、`/status` 及无头 JSON 的 `delivery` 中可查看。目标完成同时需要满足当前目标验收和配置验证；缺少覆盖或需要人工确认时，保存为待核对状态。

### 配置项目验证

在工作项目的 `.nailong/settings.json` 中配置真实命令。下面示例适用于使用 unittest 的 Python 项目，应替换成目标项目实际的测试或构建流程：

```json
{
  "verification": {
    "steps": [
      {
        "name": "tests",
        "kind": "test",
        "command": "python -m unittest discover -s tests -v",
        "timeout_seconds": 120
      }
    ]
  }
}
```

`/verify` 的每一步仍经过权限检查和审批。测试及构建以实际退出结果判断；运行观察可配置输出标记或回环 HTTP 条件。验证记录与交付覆盖分别评估，可据此判断“执行了什么检查”以及“这些检查支持哪些验收条件”。实现入口：[共享工作流](../ui/flows.py)、[任务状态](../nailong/core/task_state.py)、[验证执行](../nailong/core/verification.py)、[交付评估](../nailong/core/delivery.py)。

## 工具调用与扩展机制

### 工具如何变成实际操作

工具以 `ToolSpec` 统一声明名称、参数 schema、处理函数、只读属性、并发安全性和允许的运行模式。运行时按 chat、init、review、plan、subagent 注册相应能力；模型提出的调用只有通过参数校验和执行门后，才会进入实际处理函数。

执行顺序为：参数与模式检查 → 权限判断 → 必要审批及预览 → 再次核对权限和文件版本 → 执行 → 规范化结果、脱敏与记录。需要审批而没有审批回调时，工具不会执行。文件修改和命令等操作通过项目内的变更协调器串行协调，减少同一运行进程内的冲突。

| 能力 | 主要工具 | 结果中的关键信息 |
| --- | --- | --- |
| 定位与读取 | `list_files`、`glob`、`grep`、`search_text`、`read_file` | 路径、行号、版本、分页、截断与实际覆盖 |
| 修改文件 | `edit_file`、`write_file` | 修改结果、版本冲突、diff 与变更路径 |
| 执行命令 | `run_command` | 命令与 cwd、退出码、超时、受限日志及结果引用 |
| 恢复资料 | `read_tool_result`、`read_history_result`、`read_task_context` | 归档版本、引用、分页与是否完整 |
| 读取记忆与技能 | `memory_list`、`memory_read`、`read_memory`、`load_skill` | 正文版本、预算与来源有效性，或技能资源说明 |
| 分派只读调查 | `task` | 子任务结果、实际用量及预算状态 |

已有文件修改需要先读取并满足操作要求的覆盖，再核对版本；审批等待期间文件变化会拒绝覆盖。分页和截断是工具契约的一部分，模型应按 `next_offset` 等字段继续读取。长结果采用受限正文与归档引用，引用仅能恢复已保存的内容，归档本身不完整时也会明确标记。

### 只读子代理、MCP 与 Skills

`task` 将可独立调查的子任务交给只读代理，最多并行三个；同一父会话每轮共享 30,000 tokens 额度。模型调用前预留完整输入和输出预算，返回后按实际 usage 结算；缺失用量或额度不足会限制后续调用。子代理不能替主代理执行写入或 shell 操作。

MCP 支持 stdio 和 Streamable HTTP，将外部工具 schema 转换后接入相同执行门。即使服务声明只读，默认仍按可能有副作用的外部操作审批；凭据以环境引用传入，连接超时、取消、退出和项目切换有对应清理流程。服务返回内容作为工具资料处理，不能扩大项目权限。

本地 Skills 位于项目或用户的 `.agents/skills/<名称>/SKILL.md`。初始上下文只提供目录信息，模型按需调用 `load_skill` 与资源读取工具获取正文；自定义 Markdown 命令可缩小工具集合。技能提供操作指导，执行能力仍由注册模式和权限判断决定。具体权限模式与文件边界见后文。

实现入口：[工具契约](../nailong/tools/registry.py)、[执行门](../nailong/tools/execution.py)、[文件会话](../nailong/tools/files.py)、[子代理](../nailong/tools/agents.py)、[MCP](../nailong/mcp/tools.py)。

## 记忆管理：长期信息如何复用

### 三层记忆与按需读取

记忆用于跨会话复用约定、项目背景和经验。它与当前任务状态、历史对话分别管理；对话压缩产生的摘要不会自动变成一条已确认的长期记忆，当前用户要求和权限也不会被记忆正文覆盖。

| 记忆层 | 固定文件 | 主题目录 | 适合保存 |
| --- | --- | --- | --- |
| 用户 user | `~/.nailong/context.md` | `~/.nailong/memory/*.md` | 跨项目的工作偏好与通用约定 |
| 项目 project | `<project>/.nailong/context.md` | `<project>/.nailong/memory/*.md` | 可随项目共享的结构、构建方式与约定 |
| 本地 local | `<project>/.nailong/context.local.md` | `<project>/.nailong/memory.local/*.md` | 本机环境和不提交 Git 的项目资料 |

每回合构建运行时会重新加载记忆快照。长固定文件优先选择“核心约定”或“Core”章节，否则取开头的有界内容；主题正文初始不全部加载，只提供 ID、描述、版本、大小、状态和标题索引。模型再通过 `memory_list`、`memory_read` 或按标题读取固定文件的 `read_memory` 获取相关内容。

三层摘要、目录、提示与记忆工具结果共用默认 4,000 estimated tokens 预算，可用 `memory.max_tokens` 调整，支持 512–16,384。预算为按需读取预留空间，每次模型请求也过滤历史记忆工具结果。记忆估算对非 ASCII 字符采取更保守的口径，因此不应与提供商实际 usage 混为一谈。

### 版本、适用范围与知识有效性

记忆可用文档开头的 `knowledge` frontmatter（结构化元数据）声明偏好、项目事实或经验，附带候选/用户确认声明、适用路径和来源文件 SHA-256。运行时在请求边界刷新适用范围与来源状态，显示 `observed`、`unverified` 或 `stale`；文件摘要匹配表示来源版本未变化，不能证明其中结论正确或测试通过，候选也不会自动晋升为确认知识。

读取响应包含 `version`、`truncated` 与 `next_offset`。同一运行内磁盘版本改变时，返回 `changed` 并要求下一轮重新读取，避免拼接不同版本的正文。缺失、空文件、读取失败与截断分别显示；超限或读取失败不会被解释为资料不存在。当前权限不允许读取的来源文件不会为了核对记忆而被静默读取。

用户可直接管理记忆：

```text
/memory show
/memory list project
/memory read project/build.md
/memory edit project
/memory reload
```

`project/build.md` 是主题文档的逻辑 ID，实际文件位于 `.nailong/memory/build.md`；需要先创建该文档。交互编辑先生成临时副本，显示 diff 并经审批后应用；取消不写原文件，版本冲突拒绝覆盖。自动提取和向量检索目前属于后续评估方向，当前实现以本地文件、目录和分页读取为主。

实现入口：[记忆存储](../nailong/core/memory.py)、[核心选择](../nailong/core/memory_selection.py)、[统一预算](../nailong/core/memory_context.py)、[知识有效性](../nailong/core/memory_knowledge.py)。

## 运行轨迹、用量与统计

轨迹帮助回答“这一轮调用了什么、在哪里失败、工具是否执行、结果依据是什么”。会话事件按顺序保存，`/trace` 从最近一次 `turn_start` 起投影可观察事件；展示模型请求、调用用量、工具开始与结束、审批、错误和交付状态等已有记录。

### 按问题选择观测入口

| 要了解的问题 | 入口 | 统计口径 |
| --- | --- | --- |
| 哪部分输入占用模型窗口 | `/context`、Token Weather（界面上下文状态行） | 分类估算、最近主调用实际输入、窗口容量与趋势 |
| 本会话使用了多少 tokens、估算花费多少 | `/cost` | 含子代理的输入、输出、缓存命中和按调用时单价估算的费用 |
| 某个工具执行了多久、返回什么结果 | `/tools [序号]` | 工具名称、耗时、状态、退出码及受限结果记录 |
| 最近一轮有哪些可观察事件 | `/trace` | 时间、顺序、运行/调用标识、工具配对与事件元数据 |
| 当前要求是否有足够证据交付 | `/task`、`/status`、JSON `delivery` | 任务进度、有效验收证据及 verified 等交付状态 |

模型 usage 按实际调用收集，主代理与只读子代理分别记录再汇总。缓存命中是输入的一部分，不能再次加到总 tokens；轨迹中也可能同时存在子代理汇总和逐调用记录，不能将所有 usage 行直接相加。缺失用量或价格标为不完整或未知，费用估算不等于提供商账单。回退对话不会抹去已发生的用量。

上下文管理另外保存预检查、请求核算、归档和压缩阶段的耗时，以及计数缓存命中、归档复用、压缩尝试和压缩前后占用。这些指标用于分析本地上下文处理开销，与模型生成耗时、文件操作耗时分别看待；阶段耗时与缓存信息位于请求事件的 `performance` 元数据中，压缩前后占用另见 `compaction` 和压缩事件，`/trace` 安全投影不会完整展示这些原始结构。

### 轨迹导出与解释边界

```text
/trace
/trace export
/trace export .nailong/exports/run.jsonl
```

导出格式为 JSONL，先写轨迹 manifest，再写该快照的事件；默认目录是当前项目 `.nailong/exports/`。显式路径也限制在项目内，普通模式按权限审批。轨迹省略用户/模型正文、私有思考、原始工具参数和原始输出，保留允许的元数据及已有结果引用；需要对话内容时应查看历史或使用单独的 `/export` 会话导出。

仅能证明的工具开始/结束配对才填父子关联，未知关联保留为 null，时间相邻不代表因果。轨迹中的 `complete` 只表示记录了最终事件，用户目标是否完成应看任务验收与 `delivery`。历史回退或日志重写可能改变派生运行 ID，因此导出可用于核对已记录过程，不能据此假定具备完整重放能力。

实现入口：[轨迹投影与导出](../nailong/core/traces.py)、[会话事件](../nailong/core/sessions.py)、[usage 归一化](../nailong/core/usage.py)、[成本估算](../nailong/core/costs.py)。

## 快速开始

推荐从 [GitHub Releases](https://github.com/aycxd0528/ignovate_harness/releases/latest) 下载安装包，无需预装 Python 或克隆源码。当前版本为 [v1.0.1](https://github.com/aycxd0528/ignovate_harness/releases/tag/v1.0.1)。

### macOS / Linux

下载 [ignovate-1.0.1-unix.tar.gz](https://github.com/aycxd0528/ignovate_harness/releases/download/v1.0.1/ignovate-1.0.1-unix.tar.gz)，支持 Intel / Apple Silicon macOS 和 x86_64 / ARM64 Linux。在下载目录执行：

```sh
tar -xzf ignovate-1.0.1-unix.tar.gz
cd ignovate-1.0.1
sh install.sh
. "$HOME/.local/bin/ignovate-env.sh"
ignovate set up
```

### Windows（WSL2）

下载 [ignovate-1.0.1-windows.zip](https://github.com/aycxd0528/ignovate_harness/releases/download/v1.0.1/ignovate-1.0.1-windows.zip)，在 PowerShell 中执行：

```powershell
Expand-Archive .\ignovate-1.0.1-windows.zip -DestinationPath .\ignovate-release
cd .\ignovate-release\ignovate-1.0.1
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

支持 Windows 11 或 Windows 10 2004 及以后版本，需要启用硬件虚拟化。缺少 WSL / Ubuntu 时，脚本会启动安装；已有 WSL1 发行版会尝试转换为 WSL2。系统要求重启时，重启后再运行同一个安装脚本；Ubuntu 首次启动可能要求创建 Linux 用户。安装完成后打开新的 PowerShell 或命令提示符：

```powershell
ignovate set up
```

Windows 启动器将当前目录和显式的 Windows `--project` 路径传入 WSL；应用、Git 和 MCP stdio 服务在 Linux 中运行，需使用 Linux 可执行文件。

### 自动配置与日常启动

`ignovate set up` 会检测 uv、Python 3.11–3.13、应用依赖和 ripgrep，自动下载缺少的组件并创建隔离环境，然后打开模型配置向导。应用依赖固定版本并校验哈希，无需手动激活虚拟环境。首次配置需要联网；下载失败后可重新运行此命令。重复安装会保留模型连接和项目数据，普通启动在环境就绪后不下载依赖。

只准备环境、不打开模型配置向导：

```sh
ignovate set up --environment-only
ignovate doctor --output-format json
```

未填写模型连接时，`doctor` 返回退出码 1 并给出配置指引。Git 是可选项目工具，使用 diff / review 前需自行安装。完整安装位置、PATH、校验和升级说明见 [Release 安装说明](https://github.com/aycxd0528/ignovate_harness/blob/v1.0.1/docs/INSTALL.md)。

配置指引依次收集模型连接、推理强度和本次权限。连接信息通过最终确认后保存；中途取消不写配置。API Key 使用隐藏输入，已有密钥留空可保留。旧命令 `ignovate --setup` 继续兼容。

然后切换到要分析的项目，或明确传入路径：

```sh
cd /path/to/work-project
ignovate
ignovate --project /path/to/work-project --ui inline
ignovate doctor --project /path/to/work-project --output-format json
```

首次可输入“列出项目根目录的文件，并指出主要源码入口”。`doctor` 只检查本机环境、依赖和配置，不调用模型 API；配置缺失时输出修复指引。安装包环境通过 distribution 元数据检查依赖，不要求保留源码目录。

### 从源码安装（开发者）

在已安装 Git、Python 3.11–3.13 的 macOS/Linux 终端（Windows 使用 WSL2）执行：

```bash
git clone --branch v1.0.1 https://github.com/aycxd0528/ignovate_harness.git
cd ignovate_harness
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
ignovate --setup
```

## 连接与配置

| 配置或数据 | 默认位置 | 作用 |
| --- | --- | --- |
| 模型连接 | `~/.ignovate/config.json` | 模型地址、ID、API Key 和默认推理偏好；文件权限 0600 |
| 用户偏好 | `~/.nailong/preferences.json` | 模型、界面和其他用户默认值 |
| 项目偏好 | `<project>/.nailong/settings.json` | 项目权限、模型、验证步骤和上下文设置 |
| 本地覆盖 | `<project>/.nailong/settings.local.json` | 不提交 Git 的本机偏好 |
| 项目会话 | `~/.nailong/projects/<项目路径摘要>/` | SQLite checkpoint、事件、任务、目标和历史归档 |

`IGNOVATE_CONFIG_DIR` 可调整连接目录，`NAILONG_DATA_DIR` 可调整会话数据根目录。用户连接文件优先于程序目录的 `.env`；未配置用户连接时，使用 `.env` 与进程环境变量。

兼容环境变量方式：复制 `.env.example` 为 `.env`，填写 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`。已有 `.env` 应直接编辑，避免被模板覆盖。连接地址必须是有效 HTTP(S) URL，不能带 URL 内认证、查询或片段；模型 ID 不能包含空白或控制字符。

连接配置是本机明文文件，应由当前用户保管。仓库忽略 `.env*`（保留空密钥模板）、本地配置、IDE 配置、构建输出和私人评估产物。

## 使用方式

| 方式 | 适用场景 | 示例 |
| --- | --- | --- |
| 自动界面 | 日常终端使用 | `ignovate` |
| Textual 全屏 | 对话、工具详情和审批统一展示 | `ignovate --ui textual` |
| Inline 滚动 | 使用终端原生滚动与复制 | `ignovate --ui inline` |
| Plain | IDE 或受限控制台 | `ignovate --plain` |
| 无头输出 | 脚本和流水线集成 | `ignovate -p "检查项目结构" --output-format json` |

自动模式根据 TTY 和终端能力选择界面。安装后的 `ignovate` 默认使用当前工作目录；`python main.py` 默认使用助手源码目录，处理其他项目时应传入 `--project`。

常用交互命令：

| 命令 | 行为 |
| --- | --- |
| `/help`、`/status`、`/doctor` | 查看操作说明、会话状态和本地诊断 |
| `/project <目录>` | 切换项目并开始新会话；失败保留原运行环境 |
| `/init` | 分析项目，并经审批生成 `.nailong/context.md` |
| `/review [路径或改动范围]` | 只读审查文件或 Git 改动 |
| `/plan <目标>` | 只读制定计划，批准后执行 |
| `/verify [步骤名称]` | 执行项目配置的验证步骤并保存证据 |
| `/goal <目标>`、`/goal status`、`/goal pause` | 管理有预算和轮数护栏的持续目标 |
| `/sessions`、`/history`、`/rewind` | 查找、查看或回退对话状态 |
| `/context`、`/compact`、`/cost` | 查看上下文、压缩旧内容和核对用量 |
| `/mcp`、`/skills`、`/memory` | 管理外部工具、本地技能和记忆 |
| `/permissions`、`/reasoning` | 调整本次权限与模型支持的推理档位 |

运行期间可输入任务并排队；Ctrl+C 停止当前运行并保留会话。恢复新一轮时，运行时明确关闭旧的未完成工具交换并标注结果未知，不自动重放可能产生副作用的调用。`/rewind` 只回退对话，已经执行的文件修改和命令不会撤销。

无头模式支持 `text`、`json`、`stream-json`。JSON 包含会话 ID、回答、用量、工具摘要、审批及交付状态。普通操作需要审批时默认拒绝；目标模式遇到审批会保存为暂停状态。退出码：0 为成功，1 为运行或审批问题，2 为轮次或预算限制，3 为配置问题。

## 权限与文件边界

| 模式 | 文件修改 | 命令执行 |
| --- | --- | --- |
| 请求批准 `default` | 依据规则请求审批 | 依据规则请求审批 |
| 帮我批准 `acceptEdits` | 自动批准项目内修改 | 继续请求审批 |
| 完全访问 `bypassPermissions` | 跳过路径限制和逐项审批 | 跳过逐项审批 |

可用 `--permission-mode acceptEdits` 或 `--dangerously-skip-permissions` 明确指定启动权限。权限选择只对本次进程生效。计划、审查和只读子代理仍保留模式限制。

输入 `/permissions rules` 查看已生效的项目规则与本会话授权。持久规则写在 `.nailong/settings.json` 的 `permissions.allow`、`deny` 和 `ask` 列表中；普通模式下拒绝优先于允许，需要审批的操作按 ask 规则和工具默认策略进入审批。规则格式与配置例子见 [README 的权限规则说明](../README.md#队列偏好和记忆)。

普通文件工具限制在所选项目内，并拒绝 `.env`、`.git`、`.venv`、`__pycache__` 等受保护路径；项目自定义命令的符号链接也不能把这些文件作为提示词加载。精确授权如 `Write(./safe.txt)` 只匹配该项目相对路径，不授权 `nested/safe.txt`。

读取和命令加载使用逐级目录描述符与 `O_NOFOLLOW`，防止路径解析后被符号链接替换。正则搜索先安全读取受限大小的文件快照，再交给 ripgrep；不能安全读取的文件标记为覆盖不完整。已有文件修改要求此前完整读取，并核对版本后原子写入；新文件遵循用户 umask，已有文件保留权限。读取和搜索返回分页、截断与覆盖状态，模型须按这些标记判断证据是否完整。

文件边界与 shell 审批不构成操作系统沙箱。获批命令拥有当前系统用户的权限，可以访问其他文件与网络；进程组取消也不能保证收回自行脱离进程组的程序。外部程序和其他进程仍可能并发修改文件，因此不能把工具检查解释为通用文件系统事务。

## 开发与发布验证

本机在虚拟环境中执行：

```bash
python -m unittest discover -s tests -v
python -m pip wheel --no-deps --wheel-dir dist .
python scripts/smoke_wheel.py dist/ignovate_harness-1.0.1-py3-none-any.whl
```

离线测试使用假模型、临时项目和本地 MCP 服务，不调用真实模型 API。wheel smoke 从源码目录之外加载实际发行内容，验证 `--help`、JSON doctor 和依赖检查，避免仅验证 editable 安装。

GitHub Actions 已启用，工作流位于 .github/workflows/tests.yml，参考副本位于 docs/ci/github-actions-tests.yml。每次 push、pull request 或手动触发都会在 Ubuntu 24.04、Python 3.11/3.13 上运行完整离线测试及 wheel smoke。实际运行结果以仓库对应提交的检查记录为准。

推送与 `pyproject.toml` 版本一致的 `v*` tag 后，[Release 工作流](https://github.com/aycxd0528/ignovate_harness/blob/v1.0.1/.github/workflows/release.yml) 会运行 Python 3.11/3.13 完整测试、构建发行包、在 macOS/Linux 执行实际安装及重复配置检查，并在 Windows 验证 PowerShell 启动器的参数、项目路径、退出码和校验失败处理。全部检查通过后才发布 Unix 安装包、Windows 安装包、wheel 和 `SHA256SUMS`。[v1.0.1 发布检查](https://github.com/aycxd0528/ignovate_harness/actions/runs/37727294533) 已通过；WSL 系统安装、管理员授权和重启流程仍需实机验证。

真实模型验证通过本机手动执行 python scripts/smoke_real_api.py --output /tmp/ignovate-live-api.json。脚本使用已配置的模型连接和临时项目，限制调用次数、输出和 managed 预算，保存脱敏元数据。本次 deepseek-flash 的回答、读取、编辑后验证、只读审查、审批拒绝和无头 JSON 六类检查均获得通过结果；首次无头答案断言失败与成功补测保留在验证记录中。第三方 MCP、Windows 实机与不同终端剪贴板协议仍需另行验证。 详见 [CI 与真实 API 验证记录](reviews/2026-10-08-ci-real-api-verification.md)。

项目采用 MIT License。对功能或权限行为作出修改时，应新增能够复现问题的回归测试，并更新 README、项目说明和验证记录。
