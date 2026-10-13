# 输出协议与审查流程验证记录

日期：2026-10-03

改动范围：`agent.py` 的输出、交付核查与审查策略；`ui/actions.py` 的差异批次 JSON 输入及准备状态；`tests/test_review_protocol.py`、README 和设计说明。

## 本次行为验证

新测试先在旧实现上失败：5 个用例未获得批次 JSON 数据或准备状态。落实后，连同当时的提示词组装、Git 与命令用例共 28 项通过。

随后执行：

```bash
.venv/bin/python -m unittest tests.test_review_protocol tests.test_workflow_git tests.test_workflow_commands_runner tests.test_workflow_review_regressions tests.test_permissions tests.test_headless -q
```

结果：65 tests，OK。覆盖批次与工具白名单、版本选择、重命名与删除、特殊文件名和补丁正文、截断与跳过、空选择、权限、取消及无头输出的相关回归。

最终文案修正后，再加入工具过滤和只读子代理的两个真实 Agent 图离线组装用例运行同一组测试：67 tests，OK，退出码 0。

独立只读检查使用项目 `requesting-code-review` 技能。检查中发现“无命令工具”与“不能声称独立审查完成”被错误关联，已修正为分别要求真实测试证据与独立审查证据；同时明确非差异审查标记当前版本行号。复查未发现本次范围内剩余的可确认问题。并行记忆改造和真实模型效果不在该独立检查结论中。

## 全量验证限制

执行 `.venv/bin/python -m unittest discover -s tests -v`：468 tests，6 failures、2 errors，退出码 1。

运行期间记忆实现与对应测试有并行变更，用户已确认另一聊天正在修改该项目。以下是该次全量运行的实际失败记录，不代表并行工作结束后的最终状态，也没有因此覆盖或回退其改动：

- `ERROR: test_model_inputs_share_budget_across_multiple_reads_and_later_turns (test_prompt_assembly.PromptAssemblyTests.test_model_inputs_share_budget_across_multiple_reads_and_later_turns)`
- `ERROR: test_doctor_without_site_packages_still_produces_json (test_workflow_closures.FinalMetricsTests.test_doctor_without_site_packages_still_produces_json)`
- `FAIL: test_explicit_small_core_budget_keeps_source_and_reports_omission (test_context_memory.ContextMemoryTests.test_explicit_small_core_budget_keeps_source_and_reports_omission)`
- `FAIL: test_context_report_does_not_parse_memory_content_as_catalog (test_prompt_assembly.PromptAssemblyTests.test_context_report_does_not_parse_memory_content_as_catalog)`
- `FAIL: test_next_runtime_refreshes_memory_and_preserves_user_store_boundary (test_prompt_assembly.PromptAssemblyTests.test_next_runtime_refreshes_memory_and_preserves_user_store_boundary)`
- `FAIL: test_profile_capabilities_match_real_registered_tools (test_prompt_assembly.PromptAssemblyTests.test_profile_capabilities_match_real_registered_tools) (profile='plan')`
- `FAIL: test_topic_is_read_only_after_actual_model_tool_call (test_prompt_assembly.PromptAssemblyTests.test_topic_is_read_only_after_actual_model_tool_call)`
- `FAIL: test_registry_order_is_stable_and_includes_p0_tools (test_tool_registry.ToolRegistryTests.test_registry_order_is_stable_and_includes_p0_tools)`

具体表现包括记忆工具命名与注册不一致、记忆快照缺失 `rendered_context`、刷新与小预算行为不满足断言，以及 `doctor -S` 在 `MemoryStore` 导入 `yaml` 时失败。它们涉及并行进行的记忆工作，需要在工作区稳定后再跑全量测试，当前不能宣称整仓测试通过。

## 模型效果限制

没有调用外部模型。这些离线测试验证输入生成、现有执行接口和权限边界，不证明真实模型已经减少误报或完全遵守新文案。人工评估场景见设计说明。
