# GUI 空会话检查的只读 checkpoint 连接生命周期

## 有界设计与要求

原始goal继续active。上一轮phase22是有效进展，基准为实际验收wheel SHA256 `5e71fd04fdf597b5137b9ef246cb0380c34f712692e9156e7354466ec5213ccc`，不是HEAD；保留全部旧变更、IDE/CSS及分支，无提交推送或真实模型API。继续已授权native调查、修复和验证。

Python3.13全项目回归出现unclosed database ResourceWarning。原生GUIController/empty_branch_ids连续12次读取现有只读checkpoint数据库，保留原生连接以核验，函数返回后12个仍能SELECT，FD10→22；/tmp/ignovate-empty-conversation-native-discovery.log。最初手造controller缺service/runs schema，改为真实GUIController才取得有效证据；不把夹具错误算产品失败。

这是现有入口的有界修复：sqlite3.Connection的with只结算事务，不关闭连接；用contextlib.closing在成功/查询失败/取消异常离开作用域时关闭该只读连接。只读URI、checkpoint/task/运行/分叉/草稿证据保护与无法核实返回[]保持；连接不存在不新建库。GUI history/controls已有相同原语，不建新资源registry、线程或全局SDK patch。对应测试bootstrap也显式关闭实际数据库。

## 验证与复核

- [x] 原生重复读取、真实查询失败、BaseException、无库零新建和已有checkpoint保护先RED→GREEN；实际连接/FD及不删除证据核验。
- [x] 一次fresh最终review精确phase22 wheel生产增量；作者一轮C/I修复，无二次review，Minor记录，所有Declined逐项裁决。
- [x] 全量Python3.11及实际Python3.13、JS/diff；最终wheel实际安装字节/来源/CLI和受影响GUI回归。

## 同轮独立探索

/tmp/ignovate-native-goal-wire-discovery.py 是丢弃式探针，不是保留实现。已有9轮场景改为真实默认HarnessChatDeepSeek/OpenAI/HTTPX2连接本机有限SSE服务，分片工具参数与reasoning/usage经实际SDK解析；19次HTTP请求/92个工具结果消息、压缩/暂停/Factory重开/恢复/修复/CSV验证/外部改动降级均通过，2090tokens、费用0.0007980000000000003核对，FD4→4无新存活线程。/tmp/ignovate-native-goal-wire-discovery.log。不以本机协议模拟宣称真实provider稳定或全目标完成。

## 执行记录与裁决

Ruling: 修复只读检查的短作用域连接，复用closing而不扩建通用生命周期管理 — 原生证据指向缺少close且同类GUI读取已有实现；cost: 同步close仍在原调用线程执行，native close异常沿现有无法核实路径处理，不新增失败资源registry。
Ruling: 保留并核验实际连接而不依赖GC警告消失 — sqlite3事务context退出不等于句柄关闭；cost: 测试会刻意延长连接对象寿命以检查确定性关闭，FD观测限本机支持平台。

原生RED14项3fail .279s：重复读取/查询失败/CancelledError均留下实际可读连接，无库零新建对照通过；/tmp/ignovate-empty-checkpoint-red.log。两行production修复后独立原探针12个真实连接均closed、FD10→10；/tmp/ignovate-empty-conversation-native-green.log。首次相关54项有1个既有checkpoint保护失败：测试写入bootstrap改用closing后漏保留事务提交；修正为closing+原db事务context，生产只读scope无需提交语义。未放宽分支保护断言，后续完整回归必须通过。
Ruling: 写入测试bootstrap同时保留事务context与显式连接close — closing只管理句柄而不提交INSERT，不能借资源修复改变证据；cost: 测试使用双context，生产读取仍只读、不引入提交或DDL。

相关54/54 GREEN1.064s，/tmp/ignovate-empty-checkpoint-final-targeted.log。新增4项（3项native生命周期RED后GREEN，1项无库零创建对照）；既有checkpoint/任务/草稿/分叉/项目锁保护保留。两文件精确冻结增量 /tmp/ignovate-empty-checkpoint-review.diff 已交freshAstra最终review，不派implementer、不做复审。完整311/313、两组实际安装和release gates进行中。
先前未修改phase22代码的实际Python3.13全量2142项/49skip361.173s通过，但有unclosed database警告（本轮原生GUI缺口及测试writer bootstrap已定位）；/tmp/ignovate-full313-source-regression.log，不把中途修改后的代码当作该快照结果。新的全量从冻结phase23重新开始。

同一丢弃式native-goal-wire探针在实际安装的精确锁Python3.13环境也通过：9轮/19请求/92累计工具结果消息，2090tokens/费用0.0007980000000000003，压缩与重启恢复后verified、外部更改后unverified，FD4→4无新增线程；/tmp/ignovate-native-goal-wire-313.log。探针从工作区外运行，仅tests放在PYTHONPATH，实际product来自CP313已安装wheel。不把成功路径模拟当作云端provider长期故障保证。

最终一次freshAstra已完成：0C/0I/0M、独立14项.328s及4项真实prune探针通过，报告 /tmp/ignovate-empty-checkpoint-review-final.md，七项Declined需作者逐一裁决。不是第二次review，后续不派复审。
作者对Declined第5项原生核验：真实GUIController.prune_empty遇dangling/cyclic checkpoint link都实际删除branch（link仍保留），/tmp/ignovate-empty-checkpoint-link-red.log。这不是可以仅凭本轮scope略过的用户效果，按潜在证据删除重评为Important并纳入唯一C/I pass。新增3个原生symlink用例（两坏链接+可读已提交checkpoint链接对照）先RED17项2fail，再用lstat仅将真正FileNotFound视为缺库，坏链接/权限等进入既有只读打开及无法核实保留分支路径；不改有效链接读取契约，不创建缺失库。/tmp/ignovate-empty-checkpoint-link-test-red.log与原探针GREEN保留作为证据。此前2146/701及SHA954d...是修补前快照，全部最终gate重跑且新wheel重新安装；不能用旧GREEN支持补修后验收。
Ruling: 将review未认证的损坏checkpoint链接重评为Important并原生修复 — dangling/cycle不是“已知无数据库”，实际prune会丢会话映射；cost: 每次检查增加lstat，无法核实的路径会保留本可为空的会话，仍不承诺对不合作外部writer的原子分类。

作者唯一Important修复后相关57/57 GREEN1.107s，/tmp/ignovate-empty-checkpoint-ci-green.log；坏链接原探针现在removed=[]且链接保持，合法链接已提交checkpoint仍保护会话。未改源/目标数据库正文，不做第二次review。

## 全部 review Declined 裁决

Ruling: package-wide311/313、安装、CLI、JS由作者实际门验证，独立review仅认证有界增量 — 避免重复整项目评审且不把其结论扩为release证据；cost: 包级结论必须依赖最终作者命令及输出，任何未运行项仍未证实。
Ruling: 不把本读取器的确定关闭扩展为所有SQLite/SDK/controller/process均无泄漏 — 此增量只更换短作用域并补未知链接保护；cost: 其他所有权路径和更广故障交错仍需独立证据，整体goal保持active。
Ruling: native SSE长目标探针仅作实际SDK本机协议/工具/恢复证据，不认证真实provider稳定 — 没有调用真实模型API；cost: 云端行为、延迟和长期故障仍未认证，探针保持丢弃式而不冒充CI覆盖。
Ruling: 保持普通closing语义，不增加本reader的native-close失败重试registry或替换close的双异常协议 — 未建立受支持native close自身失败的复现，现有同类reader也依该原语；cost: close本身失败不承诺释放，替换close与主取消同时失败可遮蔽主异常，不据此报告确定关闭。
Ruling: filesystem边界分开处理：已复现dangling/cycle数据风险作为Important补修；不合作writer/路径在检查后被替换的原子分类仍未认证 — project lease及原mode=ro保留但不是外部写者事务；cost: 只能声明固定坏链接安全拒绝，不能声明抵御全部外力竞态，需后续独立审计。
Ruling: 不在reader生命周期修复中重写branch/layout两库删除持久性协议 — 本次不删除内容证据且原序列保持；cost: 硬退出/断电跨库原子性没有新增保证，若另立要求需要故障注入和协议设计。
Ruling: Native证据限定本机macOS311/313，不把portable断言当作Windows/无devfd原生通过 — 实际安装和源码门会保留各自skip；cost: 其他OS的句柄/链接权限与平台路径仍需实机验收，不宣称全平台FD基线。

## 独立 review 原文（作者补修前，非复审）
# Final read-only review: GUI empty-checkpoint connection lifecycle

## Reviewed scope and evidence

Reviewed the single frozen two-file diff `/tmp/ignovate-empty-checkpoint-review.diff` (SHA256 `d3b6b3e25fe3bee3342341216413c19d91c1a7209f2f1f73d0fa307ac76d3ca4`) against `docs/reviews/2026-10-24-gui-empty-checkpoint-lifecycle.md` and its rulings. The production baseline is the phase22 wheel `/tmp/ignovate-model-http-accepted-wheel/ignovate_harness-1.2.1-py3-none-any.whl`; I independently verified its SHA256 as `5e71fd04fdf597b5137b9ef246cb0380c34f712692e9156e7354466ec5213ccc`. HEAD was used only for the explicitly attested pre-phase test file. Regenerating the combined diff from those sources exactly matched the frozen diff at the end of this review.

This was one initial fresh review. No checkout, index, HEAD, or branch edits; no subagents or real API calls. Supplemental files were confined to `/tmp`, and Python ran with bytecode writes disabled.

### Strengths

- `nailong/gui/empty_conversations.py:23`: `closing` gives the native readonly connection a deterministic lifetime. Both queries consume their results inside the scope; no live cursor escapes. Success, query exceptions, and `BaseException` unwinding all invoke `close()`. The removed transaction context was unnecessary for these readonly SELECTs.
- `nailong/gui/empty_conversations.py:22` and `nailong/gui/empty_conversations.py:26`: the existence guard, `mode=ro`, and conservative error handling are preserved. Unknown task/checkpoint state still produces no prune candidates; propagated cancellation exits before deletion. No schema, persistence format, public signature, or new dependency was introduced.
- `nailong/gui/empty_conversations.py:11`, `nailong/gui/empty_conversations.py:31`, and `nailong/gui/empty_conversations.py:55`: task associations, checkpoint rows, runs/nodes/map requests, forks, drafts, log evidence, and the project execution lock remain intact. The change does not widen the set of disposable branches.
- `tests/test_gui_empty_conversations.py:71`: the nested writer transaction exits before `closing` closes its connection, preserving commit semantics and the existing checkpoint-only protection assertion. This directly addresses the fixture regression recorded in the brief.
- `tests/test_gui_empty_conversations.py:122`, `tests/test_gui_empty_conversations.py:141`, `tests/test_gui_empty_conversations.py:160`, and `tests/test_gui_empty_conversations.py:181`: the new cases retain actual native handles, assert they are unusable after return/unwinding, verify cancellation identity and branch retention, and check that an absent database is not created. The handle assertions remain meaningful without `/dev/fd`.

### Issues

#### Critical (Must Fix)

None found in the reviewed change.

#### Important (Should Fix)

None found in the reviewed change.

#### Minor (Nice to Have)

None requiring a change. The redundant local `closing` import and compact statements in the added tests are cosmetic and do not affect their result or ownership checks.

### Validation

- Independently ran `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest tests.test_gui_empty_conversations -v`: **14 passed in 0.328s**. This includes checkpoint-only evidence, task-only evidence, unreadable task authority, drafts, history/forks/attempted requests, malformed logs, reuse, and the busy-project retry behavior.
- Ran four supplemental offline scenarios through the actual `GUIController.command('prune_empty', {})`: native SQLite missing-column failure, a corrupt nonempty database file, committed checkpoint-only evidence, and cancellation during the second query. Every scenario preserved the branch and database bytes and closed the captured native connection; cancellation propagated as the same object. Probe: `/tmp/ignovate-empty-checkpoint-review-probe.py`; output: `/tmp/ignovate-empty-checkpoint-review-probe.log`.
- Inspected the supplied 54-test passing log and native 12-read GREEN log. The latter records 12 closed handles and FD 10 → 10. These are author evidence, distinguished from the independently executed checks above.

### Recommendations

No additional code change is needed for this bounded fix. Retain the native-handle assertions and the writer transaction context. Complete the parent's already scheduled release gates before making a package-wide release claim.

### Declined to judge

Each item below is explicit for the executor's ruling; none is a demonstrated blocker in this diff.

- **Package-wide Python 3.11/3.13 regression, installed-wheel parity, CLI and JavaScript behavior:** the parent is independently running these gates; this review does not duplicate or certify their results.
- **All other SQLite, SDK, controller, and process resources being leak-free:** other ownership sites are outside this two-file delta; closing this reader establishes no global lifecycle guarantee.
- **Real-provider stability and the separate long-goal loopback SSE exploration:** unrelated to the changed reader and explicitly excluded by the parent; no real provider was contacted.
- **Recovery when native `close()` itself fails, or preserving a primary cancellation when a substituted `close()` also raises:** no supported native reproduction was established; the explicit design ruling keeps ordinary `closing` and excludes a retry/resource registry. This review establishes normal native close behavior, not guaranteed release after a failing close.
- **Pre-existing filesystem topology/race policy, including broken or cyclic checkpoint symlinks and a non-cooperating external writer replacing or inserting evidence during classification/deletion:** the existing path checks and project lock are unchanged. These adversarial filesystem/concurrency cases were not certified by the finite lifecycle probes; ordinary missing, corrupt, and unqueryable database states were assessed above.
- **Crash/power-loss atomicity across the branch and GUI-layout databases:** the existing two-database deletion sequence is unchanged; no durability redesign or fault-injection campaign is part of this reader-lifetime fix.
- **Native behavior on every supported OS, including Windows and platforms without `/dev/fd`:** independent execution here used local macOS Python 3.11. The portable native-handle assertion is present, but this review makes no all-platform FD-count claim.

### Assessment

**Ready to merge? Yes — for the bounded reviewed change.**

**Reasoning:** The implementation closes the readonly native connection deterministically without changing checkpoint classification or writer-fixture commits. The focused suite and supplemental real-database pruning probes passed, with no Critical or Important finding; package-wide acceptance remains subject to the parent's separate gates.

补修后的首轮最终门：Python3.13全量2149/49skip372.678s GREEN；Python3.11全量2149/48skip367.853s FAILED1，精确是既有MCPDirectProcessDeathTests.test_inner_owner_death_seals_admission_and_reports_unknown_cleanup，状态error只剩通用连接失败而不含清理无法核实。/tmp/ignovate-empty-checkpoint-final-full311.log。GUI相关与两组实际安装704均通过；不把该全量失败隐藏或先标整个phase验收完成。作者另立MCP异常传播原生调查，当前GUI产品两文件保持，不以重跑代替根因定位；下一步结果须衔接最终全量证据。


## 最终联合验收


## 第二十三、二十四轮联合验收：GUI checkpoint 读取与 MCP 诊断

第23轮由实际Python3.13全量的数据库ResourceWarning追到GUI空会话检查：原with sqlite3.connect只结束事务，不关闭连接。真实12次读取保留原生对象核验，FD10→22且12连接仍可查询；closing后12连接均关闭、FD10→10，查询失败与BaseException均收尾，writer测试bootstrap保留事务提交。一次freshAstra0C/0I/0M，并独立14项及4项原生prune探针。作者核验review列出的未认证filesystem边界时发现dangling/cyclic checkpoint link实际导致branch被删除，重评Important后唯一RED/GREEN补修：lstat仅把真正FileNotFound视为缺库；坏链接状态无法核实时不清会话，合法链接已提交checkpoint仍保护分支，不写原链接/数据库。相关57项通过1.107s，无二次review。

第23轮完整311运行2149/48skip有1项失败，未被忽略：既有MCP内层监护者死亡测试的unknown-cleanup diagnostic偶发被SDK退出ExceptionGroup覆盖。313全量和两组安装704已通过，仍不据此标包级验收。第24轮原生追踪记录先发布“清理无法核实”再被generic覆盖；在原SDK退出后注入故障两次稳定复现。新native回归先RED，再一行fallback保留已知具体错误；接纳仍关闭，明确重连用新generation清旧错误，协议/Task/timeout不改。相关56/1skip34.204s通过。第24轮另一次freshAstra独立审这个新MCP delta，0C/0I/0M、native新用例及首次fallback/准入调用具体错误+退出故障探针通过；不是GUI作者补修复审。

### 最终联合门

| 实际命令范围 | 总项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2150 | 48 | 368.512s |
| Python3.13源码全量 | 2150 | 49 | 372.220s |
| 工作区外实际安装3.11 | 705 | 5 | 217.441s |
| 工作区外实际安装3.13 | 705 | 5 | 223.572s |

四个最终日志均无ResourceWarning；两组安装45关键模块来源、158发行Python字节与当前源码一致。JS17/diff与实际安装CLIhelp/JSONdoctor/依赖smoke通过。wheel SHA256 `c24d068c01ba9023aff237678e916c807e563247b7fefc8ea2a125ff8d2ed86a`；最终日志前缀 `/tmp/ignovate-mcp-diagnostic-final-`（full311/full313/installed311/installed313.log）。phase23中间SHA954d.../d6d...与失败全量仅保留历史证据，不能替代联合最终门。

最终源码3.11与实际安装3.13另各跑真实默认HarnessChatDeepSeek/OpenAI/HTTPX2本机SSE长目标探针：9轮/19请求、分片工具调用与reasoning/usage，7次原生构建失败后压缩、暂停、Factory/SQLite重开、恢复、读后写修复、验证并完成Task/Goal；2090tokens、费用0.0007980000000000003及9个唯一settled round连续，外部改变CSV后降为unverified，FD4→4无新增存活线程。探针 `/tmp/ignovate-native-goal-wire-discovery.py` 为丢弃式探索，最终日志 `/tmp/ignovate-native-goal-wire-final311.log` 和 `final313.log`，不冒充保留CI用例或真实云端provider认证。

### 全部裁决与代价

以下18项包含两个review全部7+5条Declined的裁决（已复现的坏链接效果被实际修复，不仅凭scope排除）：

1. Ruling: 修复只读检查的短作用域连接，复用closing而不扩建通用生命周期管理 — 原生证据指向缺少close且同类GUI读取已有实现；cost: 同步close仍在原调用线程执行，native close异常沿现有无法核实路径处理，不新增失败资源registry。
2. Ruling: 保留并核验实际连接而不依赖GC警告消失 — sqlite3事务context退出不等于句柄关闭；cost: 测试会刻意延长连接对象寿命以检查确定性关闭，FD观测限本机支持平台。
3. Ruling: 写入测试bootstrap同时保留事务context与显式连接close — closing只管理句柄而不提交INSERT，不能借资源修复改变证据；cost: 测试使用双context，生产读取仍只读、不引入提交或DDL。
4. Ruling: 将review未认证的损坏checkpoint链接重评为Important并原生修复 — dangling/cycle不是“已知无数据库”，实际prune会丢会话映射；cost: 每次检查增加lstat，无法核实的路径会保留本可为空的会话，仍不承诺对不合作外部writer的原子分类。
5. Ruling: package-wide311/313、安装、CLI、JS由作者实际门验证，独立review仅认证有界增量 — 避免重复整项目评审且不把其结论扩为release证据；cost: 包级结论必须依赖最终作者命令及输出，任何未运行项仍未证实。
6. Ruling: 不把本读取器的确定关闭扩展为所有SQLite/SDK/controller/process均无泄漏 — 此增量只更换短作用域并补未知链接保护；cost: 其他所有权路径和更广故障交错仍需独立证据，整体goal保持active。
7. Ruling: native SSE长目标探针仅作实际SDK本机协议/工具/恢复证据，不认证真实provider稳定 — 没有调用真实模型API；cost: 云端行为、延迟和长期故障仍未认证，探针保持丢弃式而不冒充CI覆盖。
8. Ruling: 保持普通closing语义，不增加本reader的native-close失败重试registry或替换close的双异常协议 — 未建立受支持native close自身失败的复现，现有同类reader也依该原语；cost: close本身失败不承诺释放，替换close与主取消同时失败可遮蔽主异常，不据此报告确定关闭。
9. Ruling: filesystem边界分开处理：已复现dangling/cycle数据风险作为Important补修；不合作writer/路径在检查后被替换的原子分类仍未认证 — project lease及原mode=ro保留但不是外部写者事务；cost: 只能声明固定坏链接安全拒绝，不能声明抵御全部外力竞态，需后续独立审计。
10. Ruling: 不在reader生命周期修复中重写branch/layout两库删除持久性协议 — 本次不删除内容证据且原序列保持；cost: 硬退出/断电跨库原子性没有新增保证，若另立要求需要故障注入和协议设计。
11. Ruling: Native证据限定本机macOS311/313，不把portable断言当作Windows/无devfd原生通过 — 实际安装和源码门会保留各自skip；cost: 其他OS的句柄/链接权限与平台路径仍需实机验收，不宣称全平台FD基线。
12. Ruling: 将全量暴露的继承MCP异常传播独立立项，不把GUI专项通过当作包级验收 — 真实unknown状态丢失会误导长任务故障恢复；cost: 新的单次MCP增量review与完整最终门，phase23保持待联合验收。
13. Ruling: 仅在通用异常处理没有已有具体错误时填充fallback — 健康通道的已知unknown优先于SDK退出的泛化异常名；cost: 不在单一error字段聚合全部次级异常，后续明确重连才创建新connection状态。
14. Ruling: 此独立review不重复GUI及其他dirty旧变更 — 两项有各自一次review，GUI作者Important补修已有native RED/GREEN；cost: 新MCP reviewer不单独认证旧行为，联合最终门仍由作者执行。
15. Ruling: package-wide验收及发行内容依作者完整311/313/安装/CLI/JS实际命令 — 一行diff的独立结论不是包级GREEN；cost: 只有最终输出确认后才联合接受，早先失败和旧hash不作为最终证据。
16. Ruling: 不把本机POSIX native回归扩为Windows/其他OS进程监护保证 — 一行fallback无新平台分支，其他监护实机未运行；cost: 新用例在非POSIX跳过，其他平台仍需原生证据。
17. Ruling: 只保证已发布具体diagnostic不被次级fallback覆盖，不改通知晚于connection.closing的既有producer策略 — 此次有原生顺序证据的是先发布再覆盖；cost: 更晚通知仍可能未发布具体unknown，不能据此宣称所有通知顺序均已解决，该边界仍在整体review范围，接纳已关闭不等于原生清理核实。
18. Ruling: 测试pre-addition副本是作者反向移除新增method的重建，不冒充wheel-attested历史 — 旧文件来自早期untracked阶段且wheel不含tests；cost: 只独立审实际新增方法和相关现行行为，不能由副本单独证明旧测试历史字节，当前product安装字节与源码仍需实际核对。

本两轮无新增deferred minor；phase22 M1公共MockBackend兼容问题仍在待办，不借本两轮C/I pass修改。未调用真实模型API、未提交/推送/合并，保留codex/adaptive-task-runtime、用户IDE/CSS和全部前序工作；无本轮scratch需要删除。原始goal继续active，后续仍需推进已列明的兼容待办、通知排序、更大历史容量、跨平台矩阵及实际工作区历史外部链接等证据；不把这些有界修复定义为完整目标已实现。
