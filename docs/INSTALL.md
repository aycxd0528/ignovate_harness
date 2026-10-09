# 从 GitHub Release 安装 ignovate

下载地址：[GitHub Releases](https://github.com/aycxd0528/ignovate_harness/releases/latest)。安装包包含应用 wheel、环境配置脚本、固定版本的依赖清单和 SHA-256 校验清单，不需要预装 Python，也不需要克隆源码。

## macOS / Linux

支持 Apple Silicon、Intel macOS，以及 x86_64 / ARM64 Linux。下载 `ignovate-1.0.3-unix.tar.gz`，解压后执行：

```sh
tar -xzf ignovate-1.0.3-unix.tar.gz
cd ignovate-1.0.3
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

## Windows 11

支持 Windows 11 x64 原生运行。无需 WSL、Linux 环境、虚拟化或预装 Python；安装在当前用户目录，无需管理员权限。

下载 `ignovate-1.0.3-windows.zip`，在 PowerShell 中执行：

```powershell
Expand-Archive .\ignovate-1.0.3-windows.zip -DestinationPath .\ignovate-release
cd .\ignovate-release\ignovate-1.0.3
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

安装完成后打开新的 PowerShell 或命令提示符：

```powershell
ignovate set up
```

Windows 交互控制台默认使用与 macOS 相同的 Textual 界面，包括双线轮廓欢迎字标和输入框上方的 Token Weather。Windows Terminal、CMD 和 PowerShell 无需设置 `TERM`。IDE 控制台、重定向输出或 `TERM=dumb` 保留原有回退；可用 `--ui textual` 显式选择界面。

不想重开终端时，在当前 PowerShell 执行：

```powershell
$env:Path = "$env:LOCALAPPDATA\Ignovate\bin;$env:Path"
ignovate set up
```

启动器保留当前目录、原始参数和退出码，Python 应用直接使用当前控制台。命令工具使用 Windows PowerShell；Git 与 MCP stdio 服务应配置 Windows 可执行文件和路径。模型连接位于 Windows 用户目录 `~/.ignovate/config.json`，使用私有 ACL；项目文件替换保留原 ACL，只读文件不会被强制覆盖。文件工具支持本地 NTFS 盘符路径，拒绝目录联接、重解析点、备用数据流和设备路径；网络共享路径暂不支持。项目编辑暂不支持压缩、加密、稀疏文件及含备用数据流的文件；遇到这些文件会拒绝修改并保留原文件。

从旧 WSL 安装升级时，安装器会替换旧的 Windows 启动器。WSL 内原有模型连接和会话保留在那里，原生版使用 Windows 用户目录，首次需要重新配置模型。

## 安装位置和网络

- Unix 启动器：`~/.local/bin/ignovate`；Windows 启动器：`%LOCALAPPDATA%\Ignovate\bin\ignovate.exe`。
- 环境与应用：`${XDG_DATA_HOME:-~/.local/share}/ignovate/`，按版本保存环境；Windows 默认在 `%LOCALAPPDATA%\Ignovate\venvs\<版本>`，工具在同目录的 `tools`。
- 模型连接：`~/.ignovate/config.json`（或 `IGNOVATE_CONFIG_DIR`）；重复安装不会覆盖模型连接和项目数据。
- `IGNOVATE_INSTALL_HOME` / `IGNOVATE_BIN_DIR` 可自定义 Unix 安装目录；Windows 使用 `install.ps1 -InstallHome <目录> -BinDirectory <目录>` 或 `IGNOVATE_INSTALL_HOME` / `IGNOVATE_WINDOWS_BIN_DIR`；安装时用 `IGNOVATE_NO_MODIFY_PATH=1` 可禁止 shell / 用户 PATH 的持久修改。
- 首次配置需要访问 astral.sh、GitHub 和 PyPI。支持这些工具各自的代理配置；下载失败会停止，不标记环境已就绪，恢复网络后可重新运行 `set up`。
- uv 安装器固定为 0.12.23；依赖清单固定版本并校验哈希；ripgrep 固定为 15.2.0，下载后校验官方 SHA-256。下载地址见 [uv 文档](https://docs.astral.sh/uv/reference/installer/) 和 [ripgrep Releases](https://github.com/BurntSushi/ripgrep/releases)。
- Git 是可选的项目工具。缺少时会提示，普通对话及文件工具仍可用；使用 diff / review 前需安装 Git（macOS 可安装 Command Line Tools，Ubuntu 可运行 `sudo apt install git`，Windows 安装 Git for Windows）。

Release 页面同时提供外层 `SHA256SUMS`，可在解压前校验下载文件。解压后的安装器还会校验内部 wheel、脚本和依赖清单。

## 构建与发布

```sh
python -m pip wheel --no-deps --wheel-dir dist .
python scripts/build_release.py --wheel dist/ignovate_harness-1.0.3-py3-none-any.whl
```

更新依赖锁定清单时：

```sh
uv pip compile requirements.txt --python-version 3.11 --universal --generate-hashes --no-annotate --no-header -o requirements-release.lock
```

修改版本后同步此文档中的下载文件名。推送与 `pyproject.toml` 版本一致的 `v*` tag，会运行 `.github/workflows/release.yml`：测试、构建、macOS/Linux 真实安装验证、Windows 原生启动器、实际自动安装及文件/进程工具验证，然后创建 Release 并上传两个安装包、wheel 和校验清单。测试失败不发布。
