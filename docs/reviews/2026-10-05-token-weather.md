# Token Weather 验收记录

日期：2026-10-05。按照用户提供的 Clear、Showers、Storm 截图增加终端状态栏。

## 显示与数据含义

- Textual 输入区下方增加一行 Token Weather；累计 Token 和会话轮数保留在上一行。天气行不会写入聊天记录。
- inline 滚动终端的底部工具栏增加同样的文字天气行。
- 上下文占用使用主模型最近一次真实 `input_tokens`，分母来自当前模型的配置窗口；累计调用用量继续单独统计。
- 一个可见用户轮次只保留一个样本，轮内多次模型调用更新同一样本。最近 8 轮组成趋势条，本轮增量与上一轮的最终样本比较。
- 子 Agent、已回退轮次的计费用量不改变主对话天气。隐藏的自动目标轮次更新当前样本，不增加用户轮次。
- 上下文缩小后显示向下箭头及负增量。缺少真实用量、仅有估算或未知容量时明确显示未知；未知的上一轮不会被当作零计算增量。
- 百分比按整数显示，天气按该显示值划分：Clear < 30%，Cloudy 30–49%，Showers 50–79%，Storm ≥ 80%。超过容量时保留实际百分比。
- 深色和浅色主题使用不同的天气颜色；支持 ANSI、ASCII 和 NO_COLOR。窄屏按顺序省略说明、趋势或增量，以保留天气和占用。

截图中的数值已复现：

| 天气 | 上下文 | 本轮增量 |
| --- | --- | --- |
| Clear 10% | 19.2k / 200k | +19.2k |
| Showers 54% | 107.3k / 200k | +88.1k |
| Storm 81% | 161.1k / 200k | +26.7k |

未知模型可通过项目 `.nailong/settings.json` 中的 `context_windows` 配置容量。例如 `{"context_windows": {"my-model": 200000}}`。预览使用临时项目和 200k 窗口，不修改实际项目配置。

## 实现位置

- `ui/token_weather.py`：天气、趋势、增量及单行宽度适配。
- `ui/presentation.py`：用户轮次上下文采样和恢复。
- `tui.py`：固定状态栏及实时、会话、主题更新；缩放时使用 Resize 消息携带的新宽度。
- `ui/app.py`：inline 底栏显示。
- `ui/transcript.py`：保持连续布局更新期间的末尾跟随，保留阅读旧消息时的滚动位置。

## 验证

11 项专用测试覆盖截图数值、天气边界、超容量、轮内多次调用、子 Agent、回退、缺失与估算用量、历史上限、负增量、ASCII、NO_COLOR、主题、窄屏以及会话恢复和清空。

最终相关回归：

```sh
PYTHONPATH=tests .venv/bin/python -m unittest \
  test_token_weather test_ui test_tui test_workflow_accounting \
  test_execution_display test_ui_conversation test_dashboard_presentation -q
```

结果：**120 项通过，24.282 秒，无失败和错误**。修改的 Python 文件编译检查通过。

扩展回归最初发现旧测试仍期望英文调用统计、已移除的 `#live-response`、以及独立的工具进度区。测试已改为验证当前中文统计和聊天记录中实际可见的流式内容、工具进度，保留原有功能断言。

新增底栏在窄屏会触发连续布局更新，曾导致延迟滚动完成前失去末尾跟随；已修复，并通过原有长对话缩放测试，包括阅读旧消息时不强制跳到末尾。

独立只读审查未发现阻塞问题。验证使用本地假服务和临时配置，没有调用模型 API。

## 预览

- [Clear](../previews/token-weather-clear.svg)
- [Showers，深色 120×26](../previews/token-weather-showers.svg)
- [Storm](../previews/token-weather-storm.svg)
- [窄屏 50×18](../previews/token-weather-narrow.svg)
- [浅色 120×26](../previews/token-weather-light.svg)

预览来自实际 Textual 测试布局；仅校正 Rich SVG 导出的中文字符宽度，未修改终端渲染。深色、浅色和窄屏图片均已检查。
