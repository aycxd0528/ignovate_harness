# MCP stdio控制端崩溃后的服务所有权

原始goal：全面review和修复，使复杂长任务可稳定交付。上一goalturn是progress：第十九轮受管命令2069Python/47平台跳过、JS17、安装584/153验收，原goal active。沿用授权native inline、离线测试、不提交/推送/真实API；保留原分支/用户IDE/CSS及已修复状态。

## 根因与现状

/tmp/ignovate-mcp-controller-hard-exit-probe.log：真实MCPManager+SDK stdio initialize/list_tools完成后os._exit81，普通服务后代由尚未写入变成之后写入。SDK finally无法在host硬退出时执行；stdio EOF至多使服务退出，不自动清理其后代。SDK参数没有自定义process factory或pass_fds，本机anyio支持pass_fds但SDK未暴露。Windows SDK已有Job（非暂停创建，无法从macOS推断原生安全）；本轮不改变Windows实现，保留原生待测。

## 备选

1. 重写stdio transport并接OwnedProcess：可控制pipe，但复制SDK JSON-RPC、背压、AnyIO TaskGroup和encoding协议；风险与范围过大。
2. 单个PPID monitor直接启动服务：SDK直接kill该monitor而未kill整组时可再次泄漏，不能接受。
3. SDK仍启动outer A（独立SDK SID），A观察真实getppid而不signal任何历史PID；A复用第十九轮PosixProcessGuard启动inner B和真实服务C（B/C独立SID）。只有A持B的lifeline写端。host硬退出由A监测后退出/close；SDK直接强杀A也会由OS关闭B的lifeline，使B收回C及普通同组子孙。选择3，SDK parser/stdio字节流直接继承不转发、不改变协议。

## 架构与契约

- 仅POSIX stdio包装参数，不原地修改服务定义；Windows/HTTP路径保持原SDK。原command/args/cwd/env/encoding/error policy保持，SDK最小env allowlist+显式引用仍是唯一环境来源，不放大权限或增加自动重放。
- SDK command改为当前sys.executable，args为-I -S、绝对trusted helper路径、os.getpid与原命令参数。helper位于core，可按受信任相邻绝对文件加载posix_process.py，不经cwd/PYTHONPATH导入；-I/-S仍只需stdlib。必须随wheel/安装器带齐。
- A首先核对自身为SDK新SID/PGID leader，以及os.getppid==传入host PID，错误不运行用户命令；A后台thread每20ms核对真实parent关系，改变则os._exit关闭所有A FD。只比较getppid身份，不用kill(parent,0)或历史PID kill。A初始尚未启动内层时parent消失也不得留下无owner服务。
- A使用PosixProcessGuard，B带原有启动门、CLOEXEC lifeline/私有状态协议、SIGSTOP修复与异常回执规则；A被SDK直接SIGKILL也由B收尾，不仅依赖A的finally。
- MCP专用opt-in release_stdio令A/B在C启动后释放不用的0/1/2副本，不能掩盖C关闭stdin/stdout；普通guard默认行为和旧wire保持。
- 复核补修：host与A在独立、仅127.0.0.1的临时socket上用随机32字节token完成握手，先于B启动/SDK初始化；不产生磁盘socket残留。A只有在guard已核实关闭后发清理回执，EOF无回执时host立即撤下工具、关闭接纳并报告清理无法核实，通过同一SDK owner单次取消收尾；不能只依赖仍由C持有的stdio。C/其他连接不继承该通道；本机loopback不可用则启动失败，不退回无监护执行。
- MCP stdin/stdout/stderr直接继承，不做JSON解码/转发，不消费stdin协议数据，不输出bootstrap文本到stdout。实际服务C不得继承B的控制/状态FD；私有helper自己的错误只用退出状态让SDK正常报告连接失败。
- 只有初始化确认/工具清单成功才能connected；启动失败/错误命令/已消失parent须失败关闭，不能假注册。相邻模块缺失或导入失败明确失败，不用不受管直接执行兜底。
- A在创建B后转发TERM/INT/HUP给guard，允许SDK原有stdin EOF/TERM grace与KILL escalation；不能因为外层只被直接kill而遗留C，不能让A handler改变C原有忽略信号。A真实服务signed退出码在清理后对应返回/信号退出，不作为新的工具执行证明。
- 正常断开、取消已执行调用、超时、反复关闭/重连/项目切换均回收A/B/C和普通同组子孙；取消排队请求不能影响活连接，HTTP不插进程owner。源stdio定义/secret过滤/descriptor分页/fee无变化。
- 新增两个POSIX监护进程、一个A parent-watching thread及host一个健康通道Task；是连接生命周期成本，不逐个RPC支付。边界仍是普通同组子孙；主动脱组daemon或独立强杀内层monitor（非SDK终止其直接子进程A）仍不由POSIX组保证，原goal保留。不能把SDK Windows注释当native证据。

## 验证与复核

先真实SDK+Manager硬退出RED，再实现。测试os._exit/SIGKILL host、SDK单独kill outer、normaldisconnect和activeRPC取消、忽略stdin的service、helper启动前parent已消失、missing可执行文件、原参数/stdin/encoding/env/secret/cwd、不注入项目同名模块、轮流重连/多连接与FD基线；只用专属临时目录和有限时probe子孙，失败时只cleanup已拥有handle。

精确第十九轮attested wheel SHA4cd2394a7d9dc3e8838c6124273d244b659c71f298b1d288f18471e78e7ab88c之后增量，一次fresh最有能力reviewer，作者一轮nativeRED/GREEN修C/I，无第二review。完整Python/JS/diff、外部pip --target实际来源/字节、CLI smoke。所有裁决代价持久化后只删本轮scratch，未完成整体goal前保持active。

## 一手依据

本机已安装mcp.client.stdio与mcp.os.{posix,win32}.utilities全文；requirements mcp>=1.30,<2.0。
https://docs.python.org/3.11/library/os.html#os.getppid ：Unix parent退出后返回新parent ID，区别于Windows可能复用的记录PID。此处只用于POSIX身份变化，不对该PID发信号。
