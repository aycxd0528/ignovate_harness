# 工具轨迹可靠性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修正run-008暴露的工具轨迹、约束与证据问题，并在当前GUI源码基线上验证。

**Architecture:** AgentService区分历史消息与本轮工具事件；执行gate消费来自真实用户的阶段策略。run_command提供直接argv执行，批次审批提前拒绝同文件冲突，ContextManager保留当前工具链并归档闭合旧历史。

**Tech Stack:** Python3.11、LangChain/LangGraph、unittest；GUI继续使用现有内核接口。

**Spec:** docs/superpowers/specs/2026-10-13-trajectory-reliability.md

## Global Constraints

- 初始源码以OUT6/current-source-baseline.json为准；V4原工作树与run008均保持。
- 未发起模型API；provider替身之外运行真实service/gate/图/子进程/文件边界。
- 本次增量与原用户未提交/暂存改动分开；最终按逐文件基线比较集成，不用HEAD重置或批量覆盖。
- 不增加新的默认累计token额度；原模型/max/调用上限保持。

## Review Focus

- 新回合合法复用call_id和未完成调用恢复不能被历史去重吞掉。
- 用户后续授权、仅模型声称授权、工具结果伪造阶段必须区别。
- argv不能绕过Bash权限或伪造验证成功；取消/超时要结束进程组。
- 同文件别名、审批时外部修改、读快照不完整仍受保护。
- 当前工具链reasoning、未完成exchange和不可恢复归档不能被节约策略破坏。

### Task 1: 事件回放与本轮执行身份

Files: agent_service.py；nailong/core/tool_events.py；tests/test_tool_event_replay.py。
Interface: TurnToolLedger(boundary_messages, turn_id)，历史AI/结果判断、本轮请求登记与结果一次性消费；服务统一用于流式与非流式观察。

- [ ] 真实service流更新复现旧40类消息回放、新call_id复用、未完成结果恢复、同轮重复结果，验证事件/观察/钩子和统计RED。
- [ ] 实现身份ledger，明确requested/started/unknown与独立回放诊断。
- [ ] 对应模块及既有service、managed integration回归GREEN，记录日志。

### Task 2: 直接argv验证与可信进程结果

Files: local_tools.py；nailong/tools/registry.py；nailong/tools/execution.py；nailong/core/validation_output.py；相关新测试。
Interface: run_command(command="", timeout_seconds=30, *, argv=None, expected_exit_code=0)；command_for_permission(arguments)只生成权限展示，不修改审批原args。

- [ ] 真实失败脚本/echo掩盖、argv引号字面量、预期非零、无效双输入、权限deny、超时/输出归档与假摘要RED。
- [ ] 保留旧shell行为，增加shell=False与可信过程元数据；输出分类不自动验收。
- [ ] command/permission/approval/generation schema相关回归GREEN。

### Task 3: 有来源的明确阶段约束

Files: nailong/core/stage_constraints.py；agent.py；nailong/tools/execution.py；nailong/core/context_runtime.py；新tests。
Interface: compile_stage_policy(task_snapshot)；策略check(tool_name,args)返回拒绝或无拦截；policy.render()保留来源/未知阶段/解除条件。

- [ ] 不带本题名称的多阶段真实工具场景：提前执行拒绝、更晚明确授权通过、一般测试授权不放行、模型/文档伪授权无效RED。
- [ ] 从requirement_records真实用户来源编译；gate在handler/审批前核对，ContextManager展示。
- [ ] stage/TaskStore/审批真实图回归GREEN，列明无法静态覆盖任意程序行为的限制。

### Task 4: 同文件批次冲突与读快照提示

Files: nailong/core/tool_approval.py；nailong/tools/registry.py；tests/test_batch_approval.py及新回归。
Interface: 批次预检以FileSession.resolve后的真实路径分组，重复变更不产生审批和handler副作用，ToolMessage返回batch_file_conflict。

- [ ] 两次同文件编辑、路径别名、无读快照与另一独立文件的实际图RED。
- [ ] 提前拒绝冲突，保留nonce/task/spec/version绑定、ALLOW冻结版本和原取消处理。
- [ ] 全部批次/取消/MCP/子工具回归GREEN。

### Task 5: 可恢复的前轮历史缩减与环境/交付提示

Files: nailong/core/context_runtime.py；nailong/core/prompts.py；相关新tests。
Interface: 请求视图按历史输入目标缩减last real Human之前的闭合交换，归档引用可完整恢复；当前工具链/原checkpoint不变。

- [ ] 长前轮日志/推理、当前工具链、无恢复工具、截断归档、历史失败与phase来源保留RED。
- [ ] 主动历史缩减及结构化运行环境/active goal提示，交付声明证据核对指导。
- [ ] 实际序列化、reasoning/compaction/task-projection回归GREEN，节省比例保持未测。

### Task 6: 固定源码复测入口与最终审查/集成

Files: OUT6中的固定源码复测计划、增量patch/manifest与报告；必要项目文档。

- [ ] 从固定WT生成离线复测计划与源码/依赖/题目seal，不执行模型调用、不改旧run。
- [ ] 全套1360基线以上的最终实际测试；独立增量审查并修复真实问题。
- [ ] 对SOURCE每个拥有文件核对基线，三方合并并发变更；完成实际SOURCE相关检查，保存未验证范围。
- [ ] 最终报告写明已修复、实际测试、阶段/复杂命令边界、未测token收益与当前版本身份。

审查修正：更晚真实用户“现在执行目标”属于对该目标及明确参数的授权，阶段已知也无需特定解除口令；一般测试要求不构成目标授权。--dry-run授权不得扩大到--publish。原生结果由gate生成tool_result_id，历史/当前消息身份之外按结果身份去重及记录任务证据；旧适配器改写ID时的不可判定情况保留限制。
