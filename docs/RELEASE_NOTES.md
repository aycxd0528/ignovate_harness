Windows 11 x64 现在原生运行，无需 WSL 或 Linux 环境。

从 Release 下载 `*-windows.zip`（Windows 11）或 `*-unix.tar.gz`（macOS / Linux），解压后运行 `install.ps1` 或 `install.sh`。Windows 安装在当前用户目录，无需预装 Python或管理员权限。

安装后打开新的终端，输入：

```sh
ignovate set up
```

自动检测或下载 uv、Python 3.11–3.13、固定版本的应用依赖和 ripgrep，然后进入模型连接配置向导。支持重复配置和依赖修复；无需手工激活虚拟环境。

Windows 文件工具使用原生句柄和 ACL；命令使用 Windows PowerShell，并在取消或超时时清理子进程。MCP stdio 服务和 Git 使用 Windows 可执行文件。文件工具支持本地盘符路径，网络共享路径暂不支持。

`ignovate set up --environment-only` 只准备环境；`ignovate doctor` 检查环境和配置。Git 是可选工具，diff / review 需要 Git for Windows。

从旧 WSL 安装升级时，WSL 内原有配置和数据保持原位置，原生版首次需要重新配置模型。详细步骤见安装包中的 `INSTALL.md` 和 [安装文档](https://github.com/aycxd0528/ignovate_harness/blob/v1.0.2/docs/INSTALL.md)。
