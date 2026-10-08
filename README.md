# ignovate harness v1.0.1：本地 CLI 编码助手

[项目说明](docs/PROJECT.md) · [2026-10-08 审查与修复记录](docs/reviews/2026-10-08-project-audit.md) · [飞书项目说明](https://qcnvafay57br.feishu.cn/wiki/Sj4NwUflgiALgnkrRCMc6FxRn8b)

## 欢迎与模型配置

首次在交互终端运行 `ignovate`，会显示左对齐的品牌欢迎页。Textual 引导分为“连接模型 → 推理强度 → 本次权限”三步：Enter 前进、方向键选择，Esc 返回上一步，Ctrl+C 退出。模型连接页填写 API 地址、模型 ID 和隐藏的 API Key；已有密钥留空保留。最后确认权限后才保存并进入会话，中途退出不写配置。可随时重新打开指引：

```bash
ignovate --setup
```

模型连接保存在用户目录 `~/.ignovate/config.json`，文件权限为 0600，优先于原有 `.env`，不进入项目或会话日志。API Key 使用隐藏输入；此文件是本地明文连接配置。`IGNOVATE_CONFIG_DIR` 可指定配置目录。推理偏好仍按用户/项目/本地/CLI/会话覆盖顺序生效。无头模式与非交互终端不弹欢迎，缺失配置时会给出配置指引。

产品名称已更改，现有 `nailong` 命令、Python 包、`.nailong` 项目目录和会话数据继续兼容。

## 推理与权限设置

```text
/reasoning
/reasoning low
/reasoning max --global
/permissions
/permissions request
/permissions assist
/permissions full
/permissions rules
```

| 设置 | 行为 |
| --- | --- |
| 请求批准 | 文件修改和命令按现有审批规则处理 |
| 帮我批准 | 项目内文件修改自动批准，命令执行仍需批准 |
| 完全访问权限 | 任意文件路径与网络目标，跳过逐项审批；仍受系统权限和网络环境约束 |

权限只对本次进程生效，不写入配置。计划、审查及子代理的只读限制继续生效。推理档位为模型默认、关闭、低、高、最高，分别对应 `default/none/low/high/max`；已知 DeepSeek V4 模型使用真实的 `reasoning_effort` 和 thinking 参数，未知模型只提供默认档位。参数依据 [DeepSeek 官方文档](https://api-docs.deepseek.com/guides/thinking_mode/)，推理强度不改变工具调用次数上限。

启动时也可以指定：

```bash
ignovate --reasoning-effort high --permission-mode acceptEdits
ignovate --reasoning-effort max --dangerously-skip-permissions
```

这是一个用 Python、LangChain、LangGraph 和 DeepSeek 搭建的本地命令行编码助手。支持全屏的终端默认启动 Textual 仪表盘；其他交互终端使用保留滚动历史的 inline 界面；PyCharm 等非 TTY 控制台使用纯文本模式。默认情况下文件写入和命令执行需用户审批。

## 环境准备

推荐从 [GitHub Release](https://github.com/aycxd0528/ignovate_harness/releases/latest) 下载安装包：macOS / Linux 使用 `*-unix.tar.gz`，Windows 使用 `*-windows.zip`（通过 WSL2 运行）。解压后运行 `sh install.sh` 或 PowerShell 的 `install.ps1`，重新打开终端，然后输入：

```sh
ignovate set up
```

命令会自动检测或下载 Python、隔离环境、固定版本的应用依赖和 ripgrep，然后进入模型配置向导。无需预装 Python 或手动激活虚拟环境；`ignovate set up --environment-only` 只准备环境。Windows 缺少 WSL / Ubuntu 时自动启动安装，系统要求重启时重启后运行同一个安装脚本续装。详细步骤与安装目录见 [Release 安装说明](docs/INSTALL.md)。

从源码安装：

需要 Python 3.11 或更新的 Python 3 版本。macOS / Linux 下在项目根目录运行：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

首次交互启动可直接使用配置指引。若继续使用 `.env`，复制环境变量模板：

```bash
cp .env.example .env
```

编辑 `.env`，填入自己的 DeepSeek 配置：

```dotenv
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

如果项目中已经有 `.env`，请保留现有文件并检查变量是否齐全，不要用模板覆盖它。`.env` 不要提交到 Git，也不要把密钥贴到聊天、截图或代码示例中。模型必须支持工具调用；如果请求提示模型不支持工具调用，请换成 DeepSeek 提供的工具调用模型。

下面的 `python main.py` 写法都要求先进入 ignovate harness 的目录（不是被分析的项目目录），并激活本项目的虚拟环境。默认按终端能力自动选择界面：

```bash
python main.py
```

首次演示时，在提示符输入“列出项目根目录的文件，并指出主要源码入口”；这是只读请求，不会修改仓库。

无界面处理一条提示后退出，可选择纯文本、单个 JSON 对象或逐行 JSON 事件：

```bash
python main.py --print "检查当前项目的结构和测试方式"
python main.py -p "总结未提交的改动" --output-format json
python main.py -p "检查核心模块" --output-format stream-json --max-turns 12
python main.py -p "修复并运行测试" --goal --goal-max-rounds 8 --goal-max-cost-usd 0.35 --output-format json
```

`--max-turns` 限制本轮主 Agent 的模型调用次数，范围为 1–40，默认 40。最后一次调用会禁用工具、汇总已有证据；未完成的操作和验证会明确标出。任务检查、上下文处理、响应脱敏和工具执行属于内部步骤，另有 256 步保护，不计作模型调用。审批后继续执行仍共享本轮预算，子代理保留独立的受限预算。`--goal` 将提示作为持久目标运行，目标上限可由 `--goal-max-rounds` 和 `--goal-max-cost-usd` 设置；费用保护需要当前模型单价可查。无界面模式不启动终端界面，普通操作待审批时默认拒绝；目标模式会保存为暂停状态，可在交互模式恢复并处理审批。完成为退出码 0，模型或工具错误为 1，步骤或目标预算上限为 2，配置错误为 3。JSON 结果含会话 ID、回答、token 用量、工具摘要、审批信息和目标状态。

要强制使用 Textual 全屏仪表盘，请运行：

```bash
python main.py --tui
```

也可以显式选择 `--ui auto|textual|inline|plain`。`--plain` 和 `--tui` 分别是纯文本与 Textual 全屏模式的快捷参数。在 IDE 控制台中需要稳定交互时可直接使用 `python main.py --plain`。

启动纯文本界面：

```bash
python main.py --plain
```

默认 `default` 模式按现有审批规则处理文件修改和命令。可用 `--permission-mode acceptEdits` 允许项目内文件修改、同时继续审批命令；`--permission-mode plan` 会拒绝所有有副作用的工具调用。

### 完全访问模式

显式使用以下参数，允许访问电脑上任意文件路径，并跳过权限配置规则和逐项审批。网络请求可通过已注册的 `run_command` 执行，Agent 不额外限定网络目标。

```bash
ignovate --dangerously-skip-permissions
python main.py --dangerously-skip-permissions
ignovate --dangerously-skip-permissions -p "执行当前任务" --output-format json
```

该选项仅对本次启动生效，不写入项目配置；不能与 `--permission-mode` 同时指定。文件访问仍受当前系统账户权限约束，联网仍受系统网络、代理和防火墙约束，不会获得 root 权限。计划、审查和只读子代理仍保持只读，已有文件的版本冲突检查、输出限制和 API 密钥值脱敏继续生效。项目外文件的修改会记录到运行事件，项目内验证不能证明这些文件已验证。

输入 `/plan <目标>` 可先只读探索，再查看计划并选择批准、拒绝或通过 `$EDITOR` 编辑。计划只有在批准后才会写入 `.nailong/plans/`，随后会作为固定上下文在同一会话中执行；新版本替换同类固定消息。`/compact` 先省略已完成的旧工具正文，再在完整回合边界生成任务摘要，通常保留最近四轮。原始要求和工具证据可按摘要引用恢复。

使用 `python main.py` 时默认读取 ignovate harness 自身目录；安装后的 `ignovate` 默认读取当前工作目录。若要明确分析另一个项目，用 `--project` 选择；模型连接优先使用用户配置，未配置时读取 Agent 目录的 `.env` 或环境变量。即使当前终端位于图书管理系统目录，也可以直接运行下面这条完整命令：

```bash
/path/to/ignovate_harness/.venv/bin/python \
  /path/to/ignovate_harness/main.py \
  --tui --project "/path/to/work-project"
```

已经启动时，可在界面输入 `/project /path/to/work-project` 切换项目，随后问“看一下这个项目”；也可以用 `/review .`。切换项目会开始新会话。

运行本地用例（不调用 DeepSeek API）：

```bash
python -m unittest discover -s tests -v
```

Agent 的 system prompt 定义在 `nailong/core/prompts.py`，由 `agent.py` 接入：基础规则由主代理和只读子代理共享，探索、修改、命令、委派和 Skill 说明按本轮实际注册的工具加入；目标规则仅在所属会话有活动目标时加入。各模式继续追加自己的限制、项目目录、输出偏好和适用的上下文。工具说明位于 `nailong/tools/registry.py`，列明参数、分页、失败处理与权限边界。参考来源、适配取舍和人工评估场景见 [提示词优化说明](docs/design/2026-10-03-agent-system-prompt.md)。

### 在任意项目中启动

安装后可直接在要处理的项目目录启动。若终端未激活虚拟环境，可使用本机已经配置的 `~/.local/bin/ignovate` 启动入口，或使用安装所在虚拟环境的完整路径。

```bash
cd /path/to/your/project
ignovate
ignovate --ui inline
ignovate doctor
ignovate --doctor --output-format json
```

`ignovate` 默认把当前工作目录作为项目；`--project` 可以明确指定目录。模型认证优先使用用户连接配置，未配置时读取 Agent 程序目录的 `.env` 或环境变量。`python main.py doctor` 与 `--doctor` 在缺少 API 配置或依赖时仍能输出本地诊断；默认不联网、不自动安装或修改配置。

## 终端界面

`python main.py` 等同于 `--ui auto`：标准输入和输出均为 TTY 且支持全屏时进入 Textual 仪表盘；能交互但不适合全屏时进入 inline；非 TTY 或 PyCharm Run 控制台进入 plain。某些 SSH 终端虽能启动全屏但显示效果较差，可手动指定 `--ui inline`。界面选择只改变显示与输入方式，不改变权限规则、工具或会话语义。

| 仪表盘元素 | Textual 全屏 | Inline 终端滚动 |
|---|---|---|
| 品牌标识 | 空会话顶部显示单行名称；开始对话后收起，窄屏隐藏 | 启动时打印一次块字横幅，随后进入滚动历史 |
| 模型、项目、会话、状态 | 项目、会话和状态在顶部；模型与用量固定在输入框下方 | 输入提示下方状态栏 |
| 对话与工具 | `❯` 用户消息、`●` 模型回复、工具执行记录按顺序展示；工具开始即显示，结果立即入历史 | 同样的对话与执行层级追加到终端历史，可原生滚动复制；详细模式额外显示 run、step 和 token |
| 输入框与命令菜单 | 圆角边框输入框、框内占位符与双列菜单 | prompt_toolkit 输入与行内补全 |

Textual、inline 和 plain 共用命令解析与本地查询，均支持 `/init`、`/review`、`/plan`、`/goal`、`/cost`、`/context`、`/compact` 和 Markdown 自定义命令。全屏输入框下方固定显示当前模型、会话轮数、累计 token 和最近一次主模型调用的上下文用量；宽度足够时也显示上下文占比。累计 token 和费用包含子代理全部模型调用，子代理用量不会替换主会话的上下文占用。会话恢复时从已保存的事件恢复这些数字。模型尚未返回用量时，上下文显示 `—`。详细模式的 `tokens` 行里，`im`、`out`、`cache` 分别为输入、输出和缓存命中 token；`ctx` 是当步输入量占模型上下文窗口的估算比例。

可在所选项目的 `.nailong/settings.json` 中关闭横幅，或为未内置窗口长度的模型配置上下文大小：

```json
{
  "ui": {"banner": false, "reduced_motion": true},
  "context_windows": {"my-model": 65536}
}
```

终端宽度小于 72 列或高度小于 24 行时隐藏横幅；`NO_COLOR=1` 会关闭颜色。横幅设置只影响显示。`ui.reduced_motion: true` 使用静态活动标记，每秒更新真实耗时；默认仍使用字符旋转。

### Inline 终端滚动界面

回答会按 Markdown 段落渲染后追加到终端滚动历史；尚未完成的片段会在临时状态行预览。工具记录显示动作、文件或命令、执行结果和耗时；命令同时显示退出码和末尾输出，失败与超时会明确标注。正常模式显示简短摘要，`/config set output_style detailed` 显示更多日志与步骤统计，`concise` 收起普通预览。Plain 模式使用 ASCII 标识。恢复会话和 `/history` 展示对话，已保存的工具详情可用 `/tools [序号]` 查看。审批默认拒绝：`y` 批准一次、`n` 或直接回车拒绝、`a` 在本会话允许、`d` 展开完整参数。审批提示输入时由 prompt_toolkit 持有终端，Agent 恢复后 Rich 接回终端。

输入 `/` 可补全命令，输入 `$` 可补全启用的 Skills，输入 `@` 可补全项目根目录内的路径。`Ctrl+C` 停止当前运行并暂停等待队列，空闲时清空输入；使用 `/exit` 退出。`Ctrl+L` 清屏。

### Textual 全屏仪表盘

`/model`、`/reasoning`、`/permissions`、`/help`、计划确认和编辑、回退、F7 正文选择均在主布局中展开。列表滚动时保留操作行，Esc 逐层返回并保留输入草稿。模型切换默认保存为项目默认，`--global` 保存为用户默认；保存失败可在当前表单重试。

顶部显示项目、会话 ID 和当前状态；中间以 `❯` 区分用户消息、`●` 区分模型回复。进展、工具活动和流式回答保留在同一个模型回复位置；命令记录保留退出码和简短输出，文件变更显示有颜色的 diff 摘要。点击工具记录可在对话内展开命令、工作目录、结果和保留的日志，也可使用 `/tools [序号]`。长日志默认折叠，发生截断时明确提示。输入框上方左侧常驻 Token Weather，空会话显示 Clear 0%；右侧显示实际模型、推理强度和权限，F8 可查看完整会话与用量信息；最终回答更新原位置，不另外追加重复正文。

正文可直接用鼠标拖选任意文字，再按 `Ctrl+C` 复制；有选区时不会停止后台任务或清空输入，`Esc` 取消选区。复制期间暂缓正文重绘，取消选区后恢复显示最新内容。`F7` 可打开只读 Markdown 原文快照作为备用复制入口。macOS 使用 `pbcopy`，其他终端尝试 OSC52；终端须支持相应剪贴板协议，OSC52 请求没有成功确认。

缩放窗口时，已有中文消息会重新换行；位于末尾时继续显示最新内容，阅读较早消息时保留滚动位置。切换主题也会更新已有消息的颜色。`/config set output_style detailed` 可显示步骤和 Token 统计。

界面示例（使用本地演示数据）：[完整界面](docs/previews/terminal-ui.svg)、[窄屏界面](docs/previews/terminal-ui-compact.svg)、[执行中](docs/previews/tool-running.svg)、[工具详情](docs/previews/tool-details.svg)、[浅色主题](docs/previews/terminal-ui-light.svg)。

- `Enter` 发送消息；命令菜单打开时选择当前命令
- `Ctrl+Enter` 在输入框中换行
- 输入 `/` 打开斜杠命令菜单，继续输入可过滤；用 `↑` / `↓` 移动，`Tab` / `Enter` 选择，`Esc` 关闭当前草稿的菜单（清空后重新输入 `/` 可再次打开）
- `Ctrl+C` 停止当前运行，保留会话并暂停等待队列；空闲时清空输入，使用 `/exit` 退出
- 生成期间仍可输入，Enter 将新任务加入队列；点击工具行在对话内展开安全详情，再次点击收起，`Ctrl+O` 折叠工具组

## 命令

- `/help` 查看帮助和命令列表
- `/mcp` 管理外部 MCP 服务；连接后可在聊天中调用服务提供的工具
- `/init` 分析项目并生成 `.nailong/context.md`；首次创建和覆盖都会先在会话内请求审批
- `/diff [--staged | --branch <ref>]` 查看当前、暂存或分支改动
- `/review [--staged | --branch <ref>]` 默认只读审查当前改动；`/review <文件或目录>` 保留指定范围审查
- `/verify [步骤名称 | --list]` 运行或列出项目配置的真实验证流程
- `/doctor` 离线诊断环境、配置、Skills 和终端兼容性；`/status` 查看当前会话和运行状态
- `/plan <目标>` 只读探索并审批执行计划；计划可以用 `$EDITOR` 修改
- `/compact` 分层清理旧工具正文并生成最多 6,000 字符的任务摘要；保留用户约束、Skill 加载状态和证据引用，完整要求索引可分页恢复。未完成的并行工具交换和当前输入保留
- `/cost` 查看包含子代理的会话 token 用量和费用估算；模型价格未知时只显示用量，缺少调用用量时标记统计不完整
- `/context` 查看最近实际请求的模式、工具集合、分类估算、提供商输入用量、输出预留、窗口余量及压缩原因/收益；调用前或缺少 usage 时明确显示估算/未知
- `/goal [--max-rounds 1-100] [--max-cost-usd USD] <目标>` 启动有轮数、费用、空转与审批护栏的持久目标；`/goal status` 查看状态，`/goal pause` 暂停，`/goal` 继续活动或恢复暂停目标
- `/permissions` 查看当前项目的持久规则和本会话授权
- `/project <项目目录>` 切换要操作的项目，并开始新的会话
- `/history` 查看当前会话历史
- `/sessions [关键词]` 按名称、ID、提问和安全摘要搜索会话；恢复序号绑定本次显示结果
- `/rename <名称>` 保存会话名称；`/recap` 查看完成事项、待办及当前验证状态
- `/export [项目内路径]` 导出 Markdown；默认到 `.nailong/exports/`，覆盖前审批
- `/skills [show|enable|disable <名称>]` 查看或管理本地 Skills；`/reload-skills` 热重载目录
- `/model` 打开模型选择与新增配置；`/model <已配置名称>` 直接切换；`/theme dark|light|ansi` 切换主题
- `/config` 查看非敏感有效值及来源；`/config set <model|theme|output_style> <值> [--global]` 修改偏好
- `/memory show [user|project|local]` 查看三层状态、版本及截断情况；`/memory edit <scope>` 编辑旧 context 文件，`/memory reload` 刷新记忆
- `/memory list [scope] [offset]` 查看主题目录；`/memory read <scope/文件名.md> [offset]` 按字符分页读取全文
- `/stop` 停止当前运行；`/queue [remove <序号>|clear|resume]` 查看、移除、清空或恢复输入队列
- `/tools [调用序号]` 查看已保存的安全工具详情
- `/resume <ID|序号>` 恢复 `/sessions` 中的会话
- `/rewind` 将对话状态回退到最近一轮用户输入之前
- `/clear` 开始一个新的会话上下文
- `/count` 查看本次运行成功完成的对话轮数
- `/about` 查看项目简介
- `/exit` 退出程序

项目、用户或内置的 Markdown 命令放在 `.nailong/commands/*.md`、`~/.nailong/commands/*.md` 和内置命令目录中，后加载的同名命令覆盖前者。Front matter 可设置 `description`、`allowed-tools` 和 `model-profile`；项目命令的工具列表只会收窄当前模式能用的工具。

目标模式在每次模型调用前，按完整输入与输出上限保守预留费用，主代理和子代理共用剩余预算；不足时暂停。返回后按提供商实际 usage 结算，调用失败或缺少完整 usage 时保留预留费用并暂停。费用控制依赖配置单价和提供商计量，属于应用侧估算，不承诺精确的账单硬上限。

### 外部 MCP 服务

先安装更新后的依赖：`python -m pip install -r requirements.txt`。stdio 服务的可执行文件或运行器需已安装。三种交互界面共用 `/mcp`；配置保存在当前工作项目的 `.nailong/mcp.json`。服务不会随程序启动自动运行，每次启动后需显式连接。

远程 Streamable HTTP 示例（将地址替换成你的服务）：

```text
/mcp add docs --transport http https://example.com/mcp
/mcp connect docs
/mcp tools docs
```

本地 stdio 示例：

```text
/mcp add local --transport stdio -- /绝对路径/python /绝对路径/server.py
/mcp connect local
/mcp tools local
```

stdio 命令直接启动可执行文件，参数单独传递，工作目录为所选项目；路径含空格时用引号包裹。不会运行 shell 字符串。添加配置不启动服务；`connect` 会启动本地进程或访问远程地址。

连接后直接在对话中描述任务，ignovate harness 可选择 `mcp__服务名称__工具名称` 工具。外部工具默认需要审批，远端的只读标记不会自动免审批；可在审批时选择本会话允许，或在项目 permissions 中配置具体工具规则（例如 `mcp__docs__search(*)`）。计划、审查、初始化及只读子代理不提供 MCP 工具。工具说明和服务结果均按外部资料处理。

```text
/mcp
/mcp disconnect docs
/mcp remove docs
```

认证通过环境变量配置，不把凭据放进 URL 或命令参数。启动 ignovate harness之前先设置所需环境变量，再在 `mcp.json` 中引用；这里只展示变量名：

```json
{
  "mcpServers": {
    "docs": {
      "transport": "http",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "${DOCS_AUTH_HEADER}"},
      "timeout_seconds": 30
    },
    "local": {
      "transport": "stdio",
      "command": "/absolute/path/python",
      "args": ["/absolute/path/server.py"],
      "env": {"SERVICE_API_KEY": "${LOCAL_SERVICE_API_KEY}"}
    }
  }
}
```

`DOCS_AUTH_HEADER` 需包含服务要求的完整请求头值（例如 Bearer 前缀）。`env` 将引用值传给服务指定变量；stdio 仅继承 SDK 的最小系统环境，不传递 DeepSeek 配置。修改已连接服务的声明后先断开，再连接。默认超时 30 秒，可配置 1–120 秒；超时或取消会关闭连接，已产生的服务副作用需核对，不会自动重试。退出与项目切换会清理连接和本地服务进程。第一版展示文本和结构化结果，省略二进制正文；暂不提供 OAuth 登录、旧 SSE 或资源/提示模板管理。

连接时会验证工具参数 Schema 及其 LangChain 转换；不支持外部 Schema 引用或当前适配层无法转换的本地锚点引用，发现这些声明会拒绝连接。长名称使用摘要缩短，注册名称冲突时也会拒绝连接。已连接服务最多 256 个工具。

### 本地 Skills

把 Skill 放在项目的 `.agents/skills/<名称>/SKILL.md` 或用户目录的 `~/.agents/skills/<名称>/SKILL.md`。启动时只把名称和描述提供给模型；Agent 需要时会调用 `load_skill` 读取完整说明，再按需读取同目录的 UTF-8 资源。项目与用户目录同名时以项目版本为准。新增或修改 Skill 后输入 `/reload-skills`，在当前任务结束后的安全边界生效，无需重启。

`name` 必须与目录名相同，只能使用小写字母、数字和连字符；`description` 必填。`SKILL.md` 和单个资源文件最大 64 KiB，frontmatter 必须在前 8 KiB 内结束。无效项在 `/skills`、`/reload-skills` 和 doctor 中列出路径与原因。禁用开关持久保存；禁用项目版本不会回退到同名用户版本。已加载说明保留历史版本，下一次显式使用会重新加载最新正文。

例如创建 `.agents/skills/code-review/SKILL.md`：

```markdown
---
name: code-review
description: 审查代码变更，报告可验证的问题及文件行号。
---

先确认范围，再检查相关文件。按严重程度报告问题，并给出证据。
```

输入 `$code-review 审查 main.py` 可明确要求本轮先加载该 Skill；也可以直接描述任务，让 Agent 根据 `/skills` 所列用途选择。Skill 只是任务说明，不会授予额外工具权限。Skill 内提到的脚本不会自动执行，实际操作由当前权限模式决定；普通模式继续执行项目根目录、受保护路径限制及审批。

启动时会按固定顺序读取 `~/.nailong/context.md`、项目的 `.nailong/context.md` 和本地 `.nailong/context.local.md`，并将其作为固定记忆注入。`settings.json` 中可配置 `UserPromptSubmit`、`PreToolUse`、`PostToolUse` 和 `Stop` 命令钩子。每个钩子命令首次执行前会逐项审批；批准摘要保存在被 Git 忽略的 `.nailong/settings.local.json`。钩子超时为 10 秒，子进程不会收到 `DEEPSEEK_API_KEY`。

Agent 如果要写文件或运行命令，会显示审批原因和完整参数；文件编辑会显示路径和 unified diff，运行命令时也会显示工作目录。审批框默认聚焦“拒绝”，你可以选择“批准一次”“本会话允许”或拒绝，也可以按 `Esc` 拒绝。直接回车和拒绝都会阻止工具执行。`/review` 使用只读工具集，不会显示写入或命令执行审批。

`/plan <目标>` 会在内存中准备计划草稿；只有用户审批后才会写入 `.nailong/plans/` 并把批准的计划固定到执行会话。配置验证流程的项目，`/goal` 必须在当前目标会话完整运行 `/verify` 并全部通过才能完成，部分步骤不能放行；未配置时沿用真实成功命令证据。模型不能自行填报证据。新的命令、文件修改、目标恢复或会话变化会使旧证据失效。已达到轮数或费用上限的目标不能继续恢复，可创建新目标。目标暂停或运行取消时，已经发生的轮次与费用仍会保存。

### 改动审查与验证

`/diff` 展示相对 HEAD 的工作区最终变化并标记未跟踪文本文件；`--staged` 仅看暂存区，`--branch <ref>` 看共同祖先到 HEAD 的提交差异。默认 `/review` 按同一选择只读审查，每批最多 20 个文件，总选择最多 200 个文件、256 KiB 差异正文。受保护路径、越界链接和二进制内容被跳过，截断与未覆盖范围明确显示；无变化时不调用模型。

差异审查逐批传入 JSON 资料：选择类型与基准、本批编号和总批数、本批补丁、读取白名单、截断及跳过原因。模型只对当前批次的实际检查下结论；选择清单不代表已审查清单。暂存区和分支审查以选定补丁版本为准，不能混用当前磁盘文件；新增行注明新版本、删除行注明旧版本，重命名保留新旧路径。

审查提示词要求先发现候选问题，再核实触发条件、执行路径和影响，最后报告有代码证据的缺陷。问题按 P0 紧急、P1 高、P2 中、P3 低排序，每项包含位置、触发条件、实际错误、影响、证据和最小修复方向；证据不足的事项放在待核实限制中。结尾交代实际覆盖范围和未执行测试；没有确认问题也不能推断整个项目安全。`/review` 仍为静态只读审查，需要运行验证时使用 `/verify`。

无头 JSON 输出的 `workflow.review` 提供审查准备信息，`status=prepared` 仅表示已生成批次，`empty` 表示没有可审查文本；都不表示审查通过或模型发现已经过运行复现。模型报告继续放在现有回答正文中。

验证步骤配置在项目 `.nailong/settings.json`，不会自动猜测并执行项目命令。例如：

```json
{
  "verification": {
    "steps": [
      {"name": "unit", "kind": "test", "command": "python -m unittest discover -s tests -v", "timeout_seconds": 60},
      {"name": "build", "kind": "build", "command": "python -m build", "timeout_seconds": 120, "generated_paths": ["dist"]},
      {"name": "startup", "kind": "run", "command": "python -m http.server 8765 --bind 127.0.0.1", "timeout_seconds": 10, "http_url": "http://127.0.0.1:8765/", "expected_status": 200}
    ]
  }
}
```

只配置当前项目实际可用的步骤。`kind` 为 test/build/run；超时默认 30 秒，范围 1–300 秒，未知字段或重复名称报错。run 必须有 `stdout_contains` 或回环 IP 的 `http_url` 观察条件，观察完成后会清理创建的进程组，记录实际终止码。test/build 要求退出码 0 且未超时。

每一步仍需权限审批，拒绝或 plan 模式零执行；无头待审批显示 unverified。完整记录保存命令、cwd、时间、退出码、输出、观察条件、配置和输入摘要、Git HEAD/暂存状态。执行时或之后输入改变，会使证据失效。`generated_paths` 仅能声明新的安全生成物，不能隐藏现有源码、已跟踪文件或配置；记录实际生成文件，目录中新出现的外部源码仍会使旧证据失效。

```bash
nailong -p "/diff" --output-format json
nailong -p "/verify" --output-format stream-json
```

### 当前任务、证据与停止条件

工程请求会建立独立的任务记录，按项目与会话保存在私有数据目录的 `tasks/<会话ID>.json`。`/resume` 恢复后继续读取目标、补充要求、约束、步骤和验收；不会重新执行旧命令。新要求、文件变化或无法核实的输入会使旧证据失效。`/rewind` 只回退对话，任务记录与已执行操作保留，并暂停以便核对。

```text
/task status
/task new 修复启动问题
/task scope main.py ui
/task constraint 保留现有工作区修改
/task step startup doing 修复启动入口
/task accept startup-ui manual 在实际终端确认输入和输出正常
/task confirm startup-ui 已在目标终端检查输入、回复与滚动
/task complete
```

`/task accept` 支持 static/test/build/run/review/manual。`confirm` 仅接受 manual 类型，记录用户明确确认及当前输入摘要；不能伪造测试或构建结果。`/task waive ID 说明` 单独记录免除，免除不算验证通过。`/task complete` 要求当前有效证据和已完成步骤。步骤更新、验收登记和用户决定通过本地命令完成；模型最终回答不能修改这些事实。

测试、构建或运行类的功能验收，需要由用户明确关联已配置的验证步骤和覆盖路径。例如项目已配置名为 `unit` 的 test 步骤：

```text
/task accept add-correct test add(2, 3) 返回 5
/task bind add-correct unit calc.py tests/test_calc.py
/verify
```

绑定表示用户声明该检查覆盖此验收；运行时仍要求命令真实成功、需求版本和输入指纹一致，并核对全部任务范围。命令失败、输入变化或只覆盖部分范围不能标为已验证。

`/verify` 的 `verify:<步骤名>` 是配置流程条件，成功执行与用户目标覆盖分别报告；只有配置命令退出成功不会标为目标 verified。交付报告区分 verified、reviewed、unverified、failed 和 blocked，在终端答案后、`/task`、`/status` 与无头 JSON 的 `delivery` 字段可见。只读审查会按本轮成功模型请求实际消费的文件分页收集覆盖证据；范围超限、漏读、截断或版本变化时仍是 unverified。reviewed 只证明指定范围的静态审查输入和流程完整，不证明代码无缺陷，也不表示测试通过。

连续相同参数的成功读取得到相同结果，第三次提示调整调查方式，调查阶段第六次、其他阶段第五次暂停。分页偏移、结果或文件版本改变不视为重复；命令轮询不靠此启发式停止，仍受轮数和成本上限限制。暂停保留等待输入，用 `/queue resume` 恢复队列；`/task resume` 只恢复任务状态。目标模式缺少必需验收或等待人工确认时暂停，核对 `/task` 后输入 `/goal` 恢复暂停目标。

工具的同步、异步、只读子代理均走执行时权限入口；ASK 没有审批回调时零执行。已有文件完整覆盖需完整读取和版本匹配，审批等待期间文件变化返回冲突。文件修改、命令、配置验证和允许的 shell 钩子共用项目内进程级变更协调器；它不替代操作系统沙箱。init/review/plan/subagent 及 plan 权限模式不运行 shell 钩子。

### 队列、偏好和记忆

生成期间可以继续发送，最多 10 个等待项。上下文变更命令也按顺序执行，下一次请求使用当时有效的模型、Skill 和记忆。队列满时保留输入。`/stop` 或 Ctrl+C 取消当前流和所属进程、暂停当前会话目标，保留 ID、历史、已知费用及等待项；只能通过 `/queue resume` 恢复。切换项目或会话前先停止并清空等待队列，不能把等待任务转移给新会话。审批中 Ctrl+C 拒绝操作并暂停队列。

偏好顺序为默认 → `~/.nailong/preferences.json` → 项目 settings.json → settings.local.json → 启动 CLI 参数；本会话显式命令覆盖启动值。默认写入 settings.local.json，`--global` 写用户偏好，私有原子写入并保留权限、价格、钩子等其他字段。有效模型配置仅声明 model ID，不允许配置认证或另一个 API 主机：

```json
{
  "models": {"fast": {"model": "deepseek-flash"}, "pro": {"model": "deepseek-v4-pro"}},
  "model": "fast",
  "theme": "dark",
  "output_style": "normal",
  "context_windows": {"deepseek-v4-pro": 1000000}
}
```

```bash
ignovate --model pro --theme light --output-style concise
```

环境中的模型始终保留为 `default` 可选项。切换模型保留会话，每次调用记录实际模型、API 主机和当时的单价快照；`/cost` 按各次单价累计。未知单价或缺失 usage 显示不完整/未知，Textual 的累计 Token 以 `+?` 提示缺失部分，不能把估算当账单。未知价格模型可普通对话，目标模式会暂停费用保护。`output_style` 支持 concise/normal/detailed；不会删减失败和审查证据。

输入 `/model` 可以选择已有模型或新增模型。Textual 界面显示选择列表和新增表单；inline/plain 界面按提示输入序号、名称或 `a` 新增。新增时填写名称与 API 支持的模型 ID，保存后立即切换，下一次模型请求生效；沿用当前 `.env` 的 API 地址和密钥。表单支持取消，不写入配置。名称不得重复或使用保留名称 `default`。

也可以直接使用以下命令；默认保存到当前项目 `.nailong/settings.local.json`，加 `--global` 保存到 `~/.nailong/preferences.json`。配置操作不发送模型请求，不检查远端是否支持该模型 ID。

```text
/model add pro deepseek-v4-pro
/model pro
/model add                    # 直接打开新增配置
/model add fast deepseek-flash --global
/model default               # 切回环境默认模型
```

输出提示词区分任务开始说明、关键进展和独立可读的最终交付。普通问答直接回答；工程结果先说明产出，再给相关文件、依据、实际验证命令与结果、必要限制，并区分已修改、已验证、未验证和受阻。交付前核对需求、最终改动与验证证据；简洁输出也保留失败和未覆盖范围。这些是模型行为要求，真实遵从度仍需模型评估。

旧记忆仍使用 `~/.nailong/context.md`、项目 `.nailong/context.md` 与 `.nailong/context.local.md`。长文件优先选取 `# 核心约定` 或 `# Core`，否则选取开头；默认只注入受预算限制的摘要。每层明确记录缺失、空文件、读取失败、已加载或已截断，并按完整文件版本判断磁盘变化。截断不代表全文已被模型读取。

主题正文按需读取，三个目录分别为 `~/.nailong/memory/*.md`、项目 `.nailong/memory/*.md`、项目 `.nailong/memory.local/*.md`（本地目录已忽略 Git）。例如 `.nailong/memory/build.md`：

```markdown
---
description: 构建与验证方式
---
# 构建
在虚拟环境中运行项目实际配置的验证步骤。
```

`description` 可省略，目录描述默认使用文件名。单文件最大 160000 bytes，frontmatter 最大 8 KiB；最多发现 200 个主题，每层最多扫描 1000 个目录项，超限与不可读项会显示诊断。默认提示词只放 ID、描述、版本、大小、状态与旧文件标题；主题正文由只读工具 `memory_list`、`memory_read` 读取。旧文件也可用 `read_memory` 按标题或偏移读取。逻辑 ID 为 `scope/文件名.md`，其中 `local/context.md` 对应旧 `.nailong/context.local.md`；禁止子目录、路径穿越、符号链接与非普通文件。工具限 chat/plan 模式，审查、初始化和只读子代理保持各自范围。

项目 settings.json 或 settings.local.json 可以设置 `{"memory":{"max_tokens":4000}}`，范围 512–16384。三层摘要、目录、提示及记忆工具输出的 JSON 共用同一预算，默认给摘要与目录最多 75%，为读取预留余量；较小预算会进一步预留合法长 ID 与响应包装所需空间。每次模型调用也限制历史记忆工具结果，优先保留较新内容，不改写持久会话记录。预算按 ASCII 字符 / 4 向上取整、非 ASCII 字符按 2 tokens 保守估算；实际用量仍以 API usage 为准。分页响应返回 `version`、`truncated` 和 `next_offset`，必须继续读取才能取得尾部。同一运行内版本改变会返回 `changed`，下一轮刷新后重新读取，避免拼接不同版本的内容；目录元数据过长会精简描述和标题以保留分页进度，`metadata_truncated` 与 `diagnostics_truncated` 标记被缩短的元数据与诊断。标题索引截断时按全文读取，不能用截短标题作为精确 section 参数。

Textual 使用编辑框，inline/plain 用 `$VISUAL` 或 `$EDITOR` 编辑权限 0600 的临时副本，再显示 diff 并审批应用；取消不会写原文件，外部并发修改会拒绝覆盖。没有编辑器时显示手动编辑和 `/memory reload` 的说明。下一轮自动刷新记忆；手动重载保留会话与历史费用。`/context` 显示统一记忆预算和各层状态，`doctor` 检查目录与预算配置。[自动提取与向量索引的评估](docs/design/2026-10-03-memory-storage-evaluation.md)记录了后续引入条件。

`task` 子代理只使用只读工具，最多并行 3 个任务，同一父会话每轮共享 30,000 token 额度。每次模型调用前按完整输入保守预留额度，并收窄输出上限，返回后按实际用量结算；缺少完整用量时保留预留额度并停止新的子代理调用。取消任务仍保留此前成功调用的用量。

项目权限规则放在 `.nailong/settings.json`，支持 `Bash(git status:*)`、`Read(./src/**)`、`Edit(./src/**)`、`Write(./.nailong/**)` 等 `allow`、`deny` 和 `ask` 规则。普通权限模式中拒绝规则优先于允许规则；`.env`、`.git`、`.venv`、项目根目录以外的文件由文件工具硬边界拒绝。命令规则会逐段检查 `&&`、`||`、`;`、`|` 和换行；命令替换、反引号、重定向、here-doc 等复杂语法会回到人工审批。直接编辑配置规则前可先用 `/permissions` 查看已生效内容。显式完全访问模式跳过这些规则及文件路径限制。

文件工具的根目录限制与命令审批不构成操作系统沙箱；批准的 shell 命令仍拥有当前用户的系统权限。

启动时可以恢复当前项目最近的会话，或按 ID 恢复指定会话：

```bash
python main.py --continue
python main.py --resume 1
python main.py --plain --resume 0123456789abcdef0123456789abcdef
```

记忆文档可用可选 `knowledge` frontmatter 声明类型、适用路径、来源文件 SHA-256 和候选/确认声明。目录、读取和固定上下文显示 observed/unverified/stale；来源文件匹配只表示依赖未变化，`fact_verified` 始终为 false，候选不会自动晋升。来源核对只读取当前项目安全文件，当前会话 Read 的 ASK/DENY 不读取。旧文档保持兼容并标为 legacy/unverified。每步请求同步刷新记忆正文、预算和报表，详情见 [记忆交接](docs/reviews/2026-10-03-managed-runtime-memory-handoff.md)。

## 项目结构

```text
main.py          自动界面选择、无界面打印模式与 --project 项目选择
headless.py      单条提示的无界面流式运行和机器可读输出
tui.py           默认 Textual 会话界面、斜杠菜单、输入框和内联审批
ui/              两端共享展示与流程、inline Rich 输出和 prompt_toolkit 输入
agent_service.py 异步回合、审批协作和单轮步数限制
agent.py         ChatDeepSeek、Agent 运行时与按模式隔离的工具注册
config.py        用户连接配置与 .env 的 DeepSeek 配置加载
nailong/         文件工具、注册表和权限规则引擎
tools.py         LangChain 工具声明及 chat/init/review 权限范围
local_tools.py   项目文件读写、搜索和命令执行
tool_demo.py     手写 API 工具调用往返的低层学习示例
check_config.py  环境变量读取示例
```

一次对话的顺序是：界面将用户输入交给 `agent_service.py` → `agent.py` 中的 LangChain Agent 调用 DeepSeek → Agent 选择 `tools.py` 中注册的工具 → `local_tools.py` 执行只读操作，或在写入/命令前通过 LangGraph 中断等待用户审批 → 工具结果返回给 Agent → Agent 生成最终回答。Textual 仪表盘在后台 Worker 中运行模型请求。

## 工具和安全边界

以下文件边界与审批说明适用于普通权限模式；完全访问模式的差异见上文。

- `list_files` 最多列出 100 个文件，跳过 `.env`、`.git`、`.venv` 和 `__pycache__` 等目录；`/review` 会把上限收紧为 20 个文件。
- `read_file` 只读项目根目录内的 UTF-8 文件，单次最多返回 12,000 个字符；`/review` 还会限制为本次指定路径，并最多读取 20 个不同文件。
- `search_text` 只搜索项目根目录内的文本文件，最多返回 50 条匹配。
- `write_file` 只写项目根目录以内的文件，每次最多写入 120,000 个字符；执行前需要批准。
- `/init` 运行时只注册列文件、读文件和写入 `.nailong/context.md` 的工具，不提供命令执行能力；如果目标路径被符号链接重定向，写入会被拒绝。
- `run_command` 在项目根目录作为当前工作目录运行，最长 30 秒；macOS/Linux 下超时会终止命令进程组，返回输出最多 12,000 个字符并标明是否截断。执行前需要批准，并且不会把 `DEEPSEEK_API_KEY` 传给子进程。

文件工具会先解析路径再做根目录检查：只有解析后落到所选项目根目录以外的路径才会被拒绝，因此项目内绝对路径和规范化后仍位于项目内的 `..` 路径可以使用；指向项目外部的符号链接会被拒绝。密钥文件、虚拟环境和 Git 内部目录也不能通过文件工具读取。

终端命令仍是普通 shell 命令。审批让你检查和拒绝操作，但它不是操作系统级沙箱；获批命令可能访问项目目录以外的文件或网络。请仔细检查完整命令，尤其不要批准来源不明的删除、安装或上传命令。命令输出也会返回给模型。

会话状态保存在用户目录下按项目路径隔离的 SQLite 检查点中；脱敏后的事件摘要保存在相邻 JSONL 文件。默认位置为 `~/.nailong/projects/<项目路径摘要>/`，可用 `NAILONG_DATA_DIR` 修改数据根目录。`--continue` 恢复最近一次会话，`--resume` 按 ID 或序号恢复，`/clear` 则分配新的会话 ID。`/rewind` 回退对话状态到最近一轮用户输入之前，并移除该轮之后的对话事件；它不会撤销已经执行的文件修改或命令。已发生的 token 与费用记录会保留，不会随回退减少，也不会影响回退后的上下文统计。旧日志的回退边界若可能已截断，会拒绝回退以保留历史。

### 查看与导出运行轨迹

```text
/trace
/trace export
/trace export .nailong/exports/run.jsonl
```

`/trace` 显示最近一轮已保存的可观察事件：模型请求、用量、工具执行、审批、错误及交付状态。`/trace export` 以 JSONL 导出，默认放在当前项目 `.nailong/exports/`；显式导出路径也限定在项目内，普通模式写入前审批。轨迹省略用户和模型正文、原始工具参数及输出，保留元数据和已有结果引用，不包含模型私有思考。缺失的关联标为未知；按时间排序不代表证明因果关系，历史回退可能改变派生轮次 ID。

每次模型请求（含工具循环和只读子代理）都会检查系统提示、工具定义、调用参数及对话的完整预算。默认软压缩阈值为 150,000 tokens，可在 `.nailong/settings.json` 的 `context.soft_threshold_tokens` 配置；已知窗口另扣输出预留和安全余量。普通工具清理至少节省 20% 才生效，硬窗口限制可进一步清理最新已完成结果并缩短旧回合。无法安全容纳当前输入、固定约束或未完成工具交换时，在调用提供商前停止并说明原因。未知模型请配置 `context_windows`；未知窗口仍执行软阈值，不能保证提供商的硬限制。

预检查和最终请求检查共用运行时内的有界计数缓存：按实际文本及工具 schema 复用字符统计，分类合并后再取整并应用当前校准系数。消息 ID 相同或任务需求版本相同不会阻止内容更新；记忆、任务投影和配置仍在调用边界刷新。未变化的工具结果复用提前归档记录，清理正文前仍校验磁盘归档。自动软压缩以触发阈值的 80% 为目标，保留最近四轮；同一输入压缩无收益时暂停重复尝试，输入、工具、校准或策略变化后重新评估，硬预算检查始终执行。

会话的 `context_request.performance` 记录预检查和最终请求的分阶段耗时、归档保存/复用次数、压缩尝试次数及运行时累计缓存计数；检查失败时保存 `context_performance` 事件。计时不包含提供商请求和报表事件写入，压缩阶段包含其内部重新核算的耗时，分阶段数值不能简单相加。本地基准可运行 `python scripts/benchmark_context.py`，不调用模型 API。

省略前的原始工具结果和旧用户要求脱敏保存在会话私有 `history-results/` 归档中，目录权限 0700、文件权限 0600。聊天模型可用 `read_history_result(reference, offset, max_chars)` 分页读取摘要引用（每页最多 6,000 字符），不能传任意路径或查询其他会话。单条归档上限 2 MiB，超出会明确标记 `archive_truncated`；归档失败会保留原正文。文件内容带历史版本和行范围，使用前应核对当前文件。摘要保存完整任务索引的引用，早期要求不会因超过八条而被自动淘汰；索引自身无法完整归档时不执行回合摘要。

`/context` 的分类与工具集合来自最近送入模型的请求并随会话保存。估算区分 ASCII 与非 ASCII 内容，按同模型、同提供商的实际 usage 保守校准；它仍不是精确 tokenizer 或费用账单。手动压缩后，最近实际请求统计要等下一次调用才更新。Skill 正文被省略时保留名称、路径和版本及已加载状态，持续规则可按需恢复，一次性初始化不应重复。



## 发布验证

macOS/Linux 下的文件描述符与进程组实现为本项目当前验证的平台边界。新建文件遵循 umask；完整验证的输入指纹包含文件权限。目标存储使用跨进程事务锁，并对驱动执行加独占锁，防止并发结算丢失及重复消费同一目标预算。

```bash
python -m pip wheel --no-deps --wheel-dir dist .
python scripts/smoke_wheel.py dist/ignovate_harness-1.0.1-py3-none-any.whl
```

GitHub Actions 已启用，在 Ubuntu 24.04、Python 3.11/3.13 上运行完整离线测试和发行包 smoke，支持 push、pull request 与手动触发。配置见 [CI 工作流](.github/workflows/tests.yml)。

真实模型检查按需手动执行 `python scripts/smoke_real_api.py --output /tmp/ignovate-live-api.json`；`--only headless_json` 可定向补测。脚本使用本机连接与临时项目，限制调用次数并保存脱敏结果。本次实际结果及首次失败/补测记录见 [CI 与真实 API 验证记录](docs/reviews/2026-10-08-ci-real-api-verification.md)。第三方 MCP 和 Windows 实机仍需另行验证。
