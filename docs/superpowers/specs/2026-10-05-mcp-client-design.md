# MCP 客户端接入设计

用户已确认设计并要求开始执行（2026-10-05）。目标：奶龙连接外部 MCP 服务，发现工具，在聊天中经过现有权限审批调用并展示真实结果。

## 范围与使用方式

- `/mcp` 或 `/mcp list` 显示已配置服务、传输、连接状态和工具数。
- `/mcp add <名称> --transport http <URL>` 配置 Streamable HTTP；`/mcp add <名称> --transport stdio -- <可执行文件> [参数...]` 配置本地服务。不运行 shell。
- `/mcp connect <名称>` 明确启动/连接服务并发现工具；`/mcp disconnect <名称>` 释放连接；`/mcp remove <名称>` 先断开再移除配置；`/mcp tools <名称>` 展示工具名称与说明。
- 配置位于当前项目 `.nailong/mcp.json`，使用 `mcpServers` 映射，原子写入、0600 权限，拒绝符号链接重定向和无效配置。配置只保存服务声明；每次启动需显式连接，启动程序不执行配置中的命令。
- 本地环境变量与 HTTP 请求头通过 `env` 和 `headers` 的 `${ENV_NAME}` 引用配置；不将 DeepSeek 密钥传给服务。凭据不写进配置、展示或工具日志。第一版不提供 OAuth 登录、旧 SSE、resources/prompts 管理或自动重放工具调用。

## 架构与边界

直接使用官方 MCP Python SDK 的 ClientSession、stdio 和 Streamable HTTP transport；依赖约束采用稳定 v1 API。配置存储、连接管理、工具适配和命令动作分别放入小模块。

每个服务由独立、长期运行的 asyncio 任务拥有 SDK 上下文；该任务负责进入和退出上下文，避免跨任务销毁 AnyIO cancel scope。请求通过有界队列发送，连接发现及调用都有超时。用户取消或请求超时后关闭该连接，结果标记未知副作用，不自动重试；断开、退出与项目切换等待连接任务清理。

动态工具命名 `mcp__<服务>__<工具>`（长名使用确定性摘要），保留服务来源、原始名字与 JSON Schema。仅注册到 chat；init、review、plan、subagent 不注册，计划权限模式拒绝 MCP 连接及调用。远端 readOnlyHint 不作为免审批依据。

注册名称最多 64 字符；非标准名称使用稳定摘要，发现注册名冲突时拒绝连接。权限规则按 MCP 工具名大小写精确匹配。连接时预先校验 JSON Schema 及模型工具格式转换；拒绝外部引用和当前适配层不能转换的本地锚点引用。

工具复用 ToolExecutionContext，默认 ASK，可按工具名授予会话权限。MCP 的 path 参数是服务业务参数，不使用本地文件路径边界，但所有原生文件工具边界继续有效。返回文本及 structuredContent；二进制正文省略并注明类型，避免 base64 占满上下文。失败、超时、取消明确展示；结果由现有归档层限长。

## 验收

配置拒绝无效 URL、命令、名称和符号链接；不覆盖无效 JSON。通过真实本地 stdio 和本地 HTTP 测试服务验证连接、分页工具发现、复杂参数调用、服务错误、拒绝审批无副作用、超时、取消、断开与重连。验证运行时注册及上下文报表一致，所有交互界面共享命令入口。最后运行项目 unittest 全套测试。
