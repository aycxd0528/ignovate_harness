# ignovate harness 项目审查与修复记录

日期：2026-10-08。范围：现有 CLI、文件与命令工具、权限、任务/目标/会话运行时、MCP、核心终端交互及 Python 发行配置。

## 基线与方法

本地原 HEAD 为 `7bf6d41`，产品代码多数尚未纳入 Git；远端仓库只有初始化的 MIT LICENSE，提交时保留两侧历史。本轮按模块阅读源码，安排三个独立只读基线审查，并以临时项目、假模型和本地 MCP 服务复现问题。

基线完整测试：`python -m unittest discover -s tests -v`，1018 项，99.304 秒，OK。已有测试通过并不代表下列边界正确，所有行为修复均新增对应失败回归并验证修复后通过。

## 已修复问题

| 问题 | 根因与修复 | 回归位置 |
| --- | --- | --- |
| wheel 安装后的 doctor 崩溃 | 安装环境不能依赖源码 requirements.txt；改读 distribution Requires-Dist，修复安装提示 | test_workflow_diagnostics.py、scripts/smoke_wheel.py |
| doctor 接受启动会拒绝的连接参数 | 两套校验规则不一致；复用 validate_connection，非法地址、模型和控制字符不再通过 | test_workflow_diagnostics.py |
| 项目命令链接读取受保护文件 | 检查解析后的保护组件，并使用安全 opener，关闭校验到读取之间的链接替换窗口 | test_v1_features.py |
| 精确文件授权扩展到同名后缀 | Path.match 使用后缀语义；改为项目根锚定规则，保留 **/ 零层目录行为 | test_permissions.py |
| 读取期间替换链接导致越界 | resolve 与 open 有窗口；用逐级 dir_fd、O_NOFOLLOW 和普通文件描述符读取 | test_nailong_files.py |
| 含 NUL 的 UTF-8 正则搜索漏检 | ripgrep 默认 binary 策略与 literal 语义不一致；明确 --text | test_nailong_files.py |
| 正则后端可查询竞态替换的受保护文件 | rg 跟随显式路径链接；改为安全读取的有界私有快照，失败文件记为部分覆盖 | test_nailong_files.py |
| 新文件忽略限制性 umask | 临时文件被固定 chmod 0644；创建模式由系统 umask 作用，已有文件保留原权限 | test_nailong_files.py |
| 权限变化仍沿用旧验证证据 | 指纹只包含路径和内容；加入 mode，并检查读取期间 mode 稳定性 | test_workflow_verification.py |
| 取消审批后下一轮无法恢复 | 新轮补结果未知消息，通过 END 清旧调度，兼容禁用全部工具的图，避免自动重放 | test_managed_runtime_integration.py |
| 目标并发结算丢失费用和轮次 | 实例内 RLock 无法保护多个存储实例/进程；加入 fcntl 事务锁和驱动独占锁 | test_goal_store_regressions.py |
| 间隔阻塞算成连续三轮 | 未检查观察轮次邻接，恢复时未重置；新增连续性判断和恢复重置 | test_goal_store_regressions.py |
| MCP 名称暴露已知凭据 | 只净化 description/schema；发现时拒绝含凭据的名称 | test_mcp_client.py、fixtures/mcp_server.py |
| 完整审批漏显示外部 command/cwd | 无条件过滤业务参数；只过滤已实际展示字段 | test_ui_approval_panel.py |
| 项目切换失败后状态错位 | 先改状态、关旧 factory 再构造新 factory；完整构造后才采用新状态 | test_ui.py |
| 回退确认 Ctrl+C 结束 inline UI | 交互取消与外部 Task 取消混用；独立 InteractionCancelled，本地确认处理用户取消 | test_ui.py |

## 发布整理

- 补齐 README、MIT 许可和仓库/说明文档元数据。
- 新增 Python 3.11/3.13 GitHub Actions 与实际 wheel smoke；后续完成 workflow 授权并启用远端 CI，见补充验证记录。
- 新增 docs/PROJECT.md，飞书指定文档使用相同正文。
- 使用明确提交清单，避免纳入密钥、本机配置、IDE 文件、运行数据和私人评估产物；保留本地已有文件。

## 验证

修复后完整测试：`python -m unittest discover -s tests -q`，1043 项，99.744 秒，OK，比基线新增 25 项。`python -m pip check`：No broken requirements found。

实际构建 wheel 后，从源码目录之外加载发行包，检查 CLI help、JSON doctor 和依赖报告，缺少凭据返回报告而非 traceback。smoke 未调用模型 API。

独立最终复审额外复现了命令读取竞态、正则后端查询竞态及禁用全部工具时的审批取消恢复问题。新增四项回归先失败，修复后定向测试与上述全量测试通过。其余边界按静态阅读和本地假模型验证，不视为第三方实机验证。

指定飞书项目说明已发布并回读全文一致：9 个章节、5 张表格、4 个代码示例。后续远端 CI 已通过 Python 3.11/3.13 测试和 wheel smoke，详见 [补充验证记录](2026-10-08-ci-real-api-verification.md)。

## 保证范围与后续验证

初次审查未调用真实模型 API；后续已使用 deepseek-flash 完成六类场景及补测，原始失败记录保留在补充报告中。第三方 MCP、Windows 实机与不同终端剪贴板仍未验证。当前安全文件打开与跨进程锁依赖 POSIX；推荐 macOS/Linux。未知模型的窗口和价格需配置。

批准 shell 命令不是操作系统沙箱；自行脱离进程组的后台程序可保留副作用。文件修改前的版本检查无法提供跨其他进程的通用原子比较替换保证。以上边界在项目说明中明确记录，不作为已消除风险宣告。
