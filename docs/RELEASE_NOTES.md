Ignovate v1.2.3 新增 Windows 11 x64 桌面版，并修复 Windows Python 3.13 的取消测试启动等待超时。

- 修复发布验证在 Node 22 下向中文目录同步复制文件时产生的路径错误，继续验证实际安装包的中文路径启动。
- Windows 桌面包：下载 `Ignovate-1.2.3-windows-x64.zip`，解压完整目录后双击 `Ignovate.exe`。内置 Python 3.11、锁定依赖及 ripgrep，无需安装 Python、Node.js 或 WSL。
- 支持中文及含空格的安装路径、原生剪贴板、工作区选择和现有聊天、会话地图、轨迹与模型设置。
- 关闭窗口或桌面父进程退出时，通过私有管道通知内核，先取消工具进程并关闭数据库，避免 Windows 强制终止跳过清理。
- 修复取消计费测试将真实文件读取、SQLite 与图执行误限为 3 秒的问题；保留取消、费用结算与预算断言，增加受控慢启动回归。
- 修正原生 Windows 测试中的 POSIX `printf`、全局用户配置干扰和后台收尾竞态。
- 新增 Windows 桌面构建、固定 SHA-256 下载校验、ZIP 重定位启动检查及 Release 发布门禁。

Windows 桌面包当前未签名。macOS 13+ Apple Silicon 用户下载 `Ignovate-1.2.3-macos-arm64.dmg`，拖入 Applications；该包采用 ad hoc 本地签名，尚未取得 Developer ID 签名与 Apple 公证。Unix、Windows CLI 安装包、wheel 和各平台 SHA-256 清单继续提供。

升级前退出旧版，将新版解压到新目录或替换 Mac 应用；已有用户配置与项目会话保留。详见 [安装文档](https://github.com/aycxd0528/ignovate_harness/blob/v1.2.3/docs/INSTALL.md)。
