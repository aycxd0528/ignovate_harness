# ignovate harness 项目说明

本文面向首次使用或维护项目的开发者，说明如何安装、连接模型、选择工作项目，以及核对工具权限和验证结果。适用版本为 1.0.2，维护日期为 2026-10-08。支持 macOS、Linux 和 Windows 11 x64（原生运行，无需 WSL）；Release 安装器会自动准备缺少的 Python 和应用依赖。

仓库：[aycxd0528/ignovate_harness](https://github.com/aycxd0528/ignovate_harness)。完整命令与界面细节见 [README](../README.md)，本次问题与验证记录见 [项目审查报告](reviews/2026-10-08-project-audit.md)。

## 项目定位

ignovate harness 是在本地终端运行的编码助手。它用 Python、LangChain 和 LangGraph 编排模型对话，让模型调用文件浏览、搜索、编辑和命令执行工具，并由运行时管理审批、会话、上下文和验证证据。默认模型适配器为 ChatDeepSeek。

项目支持三种交互界面，也支持用于脚本集成的无头模式。旧命令 `nailong`、Python 包名 `nailong`、项目配置目录 `.nailong` 和已有会话数据继续兼容。

它适用于代码结构理解、改动审查、带审批的修改和本地开发验证。模型回答与工具证据分别记录；回答中的“完成”不直接作为项目验收依据。

## 快速开始

推荐从 [GitHub Releases](https://github.com/aycxd0528/ignovate_harness/releases/latest) 下载安装包，无需预装 Python 或克隆源码。当前版本为 [v1.0.2](https://github.com/aycxd0528/ignovate_harness/releases/tag/v1.0.2)。

### macOS / Linux

下载 [ignovate-1.0.2-unix.tar.gz](https://github.com/aycxd0528/ignovate_harness/releases/download/v1.0.2/ignovate-1.0.2-unix.tar.gz)，支持 Intel / Apple Silicon macOS 和 x86_64 / ARM64 Linux。在下载目录执行：

```sh
tar -xzf ignovate-1.0.2-unix.tar.gz
cd ignovate-1.0.2
sh install.sh
. "$HOME/.local/bin/ignovate-env.sh"
ignovate set up
```

### Windows 11

下载 [ignovate-1.0.2-windows.zip](https://github.com/aycxd0528/ignovate_harness/releases/download/v1.0.2/ignovate-1.0.2-windows.zip)，在 PowerShell 中执行：

```powershell
Expand-Archive .\ignovate-1.0.2-windows.zip -DestinationPath .\ignovate-release
cd .\ignovate-release\ignovate-1.0.2
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

支持 Windows 11 x64 原生运行，无需 WSL、Linux 环境或虚拟化。安装在当前用户目录，无需管理员权限。安装完成后打开新的 PowerShell 或命令提示符，运行：

```powershell
ignovate set up
```

Windows 命令工具使用 Windows PowerShell；Git 和 MCP stdio 服务使用 Windows 可执行文件及 Windows 路径。项目文件工具支持本地盘符路径，拒绝目录联接、重解析点、设备路径和备用数据流；网络共享路径暂不支持。项目编辑暂不支持压缩、加密、稀疏文件及含备用数据流的文件；遇到这些文件会拒绝修改并保留原文件。

### 自动配置与日常启动

`ignovate set up` 会检测 uv、Python 3.11–3.13、应用依赖和 ripgrep，自动下载缺少的组件并创建隔离环境，然后打开模型配置向导。应用依赖固定版本并校验哈希，无需手动激活虚拟环境。首次配置需要联网；下载失败后可重新运行此命令。重复安装会保留模型连接和项目数据，普通启动在环境就绪后不下载依赖。

只准备环境、不打开模型配置向导：

```sh
ignovate set up --environment-only
ignovate doctor --output-format json
```

未填写模型连接时，`doctor` 返回退出码 1 并给出配置指引。Git 是可选项目工具，使用 diff / review 前需自行安装。完整安装位置、PATH、校验和升级说明见 [Release 安装说明](INSTALL.md)。

配置指引依次收集模型连接、推理强度和本次权限。连接信息通过最终确认后保存；中途取消不写配置。API Key 使用隐藏输入，已有密钥留空可保留。旧命令 `ignovate --setup` 继续兼容。

然后切换到要分析的项目，或明确传入路径：

```sh
cd /path/to/work-project
ignovate
ignovate --project /path/to/work-project --ui inline
ignovate doctor --project /path/to/work-project --output-format json
```

首次可输入“列出项目根目录的文件，并指出主要源码入口”。`doctor` 只检查本机环境、依赖和配置，不调用模型 API；配置缺失时输出修复指引。安装包环境通过 distribution 元数据检查依赖，不要求保留源码目录。

### 从源码安装（开发者）

在已安装 Git、Python 3.11 或更新版本的 macOS/Linux 终端执行：

```bash
git clone --branch v1.0.2 https://github.com/aycxd0528/ignovate_harness.git
cd ignovate_harness
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
ignovate --setup
```

Windows 11 开发者在原生 PowerShell 中安装对应版本源码后执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\ignovate.exe --setup
```

## 连接与配置

| 配置或数据 | 默认位置 | 作用 |
| --- | --- | --- |
| 模型连接 | `~/.ignovate/config.json` | 模型地址、ID、API Key 和默认推理偏好；Unix 权限 0600，Windows 使用私有 ACL |
| 用户偏好 | `~/.nailong/preferences.json` | 模型、界面和其他用户默认值 |
| 项目偏好 | `<project>/.nailong/settings.json` | 项目权限、模型、验证步骤和上下文设置 |
| 本地覆盖 | `<project>/.nailong/settings.local.json` | 不提交 Git 的本机偏好 |
| 项目会话 | `~/.nailong/projects/<项目路径摘要>/` | SQLite checkpoint、事件、任务、目标和历史归档 |

`IGNOVATE_CONFIG_DIR` 可调整连接目录，`NAILONG_DATA_DIR` 可调整会话数据根目录。用户连接文件优先于程序目录的 `.env`；未配置用户连接时，使用 `.env` 与进程环境变量。

兼容环境变量方式：复制 `.env.example` 为 `.env`，填写 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`。已有 `.env` 应直接编辑，避免被模板覆盖。连接地址必须是有效 HTTP(S) URL，不能带 URL 内认证、查询或片段；模型 ID 不能包含空白或控制字符。

连接配置是本机明文文件，应由当前用户保管。仓库忽略 `.env*`（保留空密钥模板）、本地配置、IDE 配置、构建输出和私人评估产物。

## 使用方式

| 方式 | 适用场景 | 示例 |
| --- | --- | --- |
| 自动界面 | 日常终端使用 | `ignovate` |
| Textual 全屏 | 对话、工具详情和审批统一展示 | `ignovate --ui textual` |
| Inline 滚动 | 使用终端原生滚动与复制 | `ignovate --ui inline` |
| Plain | IDE 或受限控制台 | `ignovate --plain` |
| 无头输出 | 脚本和流水线集成 | `ignovate -p "检查项目结构" --output-format json` |

自动模式根据 TTY 和终端能力选择界面。安装后的 `ignovate` 默认使用当前工作目录；`python main.py` 默认使用助手源码目录，处理其他项目时应传入 `--project`。

常用交互命令：

| 命令 | 行为 |
| --- | --- |
| `/help`、`/status`、`/doctor` | 查看操作说明、会话状态和本地诊断 |
| `/project <目录>` | 切换项目并开始新会话；失败保留原运行环境 |
| `/init` | 分析项目，并经审批生成 `.nailong/context.md` |
| `/review [路径或改动范围]` | 只读审查文件或 Git 改动 |
| `/plan <目标>` | 只读制定计划，批准后执行 |
| `/verify [步骤名称]` | 执行项目配置的验证步骤并保存证据 |
| `/goal <目标>`、`/goal status`、`/goal pause` | 管理有预算和轮数护栏的持续目标 |
| `/sessions`、`/history`、`/rewind` | 查找、查看或回退对话状态 |
| `/context`、`/compact`、`/cost` | 查看上下文、压缩旧内容和核对用量 |
| `/mcp`、`/skills`、`/memory` | 管理外部工具、本地技能和记忆 |
| `/permissions`、`/reasoning` | 调整本次权限与模型支持的推理档位 |

运行期间可输入任务并排队；Ctrl+C 停止当前运行并保留会话。恢复新一轮时，运行时明确关闭旧的未完成工具交换并标注结果未知，不自动重放可能产生副作用的调用。`/rewind` 只回退对话，已经执行的文件修改和命令不会撤销。

无头模式支持 `text`、`json`、`stream-json`。JSON 包含会话 ID、回答、用量、工具摘要、审批及交付状态。普通操作需要审批时默认拒绝；目标模式遇到审批会保存为暂停状态。退出码：0 为成功，1 为运行或审批问题，2 为轮次或预算限制，3 为配置问题。

## 权限与文件边界

| 模式 | 文件修改 | 命令执行 |
| --- | --- | --- |
| 请求批准 `default` | 依据规则请求审批 | 依据规则请求审批 |
| 帮我批准 `acceptEdits` | 自动批准项目内修改 | 继续请求审批 |
| 完全访问 `bypassPermissions` | 跳过路径限制和逐项审批 | 跳过逐项审批 |

可用 `--permission-mode acceptEdits` 或 `--dangerously-skip-permissions` 明确指定启动权限。权限选择只对本次进程生效。计划、审查和只读子代理仍保留模式限制。

普通文件工具限制在所选项目内，并拒绝 `.env`、`.git`、`.venv`、`__pycache__` 等受保护路径；项目自定义命令的符号链接也不能把这些文件作为提示词加载。精确授权如 `Write(./safe.txt)` 只匹配该项目相对路径，不授权 `nested/safe.txt`。

读取和命令加载使用逐级目录描述符与 `O_NOFOLLOW`，防止路径解析后被符号链接替换。正则搜索先安全读取受限大小的文件快照，再交给 ripgrep；不能安全读取的文件标记为覆盖不完整。已有文件修改要求此前完整读取，并核对版本后原子写入；新文件遵循用户 umask，已有文件保留权限。读取和搜索返回分页、截断与覆盖状态，模型须按这些标记判断证据是否完整。

文件边界与 shell 审批不构成操作系统沙箱。获批命令拥有当前系统用户的权限，可以访问其他文件与网络；进程组取消也不能保证收回自行脱离进程组的程序。外部程序和其他进程仍可能并发修改文件，因此不能把工具检查解释为通用文件系统事务。

## 架构与执行流程

| 模块 | 职责 |
| --- | --- |
| `main.py`、`nailong/cli.py` | 命令行参数、默认项目和界面选择 |
| `config.py`、`nailong/core/bootstrap.py` | 连接配置、首次引导和参数校验 |
| `agent.py` | 模型创建、LangGraph 图、按模式注册工具和中间件 |
| `agent_service.py` | 异步回合、流式事件、审批、取消和交付汇总 |
| `nailong/core/` | 权限、预算、上下文、记忆、任务、会话、目标及验证 |
| `nailong/tools/`、`tools.py` | 工具 schema、执行门、文件版本和只读子代理 |
| `nailong/mcp/` | MCP 配置、连接生命周期、schema 校验与工具适配 |
| `tui.py`、`ui/` | Textual、inline/plain 以及共享命令、审批和显示 |
| `headless.py` | 单条提示和目标的机器可读输出 |
| `tests/`、`scripts/` | 离线回归、上下文基准和安装包 smoke |

一次回合依次经过：用户输入 → 任务状态与上下文准备 → 模型请求 → 工具执行门 → 审批或工具结果 → 下一次模型请求 → 验证与交付报告。运行时将模型回答、工具事件、费用和任务证据分别保存。

模型调用次数与内部图步骤分别计数：主代理每轮最多 40 次模型调用，最后一次只汇总已有证据；内部步骤另有 256 步保护。子代理只读，最多并行 3 个任务，并使用独立的受限调用与共享 token 预算。

## 任务、目标和验证

任务状态独立于对话历史，保存用户要求、约束、步骤、验收、变更路径和证据。上下文压缩不会直接删除持久任务要求；文件内容或权限、任务版本变化会使相应旧证据失效。

持续目标还有轮数、费用、空转和审批护栏。目标事务使用跨进程锁，驱动执行使用单项目独占锁，第二个驱动不会接管正在运行的目标会话。只有紧邻的三轮记录相同阻塞原因，才满足连续阻塞计数；恢复暂停目标后重新计数。

在工作项目的 `.nailong/settings.json` 中配置真实验证命令，例如 Python unittest 项目：

```json
{
  "verification": {
    "steps": [
      {
        "name": "tests",
        "kind": "test",
        "command": "python -m unittest discover -s tests -v",
        "timeout_seconds": 120
      }
    ]
  }
}
```

随后使用 `/verify`。应换成目标项目实际的测试或构建命令；配置命令成功只提供该步骤的证据，不能自动证明所有功能要求完成。交付状态区分已验证、已审查和未验证；未配置验收或证据不完整时，应补齐后再宣告完成。

## MCP、技能与上下文扩展

MCP 支持 stdio 和 Streamable HTTP；外部工具经过相同执行门，默认按有副作用工具审批，即使服务声称只读也不扩大授权。凭据使用环境引用传入，结果、描述与 schema 做脱敏，包含已知凭据的工具名称拒绝注册。审批展开后显示完整业务参数，包括外部工具的 `command` 和 `cwd`。

本地技能位于项目或用户的 `.agents/skills/<name>/SKILL.md`，按需加载正文。自定义 Markdown 命令可以缩小工具集合，不能借助元数据扩大权限。

上下文管理检查系统规则、工具定义、历史和当前任务的总预算，按完整回合边界压缩已完成的旧工具内容。被省略的内容通过私有归档引用按需恢复。ASCII/非 ASCII 估算与模型实际 usage 校准一起使用；未知模型窗口需在项目中配置 `context_windows`，估算不等于精确 tokenizer 或费用账单。

## 开发与发布验证

本机在虚拟环境中执行：

```bash
python -m unittest discover -s tests -v
python -m pip wheel --no-deps --wheel-dir dist .
python scripts/smoke_wheel.py dist/ignovate_harness-1.0.2-py3-none-any.whl
```

离线测试使用假模型、临时项目和本地 MCP 服务，不调用真实模型 API。wheel smoke 从源码目录之外加载实际发行内容，验证 `--help`、JSON doctor 和依赖检查，避免仅验证 editable 安装。

GitHub Actions 已启用，工作流位于 .github/workflows/tests.yml，参考副本位于 docs/ci/github-actions-tests.yml。每次 push、pull request 或手动触发都会在 Ubuntu 24.04、Python 3.11/3.13 上运行完整离线测试及 wheel smoke。实际运行结果以仓库对应提交的检查记录为准。

推送与 `pyproject.toml` 版本一致的 `v*` tag 后，Release 工作流会执行 Python 3.11/3.13 完整离线测试、macOS/Linux 实际安装检查，以及 Windows 原生启动器和未预装 Python 时的真实自动安装检查。Windows 原生用例验证文件工具、存储权限、跨进程锁和命令取消；全部检查通过后发布两个安装包、wheel 和 `SHA256SUMS`。Windows CI 使用 Windows Server 2025 的同代原生 API；Windows 11 桌面的交互终端仍需实机验证。

真实模型验证通过本机手动执行 python scripts/smoke_real_api.py --output /tmp/ignovate-live-api.json。脚本使用已配置的模型连接和临时项目，限制调用次数、输出和 managed 预算，保存脱敏元数据。本次 deepseek-flash 的回答、读取、编辑后验证、只读审查、审批拒绝和无头 JSON 六类检查均获得通过结果；首次无头答案断言失败与成功补测保留在验证记录中。第三方 MCP、Windows 实机与不同终端剪贴板协议仍需另行验证。 详见 [CI 与真实 API 验证记录](reviews/2026-10-08-ci-real-api-verification.md)。

项目采用 MIT License。对功能或权限行为作出修改时，应新增能够复现问题的回归测试，并更新 README、项目说明和验证记录。
