# GUI完成任务交接：已修复并完成联合验收

原goal保持active。phase28长约束生产3文件+新test已独立review 0C/0I/1M，四组门中311全量2174/48skip371.815s、两版安装732/5skip219.877s/232.940s均通过；重建后313全量2174/49skip382.085s遇到原test_gui_native.NativeGUITests.test_map_followup_from_history_forks_exactly_once_on_retry一项error，地图接口以current_task truthy误判done Task仍忙。不是用复跑掩盖此失败，phase28不单独宣称完整验收。

## 复现、边界与设计

该GUI controller与phase26/27已验收wheel字节一致；不仅靠字节推断，新增3项受控真实Factory/Graph/SQLite测试在507旧wheel外安装、当前311source、当前6c wheel313外安装均3项1error/2GREEN（.503/.500/.567s），同一地图忙碌拒绝。阶段28候选wheel SHA `6c089acc45435111278a4d3016cd4e23605ec32713907c0f786cc91809b6fc3d` 是本独立GUI差异的精确base，不伪称其313全量已通过；最终联合验收仍与507旧已验收包核对所有生产增量。

实际_pump创建_execute Task并await；_execute已完整结束、project lease/原生Graph操作已退出，但_pump尚未调度清空current_task时，run已completed且Task.done()为真。_map_send和其_fork下游都仅以truthy拒绝，用户立即从历史继续偶发报忙。只修改这两个既有入口为Task存在且not done时拒绝，不改_pump、不提前清引用、不扩大项目切换/模型更换等控制的准入；map原queue guard继续拒绝，fork clone仍同步project_execution lease保护。当前Task尚未结束的completed状态仍拒绝，不能只凭run.status放行。

新增test_gui_map_handoff三项使用实际NativeGUI fixture（不继承其测试以免多算），仅把await真实_execute结束到pump finally清引用之间用Event拉长，Task本体/Factory/Graph/checkpoint/历史分叉和request去重均真实。验证done owner/无queue立即地图分叉、排除later source内容、原要求保留、exact request重试只一child/一run、pump释放后queued followup正常执行；queued work和未完成owner仍0branch side effect/拒绝，后一项也直接检查public fork。

首次自建probe将门闩注册为普通cleanup、晚于asyncTearDown.close，触发首个预期E后teardown等待门闩；已核验PID/PPID后只停止所属进程35073（终态143）、保留first-fixture-cleanup-error.log，并把释放门闩移至asyncTearDown开始，before product fix真正3项RED终态1。首个只改map入口的69项仍被_fork相同guard拒绝，日志targeted311.log保留；补全同调用链两guard后69项4.599s GREEN。以上均为有效新故障信息，无随机循环刷通过，不改旧原生测试。

## 当前裁决与代价

Ruling: 本问题作为独立GUI生产增量，不能把旧问题归因phase28或宣称该全量绿色 — 旧wheel负对照相同RED；cost: 多一个单独审查和最终联合发行包门，保留phase28失败日志。
Ruling: 只在map→fork路径按真正Task.done准入，仍保留queue/active Task/项目lease/精确checkpoint保护 — 同步fork不会与未结束的_execute并行；cost: 其他设置/项目切换等保守busy窗口不在本轮放宽，不能将此局部修复外推为全GUI交接重构。
Ruling: 受控调度放大真实done-owner窗口而不stub历史/模型服务/clone或延长任意sleep — 可以确定性复现偶发原生失败；cost: test pump有小段复制，须维护与生产调度结构一致，门闩必须先于controller.close释放；绝不修改旧测试等待条件以掩盖产品误判。
Ruling: 最终验收使用联合新wheel和两版全量/外安装/完整字节来源 — phase28原6c wheel及其three-file+test独立结论保持，GUI增量基准明确为候选；cost: 全量与安装重跑时间，任何新失败继续调查，不仅重跑单个用例。

- [x] 旧已验收wheel/当前source/313安装原生RED对照，root cause与两处最小改动。
- [x] 最终69项+313对应组、新wheel、311/313 full及外安装/CLI/JS/diff。
- [x] 本GUI两文件冻结增量唯一fresh只读review、作者唯一C/I pass、全部Minor/Declined裁决。
- [x] 与phase28联合验收、两份brief/总表完整证据与成本。


## 唯一完成review与全部Declined作者裁决

原文 `docs/reviews/2026-10-24-gui-map-handoff-review.md`。独立8项1.596s、3项真实project lease/当前head/public fork探针.646s通过，exact frozen diff前后均一致；0Critical/0Important/0Minor。作者唯一C/I pass确认无需C/I改动，不复审，不改冻结生产/测试。

5. Ruling: D1其余controller项目切换/configuration/test_connection/reasoning/accept_plan保守busy边界独立延期 — 不把仅map/fork准入改动外推为所有状态转换；cost: 完成到pump清引用的短窗口仍可能要求这些控制重试，后续须分别核验旧factory清理/新项目状态/队列语义，尤其不能提前清pointer破坏project switch归属。

6. Ruling: D2模型选择/provider/plugin mutation及catalog刷新/work-control的相同保守窗口独立延期 — 这些操作有不同lease/version/资源更新契约；cost: 极短暂busy拒绝或catalog延迟仍存在，当前不认证所有GUI动作一致性，后续若共用idle判断须覆盖每个变更的安全条件。

7. Ruling: D3不为消除test pump复制而重构生产pump或加测试hook — 测试只在await实际execute结束之后延迟retirement，现有pump生产逻辑未改且有效；cost: test_gui_map_handoff.py复制的调度骨架须随未来pump维护，当前释放gate早于close，不以生产表面扩张换取一次性测试便利。

8. Ruling: D4保留phase28独立review及其M1/四项Declined，不把GUI评审冒充旧dirty/包装/源码安装验收 — 最终联合门须与当前e403 wheel一致；cost: 作者承担四组完整实际门和158字节/46来源核验，无追加review，未通过时不能发布完成结论。

review另评估并接受public fork已有queued work的等价done-owner场景（没有暂停点，command_lock+project lease+指定checkpoint保证隔离，不消费/重排queue）；done failed/cancelled Task的实际finally已完成但仍须独立completed/recoverable历史node；request replay在busy检查前直接重放稳定结果保留去重。这三项由第2项已有裁决承接，没有静默丢弃。

review原文SHA256 `914cae0779406913b89033cbcf6121fd049b58e2a5b3a4c7e9e8940258343d63`。


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
