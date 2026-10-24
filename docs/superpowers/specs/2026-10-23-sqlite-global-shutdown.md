# SQLite 生命周期与事件循环整体关闭

原始goal：review整个项目并修复遗漏，使复杂长任务可稳定交付；保持完整范围。上一轮phase20是progress，原始goal active：源码2094/48skip、安装639/5skip、155Python/41paths、JS17，wheel SHAe5ee993852688f3d70a37ee576e01d4520796eead3925fe6dde68ec948616fd1。继续已授权native inline、离线测试、不提交/推送/真实API，保留分支/用户IDE/CSS。

## 根因与要求

真实RuntimeResourceOwner+AsyncSqliteSaver+aiosqlite，native sqlite3.connect延迟.2秒，asyncio.run整体关闭直接cancel所有Task，包括被shield的内部opening。aiosqlite取消其连接future并清除字段，native worker之后返回时loop已关闭；Factory删除未发布记录，但探针保留供核验的原生连接仍open，worker报Event loop is closed。单caller取消对照正确close。日志 /tmp/ignovate-factory-global-shutdown-control-probe.log。不据保留句柄断言每次真实调用永久泄漏，但必须在宣称开库/回滚结束前真正等待native完成。

1. 已开始的SQLite acquire/setup/rollback/close由明确生命周期owner持有；全局Task取消、重复caller取消不得越过native完成、遗失返回值或假清除记录。
2. 原调用方仍收到取消，错误cause保留；取消初始化不发布saver、不生成新图或重开替代；setup/close失败保留原owner记录以便显式重试。
3. Factory关闭仍封接纳、等待开库和checkpoint锁，MCP/SDK独立清理与重试规则保持，单项失败不跳其他资源。
4. 原生SQLite/aiosqlite与继承SDK SQL/checkpoint逻辑的AsyncSqliteSaver保持同一事件循环上下文，不fork/复制SDK或引入全局monkeypatch、异步第二loop或额外数据库写入队列。
5. 原生资源在loop关闭前完成清理；worker无closed-loop回调，保留原生句柄仍验证closed，不以Task完成、worker线程为空、GC或进程退出代替证明。

## 设计与备选

- 仅追加shield/更多嵌套Task：所有Task同属全局cancel快照，不解决根因。
- 独立thread/loop代理整个async saver：改动checkpoint接口、锁/stream/context归属，成本过大且引入新跨loop问题。
- 选择私有SQLite生命周期Task，cancel明确拒绝中断原生已开始的获取/清理；调用方仍经既有shield/await_owned接收取消并等owner结束。只在SQLite生命周期使用，不应用到模型、MCP、任意外部工具或SDK deadline；native异常仍按原路径回滚/保留。

新增core/sqlite_lifecycle.py，使用公开Task构造/cancel接口，不读写asyncio私有注册表或改全局task factory。代价：私有生命周期task刻意不可cancel、绕开loop自定义task_factory（保留名称/上下文），取消必须等待SQLite原生操作，卡死I/O仍需外部进程终止；不能假设任意SDK timeout在此owner里仍能中断。Python>=3.11，当前本机3.11，3.13和Windows/Linux仍需原生矩阵。

## 验证

先父临时目录托管的有限时子进程nativeRED：默认SQLite连接、保留原生句柄/worker，仅故障延迟，不依赖GC。覆盖global opening、publicFactory opening、caller取消后global discard、published close/等待checkpoint锁及原生开库/close异常，记录loop返回时native是否结束、连接closed、发布/未发布记录、线程/loop错误。随后原Factory生命周期、所有入口/SQLite/checkpoint/MCP回归；完整Python/JS/diff、实际外部pip --target与字节/模块来源/CLI smoke。

一次fresh最有能力reviewer精确phase20 wheel增量，作者一轮C/I nativeRED→GREEN，无二次review；所有裁决和成本先持久化再删本轮scratch。无API/commit/push，整个goal不会因本轮通过而complete。

## 一次最终复核后的原生所有权修正

独立Astra最终复核0C/2I/0M：实际SDK close的finally会在原生关闭失败时丢掉连接指针；原生开库失败时SDK丢弃stop返回的future。两项均是本轮明确要求内的旧SDK边界缺口，不能由仅stub close或loop返回后join证明已解决。作者一次修复pass，无二次复核。

新增core/sqlite_saver.py和agent导入别名：连接在context进入前即被拥有，保存原线程创建的真实连接；先在该线程验证原生close成功，再运行SDK close，缓存stop完成并等实际线程结束。原生close失败保留原连接及worker供显式重试；失败开库同样等待stop，即使尚未yield saver。仍继承SDK全部SQL/checkpoint实现，使用默认SQLite线程亲和及chunk64，无第二loop、额外worker、全局patch或GC替代证明。requirements.txt限定aiosqlite>=0.22.1,<0.23，锁文件已有0.22.1；代价是版本升级必须重新验证内部_execute/worker/stop契约。

新增真实OperationalError+SDK stop closure线程trace gate，以及实际工作线程中sqlite3.Connection.close故障；都保留原生句柄，严格先记录loop返回瞬间stop状态，再作有限探针清理。旧wheel失败，新生产backend通过。
