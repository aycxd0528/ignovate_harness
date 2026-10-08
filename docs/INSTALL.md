# 从 GitHub Release 安装 ignovate

下载地址：[GitHub Releases](https://github.com/aycxd0528/ignovate_harness/releases/latest)。安装包包含应用 wheel、环境配置脚本、固定版本的依赖清单和 SHA-256 校验清单，不需要预装 Python，也不需要克隆源码。

## macOS / Linux

支持 Apple Silicon、Intel macOS，以及 x86_64 / ARM64 Linux。下载 `ignovate-1.0.1-unix.tar.gz`，解压后执行：

```sh
tar -xzf ignovate-1.0.1-unix.tar.gz
cd ignovate-1.0.1
sh install.sh
. "$HOME/.local/bin/ignovate-env.sh"
ignovate set up
```

安装器只安装启动器，不会在下载目录创建虚拟环境。`ignovate set up` 会依次检测或下载 uv、Python 3.11–3.13、应用依赖和 ripgrep，然后打开现有的模型配置向导，填写 API 地址、模型 ID 和 API Key。保留兼容命令 `ignovate --setup`。

正常启动：

```sh
cd /path/to/your/project
ignovate
```

如果只准备环境，适合 CI / 非交互终端：

```sh
ignovate set up --environment-only
ignovate doctor --output-format json
```

`doctor` 在缺少模型配置时返回退出码 1 并给出诊断；环境安装成功并不代表 API 已配置。首次正常启动也会自动准备缺失环境。再次运行 `set up` 可修复依赖并重新打开向导；普通启动在环境就绪后不下载依赖。

## Windows（WSL2）

支持 Windows 11 或 Windows 10 2004 及以后版本，需要启用硬件虚拟化。当前应用使用 Unix 安全文件描述符，Windows 安装包通过 WSL2 运行完整 Linux 环境。

下载 `ignovate-1.0.1-windows.zip`，在 PowerShell 中执行：

```powershell
Expand-Archive .\ignovate-1.0.1-windows.zip -DestinationPath .\ignovate-release
cd .\ignovate-release\ignovate-1.0.1
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

如果缺少 WSL / Ubuntu，脚本自动启动安装，并可能弹出 Windows 管理员授权窗口。系统要求重启时先重启，再运行同一个 `install.ps1`。Ubuntu 首次启动可能要求创建 Linux 用户名和密码，这是系统初始化步骤。

安装完成后打开新的 PowerShell 或命令提示符：

```powershell
ignovate set up
```

不想重开终端时，在当前 PowerShell 执行：

```powershell
$env:Path = "$env:LOCALAPPDATA\Ignovate\bin;$env:Path"
ignovate set up
```

也可用 `install.ps1 -Distribution <已安装的发行版名称>` 选择其他支持的 WSL2 Linux 发行版。已有 WSL1 发行版会自动尝试转换到 WSL2，缺少虚拟化平台时会启动系统安装，重启后续装。PowerShell 启动器把当前项目目录和命令参数传入 WSL；显式的 Windows `--project` 路径也会转为 Linux 路径。Linux 中运行命令、Git 和 MCP stdio 服务；它们需要 Linux 可执行文件。用户连接配置和会话数据位于 WSL 用户目录。

## 安装位置和网络

- Unix 启动器：`~/.local/bin/ignovate`；Windows 启动器：`%LOCALAPPDATA%\Ignovate\bin\ignovate.cmd`。
- 环境与应用：`${XDG_DATA_HOME:-~/.local/share}/ignovate/`，按版本保存环境。
- 模型连接：`~/.ignovate/config.json`（或 `IGNOVATE_CONFIG_DIR`）；重复安装不会覆盖模型连接和项目数据。
- `IGNOVATE_INSTALL_HOME` / `IGNOVATE_BIN_DIR` 可自定义 Unix 安装目录；安装时用 `IGNOVATE_NO_MODIFY_PATH=1` 可禁止 shell / 用户 PATH 的持久修改。
- 首次配置需要访问 astral.sh、GitHub 和 PyPI。支持这些工具各自的代理配置；下载失败会停止，不标记环境已就绪，恢复网络后可重新运行 `set up`。
- uv 安装器固定为 0.12.23；依赖清单固定版本并校验哈希；ripgrep 固定为 15.2.0，下载后校验官方 SHA-256。下载地址见 [uv 文档](https://docs.astral.sh/uv/reference/installer/) 和 [ripgrep Releases](https://github.com/BurntSushi/ripgrep/releases)。
- Git 是可选的项目工具。缺少时会提示，普通对话及文件工具仍可用；使用 diff / review 前需安装 Git（macOS 可安装 Command Line Tools，Ubuntu 可运行 `sudo apt install git`）。

Release 页面同时提供外层 `SHA256SUMS`，可在解压前校验下载文件。解压后的安装器还会校验内部 wheel、脚本和依赖清单。

## 构建与发布

```sh
python -m pip wheel --no-deps --wheel-dir dist .
python scripts/build_release.py --wheel dist/ignovate_harness-1.0.1-py3-none-any.whl
```

更新依赖锁定清单时：

```sh
uv pip compile requirements.txt --python-version 3.11 --universal --generate-hashes --no-annotate --no-header -o requirements-release.lock
```

修改版本后同步此文档中的下载文件名。推送与 `pyproject.toml` 版本一致的 `v*` tag，会运行 `.github/workflows/release.yml`：测试、构建、macOS/Linux 真实安装验证、Windows PowerShell 验证，然后创建 Release 并上传两个安装包、wheel 和校验清单。测试失败不发布。
