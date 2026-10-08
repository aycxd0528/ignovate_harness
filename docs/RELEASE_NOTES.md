从 Release 下载 `*-unix.tar.gz`（macOS / Linux）或 `*-windows.zip`（Windows / WSL2），解压后运行 `install.sh` 或 `install.ps1`。

安装启动器后，在终端输入：

```sh
ignovate set up
```

自动检测或下载 Python、环境管理器、固定版本的应用依赖和 ripgrep，然后进入 API 地址、模型和密钥配置向导。支持重复配置和依赖修复；无需预装 Python、克隆仓库或手工激活虚拟环境。

Windows 使用 WSL2；缺少 WSL / Ubuntu 时会启动系统安装，需要时先重启，再续装。Git 是可选工具，diff / review 功能需要安装 Git。

`ignovate set up --environment-only` 可只准备环境，适用于 CI。安装器保留已有模型连接和会话数据，GitHub assets 提供 SHA-256 校验清单。

详细步骤见安装包中的 `INSTALL.md` 和 [安装文档](https://github.com/aycxd0528/ignovate_harness/blob/v1.0.1/docs/INSTALL.md)。
