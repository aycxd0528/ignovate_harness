# 对话交付后普通回答的渲染就绪

日期：2026-10-25。基准 `fe5814f`（两项 POSIX/MCP 生命周期修复已提交）；本轮只更改测试与 native 先行门，产品 wheel 仍为 `ee0250e546cb9a9bbf5fa4288d438c3d2fd1e842c27ade154d42b325a8c7f4c9`。

## 合并后失败与根因

PR5 合并前 12 检查成功并已合并。合并后 main `bd8fa20` 的 Windows native run `40179640612`：installer/313 成功，311 全量 2230 项 / 99 skip / 1254.881 秒有 1 FAIL。`ConversationLayoutTests.test_delivery_once_then_plain_question_does_not_repeat_old_report` 的 verified 子场景仍显示“正在推理…”，断言“普通回答”缺失；已有任务回答与单次交付报告仍在。Tests and distribution 与 desktop 成功。311/313 完整 native 日志均未匹配旧 `branches.sqlite` / `WinError32` / `Exception ignored` 关闭警告；不因该扫描声称已认证任意资源故障。

`SessionRunner.wait_idle()` 只等待消息 pump；`TranscriptLog.refresh_renderable()` 通过 `call_after_refresh(_refresh_cached)` 调度缓存刷新。单次 `Pilot.pause()` 不构成特定答案已渲染的条件。此失败没有证明任务未执行、重复交付或答案丢失。

测试复用同类已有三秒 `wait_for_condition`，等待实际 transcript 包含“普通回答”，随后执行原先完整断言：交付报告 count 恰为1、四种状态各自标签、任务回答先于交付报告、普通回答存在、secret 不出现。没有强制刷新/滚动、修改状态、延长已有三秒 helper 预算或删除任何原断言。native 先行门加入完整六项 `test_ui_conversation`，114→120，保留全部 discovery、120 秒单项 watchdog、45 分钟 job。

## 真实渲染负对照

原生 Textual probe `/tmp/ignovate-conversation-render-probe.py` 先建立 CI 中已绘制第一轮回答的前提，仅 gate 第二轮实际 finished 普通回答的 cached/reflow 回调，在该 log 第三次 Pilot.pause 后调用原生 reflow：

- 首个探索 probe 错误地重绘已卸载旧 log，且未建立第一轮绘制前提，得到 NoScreen/ValueError；`/tmp/ignovate-conversation-render-red.log` 保留，不算产品 RED。
- 更正为当前 log 且先建立已绘制任务回答前提后，旧测试四个状态均在原“普通回答”断言 FAIL，1 项 / 0.476 秒 / 4 子场景失败，无 probe 异常；`/tmp/ignovate-conversation-render-red-corrected.log`。
- 同一 probe 下候选全部四状态 GREEN，1 项 / 0.667 秒，每个 log 第三次 pause 释放；`/tmp/ignovate-conversation-render-green.log`。
- gate 永不释放的 `--drop` 负对照，四状态都在既有三秒 helper 明确超时，1 项 / 12.264 秒 / 4 FAIL；未因为等待而错误通过。`/tmp/ignovate-conversation-render-never-ready.log`。
- 正常完整模块6项 / 2.016秒通过；`/tmp/ignovate-conversation-module311.log`。YAML已解析，实际折叠 Python -c 已 compile、loader计数120；诊断脚本初次未处理没有name的uses step，修正为get后通过，不是产品/工作流故障。

这只证明测试观察语义与真实渲染边界，不声明产品渲染硬时限或 macOS 已认证 Windows。最终冻结代码的全量、实装与新 head CI另记；不以旧 head 的成功代替。

## 唯一独立审查与作者一次处置

原文 `2026-10-25-conversation-render-ready-review.md`，SHA256 `30e9a5c4eca086f9e880a31659b4efe7223072e47ec6b6562ce5bd50db29768c`；两路径冻结 diff SHA256 `6099c6830fb3820afbd807496970c0f5d9cac29ef9e733fa37f49b76f8839f0e`。唯一 fresh reviewer 0 Critical / 0 Important / 0 Minor，只读完整报告，没有重跑门或发起其他审查。作者完整阅读并作一次无补修处置：

- R1：采纳已完成 pump 与实际绘制边界不同的分析，复用已有 helper 的最小变更；产品调度不变。
- R2：采纳第二轮唯一答案条件不能由 prompt/第一轮误满足的判断；原 count/标签/顺序/普通回答/secret 断言完整保留。
- R3：采纳 RED/GREEN/永久 gate 明确超时的证据；cost：该 probe 是 macOS 机制证明，不是 native Windows 的调度测量。
- R4：采纳完整六模块项加入 preflight 的判断；cost：每次 workflow 两版本共额外12次执行，push+PR再翻倍，仍不能删完整discovery。
- R5：采纳最终源/实装/新head CI需作者确认的边界；审查时全量进行中，后来结果另记，不能提前通过。

审查表全部12项 Considered / Declined 的作者裁决及成本：

- D1：不把等待唯一正文当掩盖缺失；永不释放仍明确失败，缺失会消耗原预算而非通过。
- D2：不认为等待绕过重复交付/标签/泄密/顺序；原断言全部继续执行。
- D3：采纳观察完成答案已绘制后的语义；cost：不认证中间每一帧，也不保证任意更晚外部修改。
- D4：不加固定sleep或延长三秒helper；cost：永久缺失四状态约12秒加清理/调度开销，取消预算不是硬wall-clock SLA。
- D5：不在测试里强制reflow或增加产品readiness API；probe释放仅用于实验，候选代码不带该行为。
- D6：不扩展框架或改写所有conversation测试；其他缺口需有独立实际证据。
- D7：不在永久测试另等第一轮；已等待第二轮实际final，原断言仍要求第一轮存在且先于交付；probe的已绘制第一轮仅为忠实重现CI前提。
- D8：采纳完整模块先行门；cost如R4，不能用本机2.016秒估算Windows运行时，也不拆成随意子测试清单。
- D9：不删除discovery/watchdog/async诊断或改job预算；现有全部保留，loader已实际计数120。
- D10：不把probe视为每种Windows延迟的唯一法证根因；它独立证明旧观察可读到陈旧缓存、新观察等待实际条件。
- D11：不重新审POSIX/MCP、全部PR5或打包；冻结两路径之外保持既有报告与最终门。
- D12：不重跑reviewer套件、不做第二轮review、不据报告合并；作者记录一次裁决并核对最终身份，未来PR的合并仍由用户决定。

## 最终作者门

审查后没有更改冻结两路径代码。最终两个源码完整 discovery 均2233项：3.11 为54skip/393.573秒，3.13 为55skip/396.254秒；两版新wheel工作区外实际安装的相关13模块各173项/4skip，82.338秒与82.643秒。四个执行句柄77919/18519/42343/67111均已读取terminal exit0。新原生回收/健康通道回归和原SDK取消严格断言包含在全部相应门内；没有删除此前红色候选日志。

发行产品未因本轮fixture/CI变更重建：wheel仍ee025，实装确认159Python+39GUI字节匹配、10相关origin来自安装目录；CLI/help/JSON doctor已在同一产品字节上通过。最终日志SHA/计数/退出码索引见 `2026-10-25-post-merge-validation.json`。新head真实Linux/Windows/installer/desktop CI在推送后另观察，不以本地全量冒充通过；本轮不自动合并新PR。
