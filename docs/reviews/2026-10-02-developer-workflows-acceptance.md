# 开发工作流 P0/P1 实施与验收

验收日期：2026-10-03。范围：用户批准的四项 P0、四项 P1，依据 [设计](../superpowers/specs/2026-10-02-developer-workflows-design.md) 和 [实施计划](../superpowers/plans/2026-10-02-developer-workflows.md)。

## 结果

八个功能组均已接入共享核心及 Textual、inline、plain；无头模式复用本地命令结果和验证证据。使用当前 `codex/nailong-v1` 工作区实施，保留原有暂存区，没有提交、合并或推送。

- 全套本地回归：361 项，全部通过；以 `final-suite8.log` 为最终证据，之前失败日志保留用于追溯。
- 实际 CLI/pipe/PTY：12 个场景通过，包含全局命令、caller cwd、headless JSON/NDJSON、两个终端适配器的排队/停止/审批中断。这里用可控假模型验证真实输入路径，没有把假模型称为真实 API。
- 真实 DeepSeek API：5 个场景通过、8 次模型调用。默认改动审查、Skill 新旧版本加载、同一会话 flash → pro 切换及当次价格记录有实际请求证据。
- Textual：dark/light/ansi × 100×32/80×24 × 对话/审批，共 12 张真实 `run_test` 渲染；浅色代码、Markdown、底部指标可读。窄屏常规回答不再出现横向滚动条。

本地验收文件位于 `.superpowers/sdd/2026-10-02-developer-workflows/`，该目录被 Git 忽略。最终日志、原始失败、复现脚本及截图均保留；没有复制 `.env`。

## 需求逐项映射

| 功能与验收点 | 实现 | 直接证据 |
| --- | --- | --- |
| 当前/暂存/分支 diff；共同祖先、未跟踪、删除、重命名 | `core/git_changes.py`、共享 `/diff` | `test_workflow_git` 的实际 Git 临时仓库；CLI headless diff JSON |
| NUL 路径、字面量 pathspec、保护路径、二进制、链接、无 HEAD | Git argv 禁用外部比较程序，筛选并显示 skipped | Git 用例及 pathspec magic 回归；无提交仓库读取当前工作树版本 |
| 200 文件/256 KiB、20 文件顺序批次、无改动无 API | `CommandActions` 创建有范围的 review 请求，工厂/只读工具收窄 | Git/registry/service 用例；真实 API 只使用 list_files/read_file，定位 `calc.py:2` 除零 |
| `/verify` 全部/命名/列表；严格字段和 1–300 秒超时 | `core/verification.py` 校验 schema，缺配置显示示例 | verification/config 测试；未知字段和旧 timeout 字段明确拒绝 |
| 成功、失败、超时、stdout 与实际 HTTP 启动观察 | `core/processes.py` 创建并拥有进程组，回环 IP 探测 | 实际 Python 进程和 HTTP 服务用例；服务结束后端口关闭，记录真实非零清理退出码 |
| 拒绝/plan/无头零执行；审批 Ctrl+C | 共享权限和审批回调 | headless JSON 与 stream-json 均 unverified，无 marker 文件；Textual/inline/plain 拒绝及取消验收 |
| 可信步骤证据、时间、命令/cwd、退出、脱敏输出、配置/Git/内容摘要 | 核心保存整次 verification 事件 | verification 用例读取持久事件；不是模型填写结果 |
| 全部通过才放行 goal；部分/外部变化/无法读取/新输入无效 | `GoalStore` 比较完整摘要；实际生成文件白名单 | partial、generated directory 新源码、读目录失败、外部变更、取消命令使旧 proof 失效的回归 |
| doctor 离线、缺认证/依赖仍启动、安装入口/caller cwd | `nailong.cli`、提前 doctor 分支、diagnostics | 实际 `python -S main.py doctor` 返回可解析 JSON；全局 `nailong doctor` 与 cwd 一致 |
| Python/解释器/venv/requirements/Git/rg/PATH/配置/YAML/记忆/TTY/编码/价格及修复说明 | `core/diagnostics.py` | diagnostics 用例与 CLI 报告，每个非 ok 项都有 repair；默认未联网；当前项目检查通过，Skill 诊断为空 |
| status 会话名称/ID/UI/主机/模式/队列/目标/费用完整性 | `status_snapshot`、持久 `usage_snapshot` | plain pipe 两个项目都显示 UI plain；持久历史与未知费用测试；生成时即时查询 |
| Skill 重载新增/修改/移除/无效、来源和版本 | `SkillRegistry.reload`、同步工厂目录/补全 | skills_memory 测试；Textual 实际 reload 后 `$probe` 菜单可用；API v1/v2 重读正文 |
| 启用/禁用持久化、项目覆盖、权限不扩大 | 项目偏好开关，禁用后不可发现或 load | 重建注册表仍禁用、同名项目不会回退用户；Textual 禁用后菜单移除 |
| 队列容量 10、身份绑定、安全边界执行、满时保留输入 | `SessionRunner`、同步 Textual 入队、`InputBroker` | runner/Ui 回归；真实 PTY 两个后续任务按序只执行一次；切换时拒绝转移 pending |
| Ctrl+C/stop 取消当前、保留 ID/费用/待办、暂停目标和队列、显式 resume | 单一串行 runner；实际输入异常转换为普通控制事件 | inline/plain PTY 的 wait/approval 两类取消均同一 session，`usage_missing` 保存，second 只在 resume 后执行 |
| 取消命令和编辑器无孤儿进程、拒绝操作零执行 | process cleanup shield/join；私有编辑临时副本 | 多次 cancel、忽略 TERM、side-effect 后取消、编辑器 PID/文件清理测试 |
| 工具简短行/安全详情、恢复可查看、不重复回答 | 筛选字段，Textual 点击 modal，`/tools` | 真实鼠标点击 ToolDetailsScreen；显示 snippet/path/退出码，无 raw args；恢复重新筛选 |
| 名称清理/80 可见字符/标题和搜索、快照序号 | 独立元数据、search snapshot、标题刷新 | 跨存储实例、坏 JSONL、改名标题、过滤后排序改变仍恢复原序号 |
| recap 完成/待办/阻碍/当前验证；不调用模型 | 读取安全回答、goal、执行与 verification | 完整通过显示已验证，外部修改变未验证；失败不能列为成功执行 |
| Markdown 导出名称/项目/对话/用量/验证，路径/冲突/覆盖审批 | `SessionActions.export` | 导出内容无原始工具 JSON/密钥；拒绝不写、受保护/越界/链接拒绝、SHA 冲突 |
| 模型声明仅 ID、保留环境 default、同会话下一次生效 | `PreferenceStore`、factory 生命周期 | 真实同一 thread flash/pro 请求；项目切换后 default 仍为环境模型；API 主机字段被拒绝 |
| 默认→用户→项目→local→CLI→会话覆盖；原子 0600 保留其他字段 | 偏好存储和启动参数 | preferences/CLI 测试；主题、模型和详略跨实例生效，坏配置不覆盖 |
| 每个主/子调用实际模型、主机、单价快照；未知不冒充 0 | 模型中间件/UsageCollector/子调用记录/usage_snapshot | safety/accounting 测试；两个子调用不同价格合计 $3，零 token 调用仍保留；API 8 条持久当次单价 |
| 取消/失败缺 usage、未知价格 goal 暂停、预算保守预留 | usage_missing、共享预算 | 取消主/子请求保留已知部分且不完整；Textual Token `+?`，上下文未知不伪造新样本 |
| dark/light/ansi、NO_COLOR/ASCII、Markdown/diff/审批/工具 | 语义 Theme 和两个渲染器 | 12 张最终截图；两个 PTY 适配器 NO_COLOR 原始输出无颜色 SGR；原有 fallback 测试 |
| 输出 concise/normal/detailed，不删失败证据 | 指引与工具默认详情 | config/actions/render 测试；Textual detailed snippet 与 `/tools` 完整安全结果 |
| memory 三个精确层、查看/编辑/重载、无任意路径读 | `MemoryStore` + 适配器编辑回调 | scope/link/size/read 测试；Textual 真编辑→diff→拒绝/批准，工厂加载新正文 |
| 编辑私有副本、取消不写、并发 SHA 冲突、无 EDITOR 提示 | 外部 editor argv；0600 tempfile；核心 commit | editor 取消后 PID/临时文件不存在且不继承模型凭据；memory 并发修改保留原内容 |
| 上下文分类互斥、合计、窗口/阈值/最近主模型实际输入 | `core/context.py` 与真实工厂 context_parts | categories 合计、tool/Skill/pin 区分；UI 与核心同一窗口；API context 实际输入 3804 |

表中的 `core/`、`tools/` 均指项目 `nailong/` 下对应目录。命令解析共享 `ui/controller.py`，执行共享 `ui/actions.py`。

## 独立审查及修复关闭

本轮按执行计划使用了一位新的独立审查者，原报告 `final-review.md` 保留原结论，不改写为“最终全部通过”。审查后继续修复，并用相同隔离场景和回归核验。

| 原问题 | 最终关闭证据 |
| --- | --- |
| C1 Git pathspec magic 泄漏受保护正文 | 全局 `--literal-pathspecs`；相同 fixture patch 不含 protected sentinel |
| C2 验证子进程继承模型认证 | 子进程显式过滤 `DEEPSEEK_API_KEY`；env 输出只有 absent |
| I1 取消 mutating command 后旧 proof 可完成 | 启动前 invalidate；真实写 side-effect 后取消，complete 返回 false |
| I2 审批中 Ctrl+C 继续下一项 | 审批适配器统一 CancelledError，runner paused；两种 PTY 都 zero execution |
| I3 inline 恢复/切模型重算历史费用 | toolbar 从持久 snapshots 读取；旧当次价格固定，新会话清空，缺 usage 标记 |
| I4 无 HEAD working 展示旧暂存版本 | working 读取当前安全内容，staged 仍是暂存版本；实际 Git 用例 |
| I5 doctor/status/recap 缺项 | 需求表逐项补齐，doctor 缺依赖可运行，recap 比较当前 proof |
| I6 子模型调用来源合并丢失 | 每调用保存 provenance，合并仅用于展示；零 token/取消保留记录 |
| Minor：改名控制字符、sessions 语法、generated '.'、工具点击 | 清理字符、兼容关键词、明确 ValueError、真实 ToolDetails 点击用例 |

审查者复核了多个修复复现；其最后一次 follow-up 因账号用量限制中止，因此最后增补由主代理自审并执行用例，不能称为再次获得独立最终通过。新增自审修复还包括 Textual 队列身份竞态、overflow 丢输入、无 worker modal、NO_COLOR 样式解析、narrow RichLog 最小宽度、真实 inline 子 Task 的 KeyboardInterrupt、工具摘要把 boolean/exit_code 转字符串。

## 真实 API 证据与费用

`api-smoke.py` 在临时 Git 项目中运行，每场景费用预算 $0.03、输出上限 512 tokens、45 秒超时、最多 12 图步骤、SDK 重试 0。凭据通过现有配置加载并在写证据前脱敏，不打印实际 key。

| 场景 | 实际结果 |
| --- | --- |
| 默认 diff review | 只读 list/read，定位当前 `calc.py:2` 常量除零，并引用差异和新/旧行位置 |
| `$smoke` v1 | load_skill 后回复 SKILL_VERSION_ONE |
| 重载后的 `$smoke` v2 | 新 load_skill 后回复 SKILL_VERSION_TWO |
| 同一 thread 第一次模型 | flash，回复 OK，记录 flash 当次单价 |
| 同一 thread 切换后第二次模型 | pro，回复 OK，记录 pro 当次单价；ID 不变 |

成功这次共 37,674 tokens、8 次模型调用，按持久单价快照估算 **$0.008681328**。包含此前两次失败定位/重试的已知费用共 **$0.015217668**。这不是供应商账单。旧失败日志未删除，其中一次 pro 请求因保守预算不足在发送前阻止；本次小上下文同会话切换通过。

API 审查输出因冒烟上限较小而截断，且模型还给出未经验证的建议。该场景证明实际只读调用和已知 bug 的定位，不能证明所有模型建议都正确。模型没有在真实用户项目写文件或执行项目 shell。

## 使用与边界

详细命令、安装和 JSON 示例见 [README](../../README.md)。本机已可在项目终端运行 `nailong`；`nailong doctor` 可先检查安装与配置。

- 命令审批是应用权限检查；不构成操作系统沙箱。批准的 shell 仍受用户自身系统权限约束。
- `/rewind` 回退对话状态，不撤销已执行的文件/命令。
- 上下文为分类字符估算，价格为当次配置估算；历史缺失不补造为精确值。
- daemon/RPC/MCP、插件市场、可写子代理、worktree 并行调度与 OS 沙箱仍在本轮范围外。
- wheel 发布、其他操作系统/真实终端字体兼容性和长时间生产负载不在这次验收内；已验收本机 Python 3.11、实际进程、PTY 和 Textual 渲染。

## 权威证据索引

- 全套：[final-suite8.log](../../.superpowers/sdd/2026-10-02-developer-workflows/final-suite8.log)
- CLI/PTY：[cli-smoke.json](../../.superpowers/sdd/2026-10-02-developer-workflows/cli-smoke.json)，[脚本](../../.superpowers/sdd/2026-10-02-developer-workflows/cli-smoke.py)；`pty-*.raw.log` 保留终端转义以核验 NO_COLOR
- API：[api-smoke.json](../../.superpowers/sdd/2026-10-02-developer-workflows/api-smoke.json)，[脚本](../../.superpowers/sdd/2026-10-02-developer-workflows/api-smoke.py)
- 独立审查：[原报告](../../.superpowers/sdd/2026-10-02-developer-workflows/final-review.md)
- 进度：[progress.md](../../.superpowers/sdd/2026-10-02-developer-workflows/progress.md)
- 截图：[浅色 80×24](../../.superpowers/sdd/2026-10-02-developer-workflows/renders/light-80x24.png)、[深色 100×32](../../.superpowers/sdd/2026-10-02-developer-workflows/renders/dark-100x32.png)、[审批 80×24](../../.superpowers/sdd/2026-10-02-developer-workflows/renders/light-80x24-approval.png)
