# Managed runtime 集成只读审查

日期：2026-10-03。审查方式：读取最新源文件与 spec，追踪调用链；未运行测试、行为样例、实现模块或真实 API。仅新增本审查文件，未修改运行代码。

范围：`task_state.py`、`progress.py`、`agent.py`、`agent_service.py`、`goal.py`、`verification.py`、`headless.py`、`ui/actions.py`，以及定位存储边界和证据生产方所需的静态引用。工作区仍由其他会话持续修改，位置以函数名为准；以下仅保留最后读取时仍存在、具有具体触发条件的问题。旧交接中的已修复问题不重复列为缺陷。

## 1. [P1] 活动 Goal 替换真实用户输入，任务要求版本不会更新

位置：`agent_service.py:223-229` 的 `AgentService._begin_task`；`task_state.py:164-171` 的 `TaskStore.begin`。

触发：当前会话存在 active Goal，任务已保存该 Goal 的 objective。用户随后提出新约束，例如“改为只读审查，后续不要运行验证”。项目文件、验证配置均未变化。

实际路径：`_begin_task` 无论 `goal_round` 是否为真，都令 `objective = goal.objective`，把这个旧文本传给 `TaskStore.begin`。后者只比较传入 objective 与 `latest_request`；二者仍相等，所以没有 revision 增长、历史追加或验收失效。新用户文本只进入聊天消息，任务投影的 `latest_request/request_history` 和已有证据仍代表旧要求。`goal_round` 目前只影响重复计数重置。

影响：新约束无法可靠进入持久任务记录，压缩或恢复后容易丢失；旧验收仍可被当作当前需求的证据。当前用户消息虽然进入模型请求，但运行时完成条件没有反映这次要求变化。

修复建议：分开长期 objective 与当前 `latest_request`。真实用户轮次按原输入更新要求版本与历史；仅由明确的自动 Goal 轮次复用旧目标，不把自动续跑提示作为新要求。`TaskStore.begin` 应接收独立的当前要求字段，或在服务中增加专门的要求更新入口。

## 2. [P1] `/verify` 审批返回后未复核输入，变化后的命令仍会执行

位置：`verification.py:169-204` 的 `VerificationService.run`。

触发：验证步骤需要 ASK，审批等待期间项目输入、验证配置或当前任务身份/版本发生变化；用户批准先前显示的操作。

实际路径：运行开始时计算 `before`，随后等待 `approval`。批准后只取得 `project_coordinator(...).async_scope()`，直接进入 `execute_process`，没有在锁内重新比较输入指纹、配置摘要或绑定任务身份。`input_after` 与 `before` 的比较直到命令结束后才发生。

影响：即使最终记录为 invalidated，命令已经针对审批等待期间变化的输入执行，可能产生副作用。协调锁能串行化已进入临界区的操作，不能证明等待前的授权输入仍适用。

修复建议：在执行临界区、真正启动进程前，重核审批绑定的输入、验证配置、task_id 与 revision；变化或无法核实时不启动该步骤，记录未执行/失效原因，并重新准备操作。重核要与执行使用同一个临界区，避免锁等待造成新的检查间隙。

## 3. [P2] 旧验证取消或失效仍会暂停同会话的新任务

位置：`verification.py:234-239` 的 `VerificationService.run.finally`；`task_state.py` 的 `record_verification/reconcile`。

触发：任务 A 启动 `/verify`；另一个入口在同一会话执行 `/task new` 建立任务 B；A 随后取消，或因异常进入 invalidated。

实际路径：`record_verification` 已按 task_id 拒绝把 A 的结果映射到 B，这是正确的。但 finally 无论映射结果如何，仍执行 `reconcile(thread_id, interrupted=True)`。此入口重新读取当前任务 B，把它设为 paused、追加“运行已中断”的 blocker；若 B 有保存指纹，还会因为未传当前指纹而失效旧验收。

影响：A 的结束状态污染了未参加该验证的新任务 B；身份隔离只保护了证据映射，没有保护后续生命周期更新。

修复建议：将中断状态更新与 record_verification 的身份核对放在同一 TaskStore 锁内，接收预期 task_id/revision。身份不同只保留原 verification 事件，不修改新任务。不要在 finally 先检查身份再无条件调用另一个未绑定身份的入口。

## 4. [P2] Goal 验证缓存只检查项目输入，遗漏任务要求版本

位置：`goal.py:227-248` 的 `GoalStore.record_verified_run`；`verification.py:250-253` 的 `verify_goal_if_configured`。

触发：Goal 的完整验证正在运行时，用户以 `/task constraint` 更新要求，项目文件不变。旧运行随后成功完成。TaskStore 正确保留旧 revision 证据、不提升当前验收。

实际路径：`record_verified_run` 只核对 goal_id、会话和执行结果，仍将旧运行写为 `verification_succeeded=True`；Goal 不保存或核对记录中已绑定的 task_id/task_revision。下一轮 helper 仅比较文件/Git 指纹，相同就返回 None，不重新验证当前任务版本。

影响：Goal 缓存显示验证有效，而任务配置验收仍为 stale。完成 guard 能阻止错误完成，但自动推进会持续跳过需要重跑的验证。已有非 manual 目标验收时，“缺少验收/等待人工确认”的暂停分支也不一定能终止这种循环。

修复建议：Goal 接收运行结果时核对当前 TaskStore 身份与 revision，保存绑定身份；复用验证缓存时同时核对当前需求版本、配置与任务配置验收。过期缓存不得阻止核心重新运行配置验证。

## 5. [P2] 非 manual 用户验收没有接入可达的有效证据生产路径

位置：`agent_service.py` 的 `_begin_task/_observe_task_tool/delivery_report`；`task_state.py` 的 `record_verification/user_decision`；`ui/actions.py` 的 `/task accept` 与 `/review`。

触发一：用户执行 `/review`。新任务的 acceptance 为空；普通读取只产生 `coverage=unknown`、指纹为空且不关联验收的操作证据。结束时 `delivery_report` 仅在 task 已保存指纹时重核输入，因此新审查没有当前指纹，也没有可支撑 reviewed 的验收。

触发二：用户登记 `/task accept feature test ...`，再成功执行相应 `/verify`。`record_verification` 只关联 `verify:<name>` 配置条件，普通工具观察也不关联目标验收；`confirm` 正确地只允许 manual。当前生产代码中的 record_evidence 调用没有把实际 test/build/run/review 结果绑定这些非配置目标的入口。

影响：只读审查始终只能得到 unverified；登记的非 manual 用户目标验收始终 pending/stale，完成 guard 无法放行。原交接第 7 项仍未闭合。配置成功与功能覆盖的分离已经建立，但目标覆盖的实际生产入口尚缺。

修复建议：为静态审查建立显式验收与独立当前指纹，并根据运行时可观察的实际范围、完整性、截断/遗漏和审查流程结束记录产生相应证据；模型结论继续作为提案。为 test/build/run 用户验收增加显式关联实际验证记录、范围和当前版本的路径，只有已声明且真实可观察的覆盖才能通过；不能把所有配置命令成功或模型“未发现问题”直接升级为目标通过。若本轮暂只支持 manual，应明确限制可登记类型和审查状态，并在这些待验收情形暂停，而不是展示不可完成的流程。

## 6. [P2] 完成工程任务后的普通聊天仍输出历史交付状态

位置：`agent_service.py` 的 `_begin_task` completed 分支及 `stream_turn` 最终交付；`headless.py` 的 finally 交付刷新。

触发：当前会话任务已 completed，用户继续普通问答，例如“你好”或询问概念；项目输入没有变化。

实际路径：`_begin_task` 对该轮返回 None，运行时 task provider 也过滤 completed。但 `stream_turn` 未保存并使用这个轮次的任务参与结果，结束时无条件调用 `delivery_report(thread_id)`；后者仍读取完成任务，因此发出历史 verified/reviewed 的 delivery 事件和 final 字段。headless finally 又无条件刷新同一报告，文本模式附加它。

影响：普通回答携带上一工程任务的验收状态，违反普通问答直接回答的边界，也让调用方把历史任务状态当作本轮交付。

修复建议：把 `_begin_task` 返回的参与任务身份保留在轮次中，仅对本轮参与的任务输出/更新 delivery；headless 同样按本轮任务或显式 `/task`、`/verify` 请求刷新。历史完成任务仍可由 `/task status` 显式查询，不能通过简单禁止所有 completed 查询来修复。

上述均为静态调用链发现，尚无运行验证；后续修复由共享文件所有者完成。工作区并行变化可能使行号移动，合并前应再次按函数名核对。
