# Execution ledger — plan: docs/superpowers/plans/2026-10-03-context-management.md

Scope: 用户授权开始落实上一轮全部 1–7 项。
Baseline: 374 tests, 1 failure: test_unbound_goal_does_not_activate_named_chat_until_attached.
Pre-flight: Task 3 consumes Task 1 archive API; Task 5 consumes Tasks 1–4 exact interfaces.
Ruling: 在当前 codex/nailong-v1 工作区执行、不建立 worktree、不自动提交 — 未跟踪源码是本次真实基线，另建 worktree 会遗漏它们；代价是需通过文件范围和审查区分已有修改。
Ruling: 用户已明确授权落实 1–7 项，保留全部设计范围并继续执行，不重复请求实施授权 — 设计和计划作为可审阅文档；如用户纠正则调整。

Ruling: 同目录另一记忆会话新增 MemorySnapshot、MemoryReadContext 和主题文档 API；保留并协调，不覆盖其接线。核心选择再包装为预算快照，完整请求预检查使用同一个 filter_messages 视图。用户已授权会话接口协调。

Tasks 1–5: implemented and focused verification passed.
Task 6: complete. Final integrated suite and offline CLI checks passed; acceptance documented.

Evidence:
- Archive module: 22 tests covering private permissions, thread binding, pagination, redaction, immutable versions and link rejection.
- Complete request budget: language/schema/call-argument accounting and provider/model-isolated conservative calibration.
- New real graph tests cover sync/async/tool loops, child guards, protected-input refusal, filtered tools, durable reports, memory budget view and chronological manual compaction.
- Latest focused command: `.venv/bin/python -m unittest tests.test_context_compaction tests.test_context_runtime tests.test_context_budget tests.test_history_archive`; 60 tests passed (`/tmp/nailong-context-final-focused.log`).
- Independent read-only review: material findings fixed and re-reviewed; no remaining confirmed material findings in the assigned core scope.
- Review RED→GREEN: pending parallel exchange across user turns; complete requirement index and >2 MiB cumulative history; soft L2 increasing size; pure-text tool error status; memory preflight causing unnecessary history mutation. Manual compaction tested against the real LangGraph reducer.
- Offline shared `/context` command: actual-request source, restricted tool list, simulated usage, 20,000-token configured window and 4,000-token memory budget all checked. No external model call.
- `python -S main.py doctor --project /tmp --output-format json`: expected exit 1 for missing packages, valid JSON with `network_checked=false`.
- Integrated run: 486 tests, 3 failures/0 errors (`/tmp/nailong-context-final-suite.log`), all in the concurrent memory tests/old public tool list. That owner is finishing these fixes; no core test failure remained in this run.
- Final integrated run after those fixes: `.venv/bin/python -m unittest discover -s tests -v`; **488 tests passed**, 42.548 seconds (`/tmp/nailong-context-acceptance-suite.log`). Syntax compilation and `git diff --check` passed. No commit or push.
