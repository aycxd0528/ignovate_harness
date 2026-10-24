# SQLite Global Shutdown Implementation Plan

> 使用executing-plans按既有授权native inline执行；TDD与一次fresh最终复核，不派implementer、不提交推送。

**Goal:** 已开始的SQLite生命周期在全局事件循环取消时仍完成原生获取/回滚/关闭，再传播caller取消。
**Architecture:** 仅SQLite生命周期使用私有拒绝cancel的Task；外层调用方仍通过原有shield/await_owned等待并传播取消。同loop/saver接口与继承SDK SQL/checkpoint实现保留；连接入口增加原生所有权适配。
**Spec:** docs/superpowers/specs/2026-10-23-sqlite-global-shutdown.md
**Tech:** Python>=3.11、stdlib asyncio.Task、现有aiosqlite0.22.1/AsyncSqliteSaver3.1.1。

## Global Constraints

完整原始goal保持active；保留现有codex/adaptive-task-runtime、已接受phase1–20与用户IDE/CSS。不真实API、提交、推送、全局SDK/asyncio monkeypatch，不引入第二eventloop/DB代理。不把Task/线程消失当资源已close，不破坏失败资源保留/取消cause/SDK timeout。当前macOS3.11不推导Windows/Linux/3.13通过。

## Review Focus

1. native开库在cancel快照前/后、setup或回滚进行中，以及重复caller取消时，必须仍收齐并关闭实际返回的连接，不能丢失或错误发布。
2. 关闭与首次开库、checkpoint锁和失败回滚重试交错时，不能重开替代、漏关闭或丢原始异常；同版并发仅一个saver。
3. 私有Task拒绝cancel必须只约束SQLite生命周期，不得吞模型/MCP/调用方取消或禁用其SDK deadline；Python3.11/3.13、task factory/context/debug差异需正面检查。
4. native setup/close失败或context已耗尽时，保留原句柄和owner并允许显式重试；失败不能清除账本或包装为成功，调用方取消仍有可核实cause。
5. 全局loop退出前native线程/连接必须完成清理，不能靠GC/进程退出或吞closed-loop错误；新增保护不能使所有权依赖永远未执行的清理Task。

## Task1：原生开库故障与owner

**Files:** new tests/fixtures/sqlite_shutdown_probe.py、tests/test_sqlite_global_shutdown.py；new nailong/core/sqlite_lifecycle.py；modify nailong/core/runtime_resources.py、agent.py、requirements.txt；new nailong/core/sqlite_saver.py。
**Interfaces:** _get_async_saver(context_factory)仍返回原生AsyncSqliteSaver；SQLite lifecycle Task持有opening；agent.AsyncSqliteSaver别名接入OwnedAsyncSqliteSaver，async_runtime接口不变。
- [x] 默认native SQLite全局cancel/actualFactory开库先RED；对照caller取消；真实句柄closed/worker无closedloop、无发布。
- [x] 私有SQLite生命周期Task保护opening；RED→GREEN，已有Factory生命周期不回归。

## Task2：回滚与关闭交错

**Interfaces:** consume Task1 owner；_close_unpublished_saver/_exit_saver仍保留原error/retry语义；published资源在受管清理成功时撤销记录。
- [x] caller取消后global discard、published close/checkpoint锁、原生失败/反复取消/context均有RED或兼容证据。
- [x] SQLite清理使用同owner策略；失败记录保留，不扩大到任意SDK timeout；Factory/入口/MCP/长任务相关全绿。

## Task3：复核与发行

- [x] 精确phase20 attested wheel增量一次fresh Astra复核0C/2I/0M；两项作者一次RED/GREEN，50项针对性通过10.962秒，无二次复核、无minor。
- [x] 完整Python/JS/diff、wheel实际pip --target+全部Python字节/来源/CLI smoke。
- [x] docs/PROJECT.md、CLI_REFERENCE.md、完整review记录和所有Ruling/成本持久化，只清本轮scratch；保持原goal active。

## 最终复核与作者唯一修复pass

报告 /tmp/ignovate-sqlite-global-review-final.md。两项Important均接受：实际SDK close失败会丢失native指针而重试假成功；真实native open失败会漏join SDK stop。作者3项RED2.124秒，另在attested pre-review wheel上验证实际worker内close失败1项RED0.708秒（原生句柄仍open、owner却已清除）。新OwnedConnection/OwnedAsyncSqliteSaver在原线程先验证close并在entry前拥有连接和stop，失败保留供retry；50项native/Factory/entry测试GREEN10.962秒，日志 /tmp/ignovate-sqlite-global-review-findings-green.log。不再次委托复核。

## 全部裁决及代价（含复核 Declined to judge 逐项）

1. **Ruling: SQLite-only生命周期Task拒绝直接cancel。** 全局asyncio.run会取消shield target，因此仅增加shield无效。成本：已接纳原生I/O/清理须完成后才返回取消，不承诺有界延迟；不得扩用任意SDK调用。
2. **Ruling: 保持同loop及SDK SQL/checkpoint接口，不使用第二loop代理或全局SDK patch。** 初版原context保持策略不足以覆盖SDK entry/close失败，已在第3项补连接入口适配。成本：直接Task构造绕过自定义task_factory，需保留名称/ContextVar及版本兼容证据。
3. **Ruling: entry前拥有连接、原worker先验证raw close、随后SDK close并等待stop和线程停止。** 接受两项Important，作者唯一RED/GREEN pass。成本：新增sqlite_saver.py与agent别名，耦合connector/stop/_execute/worker及默认chunk64，原生close持续失败时保留活跃worker供重试；不靠GC或跨线程关闭。
4. **Ruling: 依赖限定aiosqlite>=0.22.1,<0.23。** requirements-release.lock原已固定0.22.1，无需改锁；防止兼容范围默许不具备所需stop接口的旧版或未来不兼容改动。成本：升级须重新验证原生失败/停止契约，当前证据仅0.22.1。
5. **Ruling: 不承诺SIGKILL/进程强制终止后的优雅SQLite关闭。** 对应review declined1；成本：该路径依赖系统终止语义，本轮未做native强杀数据库持久性证明，不将其计入成功关闭覆盖。
6. **Ruling: 手工loop.close或永久停止未排空loop不受此保护。** 对应declined2；成本：嵌入方须遵循正常drain/await aclose，loop不再运行时无法执行owner。
7. **Ruling: 永久卡死native I/O不提供有界shutdown。** 对应declined3；成本：取消等待可能无限，需外部进程终止；不以提前清账或遗失句柄换取快速返回。
8. **Ruling: helper禁止用于模型/MCP/任意SDK timeout scope。** 对应declined4；成本：未来新增调用点必须单独审查；本轮仅SQLite，原SDK deadline不因该helper改变。
9. **Ruling: protected Task不承诺参与custom task_factory/自定义Task instrumentation。** 对应declined5；成本：这些SQLite任务无法完全复用嵌入方Task子类/监控；普通Task仍走原factory，名称/ContextVar保留。
10. **Ruling: 不由macOS3.11及3.13 helper探针推导完整跨平台SDK保证。** 对应declined6；成本：Python3.12/3.13原生SDK、Windows/Linux/uvloop仍待实际矩阵；reviewer3.13.13 helper/debug/eagerfactory通过仅该范围有效。
11. **Ruling: final reviewer只审精确phase20 wheel增量，父执行者负责全量/JS/安装发行证据。** 对应declined7；成本：本轮评审不等于重审phase1–20或整个goal完成；两项继承SDK问题因属于本轮要求已修，未降级排除。按流程不二次review，由作者RED/GREEN及发行门验证唯一修复pass。
12. **Ruling: 不新增自动全局资源注册表；未请求/未接纳的close不产生所有权。** 对应declined8；成本：调用方不可放弃已发布Factory，须显式await aclose；API接纳前取消不打开资源，但也不能替从未进入的close API接管已有资源。

## 最终发行验收（Important修复后）

- 原生/Factory入口50项10.962秒；相关169项/2平台跳过72.053秒。
- 全量Python2111项/48平台跳过326.392秒；JS17项全部通过，git diff --check通过。
- 工作区外真实pip --target安装，656项/5平台跳过177.779秒；43关键模块来源与全部157发行Python字节核对通过。不是wheel解压后借源码代替产品模块。
- 已安装CLI help、JSON doctor、依赖smoke通过，无API；wheel元数据Requires-Dist: aiosqlite<0.23,>=0.22.1核对。
- wheel /tmp/ignovate-sqlite-global-accepted-wheel/ignovate_harness-1.2.1-py3-none-any.whl SHA256 6b85f714b933ab73bb7f3d401950d0dbe8c182a3752667daac263833e8e4fd69。
- 日志 /tmp/ignovate-sqlite-global-accepted-{related,full-python,installed-native,js,wheel-smoke}.log，实际安装脚本 /tmp/ignovate-sqlite-global-accepted-installed.py，精确phase20增量 /tmp/ignovate-sqlite-global-accepted.diff。
- 四生产Python文件、requirements版本界限与两原生测试文件；无minor。fresh最终review已执行一次，作者单次C/I修复pass已验收，不再review。保留分支codex/adaptive-task-runtime、用户IDE/CSS与全部先前工作，无commit/push/真实API。原始全项目goal active，phase21是progress。

## 完整执行ledger（归档后清理本轮scratch）

# SDD ledger — plan: docs/superpowers/plans/2026-10-23-sqlite-global-shutdown.md
Previous goal turn progress: phase20 accepted2094/48skip, installed639/5skip 155files41paths, JS17 SHAe5ee993852688f3d70a37ee576e01d4520796eead3925fe6dde68ec948616fd1; originalgoal active. No blocker. Existing authorization native inline/offline/noAPI/no commitpush, preservebranchIDE/CSS.
Pre-flight Task1 SQLite owner consumed byTask2 cleanup, exact original context/saver remain same loop, external cancellation still awaited/propagated; failure record maynotbe removed untilverifiedclose. Task3 attests actualwheel notHEAD olderthan20phases. No independent implementer work.
Ruling: SQLite-only lifecycle Task explicitly declines direct cancellation rather than deeper shield nesting — global asyncio.run cancels every native Task, including shield targets; cost: native SQLite I/O must finish before cancellation returns and truly stuck I/O may need process termination, no bounded-latency promise.
Ruling: keep original async saver/context on its loop, do not proxy to a second event loop or monkeypatch aiosqlite — preserve current locking/checkpoint semantics; cost: private lifecycle Task uses publicTask constructor and bypasses custom loop task_factory, requires context/name/version tests and must never wrap arbitrary SDK deadlines.
Task1 native RED3tests1.266s/2fail /tmp/ignovate-sqlite-global-open-red.log: both actual privateowner/publicAgentFactory returnloop before delayed nativeconnectdone, retainedhandleopen, unpublished0, workerthreadclosedlooperror; singlecallercontrolgreen. New SQLite-only lifecycleTask .cancel refuses directcancellation; _get onlyopening switched first, no SDK/aiosqlite patch.
Task1 GREEN19/19 2.050s /tmp/ignovate-sqlite-global-open-green.log incl old16 Factory lifecycle and3 nativecases; context/name probe reports (caller,sqlite-context-probe), directcancelFalse. Task2 adds actual nativeclose/discard/held-checkpoint-lock globalshutdown and caller timeout/repeatedcancel. No extra SDK task protection.
Task2 native RED8tests3.163s/3fail /tmp/ignovate-sqlite-global-cleanup-red.log: globaldiscard leavesunpublished1 thoughnativeclosed; globalpublishedclose andcheckpointlock leavespublishedTrue thoughnativeclosed. Explicitnativeclose proof differentiates bookkeeping fromphysical leak. Protectdiscard/rollback/publishedclose SQLiteonly, clearpublishedfields insideverifiedprotectedoperationbeforeexternalawaiterraisescancel; retainfailurecontext unchanged.
Task2 GREEN41/41 4.727s incl8 native/factorylifecycle/entrypoints /tmp/ignovate-sqlite-global-cleanup-green.log. Added actual nativeopenfailure/setupfailure preservecancelcause, closefails2x retainsactualopenhandle/CTX untilretry3rdclose; native11/11 4.035s /tmp/ignovate-sqlite-global-failure-native.log. Legitimate injected nativefailure mayreachrunner errorhandler withoriginalmarker; no closedloop threaderror. Addedcontext/name/customfactory unchanged, precancelnoadmission and6nativeopenclose FD/thread baselines. Broadrelated currentlylive, production2files frozenforreview.
Task1/2 complete GREEN166 tests66.761s/2 platformski ps /tmp/ignovate-sqlite-global-related-green.log inclnew14 andnativeMCPtimeout/cancel/crash, phase19processowners, Factory/GUI, actual longGoaldriver/continuation. Producttwofiles frozen. OnefreshAstra sqlite_global_shutdown_review dispatched exactphase20wheelincrement. Sourcefull/JS/prewheel underway; no rereview planned.
Final original2file review /tmp/ignovate-sqlite-global-review-final.md: 0C/2I/0M, both inherited SDKownership gaps directcurrentrequirements, accepted notdeclined. Important1 originalaiosqlite.close inside_execute failure clearsnativeptr/stopsworker; retriesnoop false successactualhandleopen. Important2 realC OperationalError `_connect.stop()`futureunjoined whenaenterfailbeforeyield; deterministicthreadtraceclose_and_stop .2s gate provesrecordremoved/loopclosedbeforeworkerstop. Author onefixpass begins withrefinedrealSDKfaulttest and2stoporderingnativecases, notweakenretainedhandleassertion.
Pre-reviewfull2108/48skip313.481s and installed653/5skip170.702s all156files42paths SHAaed5116dd7d976041ff45c048e32346de306028f8eab824d56441e8fbbd055d1 are preImportantfix evidence only, notfinal. ReviewerPython3.13.13 helper/context/name/debug/eagerfactoryprobeGREEN; notfullSDKnative3.13.
Author2Important RED3tests2.124s /tmp/ignovate-sqlite-global-review-findings-red.log: trueSDKclose/execute failure retry returnsfalse successnativeopen/proxypointerlost; realSQLitenonexistentparent OperationalError withonlythreadtrace .2s delaystop global/normal bothloopreturnbeforestop andclosedloopthreaderror. Refinedoriginalsyntheticclose probe nowinsideSDKpath; tests useactualAgent-exposedbackend, notswapnativehandleassertions.
Ruling: own async SQLite connection/context before SDK entry and verify raw close on original worker before destructive SDK close — Task shielding alone cannot retain never-yielded connection orlostSDKpointer; cost: addcore/sqlite_saver.py subclassadapter plusagentalias, dependonaiosqlite connector/stop/_execute/worker contracts, fixeddefaultchunk64 andnativeversionregressionrequired; no globalpatch/secondloop/check_same_thread change/GC fallback.
Onefixpassimplementation: OwnedAsyncSqliteSaver subclass retains allSDKSQL/checkpoint logic; ownConnectioncapturesdefaultnativehandle insideoriginalworkerconnector, close firstqueuedrawclose success thenSDKproxyclose+cachedstopFuture+actualthreadjoin via1mssleep, failedrawcloseleavesliveoriginalhandle/workeraccounted forretry. Nativeopenfail finallyjoinsworker evenbeforeyield; no extraOSthread/process, no API/SDKdeadline wrapper.

Author onefixpass GREEN50/50 10.962s /tmp/ignovate-sqlite-global-review-findings-green.log. Added actual sqlite3.Connection.close failure on nativeworker: attestedpre-reviewwheel RED1test.708s /tmp/ignovate-sqlite-global-native-worker-close-baseline-red.log; failedSDK droppednativepointer/stoppedworker andexplicitretryclearedownerwhileactualhandleopen. Newadapterall17nativecasesGREEN, actualworkerpreserveduntilretryandclosedbeforeloopreturn. PublicFactory/entry old33testsGREEN. Requirements addaiosqlite>=0.22.1,<0.23 toboundadaptercontracts; lockalready0.22.1. Finalfull/installedmust rerun, pre-reviewevidence notfinal.

## 全部裁决及代价（含复核 Declined to judge 逐项）

1. **Ruling: SQLite-only生命周期Task拒绝直接cancel。** 全局asyncio.run会取消shield target，因此仅增加shield无效。成本：已接纳原生I/O/清理须完成后才返回取消，不承诺有界延迟；不得扩用任意SDK调用。
2. **Ruling: 保持同loop及SDK SQL/checkpoint接口，不使用第二loop代理或全局SDK patch。** 初版原context保持策略不足以覆盖SDK entry/close失败，已在第3项补连接入口适配。成本：直接Task构造绕过自定义task_factory，需保留名称/ContextVar及版本兼容证据。
3. **Ruling: entry前拥有连接、原worker先验证raw close、随后SDK close并等待stop和线程停止。** 接受两项Important，作者唯一RED/GREEN pass。成本：新增sqlite_saver.py与agent别名，耦合connector/stop/_execute/worker及默认chunk64，原生close持续失败时保留活跃worker供重试；不靠GC或跨线程关闭。
4. **Ruling: 依赖限定aiosqlite>=0.22.1,<0.23。** requirements-release.lock原已固定0.22.1，无需改锁；防止兼容范围默许不具备所需stop接口的旧版或未来不兼容改动。成本：升级须重新验证原生失败/停止契约，当前证据仅0.22.1。
5. **Ruling: 不承诺SIGKILL/进程强制终止后的优雅SQLite关闭。** 对应review declined1；成本：该路径依赖系统终止语义，本轮未做native强杀数据库持久性证明，不将其计入成功关闭覆盖。
6. **Ruling: 手工loop.close或永久停止未排空loop不受此保护。** 对应declined2；成本：嵌入方须遵循正常drain/await aclose，loop不再运行时无法执行owner。
7. **Ruling: 永久卡死native I/O不提供有界shutdown。** 对应declined3；成本：取消等待可能无限，需外部进程终止；不以提前清账或遗失句柄换取快速返回。
8. **Ruling: helper禁止用于模型/MCP/任意SDK timeout scope。** 对应declined4；成本：未来新增调用点必须单独审查；本轮仅SQLite，原SDK deadline不因该helper改变。
9. **Ruling: protected Task不承诺参与custom task_factory/自定义Task instrumentation。** 对应declined5；成本：这些SQLite任务无法完全复用嵌入方Task子类/监控；普通Task仍走原factory，名称/ContextVar保留。
10. **Ruling: 不由macOS3.11及3.13 helper探针推导完整跨平台SDK保证。** 对应declined6；成本：Python3.12/3.13原生SDK、Windows/Linux/uvloop仍待实际矩阵；reviewer3.13.13 helper/debug/eagerfactory通过仅该范围有效。
11. **Ruling: final reviewer只审精确phase20 wheel增量，父执行者负责全量/JS/安装发行证据。** 对应declined7；成本：本轮评审不等于重审phase1–20或整个goal完成；两项继承SDK问题因属于本轮要求已修，未降级排除。按流程不二次review，由作者RED/GREEN及发行门验证唯一修复pass。
12. **Ruling: 不新增自动全局资源注册表；未请求/未接纳的close不产生所有权。** 对应declined8；成本：调用方不可放弃已发布Factory，须显式await aclose；API接纳前取消不打开资源，但也不能替从未进入的close API接管已有资源。

Related169/2skip72.053s GREEN /tmp/ignovate-sqlite-global-accepted-related.log. FullPython/actualinstalledcurrentlylive. JS17/diff/actualwheelCLIhelpJSONdoctor/dependencies passed; finalwheel157Python43modulepaths awaiting installedtest result.

## 最终发行验收（Important修复后）

- 原生/Factory入口50项10.962秒；相关169项/2平台跳过72.053秒。
- 全量Python2111项/48平台跳过326.392秒；JS17项全部通过，git diff --check通过。
- 工作区外真实pip --target安装，656项/5平台跳过177.779秒；43关键模块来源与全部157发行Python字节核对通过。不是wheel解压后借源码代替产品模块。
- 已安装CLI help、JSON doctor、依赖smoke通过，无API；wheel元数据Requires-Dist: aiosqlite<0.23,>=0.22.1核对。
- wheel /tmp/ignovate-sqlite-global-accepted-wheel/ignovate_harness-1.2.1-py3-none-any.whl SHA256 6b85f714b933ab73bb7f3d401950d0dbe8c182a3752667daac263833e8e4fd69。
- 日志 /tmp/ignovate-sqlite-global-accepted-{related,full-python,installed-native,js,wheel-smoke}.log，实际安装脚本 /tmp/ignovate-sqlite-global-accepted-installed.py，精确phase20增量 /tmp/ignovate-sqlite-global-accepted.diff。
- 四生产Python文件、requirements版本界限与两原生测试文件；无minor。fresh最终review已执行一次，作者单次C/I修复pass已验收，不再review。保留分支codex/adaptive-task-runtime、用户IDE/CSS与全部先前工作，无commit/push/真实API。原始全项目goal active，phase21是progress。
