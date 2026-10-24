# 长约束与历史压缩容量：已修复并完成联合验收

原始goal保持active。本问题是在phase27只改测试且生产冻结期间独立原生探索发现，不能被phase27完整绿色回归覆盖，以下保留原RED与调查历史，当前结论见末尾联合验收。

## 原生复现

实际AgentRuntimeFactory → LangGraph → ContextManagerMiddleware → SQLite checkpoint，使用离线RecordingModel记录真实模型请求，绝无真实provider/API。明确配置128000窗口，output reserve4096、safety1024、实际input limit122880。有效task最新要求为`must keep condition ` +9000个ASCII字符+尾部标记；历史含一段`prior analysis `重复60000次的已完成助手分析，以及当前用户继续要求。

在3.11源码与3.13实际安装phase26 wheel均复现：preflight估算230159，L2无法丢弃已完成分析，provider调用0，抛ContextLimitExceeded。相同要求/窗口而历史改短可调用，完整要求仍在实际请求。相同长历史但要求不含约束识别词也能压缩后调用。仅在探针临时patch MAX_SUMMARY_CHARS从6000改20000（不改任何生产文件/预算/要求）时，同样长约束与历史通过，完整要求仍在实际请求：约23862/23912字符。

最终4个独立场景：1个RED、3个control GREEN，311 .328s/313 .369s。脚本 `/tmp/ignovate-history-constraints-discovery.py`，日志 `/tmp/ignovate-test-boundaries-history-constraints311-final.log`、`history-constraints313-final.log`（同前缀）。最初探索导入fixture class导致unittest额外收集10个既有用例，原日志 `/tmp/ignovate-test-boundaries-history-constraints311.log` 的12项/1fail不作为最终精确场景数；已改module导入只运行本4场景。

## 根因与后续验收要求

`compact._summary_state`保留全部持久约束；`_render_summary`在约束块超过固定6000字符时拒绝L2。`ContextManagerMiddleware`虽然知道真实窗口且这些约束足够小，仍不能压缩仅因旧助手分析变大的历史。用户被要求改输入/改模型，但在当前窗口下完整有效要求和压缩历史实际可以容纳。

该问题直接阻碍复杂长任务续作，应作为独立生产修复优先推进。不能通过删除/截断持续约束、放松最终输入预算或伪造压缩成功来解决；应让摘要表达容量与真实最终输入预算协调，仍保留全部约束、完整归档和恢复索引，并保留未知窗口/显式容量限制/真正放不下时拒绝的保护。

- [x] 两版实际runtime复现及三组单变量对照，定位固定摘要上限。
- [x] 把真实失败加入持久回归，并确定有界修复设计与容量边界。
- [x] 最小生产修复、原保护回归、独立review及实际最终包验收。

phase27新增AnyIO测试的异常close跳过服务器清理亦由review复现，属于测试Minor，后续须用嵌套finally确保server关闭；这不替代上述生产缺口优先级。

## Phase28有界设计

真实基准仍为phase26/27验收wheel SHA256 `50704687774c9e903a64f946329be563f52b875e637c0b379f9b7c726f559845`，上一goal turn有实际测试修复、最终门及新生产RED，为有效进展。不以HEAD/全dirty当增量；保留全部前序/用户IDE/CSS，无真实模型API/提交/推送。

仅改三个既有生产文件：compact增加显式summary_max_chars、共享summary_character_limit(input_limit)；已知输入预算按ASCII每估算token至多4字符生成候选容量，名义上限1000000字符（最终review M1发现固定格式可略超该值），与现有完整task projection绝对容量同阶。default/未知窗口仍6000，显式summary容量严格校验。所有持续约束仍完整，归档/索引/安全边界不动，最终完整request_report仍决定是否允许provider。

ContextManagerMiddleware在自动preflight与request-only历史视图使用该共享容量；AgentService手动compact在原owned worker里读取实际窗口并使用同一容量。扩展摘要带nailong_compact_summary_expanded标记；checkpoint或request候选已扩展后窗口变unknown，原Task扩展保护的同一检查点也拒绝该summary，避免配置变化绕过准入。既有未扩展summary兼容，不仅凭长度猜测旧metadata，已知更小窗口仍由完整输入预算拒绝。

新增`tests/test_constraint_compaction_capacity.py`：首8项先RED4fail/2error .517s，含自动、request-only、manual真实失败和新显式参数缺失；当前8项311/313已GREEN，另补非法容量及绝对容量原样保留对照，共10方法。原8的第一次58项运行只有新reopened fixture cleanup LIFO顺序错误（sync close先于aclose）1error，修正仅测试注册顺序后58项8.370s全部GREEN，3138项.749s GREEN；不隐藏中间错误。日志 `/tmp/ignovate-constraint-capacity-red.log`、`targeted.log`、`targeted-final.log`、`green313.log`（同前缀）。完整当前定向组另行运行。

## 初始裁决

Ruling: 允许已知预算下的有界摘要表示扩展，而不增大真实模型输入预算 — 固定6000字符不是provider容量；cost: checkpoint摘要可变大，名义1000000字符另有M1格式误差，完整输入仍可能拒绝，不能把4字符/token换算当精确token保证。
Ruling: default/显式容量与unknown保持严格原契约，扩展结果增加持久标记并在窗口失去核实时拒绝 — 不能仅凭先前known配置继续推断容量；cost: 配置被移除后需恢复可核实窗口才能继续该扩展历史，旧未标记summary不由长度被追溯拒绝。
Ruling: 自动preflight、request-only及手动compact共用同一容量计算 — 不能只让单个自动入口恢复而手动仍卡住；cost: 三个既有生产文件和一个私有检查helper扩展，manual本身不发provider请求，后续真实调用仍重核窗口。
Ruling: 原始持续约束/完整archive与索引保留，不使用ellipsis或删除旧要求换取通过 — 已确认9000字符要求可容纳当前窗口；cost: 归档I/O/真正超资源或完整输入预算时仍拒绝，原始checkpoint/要求保留，不承诺无限容量。
Ruling: 外部Factory缺少模型/窗口元数据时保留原手动compact契约 — 完整311/313各2174项暴露同一既有test_v1_features.CompactionTests原生兼容回归，不能删除或改造旧fixture绕过；cost: 无模型元数据的适配器仍只用6000字符摘要，不获得已知窗口扩展，必须重跑最终包门。
Ruling: 首位Astra reviewer用量限制终止且无最终报告，改由可用6.1-sol完成同一增量的唯一最终评审 — 不是已完成评审后的复审；cost: 独立结论来自fallback模型，保留初版冻结diff和失败状态，并把随后全量发现的adapter修正纳入新的最终冻结差异。

首轮两版源码2174各1error（test_service_applies_l2_replacements_without_touching_external_tool_snapshot），729项安装组均通过也不视为验收。该旧Factory Settings只有project_root，无model；新manual容量读取不该依赖扩展元数据存在。现对缺model/root使用unknown默认，不改旧测试。初版冻结差异保留 `/tmp/ignovate-constraint-capacity-review-before-adapter.diff`，唯一最终review冻结差异 `/tmp/ignovate-constraint-capacity-review.diff` 已包括修正。

首轮独立线缆探针错误地对runtime.ainvoke要求stream=True，SDK真实发送非流式，服务断开后SDK按自身策略重试，两个探针均失败且Factory finally收尾。已仅将探针改为runtime.astream(stream_mode='messages')匹配SSE协议；不是模型产品故障，失败日志native-wire311/313保留，不作通过证据。正式修改前97项定向仍有效于当时版本，后续最终定向必须包含旧adapter用例。

- [x] 首批8项原生RED/GREEN、三入口、重开、窗口丢失与小窗口控制。
- [x] 当前10项+完整相关组、311/313实际完整及新wheel安装/来源/字节、CLI/JS/diff。
- [x] 单次fresh增量review及作者唯一C/I pass，所有Minor与Declined明确裁决。
- [x] 实际默认SDK有限loopback SSE请求保留完整长约束及实际清理。


## 唯一最终评审与作者裁决

原文 `docs/reviews/2026-10-24-constraint-compaction-capacity-review.md`，SHA256 `ff1e311cb36c3dddcfab6a72c1b6603e59720f9ab66e7a0189b255c7b0d9c291`。fallback 6.1-sol独立13项.758s、4项真实Graph/SQLite探针.324s通过，完成冻结差异核验，结论0Critical/0Important/1Minor。原Astra无最终报告；本报告是本增量唯一完成的fresh review，不再复审。作者C/I pass确认无C/I修改需求，冻结生产/测试均保持review版本，不夹带Minor修复。

补充裁决延续初始六项，编号7–12（包括review全部四项Declined）：

7. Ruling: 新313环境从相同发行哈希锁离线重建并以RECORD逐文件核验，不用缺失依赖的运行作为产品验收 — 旧环境35个distribution的源码/RECORD缺失，原因未确定，不能归因产品、uv或reviewer；cost: 保留旧环境/失败日志与法证JSON、新增隔离目录与完整重跑时间。新安装76依赖5405文件、装app后77项5615文件全部存在且哈希匹配，app安装未在该新环境复现缺失；该结果不证明旧缺失原因。

8. Ruling: M1按非阻塞字符计数精度问题明确延期，不并入本轮C/I pass — renderer先按单换行计算，再加入完成banner并双换行join；公开6000→6001（旧wheel字节完全相同）、1000000→1000001，直接renderer边界还可多几十字符；cost: `summary_max_chars`及名义1M不是严格最终文本长度上限，完整最终token gate仍有效。后续独立边界修复须计算最终格式/固定标题，省可选段或拒绝L2，绝不截断完整约束；当前10项只覆盖明显超限，不声称精确边界已修。证据 `/tmp/ignovate-constraint-capacity-review-probes.py`、相邻log及原文报告。

9. Ruling: D1本增量review不认证整个goal、旧阶段或其他平台，原goal保持active — 本轮有限真实长任务/源码与安装门各自负责其实际范围；cost: 未执行的平台/loop/未来SDK/更大历史仍需原生证据，本轮通过不能替代项目整体长期稳定交付证明。

10. Ruling: D2环境缺失成因与修复验收由作者承担，reviewer只读边界保留 — reviewer明确未修改/调查旧313环境，不能以其311探针当313验收；cost: 作者须独立保留失败、法证和重建后完整终态（详第7项），新环境成功不把未知原因改写为已定位。

11. Ruling: D3采用已授权离线/有限loopback边界，继续现有带校准估算器契约 — 无真实远端模型API，实际OpenAI/HTTPX SDK仍在线缆上发送/解析但服务端usage为夹具数据；cost: 不提供远端精确token/真实服务长期稳定保证，4字符/token仅摘要候选容量，完整system/tools/framing/output reserve和校准决定最终准入。

12. Ruling: D4保留最终请求policy读取处重核的既有边界，不新增配置与网络的原子事务 — reviewer在preflight后变unknown/更小窗口/提高校准均验证provider0；cost: 最后读取完成之后到network I/O之间的并发配置变更不由本补丁原子化，不能把before-boundary故障证明外推到该窗口。

评审其余25项considered dispositions的accepted项按初始1–6及以上7–12裁决承接：完整约束/要求revision/archive/index（4）；三入口/request-only原checkpoint与manual worker/cancellation/state recheck（3）；unknown/known-small/扩展标记、legacy未标记、auto_task_context=False、caller metadata/private helper（2、5）；四字符近似/tinybudget/校准/完整system-tools/output gate（1、11、12）；重压缩/可选段与I/O（1、4，严格格式边界另为8）；既有gain/完成tool边界未变，不因此重新认证历史全量功能（9）。没有将任何Declined默认为接受或删除。

实际线缆补充：新313已安装最终wheel进行有限默认SDK复杂goal回放，9轮/19实际SSE请求/92次tool-result消息、完整响应脚本耗尽，交付verified后外部变更变unverified，真实compacted=true；FD4→4、无新增threads/errors。这是本地受控协议证据，spent/usage来自夹具，不是远程计费或真实模型推理质量。日志 `/tmp/ignovate-constraint-capacity-fresh313-native-goal.log`。


### 环境失败现场与替代环境

旧313路径记录 `/tmp/ignovate-native-matrix313-original-path.txt`；法证 `/tmp/ignovate-constraint-capacity-313-environment-forensic.json`。旧env完整运行已确认依赖缺失，核验PID/PPID/完整command后只对所属PID99951发SIGTERM，外层终态241，记录 `/tmp/ignovate-constraint-capacity-damaged313-run-stop.json`。不是因观察等待超时而重启，也不将未完成用例计为产品失败/通过；旧安装与SDK探针此前以ImportError退出1。三组旧输出保留为 `/tmp/ignovate-constraint-capacity-damaged-environment-{full313,installed313,native-wire313}.log`。

替代env `/Users/aycxd/.codex/tmp/ignovate-matrix313-sdlbf64_/venv`，actual Python3.13.13；同 `requirements-release.lock`、uv --offline --require-hashes，未改锁或版本。依赖安装前后/运行期间RECORD审计 `fresh313-before-app.json`（76 distributions/5405 files）、`fresh313-after-app.json`与`fresh313-during-tests.json`（77/5615）issues均空。所有文件使用各自RECORD的hash核验，不把pip check替代文件完整性。新环境不覆盖旧现场，main path指针另存original后指向新env供后续验证使用。

当前最终wheel SHA `6c089acc45435111278a4d3016cd4e23605ec32713907c0f786cc91809b6fc3d`，attestation `/tmp/ignovate-constraint-capacity-final-attestation.json` 核实：158生产Python逐字节匹配source，且与507基准仅compact/context_runtime/agent_service三文件改变；newtest与该三文件的最终冻结diff SHA `b274fa06fa2c949746a50193ac8034fd5526df8debbadba8e483aa122a3dfaba`，review后未改。原564 wheel仍保留于pre-adapter目录，不作为验收对象。


## 最终联合验收（phase28 + 独立phase29）

四组运行均读取真实terminal exit0并核对完整summary；新3项handoff及原test_gui_native13项加入外安装组，故732→748，不通过改旧测试等待条件使测试过关。

| 最终联合实际门 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| 3.11源码全量 | 2177 | 48 | 375.875s |
| 3.13源码全量 | 2177 | 49 | 380.450s |
| 工作区外安装3.11 | 748 | 5 | 228.751s |
| 工作区外安装3.13 | 748 | 5 | 232.895s |

最终接受的是联合wheel `/tmp/ignovate-map-handoff-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`，SHA256 `e40302a5913b4d905b1ea2a8946f15bf8aca8cdc095143b53b032c93cc84a7e3`。6c容量候选wheel独立保留，曾在full313暴露既有GUI失败，不能将其改写为全门通过。联合wheel对507旧验收wheel仅四个生产文件改变；phase28的三文件+新test冻结差异、phase29 controller+newtest冻结差异各自已独立review，review后均无作者C/I代码改动。158生产Python逐字节与当前source一致，两版外安装各46个真实module origins核对；GUI相对6c只有controller改变，未重写phase28三文件或其测试。

JS17/实际安装CLIhelp/JSONdoctor/dependency check/git diff --check全部通过，四组无ResourceWarning（不称所有warning为空，313既有editor-terminal测试pty.fork会发多线程DeprecationWarning；搜索确认调用位于tests/test_editor_terminal.py，未据此声称产品deadlock）。门与所有日志哈希 `/tmp/ignovate-map-handoff-final-gates.json`，字节来源 `/tmp/ignovate-map-handoff-final-attestation.json`。外安装311实际本地SDK OpenAI3.26.1，313实际完整发行哈希锁OpenAI3.26.0，未伪称两个环境完全同依赖。

联合最终包的实际默认SDK SSE长约束2请求跨close/reopen：全文9000约束保留，旧900000分析不入请求，输入8225/8248≤122880，FD4→4/errors空。另9轮/19请求/92tool-result复杂goal，verified后外部改动变unverified、实际compacted=true、全部脚本耗尽、FD4→4/no new threads/errors。有限127.0.0.1夹具无真实远端API，不代表远端真实token质量/计费。对应final-native-constraints313与final-native-goal313日志为最终e403实装；原3118k输入证明对应未再改变的相同phase28模块字节。

旧损坏环境/失败原日志/首次fixture错误与部分修正失败均保留；新313依赖/app安装及reinstall完整性未复现缺失，最终77 distributions/5615文件RECORD/hash审计issues空（final-integrity313.json）。缺失原因仍未知，未归咎产品或工具，也未隐瞒旧运行以SIGTERM结束的241/fixture143。

本轮无真实API/提交/推送/合并；保留全部旧工作与用户IDE/CSS。两项既有Minor（phase27 AnyIO close异常可跳过fixture server清理、phase28摘要精确字符上限）仍明确延期；其他GUI控制的保守忙碌窗口、实际远端/其他OS-loop/未来SDK/更大容量及最后policy read之后配置并发边界未被本轮认证。原始goal保持active。本轮两项生产缺口完成联合验收，不将阶段完成写作整体长期稳定目标已达成。

最终冻结核验 `/tmp/ignovate-map-handoff-final-freeze-audit.json`：与507验收wheel完整manifest路径相同，仅四个生产Python及对应dist-info/RECORD内容改变；158生产文件与当前source相同，两份review冻结diff与当前source/tests均精确相同。非Python资源未因本轮改变。

后续phase30状态：原phase28摘要格式超限M1已在独立生产增量修复并通过完整新03f包验收，见 `docs/reviews/2026-10-24-summary-rendering-bounds.md`。phase27 AnyIO fixture关闭故障及phase30新发现的可选摘要语义永久覆盖缺口仍分别待办；不修改本文件的历史review结论。
