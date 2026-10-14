# Windows / macOS / Linux UI 一致性审查

审查日期：2026-10-10。审查对象：已发布的 [v1.0.3](https://github.com/aycxd0528/ignovate_harness/releases/tag/v1.0.3)，提交 `cd2bd720396a3eac31c0107f809bbbf5eab4daac`。本轮审查在原始两处修复、Release 发布和 README/飞书更新完成后开展。

欢迎字标、配置步骤、Token Weather 与模型信息分列、输入区、菜单和审批使用三端共享的 Textual 组件与样式。未发现另一套 Windows 布局。用户截图中的平面字标和灰色天气栏来自错误选择 inline；v1.0.3 修正 Windows 缺少 TERM 时的默认选择，并纳入已有 macOS 趋势条缩放。真实 WindowsDriver 验收已通过。

仍有两项 P2 兼容差异，不能宣称所有 Windows 11 终端的颜色和按键体验已完全一致。这两项是审查结果，尚未修改生产代码或另行发布。

- **P2：经典控制台可能把 RGB 主题降为 256 色。** [SetupApp 构造](/Users/aycxd/Desktop/LLM_project/ui/setup.py:123) 和 [TerminalAgentApp 构造](/Users/aycxd/Desktop/LLM_project/tui.py:272) 先创建 Textual Console，后启动 WindowsDriver。锁定的 Rich 在输出句柄的 VT 位尚未启用时选择并缓存 `256`；驱动随后启用 VT，已有 Console 没有重新检测。Windows 11 具备 RGB 输出能力，但这一启动顺序会使颜色偏离共享主题。控制实验得到 `initial=256`、`after_vt_enabled=256`、新建 Console 为 `truecolor`；示例主题色 `#88c0d0` / `#11161c` 分别编码为 `38;5;110;48;5;16` 和精确 RGB `38;2;136;192;208;48;2;17;22;28`。比较时使用独立 Style，避免验证脚本自身复用 ANSI 缓存。启动时已经启用 VT 的 Windows Terminal 会话不受该条件影响。建议在支持的 Windows 控制台驱动初始化后、首次渲染前确认颜色深度，同时尊重显式颜色配置。

- **P2：部分 Windows 终端上的 Ctrl+Enter 换行无法生效。** [ChatInput 绑定](/Users/aycxd/Desktop/LLM_project/tui.py:104) 只处理 `ctrl+enter`。不报告增强键盘协议的宿主可把该组合键发送为 LF；锁定的 Textual 将 LF 解析成 `ctrl+j`，输入区目前不处理它。本地共享输入组件复现：`a, ctrl+j, b` 得到 `ab`，`a, ctrl+enter, b` 得到 `a\nb`。[帮助面板](/Users/aycxd/Desktop/LLM_project/ui/help_view.py:167) 的“填入命令”也依赖同一组合键。建议提供 LF 兼容绑定，并同步帮助面板。支持增强键盘报告的新版终端可能不会命中此问题；该发现不等于所有 Windows 11 会话均失败。[Microsoft 维护者说明](https://github.com/microsoft/terminal/discussions/17401) 支持该宿主编码差异。

验证范围：

| 项目 | 结果与证据 |
| --- | --- |
| 三平台发布和实际安装 | [Release 检查](https://github.com/aycxd0528/ignovate_harness/actions/runs/37927570145) 全部通过；发布资产版本、内外 SHA-256、打包的界面源码和安装后 wheel smoke 已核验 |
| Windows 完整运行时 | [原生检查](https://github.com/aycxd0528/ignovate_harness/actions/runs/37927569628) 通过；Python 3.11/3.13 各 1147 项，21 项平台跳过 |
| 实际 WindowsDriver | 安装后的 wheel 81 项验收通过，8 项跳过；覆盖无 TERM 控制台、双线字标、天气颜色样式、左右对齐和退出后的控制台模式恢复 |
| 本地完整测试 | 1147 项通过，43 项平台跳过 |
| 本轮 UI 专项 | 111 项通过，39.042 秒；覆盖配置、深浅主题、窄屏、输入、菜单、审批、复制和状态栏 |
| 本轮布局采样 | 54 个场景：深色/浅色 × 80×24、100×32、213×56 × 欢迎、连接、推理、权限、空会话、用量示例、命令菜单、帮助、审批；控件边界与天气分列断言通过 |
| 剩余差异复现 | [兼容性证据](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/compatibility-evidence.json)；颜色深度条件模拟与共享输入组件复现，未调用模型 API |

[布局观察数据](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/observations.json) 与 [采样脚本](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/capture.py) 已保存。SVG 和 PNG 使用本机 Headless Textual 及本机字体生成，用量为审查夹具中的示例，**不是 Windows 11 实机截图**。

| 代表场景 | 深色 100×32 | 浅色 80×24 |
| --- | --- | --- |
| 欢迎页 | [PNG](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/welcome-dark-100x32.png) · [SVG](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/welcome-dark-100x32.svg) | [SVG](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/welcome-light-80x24.svg) |
| Token Weather / 输入区 | [PNG](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/composer-active-dark-100x32.png) · [SVG](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/composer-active-dark-100x32.svg) | [SVG](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/composer-active-light-80x24.svg) |
| 审批 | [PNG](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/approval-dark-100x32.png) · [SVG](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/approval-dark-100x32.svg) | [SVG](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-10-windows-ui-parity/approval-light-80x24.svg) |

Windows CI 使用 Windows Server 2025。原生验收检查驱动、组件与布局，并未核验输出 RGB 深度或桌面字体的逐像素外观；实际 Windows 11 键盘、粘贴和实时窗口缩放仍缺少实机观察。字体、Unicode 字宽、终端能力、NO_COLOR、ASCII 回退和显式主题也会影响外观，不能把所有宿主差异都当作另一个 UI 设计。

剪贴板属于宿主能力差异：三端使用 OSC52，macOS 另有 pbcopy；[Microsoft 维护者](https://github.com/microsoft/terminal/issues/2946) 确认 Windows Terminal 支持默认启用的 OSC52 写入。因此未将默认 Windows Terminal 的复制功能判定为缺陷；经典控制台的系统剪贴板行为仍未实机确认。

发布验证中曾遇到与本轮 UI 无关的既有并发审批测试顺序假设；按实际 interrupt ID 绑定决策的正反序已验证，原标签完整检查重跑通过。另一轮独立 Windows 3.11 检查提前退出，重跑完整检查通过；未降低任何测试断言或发布门槛。
