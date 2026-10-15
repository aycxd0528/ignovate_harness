# Ignovate 长程任务与地图统一优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** 落实用户确认的六项机制，将有效进展、续跑决策、项目任务、关注事项、恢复回执与反馈经验接入原生 Harness 和会话地图。

**Architecture:** 继续由 AgentService 执行、TaskStore 管理唯一任务与验收、GoalStore 管理预算及目标生命周期。项目任务关联与阶段回执为内核补充持久合同，CLI/JSON/GUI 读取同一份事实；地图不新增 Agent 循环或权限来源。

**Tech Stack:** Python 3.11+、现有 SQLite/JSON 存储、原生 JavaScript/CSS/SVG、unittest 与离线 Playwright fixture。

**Spec:** `docs/reviews/2026-10-14-loopx-map-optimization-report.md`

## Global Constraints

- 用户于 2026-10-15 指示“开始实施”；按已经确认的统一报告持续推进。
- 保留当前未提交的 trace、地图拖动与 IDE 改动；沿用项目此前记录的现有源码路径开发方式，不重置、暂存或提交混合改动。
- 主代理工具权限、子代理只读、项目执行锁、预算、输入指纹、验收覆盖与精确分叉继续有效。
- 回合结束不代表任务完成；模型自述不晋升为可信证据；未知、缺失与过期分别显示。
- 已发生的模型调用费用仍计入，恢复不能重放副作用或重复计费。
- 不新增外部依赖、真实模型测试、常驻调度或隐式授权；等待使用明确状态与显式恢复。
- 会话父子关系与任务依赖使用不同的数据合同及图例。

## Review Focus

- 旧任务记录与链接任务新建/修订不能破坏唯一权威或绕过版本锁。
- 模型伪造新 evidence ID、过期验证或重复失败不得算有效进展。
- 用户暂停、审批、预算与未解决等待必须在新模型调用前生效。
- 接受请求后拖动、重载、布局延迟与实时更新不能丢坐标或重发。
- 恢复阶段、反馈确认与用户决定重复提交必须幂等，且不扩大执行权限。

## Task 1: 有效进展和原生目标续跑

**Files:** 新增 `nailong/core/goal_control.py`、`tests/test_goal_control.py`；修改 `nailong/core/goal.py`、`ui/flows.py`；新增流程集成回归。

**Interfaces:** `assess_goal_progress(before, after) -> dict` 返回 status、reason、evidence_ids、acceptance_ids；`decide_goal_turn(goal, task, *, progress=None) -> dict` 返回 action/reason/resume_condition。纯函数仅读取当前可信任务证据，明确 unknown。Goal 保留最近进展及无增量窗口，调用失败/取消仍结算费用。

- [x] 写无增量、有效负结果、伪造/过期证据、需求改变、等待、额度与暂停回归，先确认失败。
- [x] 实现纯函数与有界窗口，将准入放在原生模型流之前。
- [x] 每轮记录任务前后快照差异，发出 control/progress 事件；保持假服务兼容及原验证规则。
- [x] 跑目标、进展、工作流测试并记录结果。

## Task 2: 项目任务身份和显式跨会话交接

**Files:** 新增 `nailong/core/work_items.py`、`tests/test_work_items.py`；修改 `nailong/core/task_state.py`，必要时扩展任务查询入口。

**Interfaces:** TaskStore 提供 `list_work_items() -> list[dict]`、`link_work_item(thread_id, task_id, *, expected_revision) -> dict`；稳定 task_id 即工作项身份。`snapshot(thread_id)` 返回当前执行会话与 canonical owner 标识，要求/验收只有一份权威文件。现有 begin/new、bound_task、scope、atomic_json 与跨进程锁必须兼容。可选 `set_wait(thread_id, *, reason, condition, expected_revision)` / `clear_wait(...)` 保留明确等待条件。

- [x] 写旧任务迁移、共享修订、项目隔离、链接到有任务会话拒绝、新建独立任务、来源变化/旧写回拒绝测试，确认失败。
- [x] 实现懒迁移、稳定工作项索引、显式交接和 canonical 锁。
- [x] 保持正常任务读取与上下文投影兼容，不复制权限或目标预算。
- [x] 跑任务/验收/会话回归，提供主集成需要的准确接口。

## Task 3: 会话地图密度、路径、连线与内核投影

**Files:** 修改 `nailong/gui/static/map.js`、`style.css`；新增 `tests/gui-control-map.test.mjs` 和浏览器回归。主集成负责 app.js。

**Interfaces:** createMap 增加可选 `control={items:[],attention:[],receipts:[],feedback:[]}`、`onControl`；节点可带 task_summary/progress/continuation。UI 无新执行能力。几何常量共享，保留现有布局、草稿、发送身份、实时拖动和 onSave 合同。

- [x] 写去重统计、当前路径/全部、共享祖先、草稿隐藏提示、几何、定位及动态状态测试，确认失败。
- [x] 加概览/展开、路径强调、方向箭头、状态汇总与图例，保留正文选取及引用。
- [x] 展示任务/关注/回执/经验来源；通过 onControl 显式触发关联或确认动作。
- [x] 跑 pure map、原生实时拖动、旧布局和规模测试；提供截图与结果。

## Task 4: 阶段回执、关注投影与反馈经验

**Files:** 新增 `nailong/core/control_ledger.py`、`nailong/core/feedback.py`、`nailong/gui/work_control.py`、对应 Python 测试；修改 GUIController 与必要的 CLI 查询/命令接入。

**Interfaces:** ControlLedger 保存按 run_id/action_id 去重的 started/effects_observed/task_committed/cost_settled/closed 状态；证据与预算记录仍属现有 owner。Feedback 使用现有 MemoryStore stage/commit 写入候选、确认、撤销经验，绑定来源任务/回合、项目、修订和输入版本。

- [x] 写阶段重入/中断/未知、关注去重/来源、候选确认/撤销/作用域/版本冲突测试，确认失败。
- [x] 实现有界安全存储与只读 projection，不将 receipt 当新费用账本或授权。
- [x] GUI 运行生命周期写回回执；snapshot 输出真实 task_summary 与 control，读取不初始化模型。
- [x] 提供显式 link/wait/ready/feedback 命令，忙碌或旧修订拒绝；复用原审批入口。
- [x] 将相同状态接入本地 CLI/JSON 查询，跑控制器、服务器、预算和记忆回归。

## Task 5: 联合验证、文档与独立审查

**Files:** 更新 `docs/GUI.md`、统一报告和本次验证记录；扩展离线浏览器 fixture/端到端测试。

- [x] 验证等待 → 新事实 → 跨会话任务接续 → 人工确认 → 写回 → 重启，内核与地图一致。
- [x] 运行完整 Python 离线用例、相关 Playwright 回归、Node 语法/pure 测试与 wheel smoke。
- [x] 请求独立源码审查，修复重要问题并验证原症状。
- [x] 逐项核对统一报告，明确已实现与实验边界，展示结果；不隐式提交或发布。

## 完成记录

2026-10-15：六项机制与地图整合完成。独立审查原症状关闭，全量 1539 项（1495 通过、44 跳过）、最终针对性 74 项、纯 JS 11 项、9 组浏览器及 Electron/wheel 检查通过。详见 `docs/reviews/2026-10-15-loopx-control-map-implementation.md`。改动保留在当前工作区，不提交或发布。
