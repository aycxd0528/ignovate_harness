# 摘要最终格式容量：已修复并完成最终验收

上一goal turn为有效进展：phase28/29两项生产缺口已联合验收e403 wheel、两版全量2177/48及49skip、外安装748/5skip、两份fresh review和真实有限SDK多轮回放。本轮不重做前序增量、不以HEAD/全dirty为基准。当前真实基准 `/tmp/ignovate-map-handoff-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl` SHA256 `e40302a5913b4d905b1ea2a8946f15bf8aca8cdc095143b53b032c93cc84a7e3`，修改前158生产Python与source逐字节相同已核实。原始goal继续active，无真实API/提交/推送。

## 问题与复现

phase28 review M1：_render_summary先按单换行prefix判断，再加固定完成banner并双换行join，默认6000可返回6001、名义1M返回1000001，直接renderer还有多几十字符。最终token gate仍有效，先前按Minor明确延期；本轮作为独立生产增量修复，不回写旧review或夹带旧C/I pass。

新增 `tests/test_compact_summary_bounds.py` 6方法。真实public默认/显式6000、20000、1000000、固定格式（含Unicode）一字符不足、空state banner一字符不足、实际HistoryArchive/index完整恢复边界；对照精确容量恰好容纳仍成功，原始constraint/metadata和输入messages不变。可选标题/节选/omission通知在16种剩余空间下均计入最终长度，保留完整约束和可发现index。311 source在生产改动前6方法8个断言RED .071s，313实际安装e403同6/8RED .079s；不是import/fixture错误。失败原日志 `/tmp/ignovate-summary-bounds-red.log`、`baseline313-red.log`。

## 有界设计与修改

只改compact._render_summary及一个新test。固定title/goal excerpt/完整constraints/完整archive index/banner全部按最终双换行格式计算；必要段落放不下返回None，public compactor保留原历史和完整状态，不截constraints/删index/提高真实输入budget。可选节选继续原section预算和次序，加入heading/separator/omission通知的真实长度，若需则仅撤回可选条目直到整段（含正确遗漏数量）容纳，或省略该可选段。取消不能精确计数的200字符预留，使用真实剩余容量；没有改变归档、state、pin/tool boundaries、gain、window/helper/expanded marker/final request gate。

修复后111项5.110s通过，包含6新方法及phase28真实三入口、Graph/SQLite重开、context/history/task/legacy适配器组；日志 `/tmp/ignovate-summary-bounds-targeted.log`。当前不以局部门代替最终发行包与全量。

## 初始裁决

1. Ruling: 固定最终格式必须完整容纳，否则保留原历史 — 一字符超限也不截约束或恢复index；cost: 过去返回略超限summary的边界输入现在可能拒绝L2，完整原文与最终request gate仍保留。
2. Ruling: 可选段按标题/双换行/节选/遗漏通知整体精确计数，撤回条目时更新遗漏数量 — 不用尾部截断制造假完整，不把遗漏数量当已渲染条目；cost: 极小剩余容量可能只显示遗漏提示或不显示可选段，原始数据仍在state/archive，可检索index始终是必要格式的一部分。
3. Ruling: 保留原三入口容量/unknown和绝对1M契约，最终模型预算独立核验 — 本轮仅把名义字符上限变成最终文本严格上限；cost: 字符不是字节或远端精确token，取消旧200字符安全余量后可选文本数量略变化，gain/final完整预算仍可拒绝。
4. Ruling: 用独立最终格式和公开6000/20000/1M两侧边界作持久回归，同时真实archive恢复 — 不是只测private helper或始终拒绝；cost: fixture包含固定界面标题和143字符格式常量，未来正文格式改变时须同步维护边界期望，其他历史稳定性靠完整相关/发行门。
5. Ruling: 本轮只处理生产格式边界，不夹带AnyIO测试close异常清理或其他GUI控制更改 — 与最终可靠长任务相关的已知Minor分别留有证据；cost: phase27 AnyIO fixture close失败仍可能跳过server关闭，其他GUI短暂保守busy及平台/远端更大容量边界继续未认证，不因本小修复宣称整体完成。

- [x] e403精确基准与两版6方法8RED、精确fit正对照、真实archive和16个可选余量边界。
- [x] 单生产函数修复与111项实际相关GREEN。
- [x] 当前冻结增量唯一fresh只读review、作者唯一C/I pass、所有Minor/Declined逐项裁决。
- [x] 新wheel、两版full/外安装、158生产字节/46来源、CLI/JS/diff及实际默认SDK回放。


## 唯一完成评审与作者裁决

原文 `docs/reviews/2026-10-24-summary-rendering-bounds-review.md`。单次fresh 6.1-sol xhigh只读review 0C/0I/1Minor；作者唯一C/I pass确认无需C/I代码变动，不复审，不把Minor夹入当前冻结差异。review6项.062s、10种state/69892容量检查.931s通过，含19081仅omission提示场景和与真实e403 public超限/精确fit对照。独立模拟仅返回必要前缀的renderer后，当前optional永久测试仍通过.001s，因此M1是未来回归覆盖缺口，不是现生产逻辑错误。

作者维持前述初始1–5裁决，并明确逐项采纳review全部C1–C16（含M1）及D1–D8的以下裁决/代价，不将considered/Declined默认为已认证。初始1覆盖C1/C2/C4/C5/C13，初始2覆盖C6/C7/C10/C16，初始3覆盖C3/C8/C11/C12，初始4覆盖C9/C14/C15，初始5覆盖D1/D4/D6/D7/D8；D2/D3/D5另明确最终门、远端与配置并发边界。

C1. Ruling: Accept the exact final-format measurement and refusal if it cannot fit — Final mandatory formatting, including banner and double joins; exact independent prefix and adjacent-fit probes; cost: Previously tolerated formatting excess now conservatively refuses L2; originals remain available.

C2. Ruling: Accept fixed-format overhead as part of the same character capacity — Empty-state/tiny capacity and Unicode; new direct exact-fit tests passed; cost: A capacity below even the fixed banner/title cannot produce a summary.

C3. Ruling: Accept the existing public defaults, integer validation, and 1M ceiling with exact output enforcement — Public default/explicit 6000, 20000, and absolute 1000000 boundaries; actual baseline comparisons; cost: Capacity remains Python character count, not bytes, graphemes, or provider tokens.

C4. Ruling: Accept refusal instead of trimming protected content — Complete constraints, first goal excerpt, and recovery index remain mandatory; renderer reads without mutating state; cost: The existing first-goal excerpt is still limited to 800 characters; a large mandatory block can prevent L2 even if optional text is empty.

C5. Ruling: Accept unchanged nondestructive L2 refusal behavior — Public L2 overflow retains original messages/content/metadata in the new tests and independent probe; cost: This is not a rollback promise for a separately successful L1/pin operation in an `all` call; that accepted behavior is unchanged.

C6. Ruling: Accept the existing request-first/reverse-other-items policy and fixed section budgets — Optional section order and excerpt budgets; independently checked across 10 states and generous output equal to baseline; cost: Fitting more optional material after removing the old 200-character reserve can change a candidate's gain and which later sections fit.

C7. Ruling: Accept conservative whole-entry retraction and recomputation — Accurate omission count after retraction, omission-only sections, and complete section omission; independent exhaustive probe passed; cost: The renderer can leave spare room or show only an omission notice instead of retrying a shorter excerpt; no maximal-fill promise is introduced.

C8. Ruling: Accept a rendering-only change with no state/metadata schema change — Optional state immutability and full saved constraints/evidence; read surrounding `_summary_state` and metadata construction; cost: Complete metadata can remain larger than rendered text; this patch supplies no new persisted-state size guarantee.

C9. Ruling: Accept unchanged complete-snapshot and discoverable-index semantics — Real archive index and original assistant analysis recovery; new real `HistoryArchive` test passed; cost: The archive must remain available for later recovery; optional visible excerpts are not substitutes for originals.

C10. Ruling: Keep fail-closed completeness checks and existing immutable snapshot publication — Archive-truncated refusal and archive publication before a possible renderer refusal; surrounding code inspected; cost: A refused compaction may already have saved recoverable snapshots/index records; this is pre-existing nondestructive I/O, not an original-history deletion.

C11. Ruling: Retain the accepted algorithms unchanged — Existing pin/tool exchange boundaries, L1 behavior, hard/soft gain checks; compact.py surrounding implementation inspected; cost: A newly longer optional candidate can still fail the unchanged soft gain gate; a hard call still uses its existing gain policy.

C12. Ruling: Retain the accepted phase28/29 contracts; exact production-byte inventory shows these files are unchanged — Unknown/known-window helper, three runtime entry points, expanded marker, complete request token gates; cost: Strict character fit is candidate construction only; actual complete-request admission may still reject it; broader runtime acceptance belongs to the final author gates.

C13. Ruling: Keep that banner mandatory in the exact calculation — Fixed completion banner still describes historical assertions as unverified; cost: Its character overhead is charged even for an otherwise empty summary.

C14. Ruling: Accept the explicit fixture as a valid regression tradeoff — Independent fixed-format expectations and the public fixture's documented 143-character maintenance constant; cost: Future intentional wording/format changes must update the fixture; dynamic generous-render extraction in other cases alone is not the independent oracle.

C15. Ruling: Record/defer Minor M1 — Permanent optional test can pass after all optional content is discarded; cost: Current correctness is independently checked; retention/order/count semantics lack the proposed focused permanent assertion.

C16. Ruling: Accept the local bounded rendering work — Extra optional-section reconstruction/retraction work; cost: Existing small per-section excerpt budgets bound the candidate entry list; no complete long-run performance certification is inferred.

D1. Ruling: 作者采纳以下独立范围裁决及代价：**D1 — Whole long-task goal, previous accepted phases, and unrelated dirty files:** this review authenticates only the wheel-to-frozen rendering delta. **作者裁决:** preserve prior acceptance evidence and keep the overall goal active. **Cost:** this result cannot certify all earlier lifecycle, history, GUI, frontend, or IDE work.

D2. Ruling: 作者采纳以下独立范围裁决及代价：**D2 — Final distribution/full-matrix acceptance:** the author owns the new wheel, full 3.11/3.13 runs, actual installed package tests, 158-file/46-origin reconciliation, CLI/JS/diff checks, and finite SDK loopbacks; duplicating them was expressly excluded. **作者裁决:** require those final actual outputs before release acceptance. **Cost:** focused review success does not mean those gates are complete, green, or reproducible.

D3. Ruling: 作者采纳以下独立范围裁决及代价：**D3 — Real remote-provider behavior and exact remote tokenization:** real model APIs/external network were prohibited. **作者裁决:** retain the calibrated complete-request estimator and the authorized offline/loopback acceptance boundary. **Cost:** no cloud model reliability, latency, token-count, or service-capacity guarantee is supplied here.

D4. Ruling: 作者采纳以下独立范围裁决及代价：**D4 — Other platforms, alternate loops, and future dependency/private SDK interfaces:** no platform branch or dependency changed in this delta and no such native matrix was run. **作者裁决:** keep those claims limited to their existing actual evidence. **Cost:** source portability and this 3.11 review do not certify unexecuted OS/loop/SDK combinations.

D5. Ruling: 作者采纳以下独立范围裁决及代价：**D5 — Atomic configuration mutation after the final policy read:** this renderer change does not alter the accepted runtime revalidation boundary or introduce an atomic config/network snapshot. **作者裁决:** retain that boundary rather than attribute a new guarantee to exact character sizing. **Cost:** concurrency after the last policy read remains outside this patch's certification.

D6. Ruling: 作者采纳以下独立范围裁决及代价：**D6 — Phase27 AnyIO fixture cleanup when `sdk.close()` itself fails:** the separately evidenced deferred Minor is in a different test increment and is unchanged. **作者裁决:** keep its explicit independent deferred disposition. **Cost:** that diagnostic failure path can still skip fixture server cleanup; this is not repaired or recertified by the summary patch.

D7. Ruling: 作者采纳以下独立范围裁决及代价：**D7 — Other GUI commands' conservative idle gap:** the previous map/fork fix and other controller actions are unchanged and not exercised by this renderer review. **作者裁决:** retain their recorded separate scope/deferment. **Cost:** those actions can still briefly return busy until owner retirement; this review supplies no broader GUI admission guarantee.

D8. Ruling: 作者采纳以下独立范围裁决及代价：**D8 — Arbitrarily large or indefinitely growing history/state/archive capacity:** probes cover the specified 1M rendered limit and bounded representative states, not unlimited persistent histories. **作者裁决:** keep the existing archive/state contracts and separate long-run capacity work. **Cost:** complete metadata/archive growth, all possible historical corruption, and indefinite resource scaling are not newly certified by a strict rendered-text limit.

review原文SHA256 `cc19699fa61766efe4273d735c6b923a9adf084c6a1aa4371f721eecaee67a60`，冻结diff SHA `a133ac7ec21e9841b3680133aaa6caec86cbb28b14bd79bd4ea4eb2f397007e8`。


## 最终包与实际完整验收

四组均读取真实terminal0后核对完整summary，无ResourceWarning；不把预期RED或局部111项当完整发行验收。

| 最终实际范围 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| 3.11源码全量 | 2183 | 48 | 375.109s |
| 3.13源码全量 | 2183 | 49 | 379.159s |
| 工作区外安装3.11 | 754 | 5 | 227.430s |
| 工作区外安装3.13 | 754 | 5 | 231.648s |

最终wheel `/tmp/ignovate-summary-bounds-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl` SHA256 `03f19c3fc4a04a20ac05039d1d0175f5430ef241d5a3da15d3a481c343762993`。158生产Python与当前source逐字节相同、两版外安装46 module origins核验。相对e403完整manifest路径和其余所有内容相同，仅compact.py和对应dist-info/RECORD内容改变；单生产函数+新test精确匹配唯一review冻结差异，review后无生产/测试编辑。字节/最终freeze审计 `/tmp/ignovate-summary-bounds-final-attestation.json`、`final-freeze-audit.json`，所有门日志与哈希 `/tmp/ignovate-summary-bounds-final-gates.json`。此处都同前缀 `/tmp/ignovate-summary-bounds-`。

JS17/diffcheck/实际安装CLIhelp+JSONdoctor+dependency smoke通过。313最终77 distributions/5615文件RECORD/hash审计issues空，未重建/改发行依赖版本；311本地OpenAI3.26.1、313实际发行锁3.26.0，不伪称两套依赖一致。313既有editor-terminal fixture的forkpty DeprecationWarning不计为产品deadlock，也不说所有warning为空。

实际默认SDK loopback SSE：311source及313最终实装各2请求跨Factory关闭/重开，完整9000约束保留、旧900000分析不入请求，输入估算8221/8243及8225/8248≤122880，FD4→4/errors空；313最终包另9轮/19请求/92tool-result，实际compacted=true，verified后外部改动变unverified、脚本全部耗尽、FD4→4/no新threads/errors。对应final-native-constraints311/313、final-native-goal313日志。usage仍是有限fixture数据，无真实远端API/真实计费/模型推理质量声明。

## 进一步的200逐回合容量探索

四组门完成后独立只读探索，同一任务actualFactory→AgentService→Graph→SQLite，离线RecordingModel边界每回合一次，200新增完整要求/7次L2/4次Factory重开，全部要求进入每一实际请求、task_id保持且重开前后完整Task snapshot相同，最大输入估算122631≤122880。03f最终313实装14.731s/峰值RSS142770176bytes、进程FD4→4/无新线程；旧e403真实target实装对照14.889s/142753792bytes，同业务断言和资源关闭均通过。不是200次真实SDK/远端模型或200次交付。

独立新发现：两版关闭后主checkpoints.sqlite均约493MB、总owned scratch约517MB，peak约522MB；旧版只读SQL统计1400 checkpoints/483143372bytes BLOB，主checkpoint表约485MB。说明当前输入有界不代表长期磁盘容量已解决，这不是renderer修复新引入。后续优先独立调查无损降低checkpoint快照存储放大，保持全部历史/分叉/旧数据恢复，不擅改存储格式或删除历史来刷容量。

探针两次夹具错误完整保留：首次begin误传constraints（实际需begin后amend）、之后把活跃loop的合法默认executor误算Factory泄漏；最终改到asyncio.run退出后两个对照均FD4→4无线程。不是产品修复，也不是靠关共享线程池抹平泄漏。新/旧探针owned约0.5GB数据均在子进程已退出后由父临时目录回收，仅保留日志/指标和小target实装。完整新调查 `docs/reviews/2026-10-24-checkpoint-storage-capacity.md`，指标及日志哈希 `/tmp/ignovate-runtime-history-scale-evidence.json`。

原始goal继续active，下一步真实长任务持久化容量优先，phase27 AnyIO故障fixture清理与phase30 M1可选语义永久覆盖仍明确待办。phase28原格式M1由本独立生产增量关闭，不能与本轮新的M1覆盖缺口混淆。无真实API/提交/推送/合并，用户IDE/CSS及全部前序工作保留。完整流程/产物/数据边界不因本轮改变。
