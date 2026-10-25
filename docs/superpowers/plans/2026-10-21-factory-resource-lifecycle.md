# Factory 资源生命周期修复计划

> **For agentic workers:** Use executing-plans inline, TDD and one fresh final review. Preserve all prior work and user .idea/CSS; no commit or push.

**Goal:** 长任务运行时只拥有一套已初始化的 SQLite，关闭/反复取消后收回全部已取得资源，入口失败不遗失 Factory。

**Spec:** 用户的整个项目长任务可靠性审查目标；docs/reviews/2026-10-20-long-task-reliability.md。

**Evidence:** 原生 run_inline 的 list_sessions 失败后 SQLite 仍可执行 SELECT 1；原生 Factory.aclose 后 OpenAI/AsyncOpenAI.is_closed 均为 False。async_runtime 首次并发无锁，close 的首项错误可跳过其他资源。

**Architecture:** Factory 同步构造回滚；独占的异步初始化与关闭屏障，等待者取消不取消资源所有者；只有 setup 成功后发布 saver。失败初始化回收连接后允许重试。关闭等待初始化/写入，逐项释放 MCP、SQLite、预算及所有自己创建的模型 SDK 客户端，保留错误。模型切换保留旧客户端至最终关闭，避免中断仍持有旧图的任务。入口在取得 Factory 当刻登记清理，跨启动、切换及退出保持归属。

**Constraints:** Python >=3.11；真实 SQLite/SDK 但不请求真实模型 API；底层模型工作必须先由 runner/service 加入；不静默掩盖关闭失败；整体目标维持 active。同步 close 只能同步收尾，不在另一个事件循环关闭已使用的异步连接。

**Review focus:** 首次初始化并发及取消、setup 失败后重试、关闭与初始化交错、关闭等待者反复取消、首项关闭错误仍释放其余资源、旧模型图存活、入口早期异常与项目切换半失败。

## 1. Factory 核心

- [x] 原生 RED：构造后半失败、首次并发开库、setup 失败、打开期间关闭、重复取消关闭、SDK 关闭、关闭错误不跳过其余资源。
- [x] 实现构造回滚、初始化独占/发布、关闭所有者/屏障和逐项释放；保持既有取消打开及 SQLite 写入锁契约。
- [x] 定向原生组 GREEN，记录异常语义及同步/异步边界。

## 2. 入口归属

- [x] 原生 RED/GREEN：inline 启动失败/提示取消/项目候选失败及多 Factory 清理异常。
- [x] 审查并修复 TUI、GUI、headless/legacy CLI 的启动、切换及退出归属，逐链原生回归；GUI shutdown 不提前停清理事件循环。

## 3. 复核与验证

- [x] requesting-code-review 独立复核及修复复现的问题。
- [x] 完整 Python/JS、diff check、wheel 外部安装原生回归。
- [x] 更新总体审查、PROJECT/CLI_REFERENCE；继续其他未完成审查项。

## Ledger

- 已阅读既有 Factory/SQLite 取消及 MCP 失败回归。与本阶段共同约束：取消开库必须加入真实线程并关库；关闭不得越过 saver.lock；MCP 失败仍需关库。执行中的各任务必须保留这三项。
- 范围包括所有入口；核心与各入口分别验证，可跨目标续轮继续，不以局部测试通过代替整个目标完成。

- 核心与入口新增42项原生用例通过；独立复核补修 SDK遗失、context耗尽、MCP同步边界、shared child身份、GUI退休owner、TUI重试及跨loop重复关闭。最后补修退休owner重复错误的旧密钥脱敏，单项RED/GREEN与独立复核通过。
- 初次全量1892项/45跳过因6个fixture清理登记反序有42个错误；修正后完整1916项/45跳过通过（213.543秒）。最后脱敏补修后的完整回归正在重跑。安装模块180项首次唯一错误为fixture导入tests包搜索路径，harness仅登记测试namespace，生产模块仍严格来自安装目录。

最终验收：Python1916项/45跳过，其余通过（217.550秒）；JS17项；实际pip-target安装180项全通过（43.363秒），142个发行Python文件字节一致。wheel SHA-256 8e0b14f88e1e6bdf9fab99c5596a365a8ec79cebc2af61bd7101e1b27da54aea；外部help/doctor/dependency smoke与diff check通过。独立最终复核无剩余Critical/Important。整体目标继续active，下一阶段检查真实工作区链接/输入身份及全面交付链路，不提交、不推送。
