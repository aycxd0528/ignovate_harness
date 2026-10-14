Ignovate v1.1.0 增加本地桌面工作台和 Apple Silicon macOS 安装包。下载 `Ignovate-1.1.0-macos-arm64.dmg`，将应用拖入 Applications 后打开；首次启动选择工作区，在模型设置中配置提供商。包内包含 Python 3.11、固定版本的应用依赖和 ripgrep，无需克隆源码或安装 Python。

桌面工作台支持流式聊天、会话地图与追问分支、实际文件差异、Skills/MCP 插件管理、模型与推理强度选择、权限模式及通用设置。启动动画至少显示两秒，思考和工具执行使用轻微动态反馈。状态更新保留正文、工具展开、滚动和文字选择。

内核纳入阶段约束、工具事件身份与历史结果关联、累计与生成预算、原生命令参数和文件冲突处理。CLI 保留 Windows 11 原生 Textual 界面与已有配置、会话及项目数据兼容性。CLI 用户可继续使用 `*-unix.tar.gz` 或 `*-windows.zip`。

Mac 桌面包支持 macOS 13 及以上的 Apple Silicon Mac，使用 ad hoc 本地签名，尚未经过 Developer ID 签名与 Apple 公证；macOS 可能阻止首次打开。Intel Mac 可使用 CLI 安装包。安装和 SHA-256 校验说明见 [安装文档](https://github.com/aycxd0528/ignovate_harness/blob/v1.1.0/docs/INSTALL.md)。
