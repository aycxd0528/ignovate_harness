# ignovate harness TUI 设计与交互审查

审查日期：2026-10-07。重点：欢迎页、连接配置、模型切换、推理与权限设置，以及所有会打断对话的界面入口。

当前界面还不满足“没有任何弹窗式交互”的要求。模型和运行设置使用底部覆盖面板；计划确认、编辑、回退、正文快照仍使用模态窗口。建议先统一主界面与交互区域，再处理颜色、焦点、尺寸和刷新性能。

本轮产出为审查报告、离线复现场景和改进方案；产品代码尚未按本报告修改。

## Skill 与审查方法

已通过 skill-installer 安装 [UI UX Pro Max](/Users/aycxd/.codex/skills/ui-ux-pro-max/SKILL.md)，来源为 [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)，固定版本 `477bcb28c9812b385cb51a4605ddf30d7b2266e2`。当前对话已使用该技能，后续对话也可直接调用。

项目采用 Python、Textual、Rich 和 prompt_toolkit。技能没有 Textual 专用规则，因此使用已核对的通用指导：文字对比度、键盘焦点、错误反馈、主题一致性、渐进展示和减少无效重绘。浏览器的 ARIA、CSS 像素、SVG 图标、GSAP 等要求不直接套用到终端。4.5:1 是本次普通文字的设计审查基准，不代表完成了正式无障碍合规认证。

执行了本地技能检索，适用结果与平台适配说明保存在 [skill-evidence.json](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/skill-evidence.json)。返回行为、渐进展示和列表性能没有直接匹配的 Textual 数据库结果；相关建议使用技能 quick-reference 的通用规则，并结合本地测量。

通过 Textual `run_test` 渲染暗色、浅色和 100×32、80×24、60×18 场景，操作实际键盘路径，导出 SVG 并检查控件区域。全部使用临时目录、示例连接和模拟事件，没有真实模型请求。证据目录为 [ui-ux-pro-max-2026-10-07](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/observations.json)，源码摘要保存在 [source-snapshot.json](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/source-snapshot.json)。

## 参考项目的取舍

Tianshu 的欢迎渲染器根据宽度和可用行数，选择完整字标、简短引导或单行身份；欢迎文字设有宽度上限，先保证输入区空间。适合本项目借鉴的是尺寸预算、单一强调色、品牌与操作区分层，以及可直接照做的示例。[欢迎渲染源码](https://github.com/huiliyi37/Tianshu-harness/blob/a2eeb15e1288c4f93eeb822c69df3f69bda99e65/src/tui/format/welcome.ts#L495)

Tianshu 的模型选择明确标注“Enter 本会话、s 设为默认”，区分当前模型和光标候选项，先为底部操作提示预留行数，再计算列表窗口。这些语义和布局预算值得采用；它的模型、连接入口自身使用 overlay，本项目需按用户要求将这些内容嵌入主布局。[模型选择源码](https://github.com/huiliyi37/Tianshu-harness/blob/a2eeb15e1288c4f93eeb822c69df3f69bda99e65/src/tui/format/overlay.ts#L942)

Claude Code 可借鉴紧凑命令入口、默认折叠工具细节、按需展开历史，以及模型与推理强度的明确关联。视觉层级也参考用户提供的 Claude Code 截图。本项目继续使用实际支持的推理档位，并保持输入框上方“左侧 Token Weather、右侧模型／推理／权限”的布局。[交互文档](https://code.claude.com/docs/en/interactive-mode)、[模型配置文档](https://code.claude.com/docs/en/model-config)

参考资料描述的是观察到的行为；下文的零弹窗布局为本项目建议，并非参考项目已经采用的完整实现。

## 优先处理的问题

P1：直接违反本次交互要求或使主要操作不可见，需要优先处理。P2：影响理解、一致性或持续使用体验，随后处理。

| 编号 | 优先级 | 问题 | 直接影响 |
| --- | --- | --- | --- |
| F1 | P1 | 模型、设置和多个辅助流程仍使用屏幕叠层 | 历史阅读与输入上下文被打断，不符合零弹窗要求 |
| F2 | P1 | 模型菜单未给操作区预留空间 | 模型较多或终端较矮时，“新增／切换”和键盘提示被裁切 |
| F3 | P1 | 部分标题、选中状态对比度不足 | 帮助选中命令和浅色推理面板标题难以辨认 |
| F4 | P2 | 欢迎配置与会话界面采用不同主题规则 | 请求浅色主题仍得到深色配置页，视觉和焦点样式跳变 |
| F5 | P2 | 表单错误脱离字段，焦点没有引导回错误位置 | 用户需要自行寻找并修正无效输入 |
| F6 | P2 | 模型切换缺少持久化范围说明 | “切换”同时保存到配置，菜单和成功消息却未说明范围 |
| F7 | P2 | 等待动画触发历史整体重绘 | 长对话中的持续刷新可能影响输入与滚动响应 |

### F1：彻底统一为主布局内的交互

[ModelScreen](/Users/aycxd/Desktop/LLM_project/ui/model_view.py:15) 和 [SettingScreen](/Users/aycxd/Desktop/LLM_project/ui/settings_view.py:12) 都继承 `ModalScreen`，通过 [push_screen](/Users/aycxd/Desktop/LLM_project/tui.py:1927) 叠加到会话上。`conversation_panel=True` 只跳过通用主题处理，没有将它们变成主布局中的控件。透明背景、靠底部显示也仍会覆盖底部信息与输入区。

以下入口需要一起迁移，避免只改模型菜单后仍不断遇到弹窗：

| 入口 | 当前实现 | 建议呈现 |
| --- | --- | --- |
| `/model`、`/model add` | `ModelScreen` | 输入区上方的选择列表／就地表单 |
| `/reasoning`、`/permissions` | `SettingScreen` | 同一交互区域中的短列表 |
| `/help` | `HelpScreen` 全屏替换 | 主内容区内的帮助视图，保留状态与输入位置 |
| `/plan` 确认 | `PlanReviewScreen` | 会话内计划正文与固定的确认操作行 |
| 计划和记忆编辑 | `PlanEditScreen` | 主内容区内编辑模式，退出恢复原位置 |
| `/rewind` | `RewindConfirmationScreen` | 会话内确认行，保留影响范围与默认取消 |
| F7 正文快照 | `CopyReplyScreen` | 主内容区的可选文本模式，保留直接拖选复制 |
| 工具审批 | 已使用 `ApprovalPrompt` | 保留现有内联结构，统一视觉与尺寸预算 |

计划与回退现有入口见 [tui.py](/Users/aycxd/Desktop/LLM_project/tui.py:352)，正文快照见 [CopyReplyScreen](/Users/aycxd/Desktop/LLM_project/tui.py:152)。旧 `ApprovalScreen` 定义仍存在，本轮未发现实际打开路径，不将它当作当前必现的工具审批弹窗。

建议统一为四个区域：身份行、可滚动主内容、按需展开的交互区、固定输入区。交互区展开时真实压缩主内容的可用高度；历史内容可继续滚动，不遮盖、不变暗、不居中悬浮。确认需求仍保留，使用就地操作行呈现。

### F2：模型列表与提示必须分别预算

在 80×24、14 个模型的场景中，模型容器的内容区域为 `y=8..18`，按钮位于 `y=20`，提示位于 `y=22`。二者超出父容器的可绘制内容范围，截图中消失；模拟点击新增按钮得到 `OutOfBounds`。60×18、两个模型时，按钮也落在内容边界外，点击未命中。[复现记录](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/interaction-observations.json)

[当前菜单截图](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/model-14-80x24.svg)

根因是菜单使用自动高度，外容器另有限制，列表之外的标题、空行、按钮、提示没有整体预算。[相关样式](/Users/aycxd/Desktop/LLM_project/ui/model_view.py:19)

推理选择也存在相同问题：80×24 时父容器内容高度为 11 行，标题占 1 行、选项占 10 行，键盘提示被排到区域之外。[推理面板截图](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/setting-footer-check-80x24.svg)

建议先预留标题、操作提示、状态与输入区，再把剩余高度给可滚动列表。列表的候选项随光标滚动，提示始终可见；窄屏将说明放到当前候选项下方，避免每项都占两行。

### F3：选中项和浅色主题需要单独验证

以下数值来自导出的实际前景／背景色，按 sRGB 相对亮度计算：[contrast.json](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/contrast.json)。

| 场景 | 前景／背景 | 对比度 |
| --- | --- | --- |
| 帮助列表选中命令名 | `#88c0d0` / `#0178d4` | 2.26:1 |
| 帮助列表选中说明 | `#ddedf9` / `#0178d4` | 3.78:1 |
| 浅色推理面板标题 | `#fea62b` / `#e0e0e0` | 1.49:1 |
| 浅色 muted 文字置于 surface 时 | `#65717a` / `#e9edf1` | 4.25:1 |

帮助行内容使用自定义强调色，选中背景仍是控件默认蓝色，二者没有配对设计。[行内容](/Users/aycxd/Desktop/LLM_project/ui/help_view.py:103)、[主题应用](/Users/aycxd/Desktop/LLM_project/ui/help_view.py:224)、[截图](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/help-dark-80x24.svg)

推理／权限面板没有应用项目 Theme，沿用 Textual 的默认强调色。靠底部显示的标记又跳过了通用主题处理，浅色背景上的橙色标题因此尤其不清楚。[主题分支](/Users/aycxd/Desktop/LLM_project/tui.py:1933)、[截图](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/setting-light-80x24.svg)

建议为背景、正文、说明、焦点、当前值、错误定义统一的语义色。候选焦点用箭头和轻微底色，实际生效项另用“当前”或勾号；选中状态中的命令名与说明必须使用与背景配对的文字颜色。浅色 muted 色应同时检查普通背景和 surface。[主题定义](/Users/aycxd/Desktop/LLM_project/ui/theme.py:70)

### F4：欢迎页与日常使用共享主题和外壳

欢迎页目前有独立 `SetupApp`，固定使用 `#1d2233` 背景、蓝色强调和单行输入；日常会话使用另一套背景、强调色和输入边框。[欢迎样式](/Users/aycxd/Desktop/LLM_project/ui/setup.py:67)

离线输入 `cli_preferences={'theme':'light'}` 后，欢迎页仍显示 `Color(29,34,51)` 深色背景。[浅色请求截图](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/setup-light-request-80x24.svg)。启动的主题覆盖也在配置流程结束后才组装。[启动顺序](/Users/aycxd/Desktop/LLM_project/main.py:770)

建议欢迎页和已配置会话共用身份、主题、信息行和操作区域。首次启动在主内容中展开连接表单；完成后直接进入同一外壳的空会话。再次启动显示简短品牌与项目说明。保留用户已选定的字标风格，按可用行数缩小或隐藏字标，优先保证字段、动作和输入区可见。

欢迎文案应给出可直接执行的下一步，例如“介绍这个项目”“检查失败的测试”，并保持 `/help`、`/model`、`/reasoning`、`/permissions` 可发现。设置仅在确认后写入；返回上一步保留草稿。

本轮验证了连接 → 推理 → 权限 → 返回连接，模型与密钥草稿保留、配置未提前写入。这个行为应保留。[交互记录](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/interaction-observations.json)

### F5：错误应就近说明并恢复可修正的焦点

在连接步骤输入无效地址后，错误显示为 `DEEPSEEK_BASE_URL 必须是包含有效主机名和端口的 HTTP(S) 地址。`，与可见标签“API 地址”不一致。从“继续”按钮提交时，焦点仍在 `setup-next`，没有转回无效字段。[错误截图](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/setup-submit-error-80x24.svg)

[show_error](/Users/aycxd/Desktop/LLM_project/ui/setup.py:241) 只展示通用错误并滚动；新增模型的 [save_model](/Users/aycxd/Desktop/LLM_project/ui/model_view.py:106) 也只在表单末尾显示错误。

建议用界面标签说明修正方式：“API 地址无效，请输入完整的 https://… 服务地址”；错误直接置于相关字段下，提交失败后聚焦第一个无效字段。保留其余输入和隐藏密钥，保存失败留在原表单内重试，成功在当前交互区域显示结果。

### F6：模型切换需要明确范围与返回层级

选择现有模型会调用 `preferences.set(...)` 写配置，默认当前项目范围；菜单只显示“切换”，成功消息也只显示新模型。新增表单有保存范围说明，现有模型选择没有同样的说明。[切换实现](/Users/aycxd/Desktop/LLM_project/ui/actions.py:316)

建议菜单明确显示“保存范围：当前项目”或“用户默认”，反馈包含实际范围。若增加“仅本会话”，必须新增真实的会话覆盖路径并验证，不能只改按钮文字。也可先保留现有行为，直接把动作改为“保存为项目默认并切换”。

实际交互中，新增表单按 Esc 会关闭整个模型流程、返回输入框；菜单行显示序号，但按 `2` 不会改变候选项。这两项不导致配置损坏，却使层级和快捷操作难以猜测。[返回实现](/Users/aycxd/Desktop/LLM_project/ui/model_view.py:141)、[交互记录](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/interaction-observations.json)

建议定义清晰状态：模型列表 → 新增表单 → 保存结果。新增表单中的“返回”回到列表并保留列表位置；根层 Esc 返回对话并恢复输入草稿。序号若保留，则支持数字选择并显示提示，否则用无序号的箭头列表。

### F7：保留等待动画，限制重绘范围

已确认的字符旋转方案可以继续使用：活动文字与真实耗时保持固定位置，完成后显示结果。当前动画每 120 ms 刷新，[refresh_renderable](/Users/aycxd/Desktop/LLM_project/ui/transcript.py:184) 会调度重排，而 [_reflow](/Users/aycxd/Desktop/LLM_project/ui/transcript.py:164) 清空并重绘全部历史。

合成 Markdown 回复的单次重排中位数：10 条约 4.7 ms，50 条约 21.6 ms，100 条约 45.0 ms，300 条约 153.7 ms。300 条时已超过动画间隔。该数据是本机离线重排测量，不是生产环境完整输入延迟，也不能推出每种会话都卡顿。[测量记录](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/extra-observations.json)

建议缓存已完成的历史渲染，仅更新当前活动块；窗口尺寸或主题变化时才重排历史。可提供“减少动画”设置，关闭旋转但继续显示状态与低频更新的耗时；默认保留用户选择的旋转效果。

## 零弹窗的布局与交互方案

下面是建议布局示意，不是已经修改的界面。选择区域参与主界面的正常排版，展开时让历史视口缩小，关闭时恢复原阅读位置。

```text
ignovate harness  ·  项目名称                         ● 就绪

已有用户消息与模型回复，可继续滚动和选取正文

选择模型                           保存范围：当前项目
❯ deepseek-flash       快速编码                     当前
  deepseek-v4-pro      复杂任务
  新增模型…
↑↓ 选择   Enter 保存并切换   Esc 返回对话

☀ Clear · 0% · 0 / 1m        deepseek-flash · 默认 · 请求批准
────────────────────────────────────────────────────────
❯ 输入消息…
────────────────────────────────────────────────────────
```

推理、权限也复用该选择区域。焦点与生效状态分开标记，未经 Enter 确认时，右侧信息继续显示真实生效值。选择结束后再同步右侧模型、推理、权限与左侧窗口容量。

空会话继续显示用户选定的 `☀ Clear · 0% · 0 / 窗口容量`；没有历史时不显示假趋势和假增量。窄屏先缩短天气与次要说明，优先保留模型、推理、权限；被省略的完整信息应通过键盘可达的状态视图查看，不能只依靠鼠标悬停。

计划审批和回退使用就地确认行，明确当前动作与影响，默认取消；工具详情就地展开。直接拖选任意正文复制继续保留，顶栏不重新加入复制提示。

## 实施顺序与验收

1. 将模型、设置、计划确认／编辑、回退、正文快照迁入主布局，帮助改为主内容视图；消除实际打开的弹层路径。
2. 将欢迎与连接配置统一到共享主题和尺寸预算，固定输入、操作和提示区域。
3. 修复列表选中配色、浅色标题、表单错误焦点、模型保存范围与逐层返回。
4. 缓存历史渲染，限制动画更新范围。

验收应覆盖 213×56、100×32、80×24、60×18，暗／浅／无颜色／ASCII，零／两个／14 个模型，空会话／恢复会话／长对话。所有入口均无遮罩、无浮动窗口；活动表单的动作与错误始终可达，历史滚动和输入草稿保留；取消无配置写入；写入失败可就地修正；模型变更在请求队列边界生效。

已有 46 项帮助、审批、欢迎、运行设置、动画和输入信息测试通过；补充执行的 10 项模型 TUI、命令与偏好测试也通过。它们验证了现有功能路径，并未覆盖上述全部视觉裁切和零弹窗要求。新增的审查探针复现了这些缺口。

离线探针：[基本场景](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/ui_ux_audit_probe.py)、[焦点与性能](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/ui_ux_extra_probe.py)、[欢迎与模型交互](/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07/interaction_probe.py)。运行时需设置 `PYTHONPATH=.` 并使用项目 `.venv/bin/python`。

当前值得保留的行为是：工具调用可折叠、审批留在会话中、状态说明带真实数据、欢迎步骤返回保留草稿、取消不提前写入配置、模型切换保留同一会话。后续优化应保持这些行为。
