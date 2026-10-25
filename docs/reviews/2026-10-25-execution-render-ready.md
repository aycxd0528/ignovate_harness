# 执行面板测试的渲染就绪

用户授权合并PR5。70643c9尚未合并，已确认push Windows311完整2230/99skip/1106.429s仅1FAIL：test_tool_progress_is_visible_and_result_arrives_before_final_reply，最终log.lines中“检查完成”计数0而预期1。该job100项native先行门通过；四个Linux完整2230以及installer、desktop通过；其余三个native完整job写入时仍运行。失败日志 `/tmp/ignovate-pr5-706-native311-push.log` 未发现branches.sqlite/WinError32/Exception ignored，不重跑失败job刷绿。

生产_append_assistant对现有live turn设置text/finished后refresh_renderable，通过call_after_refresh异步更新缓存。session_runner.wait_idle保证运行处理结束，不保证显示缓存已刷新。该测试结束时仅pilot.pause一次便读log.lines。同类ExecutionDashboardTests已有三秒wait_for_transcript，仅在resize-follow方法内使用；本次提取该原有就绪规则供该类所有渲染观察复用，不改生产调度。

## 初始裁决与成本

- R1：原有screen/viewport geometry、resize/reflow/follow callback就绪条件提到类helper，全部六个dashboard方法在读取渲染结果前等待；明确resize目标40x18，保留所有原文本、颜色、正负可见性、屏幕身份、精确计数和滚动位置断言。cost：依赖既有TranscriptLog内部状态契约，未来内部重构需调整测试；每次最多三秒取消策略，非绝对wall-clock SLA。不主动scroll或调用任何产品render/refresh方法。
- R2：实际延迟render probe拦截最终reply的原_refresh_cached/_reflow，三次pause后只释放一次真实_reflow，不制造最终文本。读取git show得到的706原测试稳定FAIL 0!=1（0.422s、1pause）；新测试GREEN（0.501s、4pause）。始终不释放的negative仍在3秒以render未就绪断言FAIL（3.373s）。cost：是受控调度表征，不是实际Windows认证；原nativeFAIL保留。
- R3：原生先行门增加整个14项execution_display模块，100到114，完整2230、120秒watchdog和45分钟job保持。cost：两个trigger、两版合计多56次先行测试执行，本机14模块1.705s不是Windows耗时预测。
- R4：两路径唯一fresh只读review及作者一次处置后，最终完整源码、实装、实际native及关闭日志齐全才合并。cost：706旧head绿色不代替新head，先等当前三个native job终态核对其余失败，再提交新head；整体goal继续active，历史限制不关闭。

初版probe释放条件写为>=3，导致每次后续pause又_reflow，follow永不settle；`/tmp/ignovate-pr5-execution-render-probe-repeat-bug.log`保留。已只改probe为==3单次释放，并重新读取706源码做最终RED→GREEN，上述最终数字来自更正后同一probe，不将probe自身调度错误当产品缺陷。

冻结diff `/tmp/ignovate-pr5-execution-render-review.diff` SHA256 `5df25e81893a7e8a138039dd3c2bae94f67663bbcf5ee127a9632c02993a8ecc`，base70643c94b8628cef2d3e8a070b2f2f175118880d。产品wheel仍e1f7753f1922e32938e7c8d9ed3b0e7ec1ae229ee240ebd76497925af0021971。review、作者处置及最终门/merge身份待记录。

706全部CI已终态：11成功、1失败。三个native完整GREEN同2230/99skip：PR3111647.551s、PR3131592.356s、push3131516.815s。四个100项先行门均通过，四份完整native日志均无旧SQLite警告，只有上述push311最终render计数FAIL，未取消或重跑。

## 唯一完成review与作者一次处置

唯一reviewer `/root/execution_render_ready_review` 首轮transport断开且未写报告，恢复同一只读审查后完成，非第二review。0C/0I/0M，原文 `2026-10-25-execution-render-ready-review.md` SHA256 `0f2eedf6e16d73836daba6bfe6953bab590d18664da9f8bc2175d4a6717e49e6`。作者完整阅读一次处置，无代码补修要求，冻结代码未改。

- Ruling R1：采纳复用实际渲染生命周期就绪门；cost：现有private状态契约维护、每观察三秒取消预算，非严格wall-clock承诺。
- Ruling R2：采纳更正后单次真实render释放的RED/GREEN及不释放negative；cost：仅机制表征，真实Windows仍待新head；不认证所有cached-refresh交错，初版probe错误保留。
- Ruling R3：采纳完整14模块加入preflight；cost：每对push/PR两版最多多56项先行执行，native耗时以最终日志为准，完整suite不减少。
- Ruling R4：采纳一次review、一次作者无代码补修处置及最终全部门；cost：旧706不能替代新head，等待完整源码/实装/native和closing日志，不通过重跑旧失败擦除问题。

### 全部考虑与Declined项

- D1：丢失/重复final仍由原精确count1拒绝；cost：等待只改变观察时机，不改内容oracle，错误可能到三秒后才显现。
- D2：tool-start/end/final独立barrier及原正负断言保留；cost：测试service人为控制阶段，不认证任意远端模型时序。
- D3：helper不强制scroll；cost：真正follow错误仍由原尾部可见/scroll0断言失败。
- D4：layout和pending-work条件配合原color/viewport断言；cost：状态就绪不替代实际颜色/视口内容验证。
- D5：接受private属性、可选size及现有命名/注解维护成本，不抽象成测试框架或生产readiness API；cost：未来TranscriptLog重构需同步fixture。
- D6：不扩大到selection-deferred reflow或任意widget替换；cost：此六fixture不选择文字/不remount，helper不是通用idle证明。
- D7：不要求所有worker/spinner/timer停下；cost：工具阶段仍故意活动，观察范围是transcript所需render状态。
- D8：保留原三秒取消策略及状态诊断；cost：同步阻塞或取消清理可超此wall-clock值，120秒watchdog保留，不凭猜测改阈值。
- D9：保留原@work工具详情调用及screen断言；cost：异步worker安排仍用原Textual契约，不无证据重写API。
- D10：probe不插入文字、不改原断言，释放真实full-reflow；cost：不足以证明Windows全部渲染路径，实际native门必需。
- D11：初版>=3反复reflow只作为probe错误保留；cost：不能用于产品缺陷或最终结果论证，最终同一更正probe重新RED/GREEN。
- D12：接受整个模块重复执行和既有imports；cost：相较仅单method多13项，原模块在全量内仍执行。
- D13：不改full count、watchdog、job时长、平台依赖或其他性能架构；cost：新增先行门占现有45分钟余量，实际新head须验证。
- D14：四个706日志没有SQLite警告，不据此再改SQLite；cost：仅该日志观察，非全资源生命周期证明。
- D15：本次不重审whole-PR、安全/打包可重复性或历史验收；cost：已有历史review保留，wheel身份和旧head绿色不替代新制品/新head实证。
- D16：reviewer不执行新测试/网络CI/merge；cost：作者负责最终执行和提交身份，审查不是独立运行证明。

两版完整源码及实装门已经启动，安装范围在原830项基础加本模块14项，预期844；实际数量/结果以完成日志为准。文档写入时未合并，最终当前head与CI/merge身份由PR5及merge commit记录；整体goal保持active。

## 作者最终本地门

冻结代码与唯一review一致，作者无代码补修，四个最终执行句柄99116/84684/52892/84372均读取terminal exit0。

| 最终实际范围 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| macOS Python311源码 | 2230 | 54 | 389.312s |
| macOS Python313源码 | 2230 | 55 | 393.954s |
| 工作区外wheel实装311 | 844 | 10 | 240.773s |
| 工作区外wheel实装313 | 844 | 10 | 245.506s |

159发行Python文件逐字节相同、49个安装模块origin核对；39 GUI资源亦未改，沿用同一e1f7753产品wheel，不重复构建相同包。日志 `/tmp/ignovate-pr5-execution-final-{full311,full313,installed311,installed313}.log` 保留。实际新head的GitHub全部门及native关闭日志仍须核对；最终CI与merge身份由PR5记录，尚未合并。
