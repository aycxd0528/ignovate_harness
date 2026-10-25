# 控制进程崩溃所有权实施计划

> REQUIRED: executing-plans inline、TDD、一次requesting-code-review。已授权离线修复与native验证，不提交/推送。

**Spec:** docs/superpowers/specs/2026-10-23-process-crash-ownership.md
**Goal:** actual POSIX controller hardexit之后同组命令树由独立owner收回，保持正常运行和真实退出证据。

## Global Constraints

保留完整用户目标/要求/产物/输入/费用，不删未知用户进程或历史PID清单；只操作自己创建的组/FD。Windows Job保持；POSIX主动脱离组daemon不声称已覆盖。保留原分支及用户IDE/CSS，无真实API/提交推送。启动helper不能从cwd/PYTHONPATH注入。

## 文件边界

- nailong/core/posix_process.py：parent的lifeline/status管道、独立session、watcher wait与真实退出码proxy和故障回滚。
- nailong/core/_posix_process_child.py：stdlib隔离监护启动器与用户命令子进程；用户命令不继承控制FD。
- nailong/core/process_io.py：OwnedProcess接入新parentguard，stdio捕获兼容。
- local_tools.py及nailong/core/plan.py：普通工具与异步editor启动后台化，join创建与取消后清理。
- nailong/core/processes.py：所有平台使用同一owned capture，异步启动/取消join/观测回执保持。
- tests/test_process_crash_ownership.py：真实控制进程硬退出、启动窗口及资源边界。
- tests/test_native_processes.py/command argv/验证/runner等现有原生消费者回归；docs/PROJECT与CLI_REFERENCE、整体审查更新。

## Review Focus

1. parent死于gate之前、watch启动之间、exec之后均不能留下无owner普通命令；初始许可不是执行结果。
2. 真实命令不得继承life写端或status写端，多个并发命令不能互相保活；新session/group不是parent前台组，任何错误不能killparent组/历史PID。
3. soft terminate保留锚，force/close等EOF并wait ownchild，不在已释放组ID上再次kill；实际命令退出码与bootstrap失败不混为成功。
4. PATH/cwd/env/stdio、argv空值/Unicode/bytes、stdin和secret过滤语义不被Pythonhelper改变；-I/-S absolute helper不导入项目代码。
5. async创建慢/失败/反复取消必须join并清理，stdout观测/HTTP/超时保持真凭据，真实Windows与POSIX主动daemon逃逸边界不得隐去。

## Task1：crash原生RED与独立owner

- [x] 增加OwnedProcess及execute_process的原生控制端hardexit RED，包含孙进程和有限时写入，确认命令已启动；原实现必须实际遗留写入。
- [x] 实现posix_process parentguard与isolated child gate/watch，接入OwnedProcess；原生admission/window/exec error/capture兼容，确认GREEN。

## Task2：异步链路及故障回归

- [x] execute_process全部入口owned，启动后台join、重复cancel和错误清理；真实async crash RED→GREEN。
- [x] 参数/stdin/env/terminal信号隔离、软停止后锚、启动失败/monitor失效、并发FD不互保活、重入close/FD基线、CLI工具与Verification/Graph/goal回归。

## Task3：独立复核与发行验收

- [x] 一次fresh Astra精确六文件增量复核：0Critical/2Important/0Minor。I1公开STOP暂停监护者，I2整体loop关闭取消创建Task丢资源；作者一轮3个真实RED回归补修，相关110项48.376秒通过（3平台跳过），无第二次复核。
- [x] 最终完整Python2069项293.348秒（47平台跳过其余通过），JS17、diff通过；实际外部安装584项132.072秒（4平台跳过），39关键模块来自安装目录，153发行Python字节一致，CLI help/JSONdoctor/依赖smoke通过。
- [x] 更新文档和全部Ruling/代价、deferred minors；证据已复制整体审查，只清本轮scratch，原goal active。

## 全部裁决与代价（按发生顺序）

1. 最初选择同会话独立组以保留命令PID；原生控制终端RED否决并由第4项取代。代价已实际出现：终端输入暂停整组及监护者，不能发行为该方案；该原型没有被当作验收结果。
2. 不使用仅Linux的PDEATHSIG或历史PID重启清理，使用跨当前POSIX运行的独立监护者。代价：每命令增加一个Python监护者；本机40命令约增加30.7ms/命令，FD4→4，不是性能承诺。
3. 本轮原生修复普通同组命令树，主动setsid/改变组或监护者独立遭外部强杀/暂停仍无保证，继续属于原goal。代价：不能以本轮局部保证宣称任意daemon/多故障场景或整个产品长期可靠。
4. 否决第1项，保留独立SID/PGID；监护者拥有真实子命令，通过私有状态FD在原生终态后映射真实退出码。代价：内部process.pid代表owner，增加有界退出码协议，需参数/终端/负信号回归。
5. 验证、普通工具、异步editor统一owned边界与后台启动；POSIX改用管道读取线程。代价：精确六生产文件变更、所有消费者需要回归，同步终端editor原路径仍保留。
6. 私有管道转发句柄信号并返回真实用户状态，不直接信号监护者PID。代价：内部proxy/IPC必须维护，不能回退历史PGID清理；原生SIGUSR1错误先RED后修。
7. 构造失败关闭已启动owner与管道，取消和原生启动错误同时发生时保留取消及错误cause。代价：用户已取消时优先呈现取消，而非正常启动失败；诊断保留原生原因。
8. 需要私有清理意图回执及owner终态，缺失时报告清理无法核实，重复close也不假成功。代价：独立监护者被强杀无法由其自身继续清理；回执到系统group kill之间不是防多重外力破坏的原子事务。
9. 公开SIGSTOP按真实Popen语义只暂停实际用户进程，监护者持续可运行，修复STOP/CONT/EOF排队竞态。代价：该方法不提供整棵树冻结语义；外部直接暂停整个组仍不属于单控制端崩溃保证。
10. 后台创建用Future及手动线程上下文复制，避免整体loop关闭取消创建Task而丢失资源。代价：取消/关闭必须等待原生创建收尾；其他异步worker的全局关闭边界继续属于整体审查。
11. 本次独立意见仅限第十八轮attested wheel后的六文件，不代替全项目/Windows实机/真实模型长期证据；对应复核未判断项保留原goal。代价：本阶段全量绿色也不能代表整体目标完成。

没有deferred minor。保留原分支/用户IDE/CSS，不提交/推送/真实API。完整证据与上述裁决复制到整体审查后，只删除本轮专属SDD scratch。

## 最终证据

- 核心3原生hardexit RED：`/tmp/ignovate-process-crash-first-red.log`；TTY/SIGPIPE/线程启动/信号/取消与原生失败/监护异常RED均已有对应回归，完整日志依次为 `/tmp/ignovate-process-crash-tty-red.log`、`/tmp/ignovate-process-crash-sigpipe-red.log`、`/tmp/ignovate-process-crash-reader-start-red.log`、`/tmp/ignovate-process-crash-signal-red.log`、`/tmp/ignovate-process-crash-cancel-start-error-red.log`、`/tmp/ignovate-process-crash-monitor-loss-red.log`。
- 一次fresh Astra 0C/2I/0M；作者3条原生补修RED：`/tmp/ignovate-process-crash-review-findings-red.log`。I1排队STOP+close及STOP+EOF，I2整体asyncio.run shutdown；GREEN相关110项48.376秒（3skip）：`/tmp/ignovate-process-crash-review-findings-green.log`。无第二次复核，无deferred minor。
- 最终完整Python：`/tmp/ignovate-process-crash-accepted-full-python.log`；JS：`/tmp/ignovate-process-crash-accepted-js.log`；实际外部安装：`/tmp/ignovate-process-crash-accepted-installed-native.log`；CLI：`/tmp/ignovate-process-crash-accepted-wheel-smoke.log`。新增24项中1Windows原生skip。
- 最终wheel `/tmp/ignovate-process-crash-accepted-wheel/ignovate_harness-1.2.1-py3-none-any.whl`，SHA-256 `4cd2394a7d9dc3e8838c6124273d244b659c71f298b1d288f18471e78e7ab88c`。最终再次核对153发行Python与当前源码相同；所有安装子进程测试以实际导入runtime的路径启动，不回落源树。
- 初次2065/2066、安装581与SHA b6c77d...早于两个Important补修，不作为最终验收。

## 原goal下一项原生发现

`/tmp/ignovate-mcp-controller-hard-exit-probe.log` 使用真实MCPManager/SDK stdio连接，服务已完成initialize/list_tools，controller_exit=81，native_mcp_connected=true，descendant_wrote_at_controller_exit=false，descendant_wrote_after_controller_exit=true。测试MCP服务启动普通同组有限时后代，未调用模型/远程服务、未删除用户数据或杀未知PID；探针均已自行结束。当前六文件只覆盖OwnedProcess入口，不覆盖SDK stdio_client启动的MCP服务，须单独修复并保留第九轮并发取消与连接协议。真实工作区旧外部链接、长期历史容量、其他全局loop worker关闭及Windows原生仍在整体范围。
