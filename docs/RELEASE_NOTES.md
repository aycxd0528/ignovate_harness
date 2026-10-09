修复 Windows 11 中缺少 TERM 时自动降级到 inline 的问题。CMD、PowerShell 和 Windows Terminal 的交互控制台现在默认使用与 macOS 相同的 Textual 界面。欢迎页保留实心字标与偏移双线轮廓；Token Weather 使用相同的颜色、趋势条和输入框上方布局，模型与权限信息在右侧。

纳入 macOS 已有的趋势条缩放：最近 8 轮的实际上下文用量按相对范围显示，增长和下降可见，相同用量保持平线，缺失用量保留空点。百分比仍表示真实窗口占用。

升级：下载新的 `*-windows.zip` 或 `*-unix.tar.gz`，解压并再次运行安装脚本，然后打开新终端执行 `ignovate set up`。已有模型配置和项目数据会保留。Windows 11 x64 原生运行，无需 WSL、预装 Python 或管理员权限。

增加真实 Windows 控制台验收，使用 Textual WindowsDriver 检查欢迎字标、带颜色的 Token Weather、左右布局和退出后的控制台模式恢复。IDE、重定向输出和 TERM=dumb 保留原有界面回退；显式 `--ui` 继续有效。

安装详情见 [v1.0.3 安装说明](https://github.com/aycxd0528/ignovate_harness/blob/v1.0.3/docs/INSTALL.md)。
