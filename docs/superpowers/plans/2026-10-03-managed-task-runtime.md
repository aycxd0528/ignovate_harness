# 奶龙任务运行时实施计划

> **For agentic workers:** 使用现有四个对话按文件所有权并行实施，总控负责 workflow 与集成。用户已选择直接调度并沿用后续计划自审后执行；不再次等待形式审批。

**Goal:** 工程任务保持连续性，交付有证据，无效重复有停止条件。

**Architecture:** TaskStore 是持久状态来源，context 消费快照，memory 提供带有效性的知识，tool use 提供真实观察，delivery 输出结构化状态；总控统一接线。

**Tech Stack:** 现有 Python、LangChain/LangGraph、私有 JSON/JSONL 与现有归档，不新增网络服务。

**Spec:** docs/superpowers/specs/2026-10-03-managed-task-runtime-design.md

## Global Constraints

- 单一文件所有者；保护全部已有工作区修改；不提交或推送。
- snapshot/evidence v1 字段、枚举及身份边界以 spec 为准。
- 保持旧接口的默认行为兼容；共享接线由总控完成。
- 2026-10-03 初始实施阶段仅静态自审，没有新增/运行测试或调用 API。2026-10-04 用户设置完整目标并要求据当前状态逐项验证；进入实际验证阶段，各窗口可为自己的模块增加并运行本地验证。真实 API 使用此前用户的明确授权，限定冒烟场景、临时项目和预算，不输出认证。

## 2026-10-04 协作与证据阶段

- 总控：agent/service、headless、main、permissions、README 与统一验收；所有原改动保留，不提交推送。
- context：任务投影、当步记忆刷新及 tests/test_managed_context.py。
- memory：记忆来源/有效性及 tests/test_managed_memory.py。
- tool use：工具执行边界、read_file content_offset 及 tests/test_managed_tools.py。
- output：delivery、review_evidence 静态流程覆盖及 tests/test_managed_delivery.py。
- tui：tui.py/ui/* 交互与真实渲染验证。总控自本次调度后不编辑这些共享 UI 文件。
- system prompt：新增 prompts.py、专属提示词测试、导入接线建议；不直接编辑 agent.py。
- workflow：已发现并调度，独占 task_state、progress、goal、verification、runner 及 tests/test_managed_workflow.py，核验持久恢复、审批期间变化、证据绑定、目标续跑和暂停。
- Claude Code：用户授权可调用，先用于只读检查，禁止改写同一文件或读取密钥。

任何测试绿灯都不能代表语义目标已经验收；最终覆盖要求跨进程恢复、补充约束与连续压缩、拒绝零执行、审批期间变化、任务身份隔离、取消后核对、来源变化、当前有效验收、正常分页与停滞暂停，以及无头/终端状态一致。

## Review Focus

1. 恢复和新要求不能使用旧有效性状态或盲目重放命令。
2. 跨会话目标、记忆、证据不得混用。
3. 权限拒绝、审批期间变化、取消后部分副作用必须如实处理。
4. 过期、未知覆盖、只有模型声明不得产生 verified。
5. 调查分页和轮询不因重复启发式被错误停止。

## 任务

- [x] Task 1 总控：TaskStore 私有持久化、快照、版本更新、变更与证据失效；ProgressMonitor 阶段感知的重复信号。
- [x] Task 2 context：render_task_context；可选 provider；预算内当前任务投影与压缩保护，保持历史归档接口。
- [x] Task 3 memory：元数据来源、适用性、过期诊断及读取结果；明确候选知识和有效知识。
- [x] Task 4 tool use：统一权限入口、完整覆盖读取与快照、异步协调；统一 schema/错误/输出覆盖，在专属范围落地必要修复。
- [x] Task 5 output：delivery 报告与渲染，区分验证成功和验收覆盖，拒绝模型自报和过期证据。
- [x] Task 6 总控：agent/service/goal/headless/界面接线；普通问答兼容，恢复重核及交付/停滞状态显示。
- [x] Task 7 总控：四个交接结果核对、接口和执行路径自审，完成本地、真实 API 和 Textual 渲染验证。

2026-10-04 最终统一验收：731 个本地测试全部通过，无排除；真实 API 五个场景通过；100×32 与 80×24 紧凑/展开审批均无重叠。部分窗口因账号用量限制中断后，总控通知停止并接手未完成项，包括 UI、工具观察和核心收尾。最终接口、修复记录、范围与未覆盖项见 `docs/reviews/2026-10-04-managed-runtime-acceptance.md` 及对应 JSON 证据，不把早期静态交接或旧测试绿灯作为最终证明。

## 完成交接

各对话写 `docs/reviews/2026-10-03-managed-runtime-<context|memory|tools|output>-handoff.md`，包含修改文件、实际接口、使用示例、静态检查、未运行验证和未完成事项。完成后在本对话最终报告即可；总控读取文件及状态，不依赖自动跨对话回信。
