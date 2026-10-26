Ignovate v1.2.4 汇总长任务可靠性修复，并更新 macOS 13+ Apple Silicon 桌面安装包。

## 本次更新

- 改进长任务的要求保留、上下文压缩和完整历史归档；压缩 checkpoint 可在进程重启后恢复，减少重复快照的存储占用。
- 修复目标、费用、任务身份、阶段反馈及产物来源的恢复与并发处理问题；未知副作用保留待核验状态，不自动重放工具。
- 改进停止与退出时的工具进程、MCP、SQLite 和 HTTP 资源收尾；POSIX 监护进程在确认关闭前回收直接子进程，MCP 健康通道等待实际 guardian 生命周期结束。
- 修复 GUI 多会话任务关联、旧任务控制请求和人工验收/反馈的锁争用问题；改进长回答与恢复后的显示。
- Mac 桌面包内置 Python 3.11、锁定依赖及 ripgrep，无需预装 Python、Node.js 或克隆源码。Mac 打包现已接入 push / PR 检查，正式发布复用同一流程，并从实际 DMG 验证中文路径安装、内置运行时启动和退出后的签名。
- 继续提供 Windows 11 x64 桌面 ZIP、Unix/Windows CLI 安装包、Python wheel 与各平台 SHA-256 清单。

## 下载与安装

- **Apple Silicon Mac（macOS 13+）**：下载 `Ignovate-1.2.4-macos-arm64.dmg`，将 `Ignovate.app` 拖入 Applications 后打开。Intel Mac 可使用 Unix CLI 安装包。
- **Windows 11 x64**：下载 `Ignovate-1.2.4-windows-x64.zip`，完整解压后双击 `Ignovate.exe`，保留旁边的 resources 和 DLL。
- **macOS / Linux CLI**：下载 `ignovate-1.2.4-unix.tar.gz`，解压后执行 `install.sh`。
- **Windows CLI**：下载 `ignovate-1.2.4-windows.zip`，解压后按安装文档运行 PowerShell 安装器。

Mac 包采用 ad hoc 本地签名，尚未取得 Developer ID 签名和 Apple 公证；macOS 可能阻止首次打开。Windows 桌面包当前未签名。

## 升级与边界

升级前退出旧版与正在运行的任务，再替换 Mac 应用或将 Windows 新版解压到新目录。已有用户配置与项目会话保留。新版可读取已有 checkpoint；新写入的压缩 checkpoint 需要新版 reader，请勿用旧程序打开升级后的会话。

离线验收覆盖了 200 轮任务、201 条要求、7 次压缩、4 次关闭重开和 1400 个快照的恢复，以及桌面与浏览器场景。这些是已验证样本，不代表任意任务规模、远端模型质量或永久 I/O 停滞下的无限运行保证。

详见 [安装文档](https://github.com/aycxd0528/ignovate_harness/blob/v1.2.4/docs/INSTALL.md) 与仓库内可靠性审查记录。
