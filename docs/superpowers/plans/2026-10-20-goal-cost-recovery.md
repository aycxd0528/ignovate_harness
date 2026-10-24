# 目标费用崩溃恢复实施计划

> **For agentic workers:** Use executing-plans to implement this plan inline, task by task. Keep the existing worktree and user changes. Do not commit without a user request.

**Goal:** 模型已经开始或已返回用量时，进程硬退出不得丢失目标费用或释放未知调用的额度。

**Architecture:** Goal 中增加有界、严格校验的 pending_cost_rounds，旧记录默认空。每个持久预算从首次预留到收尾持有独占轮次租约；预留在 provider 前落盘，实际费用在模型中间件返回调用者前计入 spent_usd，record_round 在同一账本事务中减去已经计入的费用并清除 pending。恢复只处理能取得非阻塞租约的遗留轮次，未知预留保守计费并暂停，不重放任务。

**Tech Stack:** Python 3.11+、现有 atomic_write_bytes/file_lock、asyncio；不增加依赖。

**Spec:** docs/reviews/2026-10-20-long-task-reliability.md 的硬退出费用缺口及用户的复杂长任务稳定交付目标。

## Global Constraints

- 不抹去旧目标的 spent/round/settled_round_ids，不缩减项目总体审查范围。
- 全部原生 GoalCostBudget 创建入口必须绑定目标及原生轮次 ID；子代理共享该预算。
- 账本读取或落盘失败阻止 provider 调用；未知费用不能标为实际提供商账单。
- 不跨 await 持有目标账本事务锁。租约仅用于区分活跃预算和崩溃遗留。
- 保留旧未绑定 GoalCostBudget 的独立 API 用法，兼容旧 Goal 记录；持久目标运行必须绑定。
- 用户已有 .idea 和 GUI CSS 改动不触碰；不提交、不推送。

## Review Focus

- 同时运行的子请求金额相同也必须分别结算，重复 settle/release 不能重收费。
- 实际 usage 大于预留、缺失 usage、取消及写入失败均保留保守边界。
- 正常轮次、普通续作、UI goal、headless goal、独立/共享协调均有同一费用身份。
- 活跃轮次租约不能被另一个 factory/进程当作遗留；故障后的重复恢复必须幂等。
- 已完成/暂停的旧目标仍保留已知费用，恢复不能自动执行副作用。

## Task 1：持久费用账本和预算事务

Files: create nailong/core/goal_costs.py; modify goal.py/budgets.py; add tests/test_goal_cost_recovery.py.

- [x] 用真实临时 GoalStore、原生预算和独立进程写 RED，覆盖 reserve 落盘、settle 即时记账、收尾不重收费、孤儿/活跃租约、重复恢复、损坏结构及失败关闭。
- [x] 实现费用校验、原子预留/settle/release/round finish、非阻塞租约和恢复。
- [x] 绑定预算只对原生 reservation identity 结算；收尾释放租约，清理失败不释放未知额度。
- [x] 定向回归通过，记录证据。

## Task 2：绑定所有执行入口和实际硬退出

Files: modify agent.py/agent_service.py/ui/flows.py/headless.py/core/session_coordination.py; integration tests.

- [x] 普通、UI goal、headless 和协调传入 goal_store/goal_id/round_id，并在正常/取消/异常收尾关闭预算租约。
- [x] Factory、prepare/resume 在安全入口恢复遗留；显式恢复首次发现未知调用时先暂停供核对。
- [x] 离线模型在 provider 内及 usage 交付后 os._exit，重启证明费用/预留保留且不会自动再次调用 provider。
- [x] 验证共享子代理、重复费用回执及故障写入不会双计或漏计。

## Task 3：复核、完整回归与记录

- [x] 核对所有原生预算创建点、模型中间件的预留/发布/结算顺序及失败路径。
- [x] 完整 Python、相关 JavaScript、diff check；构建/安装 smoke 如发行内容改变。
- [x] 更新项目说明和持续审查记录，明确剩余总体审查范围。

证据：费用原生定向 24 项通过；相关工作流 110 项通过。完整 Python 1765 项，45 项跳过，其余通过（183.526 秒）；JavaScript 17 项通过。wheel 安装目录实际费用事务和 CLI smoke 通过。独立复核两项 Important 均修复并重新实证，最终增量复核无新的 Critical/Important。此计划完成不代表整个长任务可靠性审查完成。
