# 会话回退与执行护栏修复计划

> **For agentic workers:** Use `executing-plans` to implement this plan task by task. 每项先运行失败测试，再实现并验证。

**Goal:** 修复分层自检中确认的 7 个行为问题，并恢复完整回归通过。

**Architecture:** 保留同进程 Agent 架构。回退只改变对话，已发生的用量继续保留；目标轮次结算与退出状态分离；子代理共享调用前 token 预算，模型完成后结算。

**Tech Stack:** Python 3.11、LangChain/LangGraph、SQLite、unittest、Textual。

**Spec:** `docs/reviews/2026-10-02-layered-architecture-self-check.md`。

## Global Constraints

- 在用户当前 `codex/nailong-v1` 工作区继续修复，保留原有未提交、已暂存和未跟踪文件。
- 不调用真实 API；使用临时数据目录和模拟模型执行回归。
- 审批默认拒绝、受保护路径优先、子代理只读的规则继续适用。
- 不增加 daemon、RPC、MCP 或操作系统沙箱。
- 不提交或暂存用户工作区中的混合改动。

## Review Focus

- 旧日志中恰好 100 个边界 ID 可能已截断：拒绝不可靠的回退。
- 连续回退：保留用量一次，不能重复记账或污染当前上下文统计。
- 取消发生在图运行、事件渲染或子任务期间：关闭事件流，保留已观察的用量，结算后传播取消。
- 多个子代理共享额度：每次模型调用均预留，失败/缺少 usage 时不能返还成免费调用。
- 目标已经暂停/完成或额度耗尽：已开始的轮次仍结算，未开始的调用不能继续。

---

### Task 1: 回退边界与用量记录

**Files:** `nailong/core/sessions.py`、`agent_service.py`、`ui/presentation.py`、`tests/test_safety_regressions.py`。

**Interfaces:** `append_event` 完整保存 `turn_start.message_ids` 和数量；`truncate_events` 保留撤回范围内的 usage/usage_missing 并标记为已回退；`rewind` 拒绝不完整边界；SessionMetrics 统计费用但不把回退用量当当前上下文。

- [x] 写失败测试：102 条旧消息回退只移除新轮；旧截断边界拒绝回退；连续回退保留所有用量且不重复；统计恢复到上一轮的上下文值。
- [x] 运行 `python -m unittest discover -s tests -p test_safety_regressions.py -v`，确认相应断言失败。
- [x] 实现边界完整性和用量保留。
- [x] 运行新测试及 `test_sessions.py`、`test_agent_service.py`、`test_dashboard_presentation.py`。

### Task 2: 目标状态与恢复护栏

**Files:** `nailong/core/goal.py`、`tests/test_safety_regressions.py`。

**Interfaces:** `record_round(..., round_id=None, stop_reason="")` 可结算暂停目标并按 round_id 去重；`prepare_round(goal_id)` 在调用前检查持久状态和上限；`resume` 拒绝耗尽额度并清除旧验证；重新绑定会话使验证失效。

- [x] 写失败测试：暂停后结算、重复轮次只入账一次、不同/相同会话恢复后要求新验证、已耗尽轮数或费用不可恢复或调用。
- [x] 运行新测试，确认失败原因对应旧分支。
- [x] 实现状态独立结算、验证绑定与调用前检查。
- [x] 运行新测试和 `test_v1_features.py`。

### Task 3: 取消时关闭与结算

**Files:** `ui/flows.py`、`headless.py`、`agent_service.py`、`tests/test_safety_regressions.py`。

**Interfaces:** 目标执行流明确关闭；轮次费用在清理路径结算；取消后持久化 paused，并继续抛出 CancelledError。UI 与 headless 使用相同 GoalStore 结算契约。

- [x] 写失败测试：事件渲染时取消、模型等待时取消、headless 取消均不漏账；已暂停目标的本轮费用正确累计。
- [x] 运行新测试，确认费用/状态断言失败。
- [x] 实现 finally 结算、流关闭、取消传播；在每轮开始检查额度。
- [x] 运行新测试、`test_headless.py` 和 `test_dashboard_flows.py`。

### Task 4: 子代理逐次调用预算与取消用量

**Files:** `nailong/core/budgets.py`、`nailong/core/usage.py`、`nailong/tools/agents.py`、`agent.py`、`tests/test_safety_regressions.py`。

**Interfaces:** 新共享 token 预算通过 ContextVar 传到 ModelAccountingMiddleware；输入按序列化请求保守预留，输出上限随余额收窄；runner 在 finally 保存 collector 的用量。保留不使用模型 middleware 的旧 worker 估算兼容。

- [x] 写失败测试：子代理下一轮输入无法容纳时零调用、并发共享预算、max_tokens 随余额收窄、缺少 usage 不免费退款、取消子代理保留先前成功调用的用量。
- [x] 运行测试，确认调用次数/余额/用量断言失败。
- [x] 实现共享逐次预算与取消安全的 usage 采集。
- [x] 运行新测试、`test_agent.py`、`test_v1_features.py`、`test_agent_service.py`。

### Task 5: 测试契约与完整回归

**Files:** `tests/test_tui.py`、`tests/test_dashboard_flows.py`、README、自检报告及本计划。

**Interfaces:** 测试替身跟随真实服务参数；价格使用真实已配置 estimator；`/count` 断言成功完成次数。流式等待加明确超时，避免用例卡死。

- [x] 同步旧替身和 Mock 位置，保留输入、审批、命令和恢复行为断言。
- [x] 运行 `.venv/bin/python -m unittest discover -s tests -v`，预期全部通过。
- [x] 独立只读审查本轮差异，修复重要发现；再运行完整回归。
- [x] 更新文档与修复记录，报告真实测试数量和剩余限制。

## 执行结果

2026-10-02：全部任务完成，新增24个回归用例，完整272个测试通过。父任务取消的集成检查补充修复 tools.py 的异步 task 注册；独立审查补充修复结算后状态显示取消的暂停路径。审查无遗留重要发现。详细证据见自检报告的修复记录及 SDD ledger。
