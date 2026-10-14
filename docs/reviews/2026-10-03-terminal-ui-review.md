# 终端界面优化与验证

按已确认的 Claude Code 风格方案实现：对话采用 `❯` 用户标识与 `●` 模型标识，正文左对齐，工具执行与对话按顺序展示。Textual、inline 和 plain 使用同一套工具记录格式。

## 已实现

- 工具开始即显示动作与关键参数，完成后立即写入历史，保留耗时、命令退出码、失败或超时状态。
- 命令显示简短输出，文件修改显示 diff 摘要；点击记录或 `/tools [序号]` 展开可读详情。
- 详情包含命令、工作目录、结果和保留的输出；命令日志最多保留 6,000 字符，截断时明确提示。
- 普通模式减少步骤日志，详细模式保留步骤、Token 与完成统计；固定输入框及会话用量。
- 窄屏重新换行已有消息，跟随最新消息并保留主动向上滚动的位置；主题切换更新已有文本颜色，plain 保持 ASCII 标识。
- 完整输出模型消息后才显示工具开始，避免流式脱敏缓冲造成回复尾部丢失；任务完成时保留详情弹窗的键盘焦点。

## 验证结果

本地测试使用模拟模型，不调用远程 API。

```bash
PYTHONPATH=tests .venv/bin/python -m unittest \
  test_execution_display test_dashboard_presentation test_tui test_ui \
  test_agent_service test_workflow_preferences test_workflow_ui -q
```

130 项通过，包含执行显示的 14 项回归用例。源码编译检查及 `main.py --help` 通过。独立界面审查提出的流式消息顺序、弹窗焦点、缩窗滚动与 plain 标识问题均已修复并验证。

全量 `python -m unittest discover -s tests -q` 运行 474 项，472 项通过，剩余两项与界面修改无关，并使用修改前的界面和服务文件复现：

1. `test_doctor_without_site_packages_still_produces_json`：`python -S` 下，诊断模块导入记忆模块时需要 `yaml`，导致 JSON 输出前异常。
2. `test_build_tools_exports_the_registered_chat_tools_in_stable_order`：工具注册新增 `memory_list`、`memory_read`，原测试的固定工具列表尚未包含它们。

验证期间工作区还有记忆与上下文模块的并行改动；上述结果记录本次运行状态。未验证远程模型响应或其他操作系统终端。

## 预览

预览由真实 Textual 渲染器和本地演示事件生成，命令输出为演示数据。

- [完整界面](../previews/terminal-ui.svg)
- [窄屏界面](../previews/terminal-ui-compact.svg)
- [工具执行中](../previews/tool-running.svg)
- [工具详情](../previews/tool-details.svg)
- [浅色主题](../previews/terminal-ui-light.svg)

启动：在项目目录运行 `.venv/bin/python main.py --tui`。
