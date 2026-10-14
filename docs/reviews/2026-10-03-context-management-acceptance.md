# 上下文管理 1–7 项验收

范围：用户授权的全部七项；保留当前工作区已有修改与另一记忆会话的 API，不自动提交或推送。实现采用离线确定性处理，没有调用外部模型。

| 项目 | 落地行为 | 主要证据 |
| --- | --- | --- |
| 1 完整请求预算 | 每次主/子、同步/异步及工具循环检查系统、schema、调用参数和当前输入；输出预留与窗口共同约束，不能安全容纳时调用前停止 | `test_context_budget.py`；`test_context_runtime.py` 的 sync、child、tiny-window、tool-loop 测试 |
| 2 分层压缩 | L1 省略已完成正文；L2 在完整回合边界生成状态摘要；保护未完成并行交换，硬窗口可越过收益门槛，软压缩拒绝增大输入 | `test_context_compaction.py` 的 single-turn、pending-parallel、soft-gain、hard-limit 测试 |
| 3 任务连续性 | 多次摘要保留最初目标、用户约束和所有要求的 checkpoint 索引；摘要始终给出完整可恢复索引引用，中间要求不因轮数或累计正文淘汰 | repeated-compaction 与 cumulative-index 测试；独立审查复核 |
| 4 原文归档 | 会话绑定、脱敏、原子私有归档，版本/行范围/错误/退出码/截断信息；`read_history_result` 每页 ≤6,000 字符，拒绝任意路径和跨会话引用 | `test_history_archive.py` 22 项；运行时恢复与 pure-tool-error 测试 |
| 5 固定消息生命周期 | goal/plan 使用稳定 ID；同类保留最新版本，活动目标结束后解除固定；未绑定目标不注入其他会话 | goal-pin 与 prompt-assembly 测试；手动压缩真实图顺序测试 |
| 6 按需记忆与 Skill 状态 | 短核心再包装进三层共同预算；目录、按主题/标题分页工具与正文共用预算；实际请求过滤保留历史原文；Skill 摘要保留版本及已加载状态 | `test_context_memory.py`；`test_memory_context.py`；memory-request-view 与 Skill 状态测试 |
| 7 实际请求统计 | `/context` 使用实际模式与工具集合，分类互斥，保存窗口/输出/压缩和记忆预算快照，usage 与估算分开；重启可从事件恢复 | actual-filtered-request、report-restore、budget-category 测试；离线共享 `/context` 命令 |

## 验证记录

- 重点回归：60 项通过，日志 `/tmp/nailong-context-final-focused.log`。
- 独立审查：覆盖本次核心实现与接线；确认的重要问题已修复并重新检查，未留确认的重要发现。仅运行离线局部复现。
- 共享 `/context` 命令离线检查通过：实际请求来源、仅 `read_file` 工具、模拟 usage、20,000 窗口、4,000 记忆预算及显示字段一致。
- 缺依赖 CLI 检查：`python -S main.py doctor --project /tmp --output-format json` 返回可解析离线诊断 JSON；预期退出 1，缺失依赖有明确错误，不联网。
- 最终完整回归：`.venv/bin/python -m unittest discover -s tests -v`，**488 项全部通过**，耗时 42.548 秒；日志 `/tmp/nailong-context-acceptance-suite.log`。
- 语法编译与 `git diff --check` 通过；未提交或推送现有工作区。

## 实际限制

token 估算使用字符语言分类、请求封装和同模型/提供商 usage 校准，不能保证与供应商 tokenizer 精确一致；未知窗口仅有软阈值，需显式配置 `context_windows` 才能按已知窗口保护。未使用真实 API 验证模型遵从或提供商计量。

单条原文归档上限 2 MiB，过长原文明确标记截断；完整任务索引使用摘要和独立引用，索引自身超过上限则拒绝 L2，不静默丢弃旧回合。超出固定约束或当前输入可安全压缩范围时停止调用，需要减少输入或选择更大窗口。

`/context` 表示最近实际模型请求，手动压缩后要到下一次调用才刷新该统计。校准器在当前 factory 内按提供商与模型隔离，重建 factory 后重新学习；实际 usage 仍从会话事件恢复。
