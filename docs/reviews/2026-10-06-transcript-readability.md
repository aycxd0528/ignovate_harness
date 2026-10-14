# 正文选择与界面可读性验收

已实现用户本窗口提出的四项修正：

- 正文直接鼠标拖选任意文字，蓝色选区高亮，Ctrl+C 复制、Esc 取消。选区存在时 Ctrl+C 不会停止任务或清空输入；后台模型继续执行，正文重绘延后至取消选区，避免复制期间内容移动。顶部一键复制按钮已移除。F7 的只读 Markdown 原文快照仅为备用入口。
- `/model` 显示底部紧凑列表，薄分隔线，无四周大边框，当前项绿色高亮。列表按内容高度显示，超过 8 行可滚动。↑/↓、Enter、Esc 可直接操作；新增模型与验证逻辑保留。
- 工具详情大弹窗已删除。展开工具组后点击工具记录，在对话内展开命令、状态、退出码、结果和保留输出；再次点击可收起，Ctrl+O 保留整组折叠。流式回复仍原位更新，未重复生成正文。
- `Token …+?` 改为 `Token …（用量不完整）`，历史缺失样本显示间隔点，不能计算的上一轮增量显示 `增量待统计`。已知用量与未知部分保持区分，不伪造零值。

## 实际验证

```sh
PYTHONPATH=tests .venv/bin/python -m unittest test_tui_readability test_tui_copy test_execution_display test_workflow_closures test_model_configuration test_token_weather test_tui test_ui_conversation test_dashboard_presentation test_ui -q
```

结果：165 项通过，42.309 秒。包括正文中英文选取及可见高亮、运行中复制不取消任务、生成期间选区稳定、取消后恢复最新正文、空白行、历史恢复与清空、Mac 复制失败/超时、内联详情与最终回复、模型切换/新增/校验、Token Weather 和窄屏重排。Mac pbcopy/OSC52 用模拟进程与捕获驱动输出验证，未写入实际系统剪贴板；未调用真实模型 API。

另外以移除 NO_COLOR 的环境运行正文选择和模型交互测试：9 项通过。生成真实 Textual 100×32、80×24 截图；彩色选择高亮、就绪圆点和模型当前项已视觉检查。渲染脚本使用临时项目与示例事件，不读取项目密钥。

| 预览 | 100×32 | 80×24 |
| --- | --- | --- |
| 正文直接拖选 | [SVG](../previews/readability-selection-100x32.svg) | [SVG](../previews/readability-selection-80x24.svg) |
| 模型列表 | [SVG](../previews/readability-model-100x32.svg) | [SVG](../previews/readability-model-80x24.svg) |
| 对话内工具详情 | [SVG](../previews/readability-details-100x32.svg) | [SVG](../previews/readability-details-80x24.svg) |

三条模型时，列表高度 3 行，整个面板高度 10 行；左右无边框。Token Weather 在两个尺寸中各占一行，底部坐标分别为 32/32、24/24。工具详情保持在日志中，内容超出视口时沿原对话滚动查看。

## 自审

代码审查子代理因额度限制退出，未产生审查结论。本窗口自行核对 Textual 8.2.8 的选择事件、字符坐标与单元格宽度、样式覆盖次序、后台重绘、点击元数据保留、剪贴板异常处理、弹窗关闭与历史清空；渲染检查发现并修复了选区颜色被 Rich 样式覆盖的问题，补充失败后通过的高亮测试。没有修改运行时、命令路由、上下文模块或 Git 索引/HEAD。

OSC52 无确认机制：原生 Mac 复制成功时显示成功消息；只有 OSC52 请求时显示“已发送复制请求”。本次未验证真实 Terminal/iTerm 的系统粘贴。
