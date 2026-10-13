# Token Weather 相对趋势修复

日期：2026-10-08。问题：两轮对话的上下文用量有变化，趋势高度却相同。

原绘制逻辑按完整窗口容量分成 8 档。1m 窗口下每档约 125k tokens，11.2k 和 20.3k 都落在最低档，最终显示 `▁▁`。采样记录已更新，问题在趋势图刻度。

现在仅将趋势图按最近 8 轮已知样本的最小／最大用量缩放。11.2k → 20.3k 显示 `▁█`；百分比仍为真实窗口占用 2%，增量仍为 +9.1k。趋势满格表示最近样本中的最高值，不表示窗口已满。

| 情况 | 趋势 | 数字反馈 |
| --- | --- | --- |
| 11.2k → 20.3k | `▁█` | Clear 2%，+9.1k |
| 20.3k → 11.2k | `█▁` | Clear 1%，-9.1k |
| 20.3k → 20.3k | `▁▁` | 增量 0 |
| 11.2k → 缺失 → 20.3k | `▁·█` | 保留空点，不计算跨缺失轮次增量 |

单个样本、相同样本和全零样本保持平线，避免除零或虚构变化。旧极值移出最近 8 轮后自动重新缩放。ASCII 与 NO_COLOR 使用同样的数据规则；窄屏仍按既有布局策略省略次要内容。

修改文件：[绘制逻辑](/Users/aycxd/Desktop/LLM_project/ui/token_weather.py:75)、[回归测试](/Users/aycxd/Desktop/LLM_project/tests/test_token_weather.py:58)、[使用说明](/Users/aycxd/Desktop/LLM_project/README.md:191)。没有更改上下文采样、会话累计费用或模型调用逻辑。

## 验证

新增测试先复现原问题，包括真实 Textual 控件的两轮实时更新，然后实施修复。

- 相关天气／输入状态／展示测试：46 项通过，3.844 秒。
- 完整项目离线套件：**1048 项通过，98.465 秒，退出码 0，OK**。
- `git diff --check` 通过。

```sh
env -u NO_COLOR PYTHONPATH=tests .venv/bin/python -m unittest \
  test_token_weather test_composer_info test_dashboard_presentation test_execution_display -q
env -u NO_COLOR .venv/bin/python -m unittest discover -s tests -v
```

日志：[修复前](/Users/aycxd/Desktop/LLM_project/docs/reviews/token-weather-trend-2026-10-08/red.log)、[相关回归](/Users/aycxd/Desktop/LLM_project/docs/reviews/token-weather-trend-2026-10-08/green.log)、[完整套件](/Users/aycxd/Desktop/LLM_project/docs/reviews/token-weather-trend-2026-10-08/full.log)。验证没有调用真实模型 API；Git 未暂存或提交。
