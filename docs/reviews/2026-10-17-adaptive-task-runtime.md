# 普通会话与持续任务实施验收

实施分支：`codex/adaptive-task-runtime`。设计基准：`docs/superpowers/specs/2026-10-17-adaptive-task-runtime-design.md`。使用方式见 [ADAPTIVE_TASKS.md](../ADAPTIVE_TASKS.md)。

## 六项功能与证据

| 优先级 | 实际入口 | 验证 |
| --- | --- | --- |
| P0 固定输入 | `AgentService` 的实际请求能力视图；`discover_tools`、`search_skills` | `test_adaptive_capabilities.py` 捕获实际绑定、相邻请求提升、同批猜测拒绝、新轮重置、100 Skills、显式范围与目录变化 |
| P0 用量解释 | 共享 usage projection；GUI 节点详情、CLI context/cost、无头 JSON | `test_adaptive_usage*.py` 核对稳定调用去重、未知用量、缓存子集、旧价格；浏览器核对真实 inspector、键盘、窄屏与明暗主题 |
| P1 自然持续任务 | `prepare_task`、`start_task`、`AgentService.stream_turn`、原生流式 goal 驱动 | `test_adaptive_task_flow.py` 从草案到明确授权、失败验证、修复、验证交付；GUI/无头原生入口真实写入临时文件，无需 `/goal` |
| P1 子任务额度与续作 | 项目/本地 subagents 配置、结构化 task 返回、持久续作引用 | `test_adaptive_child_continuations.py` 覆盖阶段停止、续作、父任务版本/输入绑定、取消、未知用量、保存失败；原子预算与取消回归继续运行 |
| P2 会话协调 | 原生 session 工具组、共享串行派发驱动 | `test_adaptive_coordination.py` 通过真实工具提交和外层消费队列，保留目标 checkpoint/task_id/revision，验证依赖和目标费用，不切换总目标归属 |
| P2 控制与恢复 | 持久租约 generation、执行前检查、原生回执和结算身份 | `test_adaptive_recovery.py` 覆盖用户介入、审批中暂停、局部暂停不挡独立派发、已确认结果恢复、未知操作不重放、权限取交集、损坏状态停止、整体完成门 |

`completed` 派发状态要求原生执行观察、通过的验证或经核对的 delivery；缺少证据保留 partial，失败不放行依赖。结构化 delivery 保留实际验收结果。整体目标还受自身当前输入验证与用户目标覆盖约束。字符串 worker 不会被包装为有原生证据的成功结果。

## 输入改善

以下是相同估算方法、空历史、离线请求记录的结果；不是提供商实际计费测量。

| 场景 | 旧输入估算 | 新输入估算 | 新工具数 |
| --- | ---: | ---: | ---: |
| 问候，0 Skills | 8037 | 157 | 0 |
| 问候，100 无关 Skills | 17142 | 157 | 0 |
| 一般交流，0 Skills | 8044 | 660 | 2 |
| 一般交流，100 无关 Skills | 17149 | 660 | 2 |

无 Skills 问候输入估算降低约 98%。一般交流的工具仅为能力发现和 Skill 搜索；无关 Skills 不增加完整目录。旧会话实际 9447 输入不能与新请求估算直接作为实测费用对比。基线方法见 [请求输入基线](2026-10-17-request-input-baseline.md)；最终捕获见 [离线请求记录](2026-10-17-adaptive-request-measurements.jsonl)。

## 检查记录

- 原生能力、用量、任务、预算、目标、恢复、GUI 控制、审批取消与分支等聚焦回归通过；最终自适应运行时专项 80 项通过。
- 真实 Chrome 浏览器：实际/估算、缓存、累计、未知用量、键盘操作、内容注入、真实 inspector、窄屏与明暗主题通过。
- Desktop runtime：3 项通过。
- 当前前端源文件 esbuild 构建通过；产物写入隔离验证目录，未覆盖用户已有构建产物。
- 全量 Python：`.venv/bin/python -m unittest discover -s tests`，1672 项，OK（跳过 44 项），153.994 秒。中间运行发现的旧 512/1500 输出断言已按已接受的 1024/4096 默认值更新；GUI/无头测试发现的人工验收过早等待已修复并复验。
- 独立最终审查：6 项 Important 均补上 RED→GREEN 回归；无 Critical 或 Minor，详细处理见下表。

## 边界

离线回归使用模拟 usage 证明记账和运行路径。用户于 2026-10-18 追加授权真实 API 后，使用现有 deepseek-flash 配置在隔离临时项目进行了少量真实请求；下文独立记录提供商实际 tokens。费用仍是配置单价估算，没有读取提供商账单。全工作区指纹或父任务修订变化时，旧续作拒绝直接复用，保留原阶段记录供重新核对。局部文件接管不运行不能证明范围的命令。运行中未知副作用保留待核对；不会以清除队列、重置目标费用或自动重试来掩盖。

原有用户 staged/unstaged/untracked 改动保留；通过独立 Git 索引提交任务文件，GUI 共享文件只提交任务自身的准确变换。没有合并或发布。

## 实施取舍

1. 在当前权威工作区建立新分支，保留用户已有 GUI 及未跟踪依赖；代价是验证需区分用户基线，不能直接清理工作区。
2. “开始实施”视为当前设计的实施授权，不重复请求同范围批准；实现选择通过提交和报告供审阅。
3. 保留底层 runtime factory 的完整工具兼容接口，真实 AgentService 轮次启用按需加载；直接底层调用须显式选择新模式。
4. 仅在传入能力目录时注册发现工具，保留旧裸 registry 顺序；没有目录的裸调用无法使用发现功能。
5. 精简提示集中在能力视图，加载后复用原提示构建器；两条路径的核心规则需一起维护。
6. 保留旧 ReadOnlyTaskRunner 字符串 API 与构造默认值，原生运行时采用可配置的 4096/1024 输出上限；旧直接调用方须显式设置新额度。

7. 原有 GUI/avatar 及并行变更只核对保留，不归入本次审查与提交；代价是必须区分改动归属，该部分由其原工作流验收。
8. 真实提供商费用与自然语言成功率只报告已测范围；代价是少量样本不能代替账单核对和成功率评估。
9. 实施结束保留 `codex/adaptive-task-runtime`，不合并或发布；代价是集成仍需后续明确指令。按上层“完成已授权工作、避免无必要确认”的要求，不额外发起合并选项审批。


## 最终独立审查与修复

独立审查读取 `caae9b6..87fbce2` 并在临时项目执行原生探针，确认 6 项 Important，无 Critical 或 Minor。全部接受并在同一修复阶段补上失败复现与回归：

| 问题 | 修复与直接证据 |
| --- | --- |
| 暂停/耗尽目标可通过改写工作请求绕过费用保护 | 持久 goal_id 绑定任务，暂停目标也保留预算；`test_budget_limited_natural_goal_cannot_be_bypassed_by_rephrasing_work`；明确“新目标：…”才建立独立任务，旧工作项和账本保留 |
| 验证失败仍标记派发完成、放行依赖 | 原生验证与实际 delivery 决定 failed/partial；未验证文字报告不放行；`test_failed_verification_cannot_complete_dispatch_or_release_dependents`、`test_unverified_prose_only_stage_cannot_release_dependencies` |
| 自动验证绕过目标任务阶段限制 | 审批前和启动前均检查目标当前阶段策略；`test_automatic_verification_respects_target_stage_restriction` |
| 暂停后草案仍无法修订 | 可进入新草案版本，重新确认后沿用同一 goal_id、费用和轮数；`test_paused_brief_can_be_revised_without_resetting_goal_ledger`、`test_native_paused_replanning_reaccepts_same_goal_and_implements_revised_brief` |
| 执行会话的未知 usage 未进入协调会话账单 | usage_missing 与稳定调用身份一同归属协调会话；`test_missing_worker_usage_remains_unknown_in_owner_bill` |
| 重启恢复完成回执未核对实际文件 | 回执绑定输入指纹，文件改变保持 needs_reconciliation；`test_recovery_cannot_restore_completion_after_artifact_changed` |

最终验收另补：预算停止的原生子任务返回文件版本、下一行、行内偏移与未读项，实际从该偏移补读；模型调用前的预算拒绝不再误报未知用量。`test_native_child_budget_stop_preserves_exact_next_read_position` 使用真实文件与运行时验证两阶段接续。没有可观察验收的草案保持澄清阶段，不能启动实施。

审查明确未评判的两项已作处理：用户原有 GUI/avatar 改动不属于本次提交，逐文件核对保留；独立审查时尚未调用真实提供商，因此当时只报告离线请求估算；用户随后追加授权，真实样本单列于下节，不据此声称整体成功率。修复由回归测试验证，未另行请求第二轮审查。


## 最后入口核对

- 自然任务从准备、接受到隐藏目标轮次的所有模型调用归入同一条真实用户请求；恢复时保留用户原话。无头入口核对本轮实际总量等于该请求的累计总量。
- GUI 新指令在入队时立即作废接管，即便队列暂停、尚未开始执行；旧排队派发取消。稳定 request_id 的重试先返回原记录。
- 目标工具发现绑定原生目标 ID 和任务修订，新目标不继承旧目标的工具提升。
- 原有 21 个 dirty tracked 文件曾逐一与实施前备份比较，扣除准确的任务自身插入后全部一致；之后并行出现的 runtime_sources、队列、GUI 文档及测试变更另行核对保留。已有暂存内容未被纳入任务提交。

## 四项验收

| 指标 | 结果与限制 |
| --- | --- |
| 简单交流固定输入下降 | 相同方法的问候估算 8037 → 157；真实 API 单样本输入 7398 → 118 |
| 减少上下文搬运和手动继续 | 原生 GUI/无头测试在同一会话准备、授权、持续执行；协调工具沿用已有会话 checkpoint；未宣称真实用户使用率 |
| 预算停止可接续 | 持久阶段保留证据、未完成项和精确读取偏移；真实临时文件两阶段读取通过；原总目标费用保留 |
| 完成有验证证据 | 当前输入原生验证及任务覆盖门约束交付；失败/无证据派发不放行依赖；独立审查六项均修复 |

无延期 Minor 审查项。


## 2026-10-18 真实 API 追加验收

用户明确授权调用真实 API；沿用本地现有 `deepseek-flash` 配置，凭据不写入记录，项目和会话数据均使用隔离临时目录。原始脱敏结果见 [真实调用记录](2026-10-18-live-api-runtime.jsonl)。

| 同模型单次样本 | 实际输入 | 实际输出 | 实际总量 | 缓存输入 |
| --- | ---: | ---: | ---: | ---: |
| 旧完整提示与完整工具请求：你好 | 7398 | 93 | 7491 | 0 |
| 新原生 AgentService 问候：你好 | 118 | 9 | 127 | 0 |
| 新原生一般交流 | 776 | 127 | 903 | 0 |

该问候样本实际输入降低约 98.4%；不能与此前另一环境的 9666 tokens 直接当作同条件对照，也不能由单样本推断所有交流的收费。用量来自提供商 response usage，费用字段来自本地单价估算。

初次真实自然任务并未通过持续任务验收：模型三次提交草案失败后直接创建文件；独立两个单测虽通过，但没有持久 goal，交付仍 unverified。再次独立诊断也未形成 goal。保留这些失败记录，未把文件通过包装为持续任务通过。

RED→GREEN 回归确认两处缺口：`prepare_task.brief` 暴露为不透明 object，模型不知道嵌套验收/验证参数；完整能力提示缺少同会话启动协议。现补上严格 TypedDict schema（未知字段拒绝）和完整能力路径中的持续任务协议，保留原生授权、草案、启动交接及验收门。


修复后真实样本先成功 prepare/start，再在原生驱动中写入两文件，原生验证和独立复跑均为 2 tests / OK。最终 goal.state=`complete`，delivery.status=`verified`，持续目标执行 2 轮；调用统计为 16 次模型调用，输入 204927、输出 4920、缓存输入 114176、实际总量 209847 tokens，耗时 36.702 秒。目标记录的费用 0.019432 美元来自本地价格估算，包含阶段范围与整条用户请求的记账范围不同，不能当作提供商账单。

该样本证明同一会话持续运行到原生验证完成，且不需要用户发送“继续”；也表明多步骤任务仍有明显的累计输入开销。没有以这一次成功推断跨任务成功率、成本分布或协调会话的真实模型表现。真实跨会话、子任务耗尽/重启场景仍以原生离线回归为证据。

修复中一次真实样本因环境没有 `python` 命令而暂停，且范围被填入说明文字后交付门拒绝通过。最终样本明确使用已存在的项目解释器，并补强路径字段说明；这些失败均保留在原始记录中。

全量回归后，工作区出现并行 GUI runtime_sources 修改；保留原样。本次共享 controller 仅提交精确的入队控制权插入，不提交并行改动。

最终工作区稳定后再次执行完整 Python 测试：1672 tests / OK (skipped=44)，153.994 秒；自适应专项 80 tests / OK。新计数包含并行 GUI 测试；这些并行实现及测试不归入本次提交。`git diff --check` 通过。
