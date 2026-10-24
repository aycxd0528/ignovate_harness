# 控制进程崩溃后的命令所有权

原始goal：全面审查并修复，使复杂长任务能稳定交付。上一goal turn是progress：第十八轮临时索引验收2045Python/46平台跳过、JS17、安装482/151；当前分支codex/adaptive-task-runtime，保留用户IDE/CSS、全部历史与费用，无提交/推送/真实模型API。沿用已授权native inline离线修复，不重复设计审批。

## 事实与根因

/tmp/ignovate-process-controller-hard-exit-probe.log：实际execute_process启动命令后控制Python进程os._exit81，finished_at_controller_exit=false，但command_wrote_after_controller_exit=true。POSIX finally无法执行，没有独立持有命令生命周期的owner。OwnedProcess同样只由parent finally发killpg；Windows已有non-inheritable kill-on-close Job，不据此声称Windows实机通过。

## 备选与选择

1. 仅Linux PR_SET_PDEATHSIG不能覆盖macOS，且只约束直接进程；不选。
2. 持久PID清单+重启清理不能防重启前继续写入，存在PID复用和清理未知进程风险；不选。
3. POSIX专用lifeline+独立会话的监护启动器。最初同会话group方案在真实控制终端输入时SIGTTIN暂停全组，故拒绝该方案。最终监护者是新SID/PGID的leader，获得parent许可后用Popen启动真实命令（同组、非新会话）；自己监测lifeline及命令退出。parent唯一写端CLOEXEC且不传给真实命令；EOF/内部异常/命令正常退出均终止自身组，包括监护者自己。独立状态管道只由监护者持写端，传startup确认及真实signed退出码；parent proxy在原生监护进程终态后映射真实命令退出码。每条命令增加一个Python监护进程；不增加持久PID协议。

## 契约

- 普通shell/argv、Unicode/空参数、cwd/env/PATH、stdin/stdout/stderr、真实正负退出码、bounded capture、权限与费用语义保持。Python helper以绝对文件路径、隔离模式-I -S启动，不从项目cwd导入模块。
- guardian建立新session/group并安装信号策略前不得执行用户命令。parent死于gate/监护启动任一阶段不放行失去owner的命令；握手/exec失败必须回报且收回资源。
- 专用life写端只由parent拥有，close_fds/CLOEXEC阻止工具及并发命令继承；watcher始终作为新group leader固定组身份，不发历史PID kill，不依赖后续启动。
- watcher忽略TERM/INT/HUP以保持soft terminate时的组锚；EOF或内部异常均kill自身已验证组。正常close等待watcher及pipe cleanup；watcher自然退出先记录真实命令退出码再SIGKILL自身组。原生Popen暴露的是owner PID，proxy返回用户命令退出码；上层不把owner PID当用户程序PID。异常不冒充停止成功；失败可重试close。独立watcher本身被外部SIGKILL/SIGSTOP不属于单控制进程崩溃保证，不能默默把失败当成功。
- 公开Popen句柄的SIGSTOP只暂停真实用户进程，监护者不能一起暂停；排队STOP与关闭/EOF竞态仍须清理。终态映射须有私有stopping回执及owner原生终态；缺失回执返回清理未知错误。该回执不是对独立外力在回执与系统调用之间强杀监护者的原子保证。
- 保留POSIX start_new_session隔离及同步终端editor原控制终端路径。POSIX进程组不是OS沙箱：主动setsid/变更组的daemon仍不能由此保证清理，保留到整体goal进程边界审查，不删减验证输入/交付要求。
- execute_process与OwnedProcess共用边界，避免verification继续绕过守卫；若统一PipeCapture，构造放后台并在取消下join创建/清理，事件循环不能因启动等待被卡住；真实观测、超时、取消回执和输出限制保持。
- 后台创建用executor Future及显式copy_context，不能用会被asyncio.run整体关闭直接取消的独立Task丢弃最终资源。创建失败与取消同时发生时保留取消并携带原生失败原因；线程创建完成后取消方仍负责关闭资源。
- 监护者在隔离单线程进程中使用child preexec恢复parent原有忽略信号和默认restore_signals策略；不在有线程的controller使用preexec。Python仅stdio/argv传输bootstrap，不新依赖/全局配置/用户文件。正常/取消/失败关闭所有临时FD、wait自己的watcher，不等待未知进程。CLI/SDK/GUI所有经owned捕获的命令享有同样保证；MCP另有服务生命周期，不偷换成本轮覆盖。

## 验收

真实外层子进程kill/os._exit控制端后，实际command、child、grandchild的有限时late marker不出现（同时确认原命令确实启动）；带独立capture_bytes/普通工具/execute_process的shell和argv路径，真实返回码、启动失败、重复取消、FD/并发隔离和阻塞启动响应。gate/monitor失败和握手窗口用原生脚本/受控Popen边界拦截，不能用mock断言代替清理效果。Windows新增native硬退出Job测试可以保留平台skip，不能用模拟证明。

一次fresh最有能力reviewer只复核第十八轮attested wheel d1c7481e83c5796c18ca01d52bacee29512cf11ca46076422ff62e465bb91973之后增量；一轮nativeRED/GREEN修C/I。完整Python/JS、实际pip --target安装字节与路径、wheel CLI smoke；原goal继续active，跨平台/逃逸daemon/完整长期容量与其他全项目链路仍需证据。

## 一手参考

https://docs.python.org/3.11/library/subprocess.html （process_group、pass_fds、负信号退出码及启动边界）
https://docs.python.org/3.11/library/os.html （non-inheritable pipe fd、exec/killpg）
本机Python3.11.15 macOS没有os.waitid，不能依赖WNOWAIT来固定已退出leader；选择活监护组成员维持身份。
