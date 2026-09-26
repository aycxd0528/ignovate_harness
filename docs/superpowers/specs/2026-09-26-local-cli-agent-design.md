# 本地 CLI Agent 设计稿

## 目标

把当前的奶龙 Agent 原型扩展成一个可在本地项目目录中工作的编码 CLI。它使用 LangChain 的 DeepSeek 集成和 Agent runtime 与模型对话，能按需查看和搜索项目文件、提出文件修改或终端命令，并在每次写文件或执行命令前展示操作内容、等待用户确认。

项目用于大二学生的 Agent 应用实习准备，因此第一版使用 LangChain 直接构建，同时在学习文档中解释模型请求、工具调用、工具执行、结果回传、循环终止和本地权限边界。

## 现状

- `main.py` 已经有终端输入循环、历史记录和斜杠命令。
- `local_tools.py` 有受限的 `read_file` 示例。
- `tool_demo.py` 演示了单轮工具调用，但工具结果没有接入主 CLI 的持续对话循环。
- `.env` 中已有本地模型配置；程序不能打印或提交密钥。

## 第一版范围

### 包含

- 使用 `langchain-deepseek` 的 `ChatDeepSeek` 连接现有 DeepSeek 配置，使用 LangChain `create_agent` 管理 Agent 状态和工具调用循环，使用 `@tool` 定义本地工具。
- 用 LangGraph `InMemorySaver` 保存当前进程中的会话状态，并为需要审批的工具调用提供可暂停、可恢复的状态。
- 提供 REPL 和 `/help`、`/exit`、`/history`、`/clear`、`/count` 等命令。
- 注册 `list_files`、`read_file`、`search_text`、`write_file`、`run_command` 五个本地工具。
- `create_agent` 负责连续的模型与工具往返；设置合理的递归上限，避免 Agent 无限循环。学习文档仍解释 tool call、tool result 与 `tool_call_id` 的协议语义。
- 文件路径必须解析到项目根目录以内；拒绝访问密钥文件和 Git、虚拟环境等内部目录。
- 使用 `HumanInTheLoopMiddleware` 在写文件或运行命令前暂停 Agent；CLI 展示调用参数，用户逐项批准或拒绝，再恢复 Agent。读取工具不需要打断用户。
- 命令以项目根目录为当前工作目录运行，设置超时并截断过长输出。命令确认是用户审阅门槛，不构成操作系统级沙箱；获批的命令仍可能访问项目目录以外的资源。
- 会话通过 `InMemorySaver` 保存在当前进程内存中；提供 `/clear` 开始一个新会话，不做跨进程持久化。
- 补齐安装说明、环境变量示例和运行方式；任何示例配置都不含真实密钥。

### 不包含

- 自动执行写文件或终端命令；这些动作均需用户在终端逐次确认。
- 持久化数据库、Web 搜索、MCP、技能系统、子 Agent、后台守护进程、TUI、向量数据库和跨进程长期记忆。

## 架构与数据流

- `agent.py` 构造 `ChatDeepSeek`、LangChain `create_agent`、`HumanInTheLoopMiddleware` 与 `InMemorySaver`，并处理暂停审批和恢复执行。
- `tools.py` 用 `@tool` 定义工具及其参数说明；`local_tools.py` 负责路径校验和本地文件/命令操作，工具结果保持结构化。
- `main.py` 负责启动、REPL、命令解析、会话 thread_id，以及将待审批的工具调用逐项展示给用户。
- `config.py` 读取 `.env` 并检查必需配置，启动时只报告配置是否完整，不显示密钥。

一次工具调用按以下顺序运行：CLI 把用户消息和会话 thread_id 传给 `create_agent` → LangChain 调用 `ChatDeepSeek` → Agent runtime 识别工具请求并分发给本地工具 → 读取工具直接执行；写文件或运行命令触发 HITL 中断 → CLI 展示每项待审批操作并收集批准/拒绝 → 使用同一 thread_id 恢复 → Agent runtime 回传工具结果并继续，直到给出最终回答或达到递归上限。底层仍遵守 assistant tool call 与对应 tool result 配对的协议，但循环由 LangChain 管理。

## 错误处理与安全边界

- LangChain 参数校验、未知工具、路径不存在、用户拒绝、命令超时和模型网络错误都要转成清晰可处理的结果；可恢复错误交给 Agent 解释，网络错误提示用户后可继续。
- 通过规范化后的绝对路径做项目根目录检查，不能只检查字符串是否包含 `..`。
- 对被禁止的路径、用户拒绝的写操作/命令和非零退出码给出清晰结果。
- API 密钥只从本地 `.env` 读取；日志、对话历史和工具输出不主动读取 `.env`。
- 本地文件工具受项目根目录限制；终端命令仍以用户确认作为主要保护，界面必须把完整命令和工作目录显示清楚。
- DeepSeek 配置必须使用支持 tool calling 的模型；若所选模型不支持，CLI 应提示更换模型。官方集成文档指出 DeepSeek-R1 reasoner 不支持工具调用。

## 验收方式

- 启动后可正常使用斜杠命令并通过 `InMemorySaver` 保留当前会话历史。
- CLI 能通过 LangChain Agent 完成多轮工具调用；对多个待审批操作逐项呈现并按决策恢复。
- 在项目目录内读取/搜索文件成功；越界路径、秘密文件和不存在文件得到明确拒绝或错误。
- 用户拒绝写入或命令执行时，底层操作没有发生；确认后的文件写入仍限制在项目目录。命令以项目目录为 cwd 执行并返回结果，但不是 OS 沙箱，README 会说明获批命令可能访问其它路径。
- README 说明安装、配置、运行、权限确认和已知限制。
- 飞书目标文档正文在现有项目模板前插入原评论卡片的知识点和面试题；保留模板、表格/多维表格资源和评论卡片。

## 官方参考

- [LangChain Agent 概览](https://docs.langchain.com/oss/python/langchain/overview)
- [LangChain DeepSeek 集成](https://docs.langchain.com/oss/python/integrations/chat/deepseek)
- [LangChain Human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)
