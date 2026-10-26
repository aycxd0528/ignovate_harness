# MCP 关闭等待真实监护器生命周期

日期：2026-10-25。基准 `bd8fa20b208482a340c4254f73ada27c753307ca`；同时包含已单独审查的直接子进程回收修复，见 `2026-10-25-posix-direct-child-reaping.md`。

## 原生根因与 P1 修复

真实 SDK 内部取消可以直接 SIGKILL 它启动的外层 launcher。原健康 socket 只由 launcher 持有，其 EOF 会早于独立内层 guardian 完成 lifeline 关闭清理，关闭调用存在提前返回窗口。原严格 MCP 取消断言在直接回收修复后仍失败，不能归咎于测试或据此重试消除。

新原生回归运行真实 launcher 与 guardian，仅在 guardian 读到真实 lifeline EOF 后设受控 gate：杀死并等待 launcher 后，确认真实服务仍存在；此时 socket 的非阻塞 `MSG_PEEK` 必须得到 BlockingIOError（尚无 EOF）。旧代码稳定 FAIL：`BlockingIOError not raised`。这独立证明了通道生命周期缺口。历史早期失败缺少进程状态；本轮诊断在 exists=True 后 ps 已找不到进程，不能对每一次旧失败作运行/zombie/PID 复用的事后判定。

最小修复：`PosixProcessGuard` 增加默认空的私有 `keepalive_fds`，借用已认证健康 socket，并通过现有 Popen `pass_fds` 传入独立 guardian。guardian 不读写健康协议；实际服务 Popen 保持默认 `close_fds=True`，不继承健康 socket。launcher 被杀后，最后 socket 引用随 guardian 退出才关闭，现有关闭流程因此等待其生命周期。仍只有 launcher 能在已核实内层关闭后发送 `C`；launcher 丢失继续报告“清理无法核实”，不伪装干净退出。

没有增加进程层、协议中转、后台服务、sleep 重试或放宽原存活断言；普通 POSIX guard 默认 fd 集合及 Windows/HTTP 路径保持原行为。

## 最终本地证据

新原生回归和原 SDK 内部取消回归：2 项 / 0.627 秒 GREEN；原断言未修改。最终相关 12 模块 167 项 / 4 skip / 78.388 秒 GREEN。真实 gate 不释放时不能靠存在性轮询跳过证据。

| 最终范围 | 项数 | skip | 秒 | terminal |
|---|---:|---:|---:|---:|
| macOS Python 3.11 源码全量 | 2233 | 54 | 388.183 | 0 |
| macOS Python 3.13 源码全量 | 2233 | 55 | 390.408 | 0 |
| 工作区外新 wheel 实装 3.11，相关 12 模块 | 167 | 4 | 80.249 | 0 |
| 工作区外新 wheel 实装 3.13，相关 12 模块 | 167 | 4 | 80.466 | 0 |

wheel SHA256 `ee0250e546cb9a9bbf5fa4288d438c3d2fd1e842c27ade154d42b325a8c7f4c9`；实际 pip --target 安装后，159 个 Python 文件和 39 个 GUI 静态资源逐字节匹配，10 个相关模块 origin 来自安装目录。实装不是此前 844 项范围，本轮按实际相关模块报告。外部目录 CLI/help/JSON doctor smoke 通过，未请求真实模型服务。

原始日志 `/tmp/ignovate-mcp-lifetime-{full311,full313,installed311,installed313}.log`；完整 SHA256 索引见 `2026-10-25-mcp-guardian-lifetime-validation.json`。执行句柄 32802/30417/30128/92231 及聚焦 89330 均已读取 terminal exit0。两个冻结代码 diff 在最终门后重新计算一致，没有在审查后修改这些产品/测试路径。

## 唯一独立审查与作者一次处置

原文 `2026-10-25-mcp-guardian-lifetime-review.md`，SHA256 `aa56dd0a346b350aca6ce5195c445cc83554b4ee54868e08d350ea0b74e1cd8d`；本轮三路径冻结 diff SHA256 `77a746410c91de2400c19a2360fe5977b010ec43b21caf43e75c736491019d36`。唯一 fresh review 0 Critical / 0 Important / 0 Minor；没有重审前一直接回收修复、重复跑门或派生 reviewer。

作者完整阅读报告，一次裁决：

- R1：采纳 fd 传递覆盖 launcher 死亡到 guardian 退出窗口的判断；无补修。
- R2：采纳借用 fd 归属与实际服务默认关闭多余 fd 的判断；cost：继续保留真实服务 socket 不继承回归，不把对 guardian 的继承扩大为用户命令可继承。
- R3：采纳仅外层发 C，内层持有 fd 只延迟 EOF 的判断；cost：launcher 丢失仍有保守 unknown，需要核对副作用，不能声明清理已验证。
- R4：采纳 stdio/自然有符号退出/启动错误/默认空集合/Windows 选择边界不变的分析；cost：本机仍只证明 macOS，两版 Linux/Windows 需实际 CI。
- R5：采纳原生 gate 与 MSG_PEEK 建立因果顺序、完整相关门绿色的证据；cost：不为所有早期 PID 观测补写不存在的进程状态。
- R6：采纳没有新增取消/超时策略的判断；不做第二轮同范围审查；最终提交后真实平台门另记。

全部 Considered / Declined 项及代价：

- D1：不增加 supervisor/relay，避免另一层生命周期与失败面。
- D2：不让内层发 C，避免失去外层时被误判干净。
- D3：不让实际服务继承 socket，避免它阻止 EOF 或伪造回执。
- D4：不 sleep、重试 PID 或放宽原断言，消除资源归属顺序缺口本身。
- D5：不引入新的 guardian 超时/取消策略；cost：冻结 guardian/内核不可中断状态没有新增硬时限保证。
- D6：不将唯一私有调用扩展成任意 fd 的通用所有权 API；无效 fd 仍由原 Popen/构造清理路径处理，传入者保留原句柄所有权。
- D7：不重复相同套件或再审同一 delta；报告记录作者门与 reviewer 自己执行的区别。
- D8：不重开已审的直接回收 delta；两者分别保存冻结边界和报告。
- D9：不对每一次历史间歇失败做法证归因；新确定性因果证明独立成立。
- D10：不宣称组逃逸后代、所有孙进程回收或内层被外部破坏后的普遍保证；unknown 语义保持。
- D11：不宣称任意停止/内核状态/调度下的严格 shutdown wall-clock SLA；socket 按实际 guardian 生命周期关闭。
- D12：报告不授权整个项目 release/merge/deploy；新 PR 留待用户集成决定。

## PR5 合并后平台观察

PR5 在 12 个合并前检查成功后，于 `2026-10-25T09:11:48Z` 合并为 `bd8fa20`，main 的 tree 与已验证 head `4351b9e` 一致，原用户 IDE 暂存和 CSS 字节保留。合并后 main 的 Tests and distribution / Windows desktop 成功；Windows native run `40179640612` 中 installer 与 3.13 成功，3.11 的 2230 项 / 99 skip / 1254.881 秒有 1 FAIL：`test_ui_conversation.ConversationLayoutTests.test_delivery_once_then_plain_question_does_not_repeat_old_report`，verified 子场景观察时仍显示“正在推理”，尚无“普通回答”。日志 `/tmp/ignovate-pr5-main-native-failed.log`。

该 main 运行发生在本轮两项修复之前，不是这些 POSIX 变更的 Windows 结果，也不能把合并前绿色冒充合并后全部绿色。此 UI 渲染观察问题另行定位；本轮两个 P1 的本地关闭证据完整，新 head 平台门仍需观察。未合并新分支、未发布 release，整体审查 goal 继续 active。
