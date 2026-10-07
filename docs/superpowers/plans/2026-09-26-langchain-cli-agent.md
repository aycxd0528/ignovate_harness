# LangChain 本地 CLI Agent 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有奶龙 Agent 扩展成一个使用 LangChain、能安全读取和修改本地项目文件的 CLI 编码 Agent。

**Architecture:** 用 `ChatDeepSeek` 连接现有 DeepSeek 配置，`create_agent` 管理工具调用循环，`@tool` 暴露本地文件和命令工具。`HumanInTheLoopMiddleware` 在文件写入和 shell 命令前暂停，CLI 展示操作并收集决定，再通过 `InMemorySaver` 和同一个 `thread_id` 恢复执行。

**Tech Stack:** Python 3.11、`langchain`、`langchain-deepseek`、`langgraph`、`python-dotenv`。

**Spec:** `docs/superpowers/specs/2026-09-26-local-cli-agent-design.md`

## Global Constraints

- 文件工具路径必须解析并限制在项目根目录内；拒绝访问 `.env`、`.git`、`.venv` 等内部目录。
- 写文件和运行命令前必须由 `HumanInTheLoopMiddleware` 请求用户批准或拒绝。
- 命令以项目根目录为当前工作目录运行，设置超时、终止命令进程组并以有界内存读取输出；返回值标明输出是否截断。批准不构成操作系统级沙箱。
- 会话由 `InMemorySaver` 保存在当前进程内存中，不做跨进程持久化。
- API 密钥只从 `.env` 读取；配置检查、日志、README 和示例文件都不显示真实密钥。
- DeepSeek 模型必须支持 tool calling；不支持时提示用户更换模型。
- 保留现有 `tool_demo.py` 和 `check_config.py` 作为独立学习示例，不删除用户已有文件。
- 保留工作区现有暂存内容；不读取 `.env`，不将既有暂存文件纳入本次提交。

## Review Focus

- 项目外相对路径、绝对路径和越界符号链接：`local_tools.py` 必须按解析后的目标路径拒绝访问。
- 项目根目录或子目录中的 `.env`、`.git`、`.venv`：所有文件工具均应拒绝或跳过。
- 多个待审批调用及拒绝决定：CLI 必须按中断请求顺序逐项收集决定，拒绝时不执行对应工具。
- 命令超时、非零退出和超长输出：返回结构化状态，输出不超过 12,000 个字符。
- 缺少环境变量、错误 base URL 或不支持 tool calling 的模型：给出可操作的错误提示，不输出密钥。

---

### Task 1: 配置和依赖

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `config.py`

**Interfaces:**
- Produces: `Settings` dataclass with `api_key: str`, `api_base: str`, `model: str`, `project_root: Path`.
- Produces: `load_settings() -> Settings`, which loads `.env` relative to `config.py` and rejects missing values without printing the key.

- [x] **Step 1: Define supported package floors** in `requirements.txt`: `langchain>=1.0,<2.0`, `langgraph>=1.0,<2.0`, `langchain-deepseek>=1.1.1,<2.0`, and `python-dotenv>=1.0,<2.0`.
- [x] **Step 2: Add `.env.example`** with empty `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL=https://api.deepseek.com`, and `DEEPSEEK_MODEL` placeholders; never copy the existing `.env` value.
- [x] **Step 3: Implement `load_settings() -> Settings`** in `config.py`. Read `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`; resolve `project_root` from `Path(__file__).resolve().parent`; raise a concise configuration error naming missing variable names only.

### Task 2: Workspace-bounded local operations

**Files:**
- Modify: `local_tools.py`

**Interfaces:**
- Produces: `resolve_project_path(path: str, allow_missing: bool = False) -> Path`.
- Produces: `list_files(path: str = ".", limit: int = 100) -> dict`.
- Produces: `read_file(path: str, max_chars: int = 12000) -> dict`.
- Produces: `search_text(query: str, path: str = ".", limit: int = 50) -> dict`.
- Produces: `write_file(path: str, content: str) -> dict`.
- Produces: `run_command(command: str, timeout_seconds: int = 30) -> dict`.

- [x] **Step 1: Add shared path validation** using `Path.resolve()` and `Path.is_relative_to(PROJECT_ROOT)`. Reject paths resolving outside the root, resolved symlink escapes, and protected segments case-insensitively (including `.env` files).
- [x] **Step 2: Implement read-only tools**. Walk files without descending into protected directories; limit listing/search results; read UTF-8 text with a 12,000-character maximum; return dictionaries with `ok`, `content` or `error` fields.
- [x] **Step 3: Implement `write_file()`**. Validate the destination with `allow_missing=True`, create parent directories only inside the root, write UTF-8 content, and return the resolved project-relative path or a structured error.
- [x] **Step 4: Implement `run_command()`** with `shell=True` and `cwd=PROJECT_ROOT`; enforce a 30-second process-group timeout and stream combined output into a bounded buffer, returning exit status, timeout state, and an output-truncated flag. Do not claim this is a sandbox.

### Task 3: LangChain tool declarations

**Files:**
- Create: `tools.py`
- Consume: `local_tools.py`

**Interfaces:**
- Produces: `build_tools(api_key: str = "") -> list[BaseTool]`, which redacts the configured API key from tool results.
- Tool names: `list_files`, `read_file`, `search_text`, `write_file`, `run_command`.
- Mutating tool names must exactly match the names in the approval middleware configuration.

- [x] **Step 1: Wrap each local operation with `@tool`** and provide a docstring that explains purpose, arguments, root-directory restriction, output limits, or side effects.
- [x] **Step 2: Return serializable tool results** by JSON-encoding each local operation dictionary with `ensure_ascii=False`, redacting the configured API key before the result reaches the model.
- [x] **Step 3: Export the five tools in a stable list** from `build_tools()`; do not let model-supplied names select arbitrary Python functions.

### Task 4: Construct LangChain Agent and approval checkpoint

**Files:**
- Create: `agent.py`
- Consume: `config.py`, `tools.py`

**Interfaces:**
- Produces: `create_agent_runtime(settings: Settings) -> CompiledStateGraph`.
- Produces: a single `InMemorySaver` checkpointer owned by the runtime.
- Approval policy: `write_file` and `run_command` allow only `approve` and `reject`; read-only tools do not interrupt.

- [x] **Step 1: Construct `ChatDeepSeek`** with `model=settings.model`, `api_key=settings.api_key`, `base_url=settings.api_base`, `timeout=30`, and `max_retries=2`.
- [x] **Step 2: Configure `HumanInTheLoopMiddleware`** for `write_file` and `run_command`, with a clear approval description prefix.
- [x] **Step 3: Call `create_agent()`** with the model, `build_tools(api_key=settings.api_key)`, a concise system prompt, middleware, and `InMemorySaver()` checkpointer.
- [x] **Step 4: Keep a cumulative 40-step LangGraph budget across all pauses/resumes in one user turn**; convert limit errors into a user-readable stop message and start a fresh thread for the next turn.

### Task 5: Integrate the CLI and resumable approvals

**Files:**
- Modify: `main.py`
- Consume: `agent.py`, `config.py`

**Interfaces:**
- Produces: `run_cli() -> None`.
- Turn config: `{"configurable": {"thread_id": str}, "recursion_limit": 40}`.
- Produces: `run_turn(agent: CompiledStateGraph, message: str, config: dict) -> str`.

- [x] **Step 1: Keep the existing REPL commands** `/help`, `/exit`, `/about`, `/count`, `/history`; add `/clear`, which assigns a new UUID thread id.
- [x] **Step 2: Invoke the agent** with one `HumanMessage` and `version="v2"`; display final assistant content from `GraphOutput.value`.
- [x] **Step 3: Handle interrupts** by reading each interrupt's `action_requests` in order, printing tool name and full arguments, collecting one approve/reject decision per action, and resuming with `Command(resume={"decisions": decisions})` using the same config/thread id.
- [x] **Step 4: Handle repeated interrupts until completion**; count one successful user turn only after a final answer is returned.
- [x] **Step 5: Implement `/history`** from `agent.get_state(config).values["messages"]`, showing message roles and readable text without exposing configuration secrets.
- [x] **Step 6: Catch model/network/configuration errors** at the REPL boundary, show a concise error, and continue accepting user input when the process remains usable.

### Task 6: User-facing setup and limitations

**Files:**
- Create: `README.md`
- Consume: final CLI behavior and `.env.example`

- [x] **Step 1: Document setup**: create/activate the virtual environment, install `requirements.txt`, copy `.env.example` to `.env`, fill in the user's own DeepSeek values, and start with `python main.py`.
- [x] **Step 2: Document architecture**: `main.py` → `agent.py` → LangChain Agent → `tools.py` → `local_tools.py`.
- [x] **Step 3: Document safety and boundaries**: approval flow, project-root file limits, command execution limitations, in-memory-only sessions, and supported tool-calling model requirement.
- [x] **Step 4: Link the Feishu study document** and retain `tool_demo.py` as the lower-level tool-calling example.
