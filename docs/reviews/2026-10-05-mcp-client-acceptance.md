# MCP 客户端验收记录

日期：2026-10-05。用户已确认接入外部 MCP 服务的设计，并授权实施。

## 交付行为

- `/mcp`、`list`、`add`、`connect`、`tools`、`disconnect`、`remove` 复用三个交互界面的命令入口，不发送模型请求。
- 支持本地 stdio 和远程 Streamable HTTP；服务配置保存到项目 `.nailong/mcp.json`，启动后需明确连接。
- 已连接工具动态注册到 chat，复用现有审批、执行及结果归档。readOnlyHint 不免审批，MCP 授权按工具名大小写精确匹配。
- env/headers 仅使用环境变量引用；模型密钥不传给服务；凭据从说明、Schema 及结果的键和值中脱敏。
- 超时、取消、退出、断开及项目切换等待连接清理。进行中操作结果可能未知，不自动重试；旧工具不能调用重连后的服务。
- 连接时验证原始参数 Schema 和实际 LangChain 工具格式转换；拒绝不支持的引用、分页循环、重复工具名及注册名称冲突。

## 验证结果

项目环境：`.venv/bin/python`，Python 3.11；实际安装 MCP SDK 1.30.0、jsonschema 4.26.0。依赖范围已写入 requirements.txt。

| 检查 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m unittest discover -s tests -p 'test_mcp*.py'` | 32 项通过，8.680 秒 |
| `.venv/bin/python -m unittest discover -s tests -p 'test_permissions.py'` | 17 项通过 |
| `.venv/bin/python -m unittest discover -s tests` | 791 项，1 项失败、2 项错误，72.322 秒；均出现在实施前基线 |
| `.venv/bin/python -m pip check` | No broken requirements found |
| MCP 模块及测试的 compileall | 通过 |
| 真实 plain CLI 子进程 | add → connect → tools → disconnect → remove → list → exit，通过，退出码 0 |

专项测试使用真实本地 stdio 进程和 localhost HTTP 服务，验证分页发现、复杂参数、服务错误、审批拒绝无副作用、参数名与框架内部字段隔离、超时/取消、重连和进程清理。完整 AgentService 回合使用离线假模型，验证选择工具 → 审批中断 → 恢复 → MCP 调用 → 工具结果进入历史 → 最终回复。没有调用真实模型 API，也没有连接用户的第三方服务。

完整测试日志位于 `/tmp/nailong-mcp-complete-suite.log`，专项日志位于 `/tmp/nailong-mcp-feature-verified.log`。实施前基线为 758 项、9 项失败、5 项错误，日志位于 `/tmp/nailong-mcp-baseline.log`。工作区同时包含其他功能的更改，因此以基线中失败的测试名称核对，不将其他功能的修复计为本次成果。

本次全量测试剩余的三项原有问题：

1. `test_agent_service.AgentServiceTests.test_graph_step_budget_covers_all_approval_resumes`：预算测试假运行时耗尽输出，报 coroutine raised StopIteration。
2. `test_headless.HeadlessTests.test_json_format_contains_result_usage_tools_session_and_redacts_secret`：缺少旧测试期望的 max_graph_steps 参数。
3. `test_cli.CliTests.test_graph_step_budget_is_shared_across_approval_resumes`：预算上限错误文本与旧断言不符。

## 审查与边界

独立只读审查发现 Textual 项目切换未清理连接、名称映射碰撞、本地锚点 Schema 转换失败、JSON 键未脱敏四个问题。实现均已修复并补充回归测试；审查者复核后确认这四处无剩余缺陷。

首版不提供 OAuth 登录、旧 SSE、resources/prompts 管理或二进制正文展示。真实第三方服务和认证环境尚未验收。使用说明及环境引用示例见 README 的“外部 MCP 服务”部分。

本次没有暂存、提交或清理 Git 工作区；保留已有的暂存与未跟踪项目文件。
