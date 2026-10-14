# 输入栏状态行与等待动画验收

按本窗口用户要求完成四项调整：

- 移除顶部“拖选复制 · Ctrl+C”提示文字，正文任意文字的拖选、复制能力保留。
- 模型、推理强度、权限状态、会话轮数、Token 用量及天气集中在输入栏上方的一行。窄屏逐步简化标签和数值、缩短模型名称；悬停可查看完整模型和配置状态。
- 新建空会话及 `/clear` 后隐藏天气，不显示“Token Weather”或“待采样”占位。开始对话后的未知用量仍如实显示，不用零值代替。
- 用户已选择字符旋转方案。推理、工具调用和命令执行时使用 `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`，每 0.12 秒刷新当前状态，显示基于单调时钟的真实耗时；ASCII 模式使用 `| / - \\`。完成工具记录沿用成功/失败标记；审批、暂停、错误、就绪及界面卸载时停止定时器。动画不追加对话记录。

推理强度读取实际运行模型的 `reasoning_effort` 或 `model_kwargs.reasoning_effort`。当前 DeepSeek 工厂没有显式配置该参数，显示“默认”；显示调整没有添加模型参数或改变权限规则。

## 验证

```sh
PYTHONPATH=tests .venv/bin/python -m unittest test_composer_info test_activity_spinner test_tui_readability test_tui_copy test_execution_display test_workflow_closures test_model_configuration test_token_weather test_tui test_ui_conversation test_dashboard_presentation test_ui -q
```

169 项通过，39.292 秒。涵盖单行布局、空会话和清空、实际推理强度与权限、天气、原位活动刷新、计时、停止状态、正文选区稳定性、模型交互、工具记录和会话工作流。

清理未使用导入与测试表达式后，另运行彩色显示验证：

```sh
env -u NO_COLOR PYTHONPATH=tests .venv/bin/python -m unittest test_composer_info test_activity_spinner test_tui_readability test_token_weather -q
```

22 项通过，6.776 秒。额外通过真实 Textual 定时器验证推理和命令动画持续推进、保留记录数不变、完成后定时器停止；无模型 API 调用。测试和渲染均使用临时项目与示例事件。

## 预览与自审

| 状态 | 213×56 | 100×32 | 80×24 |
| --- | --- | --- | --- |
| 空会话 | [SVG](../previews/composer-info-empty-213x56.svg) | [SVG](../previews/composer-info-empty-100x32.svg) | [SVG](../previews/composer-info-empty-80x24.svg) |
| 对话及天气 | [SVG](../previews/composer-info-active-213x56.svg) | [SVG](../previews/composer-info-active-100x32.svg) | [SVG](../previews/composer-info-active-80x24.svg) |
| 执行中的动画帧 | [SVG](../previews/composer-info-spinner-213x56.svg) | [SVG](../previews/composer-info-spinner-100x32.svg) | [SVG](../previews/composer-info-spinner-80x24.svg) |

三个尺寸均验证信息行位于输入框上方、单行高度、天气不越界；彩色空会话、窄屏天气和执行动画帧已视觉检查。原生选区存在时继续沿用延后正文重绘机制，避免动画使选择内容移动。未修改运行时、模型调用参数或 Git 索引/HEAD。
