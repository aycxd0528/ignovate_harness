# TUI 实现与验收记录

完成日期：2026-10-08（Asia/Shanghai）。范围为 [2026-10-07 审查报告](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-07-ui-ux-pro-max-audit.md) 的 F1–F7，以及已确认的状态行、Token Weather、等待动画和正文选择行为。

已使用安装在本机的 [UI UX Pro Max](/Users/aycxd/.codex/skills/ui-ux-pro-max/SKILL.md)，安装版本 `477bcb28c9812b385cb51a4605ddf30d7b2266e2`。借鉴 Claude Code 的紧凑活动与命令层级、Tianshu 的高度预算和逐层返回；依据本项目“无弹窗”要求，所有会话交互区域参与主布局排版。

## 最终行为

| 入口 | 实现和返回行为 |
| --- | --- |
| 首次启动／欢迎 | 统一身份、主题、品牌和底部键盘区域；按高度缩小字标。连接、推理、权限逐步配置，返回保留草稿，最终确认前不写入配置。 |
| `/model` | 会话上方展开滚动列表，当前值与候选光标分开标识；底部保存动作始终可见。默认保存为项目默认，`/model --global` 保存为用户默认。 |
| `/model add` | 就地表单；模型列表内进入新增时，Esc 先回列表；直接进入新增时 Esc 回对话。错误聚焦对应字段，写入失败可在同一表单重试。 |
| `/reasoning`、`/permissions` | 在同一交互区域展示实际支持的选项；明确确认后生效，取消保留原值。权限仍仅作用于本次进程。 |
| `/help` | 常规、命令、自定义命令、Skills 分页；可搜索，列表与说明按需展开；保留状态与输入位置。 |
| 计划确认、编辑、回退 | 正文或编辑区参与主布局，动作固定在其下；计划审批和回退默认取消，编辑取消不丢失原稿。 |
| 工具审批 | 保留内联审批，默认拒绝；阅读／编辑与审批依次占用交互区，避免同时挤占输入区或误启用输入。 |
| 正文选择／F7 | 对话正文可选任意中文、英文片段；F7 提供稳定可选快照。移除复制全文／复制选区按钮和顶栏复制提示，流式更新不移动当前选择。 |
| 反馈／完整状态 | 反馈进入会话正文；关闭内置命令面板和悬浮提示。F8 在会话内显示完整模型、推理、权限、轮数、Token 与天气。 |

输入框上方保持一行：左侧 Token Weather，右侧实际模型、推理档位与权限。空会话显示 `☀ Clear · 0% · 0 / 窗口容量`，不制造历史趋势。已开始但用量缺失时显示“未知用量”；未知窗口不猜容量。长模型名优先缩短，完整状态通过 F8 可达。

等待动画沿用已确认的 braille 旋转与真实耗时，仅更新当前活动块；完成后保留成功／失败结果。项目 `.nailong/settings.json` 可设置 `{"ui":{"reduced_motion":true}}`，改用静态标记与低频耗时刷新。

## 按审查条目核验

| 条目 | 已落实 | 主要证据 |
| --- | --- | --- |
| F1 零弹窗 | 模型、运行设置、帮助、计划、编辑、回退、正文快照全部为 `InteractionPanel` 普通 Widget；主屏幕堆栈保持 1；无活动 `push_screen`／`ModalScreen` 路径。 | `test_embedded_interactions`、`test_workflow_ui`、`test_workflow_closures`、渲染记录的 screen_stack。 |
| F2 尺寸预算 | 列表滚动；标题、操作、提示分别预算；临时压缩禁用的输入区而保留多行草稿。按钮边框明确设置，内容高度大于零。 | 0／2／14 模型，60×18 多行草稿；实际控件区域及每级父容器边界断言。 |
| F3 主题与选中项 | 选中背景、文字与当前标记使用统一语义主题；输入占位色与浅色 muted 修正；普通语义文字在两种表面均达到 4.5:1。 | `test_tui_visual_contract` 的实际 Rich 选中样式、占位色、浅色 NO_COLOR 对比度测试。 |
| F4 欢迎 | 启动前传入已选主题；配置和会话共享视觉外壳；保留已确认字标；短窗口缩小品牌，优先固定操作与提示。 | `test_harness_setup`、浅色 60×18 欢迎／错误预览、`main.py` 启动顺序。 |
| F5 表单错误 | 使用 API 地址／模型 ID／API Key 等可见标签；配置错误就近显示并聚焦；模型新增错误与写入失败在原表单修正；密钥隐藏。 | `test_harness_setup`、`test_tui_visual_contract`、新增模型失败写入与重试测试。 |
| F6 范围与返回 | 明确项目默认／用户默认；候选与当前分离；新增返回保留列表和字段；无未实现的数字快捷提示或会话保存选项。 | `test_model_configuration`、`test_embedded_interactions`，真实离线持久化与队列边界 fixture。 |
| F7 历史缓存 | 当前活动更新复用已完成条目；调整窗口或主题才允许全部重排；选择期间延迟重排。 | 100 条已完成条目在 3 次更新中未重绘，resize 后重绘；300 条测量。 |

恢复／切换会话后的历史和天气、工具折叠、命令输出、原生复制降级、默认拒绝、停止／队列边界也包含在相关回归测试中。

## 测试和渲染证据

最终相关回归套件：**185 项，60.454 秒，退出码 0，OK**。完整输出保存在 [regression.log](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/regression.log)。这是本次修改涉及的 UI、命令与持久化套件，未称为整个项目所有测试。

```sh
env -u NO_COLOR PYTHONPATH=tests .venv/bin/python -m unittest \
  test_tui test_ui_help_view test_ui_conversation test_ui_approval_panel \
  test_tui_copy test_tui_readability test_workflow_ui test_workflow_closures \
  test_execution_display test_embedded_interactions test_harness_setup \
  test_harness_settings test_activity_spinner test_composer_info \
  test_token_weather test_transcript_cache test_tui_visual_contract \
  test_model_configuration -q
```

离线导出 **169 个 SVG 场景**：213×56、100×32、80×24、60×18，暗色、浅色、ASCII + NO_COLOR；0／2／14 模型、欢迎与错误、空会话、帮助、推理、计划、编辑、回退、正文、审批、长会话。每个指定动作控件均检查屏幕边界、全部父容器内容边界和正内容高度。独立行为测试另覆盖浅色 NO_COLOR、运行时互斥与多行草稿。

探针：[render_probe.py](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/render_probe.py)；[170 条观察记录](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/observations.json)（169 布局 + 1 性能记录）。所有探针使用临时目录和示例连接，没有真实提供商请求。

300 条 Markdown 历史与一个活动块的缓存更新，中位数 **0.169 ms**（10 次，本机离线渲染测量）。该值只说明缓存更新成本，不代表真实终端端到端输入延迟。

最终还导出 12 份原生 compositor 字符网格，通过本机字体按单／双终端单元绘制 PNG，避免 SVG 查看器替换 CJK 字体造成的重叠。**PNG 是实际布局数据的字体可视化，不是运行终端的系统截图**。已人工检查欢迎、模型、表单、帮助、审批、空状态的最终图像；原始网格与脚本保留供复现。

- [欢迎页预览](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/preview-welcome-dark-100x32.png)
- [模型列表预览](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/preview-models-14-dark-80x24.png)
- [60×18 新增模型](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/preview-model-form-dark-60x18.png)
- [浅色字段错误](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/preview-setup-error-light-60x18.png)
- [浅色帮助](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/preview-help-light-80x24.png)
- [60×18 内联审批](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/preview-approval-dark-60x18.png)
- [空会话状态行](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/preview-conversation-empty-dark-100x32.png)

运行 `PYTHONPATH=. .venv/bin/python .../preview_probe.py` 后，再用装有 Pillow 的 Python 运行同目录 `render_cells.py` 可复现 PNG。脚本字体路径针对本机 macOS；其他系统需替换为自己的等宽和 CJK 字体。

## 独立审查和修复

完成实现后，以单独上下文对最终代码审查。3 项重要发现均在一次修复阶段完成并观察测试 RED→GREEN，再运行上述 185 项回归：

1. 交互区所有权：实际审批期间 F7 可能挤占布局并误恢复输入；计划确认遇到已有阅读面板可能抛错。加入互斥等待，记录原 disabled 状态，并验证两个进入顺序。
2. 60×18 多行草稿：帮助动作可能被父容器裁切。压缩活动交互期间的输入区显示，保留原文本和光标；检查祖先内容边界和按钮内容高度。
3. 浅色 NO_COLOR：muted 文字对比度不足。浅色语义 muted 不再依赖颜色开关，实测两种表面均通过对比度断言。

最终视觉检查另外发现原生 Button 的内联边框覆盖 CSS，使一行按钮没有可绘制内容。已明确设置无边框及一行高度，并加入内容高度断言。无延期的小问题。

## 实现决定与核验边界

按作出顺序保留以下决定与代价：

1. 在现有共享工作区实施，遵守不暂存／提交／重置／stash 的约束；保存审查账本作为记录。代价：后续需由用户决定 Git 整理与提交。
2. 保留原有以 `Screen` 结尾的类名以兼容导入，但全部改为普通 Widget。代价：类名仍可能使后续维护者误以为它们是屏幕叠层。
3. 反馈进入会话，禁用内置命令面板和悬浮提示，用 F8 补全状态。代价：熟悉 Textual 内置面板／悬停信息的用户需改用会话命令或 F8。
4. 沿用不发送真实模型请求的验证范围；实际持久化、序列化和队列边界通过离线 fixture 验证。代价：真实提供商的网络行为尚未由本次验收覆盖。
5. 真实终端剪贴板确认和字体／编码不由无头渲染证明；相关复制、超时回收、OSC52、原生降级与 ASCII 逻辑测试已通过。代价：特定终端的复制和字体结果仍可能不同。
6. 底层运行时完整架构不在此次 UI 审查范围；改动涉及的模型写入、失败重试和请求队列边界已回归。代价：此报告不能作为未改动的整个运行时架构的保证。

首次配置仍在 Agent 构造前运行独立 SetupApp，以保留凭据验证和启动顺序；它与会话共享视觉外壳，没有嵌套弹窗。已配置会话的所有交互都保留主屏幕与原历史上下文。

未读取 `.env`、未提交 Git、未调用真实模型，未删除已有用户工作。最终生产源文件摘要见 [source-sha256.json](/Users/aycxd/Desktop/LLM_project/docs/reviews/embedded-tui-2026-10-07/source-sha256.json)。
