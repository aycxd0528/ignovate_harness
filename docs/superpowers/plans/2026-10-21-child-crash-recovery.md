# 子任务硬退出恢复与原生进度保存

> **For agentic workers:** Use executing-plans inline; retain the existing worktree and user changes. Do not commit or push.

**Goal:** 重启后能够核对子任务中断位置，找回原生读取进度及续作引用，同时保留费用和当前父任务护栏。

**Architecture:** 每个新子任务从扫描前至最终落盘持有独占非阻塞执行租约。恢复仅处理能够取得租约的 running 记录，转为 interrupted 并明确 usage_complete=false；旧版无租约记录降为 usage_unknown，不自动续作。原生子图工具中间件逐次保存有界证据元数据。task_status 提供当前会话分页查询和指定阶段详情；恢复与查询不调用模型，显式续作仍核对父任务和完整输入。

**Tech Stack:** Python 3.11+、现有 file_lock/atomic_json/ContextVar/LangChain 中间件；不增加依赖。

**Spec:** docs/reviews/2026-10-20-long-task-reliability.md 中已复现的 os._exit(29) 后 running 遗留。

## Global Constraints

- 不跨 await 持有任务或全局子任务账本事务锁；执行租约由整个 async run 的 finally 释放。
- 不自动重放模型或工具，不将未知用量记为零，不重记父目标费用。
- 子记录和历史证据不扩大权限；查询只读当前会话，续作必须绑定当前 task_id/revision/input。
- 活跃租约不能被另一 factory/进程误判为崩溃；旧版 running 缺少所有权证明，保持不可自动续作。
- 保留 200 阶段、900000 字节存储边界，失败拒绝继续；不截掉既有历史以腾出额度。
- 不触碰用户已有 .idea 和 GUI CSS 改动，不提交、不推送。

## Review Focus

- 新记录发布和租约获取顺序；活跃跨进程、正常结束及异常 traceback 保留时的确定释放。
- 已消费父续作不得再次消费；新阶段崩溃后从新阶段引用恢复，保留链路。
- 原生读取后、下一 provider 调用中硬退出仍保留分页/版本证据；自定义 ChildTaskResult 不获得原生证明。
- 存储失败不启动 worker；中途进度写失败停止后续模型调用，旧字节和未知状态可核对。
- 查询和恢复不重置费用、轮数、token 或任务暂停状态；输入/父任务变化仍拒绝显式续作。

## Task 1：执行租约、恢复及查询

Files: nailong/core/child_continuations.py, agent.py, nailong/tools/registry.py, nailong/core/capabilities.py; tests/test_child_crash_recovery.py.

- [x] 真实子进程硬退出、活跃跨进程、正常/收尾异常清理、旧版 running、查询隔离和分页 RED。
- [x] 实现 lease/recover/status；Factory 在模型/SQLite 资源前恢复；task_status 注册及发现。
- [x] 定向回归 GREEN，显式续作保留原有父任务/输入/一次消费护栏。

## Task 2：原生读取进度与重启续作

Files: nailong/tools/agents.py, agent.py, nailong/core/child_continuations.py; native subprocess cases in tests/test_child_crash_recovery.py.

- [x] 真实子图读取分页后硬退出 RED，重启前证据已保存；恢复后 task_status 可读。
- [x] 原生工具中间件保存证据元数据，正常/取消收尾合并已存进度；明确未知用量。
- [x] 写入失败、取消和并行子图隔离回归；不伪造当前完成或丢失分页位置。

补充闭合：统一工具 status 会覆盖业务阶段，现用 child_status 保留，裁剪也保留续作标识。证据校验在读取与写入前共用，兼容原生 EOF 的 content_offset=None 和 bypass 历史绝对路径。启动核对放在扫描后及 semaphore/预算等待后，绑定父任务要求与控制状态，允许正常进度变化。

## Task 3：独立复核与交付验证

- [x] requesting-code-review 精确增量复核，处理 Important/Critical。
- [x] 完整 Python、JavaScript、diff check，wheel 安装目录实际恢复/查询 smoke。
- [x] 更新项目说明和总体审查记录；整个项目目标仍 active。

最终证据：新增文件 20 项通过；完整 Python 1797 项、45 项跳过、其余通过（195.871 秒），JavaScript 17 项通过，diff check 通过。新 wheel 的 CLI smoke 和安装模块 4 项原生重启/续作/费用/队列集成通过，没有模型 API 调用。独立复核各 Important 已补回归、修复并实证关闭，最后增量未发现新的 Critical/Important。此阶段完成不代表整体项目审查完成。
