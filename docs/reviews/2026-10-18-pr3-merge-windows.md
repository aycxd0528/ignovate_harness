# PR #3 合并与 Windows 检查修复

## 证据

[Windows native 失败运行](https://github.com/aycxd0528/ignovate_harness/actions/runs/39103946576) 在 Python 3.11 和 3.13 上失败；Ubuntu 两组测试和 Windows 安装器检查已通过。失败包含清理 `checkpoints.sqlite` 时的 `WinError 32`、源码更新检查未触发，以及自然任务验证后仍停在 paused。

## 修复

- 将最新 `main` 合入 PR 分支，解决 README 与 CLI 参考的冲突，保留 v1.2.3 下载链接和 Windows 桌面支持说明。
- SQLite connection 的上下文管理只提交或回滚事务，并不关闭连接。空会话检查器改为显式关闭只读连接；对应测试也关闭自己创建的连接。新回归持有真实 SQLite connection 引用，确认检查后连接已关闭，无需等待垃圾回收。
- Windows 快速写入可能保留相同文件长度和时间戳。源码检查不再仅凭这两个字段跳过内容核对；新回归修改等长源码并还原时间戳，确认仍拒绝混用源码。136 个 Python 文件的本机测量约 3.5ms/次，Windows 行为由 CI 验证。
- 自然任务验证的测试夹具改用已有跨平台 Python 命令构造器，避免将 POSIX 的 `shlex.quote` 语法交给 Windows shell；继续真实执行失败、修复和验证成功的流程。

两项新增回归均先观察到失败，再应用实现修复。定向 Python 回归 63 项及 2 个 subtests 通过，桌面运行路径的 5 项 Node 检查通过，文档本地链接和冲突标记检查通过。独立复审未发现阻塞问题。

合并后的本地原生测试执行器：1689 tests，OK，45 skipped。v1.2.3 wheel 构建及安装 smoke 通过，未请求真实模型。Windows CI 将在修复提交推送后重新运行。
