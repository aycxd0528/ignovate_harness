# Task context projection implementation plan

> For agentic workers: use executing-plans inline, with a fresh review at the end. 用户已要求开始实现，沿用计划自审后执行；不再次等待形式审批。

**Goal:** 长任务使用有界投影，并通过可靠、不可变记录恢复被省略的资料。

**Architecture:** TaskStore 保存来源与替代关系；TaskContextHistory 复用 HistoryArchive 保存完整快照和提供分页检索；ContextManager 仅在恢复工具可用时使用精简投影。delivery 继续消费完整状态。

**Tech Stack:** 现有 Python、unittest、LangGraph、JSON 存储，不增加依赖或网络服务。

**Spec:** docs/superpowers/specs/2026-10-05-task-context-projection-design.md

## Global constraints

- 保留所有既有修改，不提交、推送或移动工作树。
- 有效硬约束、要求、验收定义不静默截断；旧任务默认全部要求有效。
- 模型不能修改替代关系；完整记录、验收与请求视图分离。
- 不调用真实 API；完整本地回归作为最终验证。

## Review focus

- requirements 必须完整映射历史，非法来源/替代链不能绕过保护。
- 归档失败、截断、损坏不得导致资料被省略。
- 不可用恢复工具时不能注入无法回读的精简视图。
- 跨会话/项目、同版本更改和旧 revision 必须拒绝。
- 精简展示不能使 delivery 漏掉验收或升级旧证据。

## Task 1: 要求记录与用户命令

Files: 新建 nailong/core/task_requirements.py；修改 task_state.py、ui/actions.py；测试 tests/test_task_projection.py。

Interfaces: requirement_records(snapshot)->list[dict]，append_requirement(task,text)->None；TaskStore.replace_requirement(thread_id,requirement_id,text)->dict。

- [ ] 写并运行失败测试：新增/控制输入/替代/旧任务迁移/持久恢复、非法映射/来源/链、命令入口。
- [ ] 实现稳定记录校验、追加和显式替代；接入 /task requirements 与 replace。
- [ ] 专项通过；完整历史及原始文件保护断言保持。

## Task 2: 完整归档与分页检索

Files: 新建 nailong/core/task_history.py；修改 history_archive.py；测试同专属文件。

Interfaces: HistoryArchive.load(thread_id,reference)->dict；TaskContextHistory(archive,thread_id,project_root).save(snapshot)->str、read(reference,section='index',record_id='',query='',offset=0,max_chars=4000)->dict。

- [ ] 写并运行失败测试：完整 roundtrip、索引/ID/关键词/分页、截断/损坏/跨身份拒绝。
- [ ] 实现快照保存、内容寻址校验和只读恢复，不返回无界工具正文。
- [ ] 新专项及已有 history_archive 测试通过。

## Task 3: 投影与真实运行时接线

Files: task_context.py、context_runtime.py、agent.py、tools.py、nailong/tools/registry.py、README.md；测试同专属文件。

Interfaces: render_task_context(snapshot,max_chars=12000,*,history_reference=None)->str；ContextManagerMiddleware 尾部可选 task_history；build_tools/specs 可选 task_history；只读 read_task_context 工具。

- [ ] 写并运行失败测试：30 步长任务、硬约束超限、旧任务保守处理、工具不可用回退、失败归档停止、真实图/费用预算/checkpoint/delivery。
- [ ] 实现保护核心、全部验收定义、最多 6 条工作证据与全量统计/引用；运行时确认工具可用后保存并投影。
- [ ] 更新说明、运行关联测试与完整 discover；记录实际计数和限制。
- [ ] 新上下文只读审查一次，必要修复先复现再实现，复验相关测试。

## Self-review

任务 1 的 requirements 校验供任务 2/3 共用；任务 2 的 save 引用只在通过完整验证后交给任务 3。read_task_context 参数及工具过滤供 renderer gating 使用。未引入步骤文件选择或自动摘要；未改变验收规则。提交与工作树步骤按会话约束省略。
