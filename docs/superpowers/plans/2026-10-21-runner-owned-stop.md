# 会话队列停止与关闭的清理所有权

> **For agentic workers:** Use executing-plans inline; preserve prior fixes and user changes; do not commit or push.

**Goal:** 停止/关闭即使被反复取消也等待当前任务清理；关闭后所有待执行 Future 都有终态；取消的排队输入不执行；清理期间不允许恢复新一代工作。

**Evidence:** 当前原生 SessionRunner.close 被取消后，在 current finally 仍等待门闩时返回，queue Future 仍 pending；释放后 queue 仍为 1、busy=true。原生 UI app 的 finally 会在 close 返回后继续关闭 Factory，故资源收尾顺序必须保留。

**Architecture:** 每个 runner 只拥有一个停止 task，捕获当前 pump/child 并只发出一次取消，所有 stop/close 调用共享清理结果。调用方取消通过 shield 隔离，等待 owner 清理后传播；resume/start/rebind 用同一停止屏障。close 先禁止新输入并取消队列，最后仍清空待执行 Future。已取消且未执行的 Future 跳过；运行中 Future 取消触发同一停止流程，暂停后续队列。

**Constraints:** 不跨 await 持文件/任务事务锁；保留 stop 后显式 resume、回调失败后继续下一项、业务协程吞取消后的正常结果。与现有原生进程、预算和 checkpoint 清理一起等待，不把完成 Future 当成资源已释放。保留 .idea 和 GUI CSS 用户改动。

## 1. RED 与实现

- [x] 原生关闭取消、反复取消/并发 stop、外层资源关闭顺序和队列 Future 终态 RED。
- [x] 原生 pump 完成回调中 resume 的停止竞态、取消待执行/运行中 Future、idle/立即 stop 边界。
- [x] 实现单一 owner 清理、屏障及取消传播；补正常进度、失败与显式 resume 兼容。
- [x] 真实 execute_process 接入 runner，取消后等待进程/管道清理且无延迟副作用。
- [x] 复核补修 Future 取消回调晚到窗口、清理错误保留及跨 Task/嵌套 runner 的自依赖；旧代 scope 不误拒绝新代。
- [x] 真实 Factory/AgentService/Graph/SQLite：重复取消 shutdown，模型清理期间 checkpoint 仍可读取，之后再关闭 Factory。
- [x] Windows plain 信号调度捕获注册时 controller Context，避免复制中断的 business scope；分支模拟和真实 POSIX SIGINT 复核。

## 2. 复核及验证

- [x] requesting-code-review 独立增量复核，修复所有 Important/Critical。
- [x] 完整 Python/JS、diff check、发行包外部安装模块原生队列/进程验证。
- [x] 更新 PROJECT 和总体审查记录；整个项目目标保持 active，MCP/真实输入边界及长期端到端仍需推进。

最终证据：Python 1844 项/45 跳过，其余通过（197.162 秒）；JS 17 项；安装模块 17 项原生集成，六个运行文件与源码字节一致。wheel SHA-256 1f82eeb6ac0f9cd5b3be9b2e6666d539cd6f3ae0f063b0733df55016a4a571db。独立最终增量复核无剩余 Critical/Important。详见总体审查第八轮；Windows 分支只模拟，未执行 Windows 实机或真实 API，不提交、不推送。
