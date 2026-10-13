# Managed workflow：实施与本地验证

依据：`docs/superpowers/specs/2026-10-03-managed-task-runtime-design.md` 与 `docs/superpowers/plans/2026-10-03-managed-task-runtime.md`。本轮仅修改 task_state、progress、goal、verification、runner，以及专属测试和本文档。不提交、不推送、不读取认证、不调用真实 API。

## 执行记录

- Ruling：使用用户指定的共享工作区及本文档作为专属 ledger，不创建工作树或使用全计划共享 scratch；其他窗口拥有同一计划中的其他任务，避免改变其文件与记录。
- Pre-flight：TaskStore 的验收及身份由 VerificationService 生产，GoalStore 与 delivery 消费；输出覆盖规则由 output 窗口拥有，禁止用虚构的全项目覆盖让局部绑定通过。
- Ruling：针对当前代码增加行为测试，已修复的旧审查问题通过回归确认，不重复按旧行号修改。
- 已覆盖：持久恢复/需求更新；审批等待后的身份、配置、输入、权限复核；旧结果隔离；Goal 缓存及完成护栏；绑定校验；进展及队列停止；相关回归及最终只读复核。
- RED：首批 22 个专属测试发现 ASK→不同 ASK 的授权复用、binding 恢复缺校验、吞取消后的队列继续执行、同步回调异常未结算 Future、旧 Goal 证据完成新任务版。旧结果阶段污染通过独立 late run_id 复现，避免重复证据 ID 掩盖路径。新 bound_task/record_review 的三项测试确认接口缺失。
- 已实施：同锁身份上下文及审查收据、binding 的结构/来源/路径严格恢复校验、旧版及模型证据不推进 verify 阶段、ASK 绑定比较、Goal 完成前验证任务版复核、队列取消及回调异常清理。完整摘要与生成路径扫描放入 asyncio.to_thread，空 generated_paths 不再扫描；真实摘要加慢 I/O 的测试先复现事件循环被阻塞，修复后 heartbeat 可持续推进。
- 独立最终审查确认 Important 一项：Goal 摘要计算期间 Task 可被另一个 TaskStore 替换，原验证会完成新 Task；Minor 一项：record_review 分次写入在超预算失败后留下部分状态。新增 3 项测试先全部失败（两次摘要各自触发任务替换、超预算状态保留），修复后全部通过。
- 修复：Goal 两次摘要完成后用 bound_task 再复核原 task_id/revision，在同锁内 reconcile、验收、设置任务完成并保存 Goal。record_review 通过 _append_evidence 在内存组装完整证据与验收关联，仅一次 _write。独立复核未发现剩余 Critical/Important/Minor；31 项专属测试全部通过，Declined to judge: []。

## 外部接线事项（持续更新）

- 已落地接口：`with tasks.bound_task(thread_id, task_id, revision=None) as snapshot:` 在 TaskStore 跨进程锁内返回匹配的深拷贝快照，不匹配返回 None；上下文内可调用现有 reentrant mutator。**不要在此同步上下文内 await**，先完成异步读取/指纹，再进入短临界区复核身份与版本并写状态。
- 已落地接口：`tasks.record_review(thread_id, evidence, expected_task_id=..., expected_revision=..., criterion_id='review:scope')` 同锁复核身份/版；拒绝非 runtime/review/passed/complete、未知 hash、不同证据版和未覆盖当前 scope 的证据。未存在时创建运行时保留的 review:scope 验收，存在时仅接受 review/static 类型；保存独立采集的 hash，关联实际证据并一次提交。旧任务/版本返回 None。changed_paths 为空时可清空 pending_verification，否则保留。调用前后的独立 hash 与完整范围枚举由 AgentService 保证，本方法没有模型工具入口。
- Ruling：verification_binding 恢复严格验证字段、类型、来源和安全路径；验证配置删除/改名后仍保留用户旧声明供重新绑定，而非静默丢失要求。声明的 step 可以暂时不在当前配置，但不会匹配其他实际步骤，更不会提升通过。
- 当前 delivery 把每个验收的证据与整个任务 scope 比较；具体路径绑定可能在 TaskStore 为 passed、delivery 为 unverified。应由 output 定义并验证验收项自己的范围，不能通过扩大 runtime evidence.paths 掩盖不足。专属测试会覆盖保守拒绝路径。
- HookRunner 的线程执行取消不终止底层子进程；属于非本窗口文件，前一轮本地样例已经复现，留给总控/相应文件所有者处理。
- 新授权 tests/test_workflow_closures.py 的取消 fixture 已迁移：真实 ToolExecutionContext approve_once、不添加默认 allow 规则；检查真实启动、两次审批、PID 退出、取消后不产生 late 文件、Goal 验证失效及拒绝完成。等待标记使用 wait_for 且及时检查操作是否提前结束。
- 当前 checkout 没有 tests/test_goal.py，也没有 test_cancelled_command_cleans_spawned_process_group；实际仍无界挂起的是非本窗口拥有的 test_workflow_review_regressions.ReviewToolCancellationTests.test_async_shell_tool_cancellation_stops_side_effects（line 98）。总控/该测试所有者需要同样迁移审批 fixture 与有限等待。

## 验证结果

- `.venv/bin/python -B -m unittest tests.test_managed_workflow -q`：本窗口最新实际执行 **31 项，3.014 秒，全部通过**；独立复核另行执行 31 项全部通过。包含真实子进程恢复、跨进程锁、审批等待变更零启动、旧取消隔离、Goal 完成与缓存、bindings、进展、队列和两个最终审查修复。
- `.venv/bin/python -B -m unittest tests.test_workflow_verification tests.test_workflow_commands_runner tests.test_v1_features.GoalStoreTests -q`：较早 25 项全部通过。最终审查修复后与专属测试组合为 56 项，55 项通过、1 项错误：AgentRuntimeFactory 将 task_scope_provider 传给当时不支持该参数的 MemoryReadContext。共享区其他窗口随后补齐参数；排除该单项后的 24 项最新实际执行 2.804 秒全部通过，headless 单项最新实际执行 0.820 秒通过。本窗口未修改这两个外部文件；整组最终复测结果如下。
- 同一整组命令最终复测：**25 项，3.160 秒，全部通过**，包含 headless，无排除。
- `.venv/bin/python -B -m unittest tests.test_workflow_closures.FinalLifecycleTests.test_cancelled_mutating_command_invalidates_prior_goal_proof -q`：最新 1 项通过，真实取消及审批 fixture 的迁移有效。
- `tests.test_workflow_closures`：较早 15/15 通过；共享 UI 后续变化后最新 15 项有 2 项失败，见下方。不得将早期通过结果当作当前全部通过。
- 较早的一次相关测试命令误写不存在的 GoalTests，产生 loader error；已更正为 GoalStoreTests 并重跑，上述 25 项为更正后的实际结果。
- 本窗口拥有的 5 个 runtime 源文件及 2 个测试文件经 ast.parse 全部通过；无提交、推送或外部 API 调用。

## 全仓回归与交接限制

- 实际运行 `.venv/bin/python -B -m unittest discover -s tests -v`，120 秒有界包装终止。挂起位置为非本窗口拥有的 `test_workflow_review_regressions.ReviewToolCancellationTests.test_async_shell_tool_cancellation_stops_side_effects`，无审批桥导致命令未启动，而测试无界等待 pid。日志：`/tmp/nailong-workflow-full-suite-20261004.log`。不能声明全仓通过。
- 在同样 120 秒包装中只排除上述一项，实际执行 **682 项，53.613 秒，5 个失败、6 个错误**。日志：`/tmp/nailong-workflow-bounded-suite-20261004.log`。失败/错误发生时其他窗口正在修改共享区，此结果是执行时快照，须总控在接线稳定后再跑完整 suite。
- 失败：`test_cli.CliTests.test_project_argument_selects_external_root_for_both_interfaces`（启动失败）；`test_execution_display.ExecutionDashboardTests.test_tool_progress_is_visible_and_result_arrives_before_final_reply`（看不到 Ran 12 tests）；`test_safety_regressions.GoalExecutionSafetyTests.test_cancel_during_settled_goal_status_pauses_without_double_charging`（缺用户验收提前停止，而 fixture 预期取消）；`test_tui.TerminalAgentAppTests.test_dashboard_tool_group_labels_multiple_tools_and_aligns_rows`（显示契约变化）；`test_workflow_closures.FinalLifecycleTests.test_textual_rename_and_tool_details_are_visible`（tool 行不可见/不可点击）。
- 6 个 error 均在 `test_ui_approval_panel`：`render_approval_content` 当时返回 None，导致 `.plain` AttributeError 或 Textual VisualError。涉及 `ApprovalContentTests.test_readable_content_keeps_full_command_diff_and_redacts`；`ApprovalPanelTests.test_default_is_reject_and_keyboard_choices_are_explicit` 的 enter/2/3/escape 四个 subtest；`ApprovalPanelTests.test_expansion_and_placeholder_rule_do_not_approve_session`。
- 最新 closures 整文件额外失败：`FinalLifecycleTests.test_textual_memory_edit_approval_and_skill_menu_refresh`，主 screen 未进入 ApprovalScreen。UI 文件不在本窗口修改范围；未弱化可见性、可点击性或审批断言。
- 后续优化仍包括摘要增量缓存与跨轮进展判定；本轮线程化扫描解决事件循环阻塞，但未减少完整文件哈希的总工作量。progress.py 无需修改，当前分页/轮询/摘要变化及真正重复读取的边界已由行为测试验证；Goal 的粗粒度 idle_rounds 仍可能被无效工具调用重置。
