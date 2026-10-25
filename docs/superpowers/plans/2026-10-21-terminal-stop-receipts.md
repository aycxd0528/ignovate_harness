# 终端停止屏障与完成回执

> **For agentic workers:** Use executing-plans inline; preserve prior changes and user .idea/CSS; no commit or push.

**Goal:** Ctrl+C 当场阻止后续工作，真实清理完成后才报告停止；重复信号共用控制者；暂停保存/停止异常脱敏报告，退出等待控制者，不留下后台回执。

**Evidence:** ui/app.py 的 plain interrupt 先 create_task(runner.stop())，随后同步 pause_session_goal 并立即打印“当前任务已停止”。调度停止本身尚未运行，清理中仍显示已停止；暂停失败会从信号 callback 逃逸，控制 Task 没有错误接收者。

**Design:** SessionRunner.begin_stop 同步建立现有唯一 owner 的停止屏障，返回与该 owner 绑定、隔离取消的等待 coroutine；既有 async stop 复用它。等待结果指明该停止是否仍是当前 paused 代，防止老回执覆盖恢复后的新工作。终端持有一个 signal-stop Task，捕获原 service/thread/key，先显示正在停止，独立处理暂停保存及停止错误；finally 等待控制者再关闭 Factory。SDK、Graph/SQLite 和原生进程继续使用已经验证的清理所有权。

**Scope:** 现有 plain/inline、共享 runner；真实 Factory/Graph/SQLite 搭配离线模型和门闩，不调用真实 API。保持普通 /stop 和关闭错误传播契约，原目标不缩小。

## 1. RED 与修复

- [x] 原生 run_inline + Factory/Graph：清理门闩期间没有完成提示，Goal 已暂停且 checkpoint 仍可读，重复 Ctrl+C 只有一次完成回执。
- [x] 同步停止 admission、旧回执在恢复后失效、反复取消等待/关闭仍加入原 owner。
- [x] 暂停保存及停止异常脱敏、无未处理 loop error；退出等待控制者并恢复 signal。
- [x] 实现 begin_stop 和终端控制 Task，补兼容回归；等待 coroutine 被转交自身 cleanup 的有限子进程 RED（exit 73）后补 await 时 guard。

## 2. 复核与验证

- [x] requesting-code-review 独立复核，无剩余 Critical/Important；补修 Minor 控制者首次执行前取消的 coroutine 回收及正常退出语义，新增原生 RED/GREEN。
- [x] 相关原生/终端组、完整 Python/JS、diff check、实际 wheel 外部安装验证。
- [x] 更新 PROJECT、CLI_REFERENCE、总体审查记录；继续真实输入范围和长期端到端审查。

最终证据：Python 1874 项，45 跳过，其余通过（213.685 秒）；JS 17 项；安装模块原生终端/Graph/队列组 80 项，1 跳过，其余通过（8.752 秒），11 个运行文件字节一致。wheel SHA-256 846455396682c3bf99cac2cdfc732c46f6940fc1aa0a8883ba6a065c21e03698。独立复核无剩余 Critical/Important，1 项 Minor 已修复并独立核对。初次全量新增SIGINT测试缺子进程模块搜索路径，显式传入后最终完整 discover 通过。没有真实 API、Windows 实机、SIGTERM/硬退出或长期 soak。本阶段不提交、不推送，整体目标 active；下一项原生初始化失败探针已证实 SQLite 未回收，继续修复。
