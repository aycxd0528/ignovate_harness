# MCP cleanup diagnostic preservation

## 有界设计

原始goal继续active。phase23 GUI两文件已通过专项与实际安装704/5skip两组，但最终311全量暴露继承MCP状态竞态，不能将其验收为全绿；313全量2149/49skip通过。此项是新MCP异常传播审查，不是对phase23作者补修的第二次review。基准为当前GUI修复冻结wheel SHA256 d6d3949b582e72efb2f0d10a494c42b2bf0480351673b56ec0e491b0cff75dd2，只审MCP后续差异，不用HEAD/未接受旧package-wideGREEN冒充基准。

失败：MCPDirectProcessDeathTests.test_inner_owner_death_seals_admission_and_reports_unknown_cleanup。SDK teardown ExceptionGroup覆盖健康通道已发布的“清理无法核实”，只剩配置建议。实际接纳已关闭，但用户失去仍需核对原服务副作用的重要状态。

原生顺序追踪 /tmp/ignovate-mcp-error-order-discovery.log：actual MCP SDK/stdio owner/真实有限服务执行destroy_owner，未注入对照保留unknown；仅在原ClientSession.__aexit__真实收尾后注入OSError，两次均先记录unknown再被generic ExceptionGroup覆盖。不是凭并发负载推测原因，不以重跑放行。补丁限_serve外层generic fallback，不覆盖已有具体错误，不改变SDK协议/取消/准入或新连接重置语义。

## 验证

- [x] 真实SDK teardown故障native新用例先RED；保持unknown、工具撤下、请求未准入、原SDK收尾已执行。
- [x] 最小实现GREEN、generic首次连接错误仍有诊断、相关MCP回归。
- [x] 一次fresh独立review精确此项delta，作者一轮C/I无复审；全部Declined裁决。
- [x] 合并当前GUI修补后的最终311/313全量、实际wheel安装字节/来源、CLI/JS门通过，归档所有失败与修复。

## 裁决

Ruling: 将全量暴露的继承MCP异常传播独立立项，不把GUI专项通过当作包级验收 — 真实unknown状态丢失会误导长任务故障恢复；cost: 新的单次MCP增量review与完整最终门，phase23保持待联合验收。
Ruling: 仅在通用异常处理没有已有具体错误时填充fallback — 健康通道的已知unknown优先于SDK退出的泛化异常名；cost: 不在单一error字段聚合全部次级异常，后续明确重连才创建新connection状态。

新actualSDK回归RED .447s同unknown被ExceptionGroup泛化覆盖，/tmp/ignovate-mcp-diagnostic-red.log。最小一行or保留既有diagnostic；相关56/1skip34.204s GREEN，/tmp/ignovate-mcp-diagnostic-targeted.log；原顺序探针无注入/两次teardown注入均GREEN，/tmp/ignovate-mcp-error-order-green.log。原SDK__aexit__在同owner任务原位await，不换协议/Task/超时，故障仅在其退出后抛出。C故障fixture3秒自退出，不追杀未知历史PID。新用例明确请求重连后status connected/error为空，既有unknown不会污染新generation。
单次freshAstra已按精确production wheel增量启动review；源码/测试冻结，父运行完整311/313、实际安装/CLI/JS，不进行第二次GUIreview或MCP复审。

单次freshAstra完成：0C/0I/0M。独立核对frozen wheel SHA及一行production差异；新native用例.599s、首次启动fallback、已准入调用具体失败+次级SDK退出异常探针通过。没有作者C/I补修或复审；五项Declined裁决如下。
Ruling: 此独立review不重复GUI及其他dirty旧变更 — 两项有各自一次review，GUI作者Important补修已有native RED/GREEN；cost: 新MCP reviewer不单独认证旧行为，联合最终门仍由作者执行。
Ruling: package-wide验收及发行内容依作者完整311/313/安装/CLI/JS实际命令 — 一行diff的独立结论不是包级GREEN；cost: 只有最终输出确认后才联合接受，早先失败和旧hash不作为最终证据。
Ruling: 不把本机POSIX native回归扩为Windows/其他OS进程监护保证 — 一行fallback无新平台分支，其他监护实机未运行；cost: 新用例在非POSIX跳过，其他平台仍需原生证据。
Ruling: 只保证已发布具体diagnostic不被次级fallback覆盖，不改通知晚于connection.closing的既有producer策略 — 此次有原生顺序证据的是先发布再覆盖；cost: 更晚通知仍可能未发布具体unknown，不能据此宣称所有通知顺序均已解决，该边界仍在整体review范围，接纳已关闭不等于原生清理核实。
Ruling: 测试pre-addition副本是作者反向移除新增method的重建，不冒充wheel-attested历史 — 旧文件来自早期untracked阶段且wheel不含tests；cost: 只独立审实际新增方法和相关现行行为，不能由副本单独证明旧测试历史字节，当前product安装字节与源码仍需实际核对。

## 独立review原文
# Fresh bounded MCP diagnostic review

## Scope and provenance

This is one initial, read-only review of `/tmp/ignovate-mcp-diagnostic-review.diff`, under `.agents/skills/requesting-code-review/code-reviewer.md` and the requirements/rulings in `docs/reviews/2026-10-24-mcp-cleanup-diagnostic.md`. No subagents or real external APIs were used. No checkout, index, HEAD, or branch edits were made; supplemental artifacts are under `/tmp/ignovate-mcp-diagnostic-review-owned/`.

I independently hashed the frozen phase23 wheel: `d6d3949b582e72efb2f0d10a494c42b2bf0480351673b56ec0e491b0cff75dd2`. Comparing its `nailong/mcp/client.py` with the checkout confirms exactly the supplied one-line production change at line 274. The reviewed checkout client SHA256 is `a3e05590e9dfeba1b67e6e2cc102fb534e7d6c94bfddd62c3ad1ea5e08983642`. HEAD was not used as the baseline.

The regression method at `tests/test_mcp_crash_ownership.py:120` was reviewed directly with its setup, cleanup, neighboring cases, native service fixture, and `StdioOwnership` producer. `/tmp/ignovate-mcp-diagnostic-test-before.py` is a reconstructed pre-addition file, not independently wheel-attested test history. The frozen package has the known inherited MCP failure; this review does not imply that baseline package acceptance passed.

## Strengths

- `nailong/mcp/client.py:274` makes the outer generic error a fallback, matching the existing connect-side fallback at line 149. The current producers of nonempty connection errors describe terminal call/ownership failures. Preserving those strings retains actionable cleanup or side-effect uncertainty when SDK teardown subsequently raises.
- The change introduces no await, cancellation, protocol, queue, admission, or ownership operation. Existing closure seals and final queue resolution remain in place. Explicit connect joins the previous owner and creates a new `_Connection` at line 130 with an empty error field, so the preservation is confined to the failed generation.
- `tests/test_mcp_crash_ownership.py:120` exercises the actual SDK/stdio service and guardian loss. The injected exit first awaits the original SDK `__aexit__` in the same owner task, then raises. Its `settled` record means that native exit await terminated, whether by return or exception; it does not claim successful verified process cleanup. The test joins the owner, checks the retained uncertainty and withdrawn tools, rejects a later call as unadmitted, and explicitly reconnects without inheriting the error.

## Evidence

I read the supplied discovery trace, native regression RED, post-fix ordering trace, and targeted result logs. These support the claimed original sequence (specific unknown-cleanup diagnostic followed by generic overwrite), the regression failure before the change, and the author’s 56-test result with one skip. They are author evidence, not independent full-suite validation by this reviewer.

Independent bounded verification used CPython 3.11.15, disabled bytecode, owned temporary paths, a 30-second outer process limit, and no external API:

- The added native regression passed once in 0.599 seconds.
- A first startup failure from an absent credential reference produced the exact generic ValueError diagnostic, error status, and no tools.
- A real native echo response followed by an injected transport exception and a later exception after native SDK exit retained the call-specific ConnectionError/side-effect warning; the request was admitted, tools were withdrawn, and the next call was unadmitted.

Probe and output: `/tmp/ignovate-mcp-diagnostic-review-owned/probe.py` and `/tmp/ignovate-mcp-diagnostic-review-owned/probe.log`.

## Issues

### Critical

None.

### Important

None.

### Minor

None.

## Recommendations

No source or test change is requested for this delta. Keep the package-level verdict separate until the parent’s planned complete source/installed Python, CLI, and JavaScript gates finish. The secondary SDK exception is intentionally not aggregated into the single error field; this matches the explicit diagnostic-precedence ruling and is not a finding.

## Declined to judge

- D1 — Earlier GUI fixes and unrelated dirty checkout behavior: outside this new one-line MCP production delta; their previous reviews are not repeated or endorsed here.
- D2 — Package-wide acceptance, distribution contents beyond the compared client, and complete Python 3.11/3.13, installed-wheel, CLI, and JavaScript behavior: these are the parent’s independent pending gates, not established by this focused review.
- D3 — Native Windows and other-platform process supervision/cleanup behavior: the reviewed native regression is POSIX-only, and those unchanged supervisor implementations were not revalidated on other operating systems; the fallback itself adds no platform branch.
- D4 — Whether an ownership notification arriving only after `connection.closing` is already true should publish a previously absent cleanup diagnostic (`nailong/mcp/stdio_owner.py:54`): that unchanged producer ordering is outside retention of an already-published diagnostic. This review makes no claim that the patch resolves every possible ownership-notification ordering.
- D5 — Historical provenance of the pre-existing untracked test file: the supplied pre-addition copy was reconstructed by removing the new method, so it cannot independently prove the rest of that file’s historical bytes. The actual added method and its relevant surrounding behavior were reviewed directly.

## Assessment

**Ready to merge? Yes, for this bounded MCP diagnostic delta.**

The change preserves the meaningful terminal diagnostic through secondary teardown failures while retaining the empty-error fallback and fresh-generation reset. No Critical, Important, or Minor issue was found; package-level acceptance remains subject to the parent’s complete gates and explicit rulings on the declined items above.


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
