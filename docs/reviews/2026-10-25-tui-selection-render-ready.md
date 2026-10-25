# TUI选择测试的真实渲染条件

用户授权合并PR5，d7d3eb0仍未合并。两版push Windows完整2229通过（99skip，1662.077s/1573.727s），PR313同2229/99skip/1566.968s通过；PR311单项FAIL：test_tui_readability.DirectSelectionTests.test_stream_updates_do_not_change_the_text_under_selection，在drag开始前报“选中的文字 is not visible in the transcript”。77项相关回归均通过。四份原始日志 `/tmp/ignovate-pr5-d7-native{311,313}-{push,pr}.log` 保留，均无branches.sqlite/WinError32/Exception ignored。

生产TranscriptLog.refresh_renderable用call_after_refresh调度渲染；夹具仅pilot.pause一次便立即扫描。已读取锁定Textual Pilot.pause：等待screen/CPU idle后触发screen timer，不能把它等同于特定文本已经可见。其他mount/focus测试已有wait_for_condition，三秒有界。此轮仅改测试与CI，不改生产GUI/渲染行为、不重跑失败job刷绿。

## 初始裁决及成本

R1. Ruling: drag在原有3秒wait_for_condition内等待同一viewport实际render_line出现目标文字，然后使用该行坐标执行原mouse down/hover/up；cost: 不自动scroll/改viewport/制造文本，真的不可见仍失败，失败可等最多3秒。

R2. Ruling: 增加确定性回归，初始render_line为pending，下一次pause才出现目标文字，验证未就绪前不发鼠标事件及原中文cell坐标；原选区高亮/剪贴板/流式更新后保留原字/escape后新文字断言不变；cost: fake pilot只证明夹具同步，真实Textual选择仍由原测试在Mac和Windows执行。新回归修前FAIL（not visible），修后全模块8项/4.528s通过。

R3. Ruling: 原生先行门加入整个TUI readability模块（85项），完整discovery保留；只将job35到45分钟，所有120秒单项watchdog保持；cost: 重复8项的有限时间与job最大额外10分钟。依据d7成功311实际job 17:18:58至17:53:15为34分17秒，原35分钟余量43秒；不改变产品业务超时或放宽单项失败。

R4. Ruling: 新两路径增量一次fresh只读review和作者一次处置后，当前最终head全量/实装/CI及关闭日志齐全才合并；cost: 不重审历史producer，不通过取消或重跑失败job替代修复，旧MCP未定现象及长Git目录限制保留，整体长任务goal继续active。生产wheel仍为e1f7753f1922e32938e7c8d9ed3b0e7ec1ae229ee240ebd76497925af0021971。

## 唯一完成审查及作者一次处置

唯一fresh只读review `/root/tui_render_ready_review`：0 Critical / 0 Important / 0必修Minor。冻结两路径diff SHA256 `91c87e44fb1c2cb8ffb1bf2d84fa3481934168c9804d768bc89e640e7b5c18ac`，base d7d3eb0。原文 `2026-10-25-tui-render-ready-review.md` SHA256 `0ac050909ddf19ff32ec6182281df4bde3ad330db38c4bd8e4fe158f799a33e0`。作者完整阅读后一次处置，不做第二轮review。

- Ruling R1：采纳同viewport实际文字就绪门；cost：ready路径一次调度，真正不可见最多3秒后失败，不改变原鼠标操作。
- Ruling R2：采纳fake deferred-render回归及原真实应用断言；cost：fake不认证Windows时序，实际平台最终门不可替代。
- Ruling adjacent Escape：同一次作者处置采纳两个既有Escape后文字观察的语义等待，仍用原3秒helper且保留原assertIn；依据selection_updated(None)通过call_after_refresh(_reflow)异步刷新。cost：两个独立测试各最多增加3秒失败等待，原Windows日志未出现这两个Escape失败，不伪称已有该失败证据。此补修不在冻结diff中，最终作者验证及实际CI覆盖。
- Ruling R3：采纳完整8项TUI先行门及45分钟job上限；cost：每个matrix/trigger重复8项，实际Windows耗时以最终日志为准，120秒单项watchdog保持。
- Ruling R4：采纳审查只清除此增量、最终head仍须完整平台门；cost：旧d7绿色和本机模块绿色不能代替新head Windows，关闭日志必须核查。

### 所有考虑及Declined项

- D1：不重设计生产渲染/selection；cost：保留原生产调度边界，避免无实际产品失败证据的行为改动。
- D2：不自动scroll/制造文字/直接选内部数据/弱化clipboard或highlight；cost：实际不可见或交互错误依然失败。
- D3：不加固定sleep、不延长fixture三秒或120秒watchdog；cost：负载下仍可能真实超时，不能借放宽掩盖。
- D4：不加永久缺失、多pending frame、逐callback重复fake；cost：未穷举Textual交错，原真实应用测试保留。
- D5：先行门选整个8项模块而非单一旧失败method；cost：有限重复执行换相邻选择回归早期信号。
- D6：不扩matrix/trigger/full discovery；cost：认证范围仍为现有两版Windows及其他既有平台。
- D7：reviewer不重跑测试/改环境/起第二reviewer；cost：报告基于读取，作者负责最终验证，未把review当独立执行证据。
- D8：历史producer、旧MCP间歇现象、长Git目录限制和整个goal完成不在本次两路径review内；cost：保留既有未定/拒绝边界，不随本次测试修复宣称已解决。

最终head、实际平台结果和合并身份以PR5及merge commit记录；文档写入时尚未合并。原整体goal继续active。

## 本地作者门

冻结候选完整源码：311为2230项/54skip/391.229s，313为2230项/55skip/395.677s；工作区外实装两版830项/10skip，240.458s及245.535s。四句柄46744/64582/40751/43795均terminal exit0。运行启动在Escape作者补修之前，不把已加载旧测试当补修后全量证据。

Escape补修后同一整个8项TUI模块重新验证：源码311/313为4.335s/4.238s，工作区外相同产品wheel实装311/313为3.998s/4.073s，四项均terminal exit0。159Python及49安装模块origin再次核对，产品字节未变，不重复构建包。最终head完整源码与原生门由新commit实际CI执行，所有门及关闭日志通过才合并。
