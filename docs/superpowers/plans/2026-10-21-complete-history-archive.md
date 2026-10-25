# 完整历史归档与压缩恢复

> **For agentic workers:** Use executing-plans inline in this worktree. Preserve prior work and user changes. Do not commit or push.

**Goal:** 压缩移除原始消息后，用户要求、工具正文、参数和完整消息元数据仍可按不可变引用分页恢复；不完整或失败的归档不得支持清除。

**Architecture:** 保持 v1 小记录和旧引用可读。大记录用 v2：1 MiB 内容寻址片段，最后原子发布清单，读取验证片段及完整记录身份。单记录最多 64 MiB，超限在发布前失败并保留原 checkpoint。read_history_result 保持默认正文，增加 message/arguments 分页 section。L1/L2 以及继承索引统一传播旧归档不完整状态。异步上下文入口的重工作由可取消、持有清理责任的 worker 执行；不在 worker 中调用 provider 或修改 Graph checkpoint。

**Tech Stack:** Python 3.11+、现有安全文件/私有 ACL/原子发布、SHA-256、asyncio；不增加依赖。

**Spec:** docs/reviews/2026-10-20-long-task-reliability.md 的真实超 2 MiB HumanMessage 截断探针及用户复杂长任务交付目标。

## Global Constraints

- 不修改、覆盖或默认为完整的旧部分归档；旧 v1 小记录引用不变，新大记录身份与旧部分记录分开。
- 清单发布前所有片段必须完整；并发/重试幂等，不覆盖既有对象，丢失/损坏/链接片段拒绝读取。
- 单片段 1 MiB，完整记录至多 64 MiB，清单至多原 2 MiB；分页正文最多 6000 Unicode 字符。
- 每字段保存前脱敏；历史资料不授予当前权限，不重放任何工具或模型。
- 不能安全归档时保留原始 checkpoint，不以关闭压缩或只扩大摘要作为成功方案。
- 保留用户 .idea、GUI CSS 及先前审查改动；不提交、不推送。

## Review Focus

- 崩溃/写失败窗口、并发相同引用和片段不可变性；清单不能引用半份内容。
- 大中文/多模态正文、参数和消息 metadata 的完整恢复；偏移、末页和旧默认 API 兼容。
- L1/L2、重复 summary、原有 cleared-tool 引用及旧部分归档，不得产生错误的完整索引。
- 损坏清单/片段、跨会话、符号链接/FIFO、资源耗尽均失败关闭，源消息不变。
- 异步预处理期间心跳/取消可用，等待清理后不调用 provider、不发布部分 checkpoint。

## Task 1：完整分片归档及分页

Files: create nailong/core/history_chunks.py; modify history_archive.py/tools/registry.py; tests/test_complete_history_archive.py plus legacy archive tests.

- [x] 大正文尾部、中文边界、大参数/metadata、分页工具 section、旧部分记录兼容 RED。
- [x] 实现内容寻址片段和清单最后发布，完整身份验证；保留 v1 小记录行为。
- [x] 实现 section=content/message/arguments，默认字段与分页语义兼容。
- [x] 并发、重试、失败发布、丢失/篡改/链接片段、资源上限与隐私验证。

## Task 2：压缩完整性与异步责任

Files: modify compact.py/context_runtime.py; tests/test_complete_history_archive.py and relevant context tests.

- [x] L1/L2 原生旧部分归档及继承引用 RED，统一完整性传播，原始内容保持可恢复。
- [x] 原生预算压力/重复压缩后尾部要求、参数和元数据仍能通过工具找回。
- [x] 有界异步心跳/重复取消回归，重工作移出事件循环；不将 Graph checkpoint/provider 放到后台。
- [x] 旧 goal/plan 解除 pin 保留正文；后续 L2 以完整归档及可发现索引替换，最低收益回退不误报 removed_ids。
- [x] provider 前重新核对 Task 停态/ID/修订，预算等待后再次检查；未发送预留释放，不增加调用计数。
- [x] 手动压缩使用 owned worker 与项目执行租约，checkpoint 仍在 owner，等待后版本变化不覆盖。
- [x] 调整旧“大记录被截断”测试为完整新格式与显式 legacy 部分记录测试，不削弱保护断言。

## Task 3：复核及交付验证

- [x] requesting-code-review 独立增量复核，处理所有 Important/Critical。
- [x] 完整 Python/JavaScript、diff check；构建 wheel 并在安装模块实际归档/压缩/分页恢复。
- [x] 更新文档与总体审查记录，整体项目目标仍 active。

最终证据：Python 1827 项/45 跳过，其余通过（195.852 秒）；JS 17 项；安装模块 14 项原生集成。wheel SHA-256 ac2ff9df4f5b3ac3a0611b15d972278236f032ce67b8f7cc470895b673395ff4。独立最终复核无剩余 Critical/Important。详见 docs/reviews/2026-10-20-long-task-reliability.md 第七轮记录；未使用真实 API、未提交或推送。
