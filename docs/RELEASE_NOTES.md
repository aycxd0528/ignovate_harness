Ignovate v1.2.6 更新 macOS 与 Windows 桌面安装包的 App 图标，沿用现有品牌蓝与双眼圆形头像。

## 本次更新

- Mac 的 `.app` 内嵌 ICNS 图标，适配 Finder、Applications 与 Dock；包含最高 1024px 的多种分辨率。
- Windows 的 `Ignovate.exe` 内嵌 ICO 图标，包含 16、24、32、48、64、128、256px 七种尺寸；窗口沿用 EXE 图标。
- 保留所选图标的白色圆角底板与外围透明区域。打包资源相对脚本位置解析，避免工作目录变化导致遗漏；资源缺失时阻止打包。
- 包含 v1.2.5 的长任务可靠性修复。运行时逻辑与依赖版本不变。

## 下载与安装

- **Apple Silicon Mac（macOS 13+）**：下载 `Ignovate-1.2.6-macos-arm64.dmg`，将 `Ignovate.app` 拖入 Applications 后打开。Intel Mac 可使用 Unix CLI 安装包。
- **Windows 11 x64**：下载 `Ignovate-1.2.6-windows-x64.zip`，完整解压后双击 `Ignovate.exe`，保留旁边的 resources 和 DLL。
- **macOS / Linux CLI**：下载 `ignovate-1.2.6-unix.tar.gz`，解压后执行 `install.sh`。
- **Windows CLI**：下载 `ignovate-1.2.6-windows.zip`，解压后按安装文档运行 PowerShell 安装器。

桌面包内置 Python、锁定依赖与 ripgrep，无需预装 Python 或 Node.js。Mac 包采用 ad hoc 本地签名，尚未取得 Developer ID 签名和 Apple 公证；Windows 桌面包当前未签名。

## 升级与边界

升级前退出旧版与正在运行的任务，再替换 Mac 应用或将 Windows 新版完整解压到新目录。已有用户配置与项目会话保留。新版可读取已有 checkpoint；压缩 checkpoint 需要支持该格式的 reader，请勿用旧程序打开升级后的会话。

既有离线验收覆盖 200 轮任务、201 条要求、7 次压缩、4 次关闭重开和 1400 个快照的恢复。这些是已验证样本，不代表任意任务规模、远端模型质量或永久 I/O 停滞下的无限运行保证。

详见 [安装文档](https://github.com/aycxd0528/ignovate_harness/blob/v1.2.6/docs/INSTALL.md)。
