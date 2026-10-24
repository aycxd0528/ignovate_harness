# MCP stdio崩溃所有权实施计划

> executing-plans native inline + TDD +一次requesting-code-review；沿用不提交/推送/真实API。

**Spec:** docs/superpowers/specs/2026-10-23-mcp-crash-ownership.md
**Goal:** localMCP服务在host控制端硬退出/SDK直接kill外层后，其普通后代仍由独立lifeline收回，保留SDK协议与现有连接隔离。

## Global Constraints

不改SDK全局factory、不复制JSON-RPC transport、不更改原stdio/HTTP定义、env allowlist/secret/encoding/权限/fee；不kill历史PID、不清用户历史。仅POSIX stdio新参数包装；Windows原生需要实测。复用已验收coreguard，保留原分支IDE/CSS，无commit/push/API，原goal active。

## 文件与接口

- nailong/mcp/client.py：本地POSIX StdioServerParameters包装为-I/-S absolutehelper+parentPID+原argv，原cwd/env和SDK上下文保持。
- nailong/core/_mcp_stdio_owner.py：stdlib入口A，核对SDK新session/parent，真实parent关系thread，绝对受信coreguard加载、服务stdio直接继承、信号/正常退出状态、所有失败关闭。
- nailong/core/posix_process.py及_posix_process_child.py：MCP opt-in释放监护者stdio副本；旧普通guard wire兼容，默认不变。
- nailong/mcp/stdio_owner.py：复核Important补修，连接独立且认证的loopback健康通道；先握手、异常撤工具/封接纳、同owner只取消一次，无磁盘socket残留。
- tests/test_mcp_crash_ownership.py：实际SDK/Manager/nativecontroller故障；不依赖mock注册存在来证明清理。
- docs/PROJECT.md、CLI_REFERENCE.md和整体review记录；所有ruling复制durableplan。

## Review Focus

1. parent死亡发生在helper/B创建任一时刻，或SDK只kill外层A，B的控制写端不能被C/其他连接持有；初始化不能放行未受管服务。
2. A必须在自己的SDK session/group中，错误参数不能killhost或旧PID；getppid是活关系而非kill历史父PID；parentthread失败不得静默失去监护。
3. TERM/INT/HUP中继保留C原语义，SDK EOF/grace/escalation及signed退出码不被A吞改；A/B异常不能冒充connected/成功调用/已清理。
4. 原command/argv/path/cwd/env/encoding、stdlib隔离、secret过滤、HTTP与Windows路径保持；源码/实际wheel helper引用不能落回source树。
5. SDK AnyIO取消域与async owner一致；队列取消不杀其他调用，连接/断开/重连/全局关闭和FD/monitor基线需原生验证，不靠Task完成猜测进程已收回。

## Task1：native故障RED与启动器

- [x] tests真实MCP已connected的host os._exit/SIGKILL后代RED；确认控制端退出前未写入、之后旧实现写入。
- [x] stdlib outer+trusted coreguard，POSIX参数包装；nativecrash→GREEN、normaldisconnect/args/env/stdio基本兼容。

## Task2：启动、取消与多连接

- [x] SDK直接终止A仍cleanup，startup parent已失效拒绝，原生execfail和helper模块隔离、service stdinEOF/TERM行为、无真实API。
- [x] activeRPC取消/timeout、排队不连带、HTTP、reconnect、反复关闭和FD基线；第十九轮24项以及SDK/UI/factory相关全部回归。

## Task3：fresh review与发行

- [x] 一次fresh Astra精确增量review，C/I一轮nativeRED/GREEN、fullsuite，不二次review。
- [x] 完整Python/JS/diff、wheel实际pip --target+来源/字节/CLI smoke。
- [x] 记录全部Ruling/代价/minor，复制持久文档后只清本轮scratch，保持原goal active。

## Task1/2证据

真实SDK三项崩溃故障原先3项失败5.399秒，现相关72项43.934秒通过、1 Windows平台跳过；另配置/工具/Factory/GUI/runner入口107项11.367秒通过。父进程在受信模块加载阻塞时退出、监测线程故障、服务主动关闭stdin/stdout、旧普通guard wire和原argv/env/cwd均有原生回归。旧测试的echo断言改按SDK实际未结构化content解析，不改变生产normalizer。

本机六次预热连接样本：旧wheel .1592–.1679秒，新源码 .1797–.1888秒；监护者两个合计RSS37,392–37,616 KiB；20次真实RPC旧 .0110–.0119秒、新 .0111–.0115秒。样本不是性能承诺，内存成本不能称为可忽略。日志 `/tmp/ignovate-mcp-crash-native-overhead.log`。

## Final review与一次修复

一次fresh Astra原始四生产文件增量复核0C/1I/0M：B死亡后A退出127但C保有管道，SDK不观察A，仍接纳调用。作者真实有限时服务自注入原生RED1.619秒，补host/A私有健康sideband后GREEN0.483秒；源码实现新增stdio_owner.py，因此最终五生产文件。无二次review；所有declined judgments分别保留Ruling，完整列于本计划最终ledger。

## 裁决记录（完整、按时间顺序；lightweight初始措辞由实测约37MiB取代）

Ruling: keepSDKprotocol/AnyIOstdio owner, addouterlaunchwrapperratherthancopyparserorpatchglobalSDKfactory — cost: newtrustedlauncherfile andcouplingtoSDKnew-sessioncontract, explicitnativeSDKregressionneeded.

Ruling: A observeslivePPID andBusesphase19lifeline soSDKdirectkillAstillcleansC; singlePPIDmonitoraloneinsufficient — cost: twopermanent-lightweightPythonhelpersperlocalPOSIXconnection, oneAthread, measuredoverheadrequired.

Ruling: comparegetppidonlyonPOSIX, neverkillrecordedparentPID; WindowsSDKJob/HTTPremainoriginalpaths — cost: Windowsnative/crashassignmentedgeisnotprovenbythisfix andbroadergoalcontinues.

Ruling: opt-inmonitorsreleaseallunusedstandardFDcopiesforMCP, ratherthanrelaxbrokenpipetimeout — cost: sharedcoreguardprotocolchanges(twoextraexistingfiles), noemergencyloggingtoMCPstdioafterinitialization; ordinaryguarddefaultpreservedandregressed.

Ruling: preserveexistingordinaryguardwire; MCPopt-inreservednon-numericpolicytag — cost: newMCPopt-instillrequirespairedcurrenthelper, mixedolderhelperrejectsstartup ratherthanlosingFDpolicy.

Ruling: use authenticated ephemeral loopback socket sideband for outer liveness/cleanup status — direct C pipes cannot report monitor death and SDK exposes no process handle/pass_fds; cost: one additional private IPC channel/task per connection and local loopback permission required, startup fails closed if unavailable; no extra service authority or remote listener.

Final: Ruling: independent B SIGKILL reclamation remains unproven, but admission/error loss is fixed rather than declined — current process-group owner cannot clean after its own death; cost: survivors may still have side effects and must remain unknown, broader goal needs containment decision.

Final: Ruling: deliberate setsid/setpgid escape is an unresolved product boundary, not universal cleanup — POSIX group membership does not contain escaped processes; cost: detached effects may continue, original goal remains active.

Final: Ruling: native Windows unchanged SDK Job behavior is not accepted from macOS evidence — new real-controller hardexit test is platform skipped here; cost: Windows assignment/crash failures can remain until native execution.

Final: Ruling: native Linux acceptance is not inferred from POSIX source/macOS tests — no Linux runner available; cost: kernel/platform differences remain unverified.

Final: Ruling: disclose measured per-connection resource cost without inventing unsupported capacity guarantee — six warm samples are not a high-load acceptance threshold; cost: connection-count memory pressure still requires capacity testing/control under broader goal.

Final: Ruling: this exact-increment review does not close the entire project goal — shared-core and fullsuite regressions cover this phase only; cost: remaining whole-runtime shutdown, history capacity, actual old workspace link and platform delivery matrix still need evidence.

## 最终验收

作者一次Important修复后完整Python2094项320.812秒、48平台跳过，其余通过；JS17/diff通过。相关原生186项57.308秒、2平台跳过；本轮新增25项中1 Windows native skip。实际外部pip --target安装639项170.684秒、5平台跳过，其余通过；41关键模块来自安装目录，155发行Python与源码字节一致。CLI help/JSONdoctor/依赖smoke通过，无真实API。最终wheel SHA256 `e5ee993852688f3d70a37ee576e01d4520796eead3925fe6dde68ec948616fd1`，日志前缀 `/tmp/ignovate-mcp-crash-accepted-`；03e6ac.../2087/632是Important修复前证据，不作最终验收。最后六次本机预热样本启动 .1557–.1805→.1838–.1875秒，两个helper合计RSS37,920–38,128 KiB；20 RPC .0110–.0117→.0112–.0117秒。包含host/A认证回环IPC，不是容量保证。

No deferred minors. 按既有授权保留当前codex/adaptive-task-runtime分支和原IDE/CSS，不提交/推送；只清本轮scratch，先持久化全部Ruling和原始ledger。原goal active。

## 原始执行ledger（供后续恢复）

# SDD ledger — plan: docs/superpowers/plans/2026-10-23-mcp-crash-ownership.md
Previousturnprogress: phase19 accepted2069/47skip JS17 installed584/153, originalgoal active; no blockers, nobudget. Workspacecodex/adaptive-task-runtime keptperexistingauthorization, noAPI/commit/push.
Pre-flight: Task1 createsSDKouterwrapper A andwrapsPOSIXparameters; Task2 consumesunchangedMCPManager/SDKpublicstdio. A mustreusecorePosixProcessGuard B throughtrustedabsolute file; B childC inheritonlystdio/env andno controlwrites. SDKminimalenv/cwd andsession remains same. GlobalparentwatchmuststartbeforeBconstruction anduseos._exitonparentloss soBkernelFDclosureworks evenifAblocked.
Ruling: keepSDKprotocol/AnyIOstdio owner, addouterlaunchwrapperratherthancopyparserorpatchglobalSDKfactory — cost: newtrustedlauncherfile andcouplingtoSDKnew-sessioncontract, explicitnativeSDKregressionneeded.
Ruling: A observeslivePPID andBusesphase19lifeline soSDKdirectkillAstillcleansC; singlePPIDmonitoraloneinsufficient — cost: twopermanent-lightweightPythonhelpersperlocalPOSIXconnection, oneAthread, measuredoverheadrequired.
Ruling: comparegetppidonlyonPOSIX, neverkillrecordedparentPID; WindowsSDKJob/HTTPremainoriginalpaths — cost: Windowsnative/crashassignmentedgeisnotprovenbythisfix andbroadergoalcontinues.
Task1 nativeRED3cases5.399s /tmp/ignovate-mcp-crash-first-red.log: realconnectedtoolspawnafterthenhostexit81/SIGKILLandSDKdirectkillallleavefiniteleafwrites. Implemented2productionfiles mcp/client andcore/_mcp_stdio_owner.py; AstdIObyteinheritsdirect, Aparentwatchthreadbeforetrustedrunpycoreguardload/Bcreation. TrustedBownsactualC ininnernewsid. First3SDKmodulesgreenpending /tmp/ignovate-mcp-crash-first-green.log.
Firstnative3corecrashcasesGREEN butSDKmodules33tests28.106s1error: test_sdk_internal_cancellation_seals_admission_before_native_teardown timesout3s becauseA/Bkeepduplicatefd0 whileactualCclosedstdin; originalSDKonlyCneededEPIPE. GenuinecompatRED /tmp/ignovate-mcp-crash-first-green.log, notfixturetimingrelaxed. Addopt-inrelease_stdio totrustedcoreguard andchildwire; BafterCspawnreleasesunused0/1/2(exceptprivatecontrolFDids), Aafterguardconstructoralso releases0/1/2. OrdinarycoreguarddefaultFalse. ProductionFOURfilesnow, allphase19testsrequired; newgreen /tmp/ignovate-mcp-crash-stdio-green.log pending.
Ruling: opt-inmonitorsreleaseallunusedstandardFDcopiesforMCP, ratherthanrelaxbrokenpipetimeout — cost: sharedcoreguardprotocolchanges(twoextraexistingfiles), noemergencyloggingtoMCPstdioafterinitialization; ordinaryguarddefaultpreservedandregressed.
stdio-release fixesoriginalSDKclosedpipe, related57pass36.112s/1WindowsSkip /tmp/ignovate-mcp-crash-stdio-green.log. Additionallegacywiretest nativeRED /tmp/ignovate-mcp-crash-legacy-wire-red.log exposesrequirednewboolfieldwoulddropoldargv0. Preserveordinaryoldwireexactlayout; encodeMCPoptin stdio-v1: onlyinnumericignored-policyfield, newhelperacceptsoldnumericmask; oldhelperfailclosesonnewtagratherthanrunningunmanaged. Basicgreenpending /tmp/ignovate-mcp-crash-basic-native-green.log.
Ruling: preserveexistingordinaryguardwire; MCPopt-inreservednon-numericpolicytag — cost: newMCPopt-instillrequirespairedcurrenthelper, mixedolderhelperrejectsstartup ratherthanlosingFDpolicy.

Task1/2 native GREEN: 72 tests 43.934s/1 Windows skip, /tmp/ignovate-mcp-crash-expanded-native-green.log. Added blocked trusted-guard-load host death and parent-watch failure, original stdin/stdout EOF, positive/signal exit, args/env/cwd, real active cancel, two connections, repeated FD baseline; three original crash cases now prevented. Expanded fixture echo has unstructured SDK dict content, corrected assertion to json.loads(content), no production result rewrite.
Native cost /tmp/ignovate-mcp-crash-native-overhead.log: six warm connects before .1592–.1679s, after .1797–.1888s; two helpers sum RSS 37,392–37,616 KiB/connection; 20 real RPC before .0110–.0119s, after .0111–.0115s. Small single-host sample, not performance guarantee. Earlier ledger lightweight wording is superseded by measured ~36.6 MiB RSS, not negligible for many connections.
Task2 entrypoints GREEN107/107 11.367s /tmp/ignovate-mcp-crash-entrypoints-green.log incl MCP config/tools/schema/commands/batchapproval, factory/GUI/runtime/runner. Task3 one fresh Astra mcp_crash_ownership_review dispatched exact attested-wheel increment; no implementer/no rereview. Full source Python and JS launched; docs disclose helper memory and unresolved platform/fault boundaries.
Goal continuation: previous turn progress confirmed authoritative fullPython2087/47skip311.681s, installed632/4skip163.787s all154 files/40 paths, SHA03e6acfe8fc99d900cc756e1bf235b4f9421691b45fe34aea85447e4c91e0f2e. Reviewer partial native finding Important: B independently killed, A exits127 but SDK C pipes keepalive -> status connected/new echo succeeds. Need admission/error propagation even though Bkill cleanup itself cannot be guaranteed. One reviewer handle restored pending_init; no duplicate review spawned. Author adds actual finite service self-injection RED; production unchanged pending complete findings.
Final Important native RED reproduced1.619s /tmp/ignovate-mcp-crash-owner-loss-red.log: finite actualC kills own live B group-leader; A unknown but SDK still connected, new admissions possible. Author one fix pass adds authenticated loopback-only host/A liveness sideband (new mcp/stdio_owner.py), no stdio relay/parser changes or global SDK hooks. Host seals before cancelling same owner once; clean receipt only after verified guard.close, missing receipt unknown. No disk socket residue across host hardexit.
Ruling: use authenticated ephemeral loopback socket sideband for outer liveness/cleanup status — direct C pipes cannot report monitor death and SDK exposes no process handle/pass_fds; cost: one additional private IPC channel/task per connection and local loopback permission required, startup fails closed if unavailable; no extra service authority or remote listener.
Final reviewer originalincrement completed /tmp/ignovate-mcp-crash-review-final.md: 0C/1I/0M. Same review seat restored across continuation, not secondreview. Important accepted and nativeRED→GREEN, related74/2skip45.318s; expanded24/1skip14.344s before descriptor inheritance addition. One author fixpass continues, no rereview.
Final: Ruling: independent B SIGKILL reclamation remains unproven, but admission/error loss is fixed rather than declined — current process-group owner cannot clean after its own death; cost: survivors may still have side effects and must remain unknown, broader goal needs containment decision.
Final: Ruling: deliberate setsid/setpgid escape is an unresolved product boundary, not universal cleanup — POSIX group membership does not contain escaped processes; cost: detached effects may continue, original goal remains active.
Final: Ruling: native Windows unchanged SDK Job behavior is not accepted from macOS evidence — new real-controller hardexit test is platform skipped here; cost: Windows assignment/crash failures can remain until native execution.
Final: Ruling: native Linux acceptance is not inferred from POSIX source/macOS tests — no Linux runner available; cost: kernel/platform differences remain unverified.
Final: Ruling: disclose measured per-connection resource cost without inventing unsupported capacity guarantee — six warm samples are not a high-load acceptance threshold; cost: connection-count memory pressure still requires capacity testing/control under broader goal.
Final: Ruling: this exact-increment review does not close the entire project goal — shared-core and fullsuite regressions cover this phase only; cost: remaining whole-runtime shutdown, history capacity, actual old workspace link and platform delivery matrix still need evidence.
Final Important expanded GREEN186 tests57.308s/2 platformskips /tmp/ignovate-mcp-crash-accepted-related.log. Newmodule25 cases incl native earlySDKkill beforehandshake, loopbackpermissionfailure beforeuserexec, actualbadtoken rejected, verifiedcleanupreceipt distinction, CnativeFDscan cannotseehealthsocketport; originalglobalSDKcancel/normalclose/FD/reconnect/corewire pass. Finalfull source/installed started onlyafterthis product implementation finished.
Final measured cost supersedes original samples /tmp/ignovate-mcp-crash-accepted-native-overhead.log: sixwarmconnects baseline .1557–.1805s, after .1838–.1875s; 2helpersRSS37,920–38,128 KiB (~37MiB), 20RPC .0110–.0117s vs .0112–.0117s. Still single-host sample, not capacity acceptance. Added host socket/watch task and A socket, no disk IPC artifact.
Final installed GREEN639 tests170.684s/5 skips /tmp/ignovate-mcp-crash-accepted-installed-native.log: actual pip --target, 41 module paths incl bothhelpers/healthmodule, all155 packagedPython byte-identical. CLIhelp/JSONdoctor/dep smoke GREEN, JS17 GREEN; SHAe5ee993852688f3d70a37ee576e01d4520796eead3925fe6dde68ec948616fd1. Sourcefull remainslive, no product mutation.
Next broader native finding (separate phase, no product changes): /tmp/ignovate-factory-global-shutdown-probe.log and repeated-probe.log use actual RuntimeResourceOwner/AsyncSqliteSaver/aiosqlite with gated native sqlite3.connect. asyncio.run globally cancels opening Task; worker returns after loop closed, raises Event loop is closed, owner removes unpublished record and retained native handle still open. Probe retains native connection solely for lifecycle inspection (e.g. callers may retain failure tracebacks); worker_threads empty is not proof connection closed. Finite process exits reclaim OS handles, exact original private tempdir cleaned and repeat parent-owned TemporaryDirectory cleanup verified. No API.
Final sourcefull GREEN2094 tests320.812s/48 platformskips /tmp/ignovate-mcp-crash-accepted-full-python.log, observed exit0. JS17/diff GREEN. Final: fixed Important ownership-loss admission — test_inner_owner_death_seals_admission_and_reports_unknown_cleanup RED→GREEN, native186/2skip plus full2094/48skip and installed639/5skip. No minors, no rereview. Preserve branch/IDE/CSS and all earlier dirtychanges, noAPI/commit/push per persistent authorization; finishing-branch keep-as-is already selected, no repeated menu.
Next native differential /tmp/ignovate-factory-global-shutdown-control-probe.log: caller-onlycancel native_connection_open=false; globalasyncio.run shutdown native_connection_open=true and Eventloopclosed worker traceback; both unpublished0/publishedfalse/workerthreadsnone. Retained native handle is deliberate inspection, not evidence all uncontrolled real-world calls leak permanently; proves cleanup can finish/remove ownership before a delayed native opener finishes. Parent-owned tempdirs auto-cleaned, no new product mutations. Original goal remainsactive/unproven.
