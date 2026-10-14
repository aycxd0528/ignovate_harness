# 常驻天气与模型信息分列验收

用户要求 Token Weather 常驻输入框上方左侧，模型、推理强度和权限靠右，并已选择空状态 `☀ Clear · 0% · 0 / 窗口`。

实现结果：

- 两侧保持同一行。左侧占剩余空间，右侧显示实际模型、实际推理参数及权限模式，如 `deepseek-flash · 默认 · 需确认`。
- 空会话和 `/clear` 后显示 `☀ Clear · 0% · 0 / 1m`，不创建虚假的模型用量样本或历史趋势。首轮调用后按实际数据更新；缺失用量仍显示未知。
- 右侧保留参数原名，如 `high`、`max`；完全访问模式使用用户要求的“完全访问”标签。不会为了展示示例而改变推理参数或权限。
- 右侧悬停详情保留完整模型名称、推理配置、权限模式、会话轮数及累计 Token；左侧悬停可查看未缩短的天气详情。
- 宽度不足时优先缩短模型名称，再由天气既有逻辑简化历史、增量等字段。213×56、100×32、80×24 验证两侧对齐；长模型名称及未知用量另外覆盖 50×18。

## 设置入口核对

当前项目的 `Settings`、偏好配置及模型工厂没有推理强度设置入口，模型调用没有显式传入 `reasoning_effort`。当前显示“默认”，不能通过现有 `/config` 设置 `max`。

权限通过启动参数选择，参数互斥，仅本次启动生效：

| 状态 | 启动方式 |
| --- | --- |
| 需确认 | `python main.py --permission-mode default`，或普通启动 |
| 自动编辑 | `python main.py --permission-mode acceptEdits`，文件编辑自动批准，命令仍审批 |
| 计划只读 | `python main.py --permission-mode plan` |
| 完全访问 | `python main.py --dangerously-skip-permissions`，跳过权限规则与逐项审批 |

`/permissions` 当前用于查看项目权限规则。

## 实际验证

先更新现有布局验收测试并确认三项失败对应缺失空状态、旧标签与旧位置，再实现调整。

```sh
PYTHONPATH=tests .venv/bin/python -m unittest test_composer_info test_activity_spinner test_tui_readability test_tui_copy test_execution_display test_workflow_closures test_model_configuration test_token_weather test_tui test_ui_conversation test_dashboard_presentation test_ui -q
```

171 项通过，39.687 秒。另在移除 `NO_COLOR` 的环境运行 `test_composer_info test_token_weather`，15 项通过，1.880 秒。

真实 Textual 渲染了六张预览，彩色空会话与窄屏活动状态已视觉检查；未调用模型 API，未修改运行时或 Git 索引/HEAD。

| 状态 | 213×56 | 100×32 | 80×24 |
| --- | --- | --- | --- |
| 空会话 | [SVG](../previews/composer-split-empty-213x56.svg) | [SVG](../previews/composer-split-empty-100x32.svg) | [SVG](../previews/composer-split-empty-80x24.svg) |
| 实际用量示例 | [SVG](../previews/composer-split-active-213x56.svg) | [SVG](../previews/composer-split-active-100x32.svg) | [SVG](../previews/composer-split-active-80x24.svg) |
