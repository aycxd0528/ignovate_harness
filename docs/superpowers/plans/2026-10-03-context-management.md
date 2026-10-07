# Context Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for the main integration; use dispatching-parallel-agents for independent archive and memory modules. Steps use checkbox syntax.

**Goal:** 完整落实上下文管理建议 1–7，保持历史可恢复、约束可持续、每次请求受窗口约束。
**Architecture:** 本地归档及按需记忆提供检索；确定性 L1/L2 处理与完整请求预算在每次模型调用前执行，实际请求快照驱动统计。
**Tech Stack:** Python 3.11+、LangChain 1.x、LangGraph、SQLite、unittest。
**Spec:** docs/superpowers/specs/2026-10-03-context-management-design.md

## Global Constraints

- 保留当前工作区已有修改，不自动提交/推送，不调用外部模型。
- 保留审批、模式过滤、会话/路径隔离、密钥脱敏与费用计量。
- 不增加在线服务依赖；所有新行为先有失败测试。

## Review Focus

- 多工具调用且部分仍等待审批：保留整个待完成交换。
- 超长用户要求/固定记忆：明确安全停止或检索引用，不能静默丢弃。
- 未知模型窗口/缺少 usage：估算与未知状态正确，不冒充精确 token。
- 旧 checkpoint/多次压缩/恢复后改文件：原始证据可追溯且版本明确。
- 项目、会话、模式切换及并发子代理：报告和归档不能串线。

### Task 1: 私有结果归档（建议 4）
**Files:** Create nailong/core/history_archive.py, tests/test_history_archive.py.
**Interfaces:** Produces HistoryArchive.save/read，签名与 spec 一致；不改公共接线文件。
- [x] 先写隔离、分页、脱敏、幂等、历史版本及符号链接失败测试，运行确认失败。
- [x] 实现原子私有 JSON 归档与当前会话引用读取。
- [x] 运行 `.venv/bin/python -m unittest tests.test_history_archive -v`，预期全部通过。

### Task 2: 核心记忆与索引（建议 6）
**Files:** Modify nailong/core/memory.py; Create tests/test_context_memory.py.
**Interfaces:** Produces MemoryStore.catalog/read_section 与短核心 load_project_memory；不改 agent.py/registry.py。
- [x] 先写长/短记忆、按标题读取、版本变更、三层隔离、分页、脱敏测试，运行确认失败。
- [x] 实现核心提取、索引与只允许三层路径的读取。
- [x] 运行 `.venv/bin/python -m unittest tests.test_context_memory tests.test_workflow_skills_memory -v`，预期全部通过。

### Task 3: 分层压缩及状态（建议 2、3、5、6）
**Files:** Modify nailong/core/compact.py; Create tests/test_context_compaction.py.
**Interfaces:** Consumes HistoryArchive.save；produces 兼容 CompactResult 与 L1/L2 状态/固定生命周期。
- [x] 先写单轮大历史、工具配对、早期约束、多次摘要、错误/退出码、归档引用、Skill 状态和固定消息替换测试，运行确认失败。
- [x] 实现工具省略、结构化摘要及稳定固定消息处理；保护当前输入和未完成交换。
- [x] 运行 `.venv/bin/python -m unittest tests.test_context_compaction tests.test_v1_features.CompactionTests -v`，预期全部通过。

### Task 4: 完整请求预算与校准（建议 1、7）
**Files:** Modify nailong/core/context.py; Create tests/test_context_budget.py.
**Interfaces:** Produces 完整分类/估算、校准器、窗口与输出预留策略；现有 context_report 兼容。
- [x] 先写中文/schema/参数、未知窗口、小窗口输出预算、usage 校准和互斥分类测试，运行确认失败。
- [x] 实现完整请求大小与软/硬预算策略，保持费用保守上界独立。
- [x] 运行 `.venv/bin/python -m unittest tests.test_context_budget tests.test_workflow_sessions_context -v`，预期全部通过。

### Task 5: 每次调用、工具及统计接线（建议 1、4、5、6、7）
**Files:** Create nailong/core/context_runtime.py, tests/test_context_runtime.py; Modify agent.py, agent_service.py, tools.py, nailong/tools/registry.py, ui/flows.py, ui/actions.py.
**Interfaces:** Consumes Tasks 1–4；每次模型输入与图 checkpoint 一致，报告落到绑定会话。
- [x] 先写真实离线图的同步/异步工具循环、小窗口拒绝、模式工具过滤、恢复检索、目标生命周期及报告测试，运行确认失败。
- [x] 增加中间件，接入 read_history_result/read_memory，替换旧自动压缩入口，固定目标/计划稳定 ID。
- [x] 修复基线已有未绑定目标注入问题。
- [x] 运行 `.venv/bin/python -m unittest tests.test_context_runtime tests.test_prompt_assembly tests.test_agent_service tests.test_tool_registry -v`，预期全部通过。

### Task 6: 文档与全范围验收（全部 1–7）
**Files:** Modify README.md; Create docs/reviews/2026-10-03-context-management-acceptance.md.
- [x] 更新 /compact、/context、记忆/归档工具、限制和恢复说明。
- [x] 运行 `.venv/bin/python -m unittest discover -s tests -v`，预期零失败；实际无网络 CLI 检查。
- [x] 独立代码审查；重要发现按 RED→GREEN 修复。
- [x] 按 spec 每项记录权威证据，再完成目标。
