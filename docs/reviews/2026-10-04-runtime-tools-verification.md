# 工具执行边界验证交接（2026-10-04）

最新工具专项验证通过：获授权迁移的三个旧测试文件与 `tests/test_managed_tools.py` 合计 **80 个用例全部通过，退出码 0**，包括新增 managed tools 的 43 个用例。此前旧工具回归的 8 个失败已按授权契约迁移；历史扩大仓库回归 611 个用例仍曾有 26 个失败、2 个错误，且排除了一个无限等待的旧取消测试。迁移后未重跑全仓，不代表全仓绿色验收。最新变更与理由见本文末尾追加交接。

用户的新验证授权替代此前“只做静态审查”的阶段限制。本轮新增和运行本地测试，未调用真实模型/提供商 API，未提交或推送，未修改其他窗口的生产文件和测试。测试使用临时目录、真实本地文件、真实 PermissionEngine/LangChain ToolMessage、少量本地子进程；用事件控制并发和取消。统一全仓回归后续由总控执行，本窗口已停止扩大测试。

## 发现并修复的实际缺陷

`ExecutionTool` 覆盖了 `_arun()`，但 `StructuredTool.ainvoke()` 在 `coroutine` 字段为空时仍回退为线程中的同步 `invoke()`。真实 `.ainvoke()` 因而进入 `invoke_sync()`：异步审批返回 `execution_error`，取消也绕开本执行层的线程 join。

首次 35 用例运行出现 2 个失败、2 个错误：异步审批等待/允许、审批期间取消、重复取消后的工作线程等待。随后用最小复现读取实际错误，并检查安装依赖的 `langchain_core/tools/structured.py` 调用路径，确认不是审批服务或文件层的问题。

唯一生产修复位于 `tools.py`：显式覆盖 `ainvoke()`，进入 `BaseTool.ainvoke()`，保留 BaseTool 参数验证、真实 ToolCall ID 和 error ToolMessage 格式化，使用本适配器的 `_arun()`。没有设置伪造 coroutine、放开默认权限或改变文件安全约束。修复后原 10 个异步用例全部通过，随后专项增加到 43 个全部通过。

`read_file.content_offset` 此前已实现，本轮用真实 ToolMessage 验证：Unicode 多行与首行字符偏移、分页续读、空文件和超出文件的空选择均符合绝对字符位置契约。

## 具体覆盖

| 边界 | 已验证的行为 |
| --- | --- |
| sync/async 权限 | DENY 在 handler 和前置钩子之前终止；结构化 error、started=False；观察含 thread_id/call_id |
| ASK | 缺桥接、拒绝、未知决定零执行；同步/异步批准路径；可信 action.profile；子代理 ASK 不调用审批 handler |
| 只读限制 | 即使父引擎有 allow，子代理仍无法执行变更；plan 动态 read_memory 使用可信 tool_spec |
| 钩子/规则 | 前置异常没有开始 handler 或 post；前置后重新检查 deny；观察失败不重放写入 |
| 文件版本 | 批准期间外部修改、相同内容 inode 替换、新建目标出现、GraphInterrupt 后同 Call ID 续执行均不能覆盖新的用户状态 |
| 读取资格 | 未读/部分读不能完整覆盖；同版本页面合并；缺页/跨版本拒绝；content_offset 的手算值；整体 JSON 裁剪撤销 full-read |
| 搜索 | list/glob/literal/真实 rg 逐文件 deny；候选 ASK 跳过而非广泛审批；大文件跳过与不完整覆盖；长上下文有界；缺 rg 不改变 regex 语义 |
| 变更协调 | 不同 FileSession 在同项目复用 gate；异步命令持锁期间同步真实文件写入等待；取消等待者不泄漏 lock |
| 取消 | 同步工作线程开始后重复 cancel 仍等待完成，再发 interrupted；审批等待时取消 started=False；明确批准的真实异步命令退出且不产生延迟副作用 |
| schema/结果 | 严格类型、未知字段、offset/limit/context/timeout/content 超界拒绝且零 handler；注册 schema 与适配器相同；失败命令的 ToolMessage.status=error |
| 日志/归档 | 同步大量 Unicode 日志保留首尾无字符解码损坏；JSON 有界、归档脱敏、分页、私有权限、未知引用/任意路径拒绝；归档截断显式；读归档不恢复文件 full-read |

## 实际命令和结果

1. 初始历史回归：

```sh
.venv/bin/python -m unittest tests.test_nailong_files tests.test_permissions -q
```

33 个用例，1 个失败（旧“缺 rg 自动字面量回退”预期），退出码 1。

2. 首轮新增用例：

```sh
.venv/bin/python -m unittest tests.test_managed_tools -v
```

35 个用例，2 个失败、2 个错误，退出码 1，随后定位和修复上述 async 入口缺陷。修复后 `tests.test_managed_tools.ManagedAsyncToolsTests` 为 10/10 通过；新增其他边界用例后专项为 42/42 通过。

3. 最终专项命令：

```sh
.venv/bin/python -m unittest tests.test_managed_tools -v > /tmp/2026-10-04-runtime-tools-new-tests.log 2>&1
```

----------------------------------------------------------------------
Ran 43 tests in 0.968s

OK

退出码 0，43 个用例，无跳过。实际 rg 及真实本地命令用例执行成功。

4. 旧工具回归：

```sh
.venv/bin/python -m unittest tests.test_managed_tools tests.test_tools tests.test_tool_registry tests.test_nailong_files tests.test_local_tools tests.test_permissions -v > /tmp/2026-10-04-runtime-tools-focused.log 2>&1
```

99 个用例，91 通过、8 失败、无错误，退出码 1。失败如下：

- `test_all_tool_results_redact_the_configured_model_key (tests.test_tools.LangChainToolsTests.test_all_tool_results_redact_the_configured_model_key)`
- `test_build_tools_exports_the_registered_chat_tools_in_stable_order (tests.test_tools.LangChainToolsTests.test_build_tools_exports_the_registered_chat_tools_in_stable_order)`
- `test_init_profile_only_exposes_context_write_target (tests.test_tools.LangChainToolsTests.test_init_profile_only_exposes_context_write_target)`
- `test_review_profile_is_read_only_scoped_and_caps_unique_files (tests.test_tools.LangChainToolsTests.test_review_profile_is_read_only_scoped_and_caps_unique_files)`
- `test_tool_result_is_json_and_preserves_chinese_text (tests.test_tools.LangChainToolsTests.test_tool_result_is_json_and_preserves_chinese_text)`
- `test_profile_filters_preserve_registry_order (tests.test_tool_registry.ToolRegistryTests.test_profile_filters_preserve_registry_order)`
- `test_registry_order_is_stable_and_includes_p0_tools (tests.test_tool_registry.ToolRegistryTests.test_registry_order_is_stable_and_includes_p0_tools)`
- `test_grep_without_ripgrep_uses_literal_fallback_only (tests.test_nailong_files.FileSessionTests.test_grep_without_ripgrep_uses_literal_fallback_only)`

已经核对失败栈：5 项固定工具列表没有包含新增 read_tool_result；1 项对原始 JSON 逐字断言不允许 status/started 新字段；1 项命令脱敏 fixture 没有提供审批而被 approval_required 拒绝；1 项要求缺 rg 时自动 literal 回退，与明确失败保护冲突。没有为了这些断言放宽生产权限或搜索语义，旧测试迁移由总控负责。新的授权路径脱敏/能力引用用例继续通过。

5. 标准全仓命令：

```sh
.venv/bin/python -m unittest discover -s tests -v > /tmp/2026-10-04-runtime-tools-full-suite.log 2>&1
```

挂在 `test_workflow_review_regressions.ReviewToolCancellationTests.test_async_shell_tool_cancellation_stops_side_effects`：fixture 未提供审批桥接，命令被拒绝后仍无限 `while not pid.exists()`。已向仅属于此运行的进程发送 SIGINT，退出码 130；没有把不完整运行计为通过。新测试 `test_approved_async_command_cancellation_stops_process_and_late_effect` 明确批准并设置等待时限，验证真实取消路径，旧用例还需迁移。

6. 随后用标准 unittest discovery 加显式选择器排除上述唯一无限等待用例，未改动测试源文件：

```python
import unittest
excluded = 'test_workflow_review_regressions.ReviewToolCancellationTests.test_async_shell_tool_cancellation_stops_side_effects'
def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item
suite = unittest.defaultTestLoader.discover('tests')
selected = []
for case in cases(suite):
    if case.id() == excluded:
        print('EXCLUDED_UNBOUNDED_WAIT: ' + case.id(), flush=True)
    else:
        selected.append(case)
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
raise SystemExit(not result.wasSuccessful())
```

通过 `.venv/bin/python - <<'PY' > /tmp/2026-10-04-runtime-tools-bounded-suite.log 2>&1` 执行。611 个用例，26 个失败、2 个错误，退出码 1；失败含 subTest，不能按简单减法声称剩余全部通过。这次已启动运行结束后，遵照总控指令停止扩大测试。以下为完整失败/错误标题：

- `test_write_tool_pauses_then_executes_only_after_approval (test_agent.AgentRuntimeTests.test_write_tool_pauses_then_executes_only_after_approval)`
- `test_mode_filtered_tools_are_the_only_tools_counted (test_context_budget.ContextBudgetTests.test_mode_filtered_tools_are_the_only_tools_counted)`
- `test_async_service_uses_real_langgraph_interrupt_and_resume (test_agent.AgentRuntimeAsyncTests.test_async_service_uses_real_langgraph_interrupt_and_resume)`
- `test_init_requires_approval_for_creation_and_overwrite (test_agent.AgentRuntimeAsyncTests.test_init_requires_approval_for_creation_and_overwrite)`
- `test_multiple_approval_decisions_follow_request_order (test_agent.AgentRuntimeTests.test_multiple_approval_decisions_follow_request_order)`
- `test_review_profile_auto_rejects_mutating_actions (test_agent_service.AgentServiceTests.test_review_profile_auto_rejects_mutating_actions)`
- `test_plain_project_command_switches_root_and_starts_fresh_history (test_cli.CliTests.test_plain_project_command_switches_root_and_starts_fresh_history)`
- `test_run_cli_switches_threads_after_turn_limit (test_cli.CliTests.test_run_cli_switches_threads_after_turn_limit)`
- `test_run_turn_prompts_for_each_tool_and_resumes_in_order (test_cli.CliTests.test_run_turn_prompts_for_each_tool_and_resumes_in_order)`
- `test_catalog_small_dependencies_within_actual_io_budget_remain_observed (test_managed_memory.ManagedMemoryTests.test_catalog_small_dependencies_within_actual_io_budget_remain_observed)`
- `test_invalid_shapes_and_excessive_metadata_do_not_read_dependencies (test_managed_memory.ManagedMemoryTests.test_invalid_shapes_and_excessive_metadata_do_not_read_dependencies) (overrides="{'state': 'confirmed'}")`
- `test_source_size_and_operation_limits_are_explicit (test_managed_memory.ManagedMemoryTests.test_source_size_and_operation_limits_are_explicit)`
- `test_grep_without_ripgrep_uses_literal_fallback_only (test_nailong_files.FileSessionTests.test_grep_without_ripgrep_uses_literal_fallback_only)`
- `test_profile_capabilities_match_real_registered_tools (test_prompt_assembly.PromptAssemblyTests.test_profile_capabilities_match_real_registered_tools) (profile='init')`
- `test_profile_capabilities_match_real_registered_tools (test_prompt_assembly.PromptAssemblyTests.test_profile_capabilities_match_real_registered_tools) (profile='review')`
- `test_profile_capabilities_match_real_registered_tools (test_prompt_assembly.PromptAssemblyTests.test_profile_capabilities_match_real_registered_tools) (profile='plan')`
- `test_subagent_receives_readonly_capabilities_without_parent_memory (test_prompt_assembly.PromptAssemblyTests.test_subagent_receives_readonly_capabilities_without_parent_memory)`
- `test_cancel_during_settled_goal_status_pauses_without_double_charging (test_safety_regressions.GoalExecutionSafetyTests.test_cancel_during_settled_goal_status_pauses_without_double_charging)`
- `test_model_pausing_goal_still_settles_the_entire_round (test_safety_regressions.GoalExecutionSafetyTests.test_model_pausing_goal_still_settles_the_entire_round)`
- `test_child_stops_before_next_input_exceeds_remaining_budget (test_safety_regressions.SubagentBudgetSafetyTests.test_child_stops_before_next_input_exceeds_remaining_budget)`
- `test_profile_filters_preserve_registry_order (test_tool_registry.ToolRegistryTests.test_profile_filters_preserve_registry_order)`
- `test_registry_order_is_stable_and_includes_p0_tools (test_tool_registry.ToolRegistryTests.test_registry_order_is_stable_and_includes_p0_tools)`
- `test_all_tool_results_redact_the_configured_model_key (test_tools.LangChainToolsTests.test_all_tool_results_redact_the_configured_model_key)`
- `test_build_tools_exports_the_registered_chat_tools_in_stable_order (test_tools.LangChainToolsTests.test_build_tools_exports_the_registered_chat_tools_in_stable_order)`
- `test_init_profile_only_exposes_context_write_target (test_tools.LangChainToolsTests.test_init_profile_only_exposes_context_write_target)`
- `test_review_profile_is_read_only_scoped_and_caps_unique_files (test_tools.LangChainToolsTests.test_review_profile_is_read_only_scoped_and_caps_unique_files)`
- `test_tool_result_is_json_and_preserves_chinese_text (test_tools.LangChainToolsTests.test_tool_result_is_json_and_preserves_chinese_text)`
- `test_cancelled_mutating_command_invalidates_prior_goal_proof (test_workflow_closures.FinalLifecycleTests.test_cancelled_mutating_command_invalidates_prior_goal_proof)`

两项错误详情：`test_write_tool_pauses_then_executes_only_after_approval` 的 pending.interrupts 为空导致 IndexError；`test_mode_filtered_tools_are_the_only_tools_counted` 的默认 review 根目录不存在导致 FileNotFoundError。其他根文件、memory、prompt、goal、accounting 的失败仅报告，未改动它们；总控应对当前快照逐项核对旧 fixture 迁移与真实缺陷，不能把它们全部假定为旧断言。

首次被中断运行中还出现以下失败/导入错误，未出现在后一轮完成运行的失败标题中（共享目录在此期间有其他窗口持续修改，不能据此推断跨窗口修复归因）：

- `test_managed_delivery.ReviewCoverageTests.test_duplicate_call_id_cannot_fill_another_page`
- `test_managed_delivery.ReviewCoverageTests.test_each_selected_file_requires_its_own_input`
- `test_managed_delivery.ReviewCoverageTests.test_empty_file_requires_a_successful_explicit_read`
- `test_managed_delivery.ReviewCoverageTests.test_evidence_does_not_persist_original_file_or_model_prose`
- `test_managed_delivery.ReviewCoverageTests.test_failed_truncated_and_malformed_pages_do_not_count`
- `test_managed_delivery.ReviewCoverageTests.test_historical_calls_do_not_prove_current_input`
- `test_managed_delivery.ReviewCoverageTests.test_incomplete_selection_or_skipped_file_blocks_workflow_evidence`
- `test_managed_delivery.ReviewCoverageTests.test_inconsistent_total_for_one_version_cannot_claim_complete`
- `test_managed_delivery.ReviewCoverageTests.test_missing_page_is_not_replaced_by_read_complete_flag`
- `test_managed_delivery.ReviewCoverageTests.test_model_claim_does_not_create_file_coverage`
- `test_managed_delivery.ReviewCoverageTests.test_outside_scope_page_cannot_satisfy_selected_file`
- `test_managed_delivery.ReviewCoverageTests.test_overlapping_pages_do_not_add_duplicate_characters`
- `test_managed_delivery.ReviewCoverageTests.test_same_version_pages_cover_only_after_both_are_consumed`
- `test_managed_delivery.ReviewCoverageTests.test_unchanged_independent_input_and_completed_flow_are_required`
- `test_managed_delivery.ReviewCoverageTests.test_version_change_cannot_merge_old_and_new_halves`
- `unittest.loader._FailedTest.test_managed_prompts`

所有原始输出仍保留在上述 /tmp 日志中。

## 未覆盖和总控接线

- 不是全仓/用户目标验收；611 用例扩大运行明确非绿色，一个旧无限等待用例未完成。
- 未调用真实模型 API，未验证真实 provider tool-calling/计费、网络异常、流式模型请求。
- GraphInterrupt 版本用例覆盖执行上下文缓存与重入；未在本专项独立验证完整服务 UI 审批链、进程重启或持久命令幂等。全仓 Agent 审批用例有上述失败，由总控处理。
- 并发采用受控事件和少量线程/协程，未进行长时压力、跨进程/跨机锁验证；文件最后检查与外部替换之间仍存在此前交接说明的 OS 竞态边界。
- 本专项不证明 review 覆盖的完整服务 evidence 消费链。content_offset 不能绕过 result_truncated/版本检查；档案和 read_complete 不构成用户 reviewed/accepted。
- 归档 1 MB 上限已测试；128 条/20 MB 累积预算、磁盘满、恶意外部归档篡改和长期清理未做故障注入。
- 未验证全部可用 Skill 资源/任务 runner 预算组合和所有 approval 答案类型。
- 总控已反馈并已从源文件确认：PermissionEngine 支持 tool_spec；子代理 config 包含父 thread_id；core/processes 采用 HeadTailBuffer。未编辑这些文件，统一接线仍由总控负责。

## 本窗口产物和快照

新增专属测试 `tests/test_managed_tools.py`、本报告；生产修复仅 `tools.py` 的 async ainvoke 入口。此前 content_offset 变更保留。不修改别人的测试、root agent/service/permissions/processes/UI，不 commit。

报告整理时间：2026-10-04 02:52:53 Asia/Shanghai

以下为报告整理时工具模块和专属测试的 SHA-256；共享 root 模块仍可能继续变化：

```text
576df1a5eb05a4ac39cc78db0194594b3ce82807d8ea291db57e2a1cca12721d  tools.py
c1d269e96fd67f74f748155e2e01045bdfecd95be7872dbff28b818e752a65dd  local_tools.py
abcb15cf2cd344d2f6366f6721b842a7f52eec494be774a69efa835b0a8cfa84  nailong/tools/files.py
f004a1c3adfc3346cce4638af22adc8041a8954c74d7b92a90d82bc409658aff  nailong/tools/registry.py
cebd3df982f15dfec1c1e11e2373f3fc4ec7ca718a26b25832221eee312306a3  nailong/tools/execution.py
e9f501c0611b77941cf1b8c14dbad910d3ba270046cc1615316823e4c5eeac9d  nailong/tools/coordination.py
8490db093455ce200ee54e6065818613da79d6bfa34e5d1d9068a5ea9eeb5d6a  nailong/tools/schemas.py
e41b647d776384da586fe6f34b57ff7dfe0a2d5e1cf6f65842c7db6ca444625a  nailong/tools/results.py
cc3031052d99d013d10f2bbb1c8a616f81ece731fbd38705eb9aa2e5b3bf6b86  tests/test_managed_tools.py
```


## 追加交接：旧测试迁移与默认项目上下文验证

总控额外授予本窗口独占 `tests/test_tools.py`、`tests/test_tool_registry.py`、`tests/test_nailong_files.py`。只修改这三个授权文件；保留 managed tools 测试，不修改 test_agent/main/service/permissions/processes/UI 或其他窗口的测试，不改变生产默认权限，不运行全仓。

### 八项旧失败的分类及迁移理由

| 原失败类别 | 数量 | 迁移方式 |
| --- | --- | --- |
| 固定工具列表缺 read_tool_result | 5 | 在 chat/init/review 的稳定注册顺序与能力集合中纳入该分页工具；不移除生产能力来凑旧断言 |
| 逐字比对旧 JSON 字符串 | 1 | 用真实 UTF-8 临时文件读取，解析业务字段并检查 status/started 和中文无转义显示；允许新增安全元字段，不弱化内容断言 |
| 未提供审批桥接的命令脱敏 fixture | 1 | 在测试中显式构建 approve 的 ToolExecutionContext，执行真实本地 printf，先断言 ok 再验证敏感哨兵替换；另增加无桥接命令零副作用用例 |
| 缺 rg 时将 regex 静默当 literal | 1 | regex 明确要求 backend_unavailable（missing backend）、无 matches 和不完整 coverage；独立 literal 模式用例确认仅匹配字面原文 |

额外修正和增强：

- 两个工具测试类都使用存在的临时项目及显式 FileSession；review fixture 创建真实目录/文件，避免全局 PROJECT_ROOT mock 或残留不存在根目录。
- 原受保护路径、init 精确路径、review 范围和 20 文件上限断言保留；受保护/init 错误进一步要求 started=False，并验证文件未变或未生成。
- 新增批准后的已有文件覆盖用例，未读取和部分读取仍为 read_required 且原文不变；只有同一会话完整读取后才允许替换。
- registry 变更串行测试不再 mock 已不在调用链的 local_tools.write_file。现在通过真实适配器、真实 edit/write/本地 command，预先完整读取两个不同目标，用 barrier 同时提交，并在真实原子写入和命令捕获下面记录并发区间；三种操作都实际完成且最大重叠为 1。
- 原 rg timeout 用例曾 mock 已不使用的 subprocess.run，无法证明超时路径。现在在实际有界捕获接口注入 TimeoutExpired，要求 search_backend_failed 且无 matches，不允许任何 literal 回退。

### 默认根目录来源

已再次核对所有 owned 根目录入口：`tools.build_tools()` 使用 `FileSession()`，`registry.build_tool_specs()` 同样使用 `FileSession()`；`FileSession.__init__` 默认调用 `local_tools.selected_project_root()`。文件操作和同步命令也通过 selected_project_root 或会话绑定根目录。没有发现 owned 工厂直接从全局 PROJECT_ROOT 构建默认会话的残留，因此本轮没有添加重复的生产根目录修改或全局赋值。

新增 `test_default_builders_follow_context_project_in_parallel_threads`：两个不同的真实项目有同名 identity.txt；两个线程各自在 use_project_root 上下文中直接调用默认 build_tools() 和默认 build_tool_specs()，不传 file_session/root。barrier 保证两套构建并发交错，随后真实读取和显式批准的新建都分别落在正确根目录，父目录没有跨项目生成文件，调用者上下文和全局 PROJECT_ROOT 未改变。此验证覆盖 owned 工厂的默认上下文消费，CLI 启动异常是否被 main 吞下仍由总控的集成测试负责。

### 本轮实际命令和结果

```sh
.venv/bin/python -m unittest tests.test_tools tests.test_tool_registry tests.test_nailong_files tests.test_managed_tools -v > /tmp/2026-10-04-runtime-tools-migrated-tests.log 2>&1
```

第一轮完成旧测试迁移为 79/79 通过；收到根目录追加要求后，增加一个并发项目隔离用例，再执行相同专项命令：

```text
----------------------------------------------------------------------
Ran 80 tests in 1.170s

OK
```

退出码 0，80 个用例无失败、无错误、无跳过。没有运行全仓或 API，没有 commit。前文 99 用例/8 失败及 611 用例/26 失败、2 错误属于迁移前真实历史证据，已保留，不能据本轮专项结果宣称所有其他根模块问题已经修好；统一回归留给总控。

追加整理时间：2026-10-04 11:53:02 Asia/Shanghai

当前授权文件与工厂 SHA-256：

```text
717ffe0a5edf72329ef9985b27e6d3ab3615e20fcecdb953cdb40dd5a6c62cc9  tests/test_tools.py
de291c6f1b4fb7391247094ae8d2ce3cab8a123f7e839542257f328ee21f986c  tests/test_tool_registry.py
2f0d0bf630f72a1954c5fd3a95b7bdb7ccb333a26c02d41c0568230a5d2fc140  tests/test_nailong_files.py
cc3031052d99d013d10f2bbb1c8a616f81ece731fbd38705eb9aa2e5b3bf6b86  tests/test_managed_tools.py
576df1a5eb05a4ac39cc78db0194594b3ce82807d8ea291db57e2a1cca12721d  tools.py
f004a1c3adfc3346cce4638af22adc8041a8954c74d7b92a90d82bc409658aff  nailong/tools/registry.py
```
