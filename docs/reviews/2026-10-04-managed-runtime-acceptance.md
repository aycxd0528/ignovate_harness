# 奶龙任务运行时统一验收

状态：本轮实施与验收完成。用户目标：保持任务连续性、用真实证据交付、及时停止无效工作的本地编码 Agent。最终证据见本文末尾及 `2026-10-04-runtime-final-verification.json`；历史测试记录只说明当时状态。全部已有工作区修改保留，没有提交或推送。

## 协作与所有权

context、memory、tool use、output、system prompt、tui、workflow 均已调度；workflow 负责任务、进展、目标、验证及 runner，总控负责 agent/service/headless/main/permissions 和统一验收。UI、提示词、工具、上下文、记忆使用各自交接文档。测试允许范围来自用户本轮实际验证目标和此前真实 API 授权。

续跑时 context、system prompt、workflow 已交接；其余窗口因账号用量限制中断。总控明确通知停止修改和测试后接手未完成项，保留各窗口的实现及历史证据，完成最终统一回归。

## 已执行的根集成验证

| 实际命令 | 当前结果 | 覆盖与限制 |
| --- | --- | --- |
| `.venv/bin/python -m unittest tests.test_managed_runtime_integration -v` | 10 项通过 | 模型实际输入与静态审查覆盖、历史页排除、缺页拒绝、失败请求零覆盖、旧任务取消/编辑事件隔离、异步日志首尾、枚举缺口、真实 LangGraph 并行审批批准/拒绝分离；模型为本地替身 |
| `.venv/bin/python -m unittest tests.test_agent tests.test_agent_service tests.test_managed_runtime_integration -v` | 当时 46 项通过 | 持久会话、真实图审批/恢复、完整读取后初始化覆盖、流式事件、拒绝和对话回退；后续根修改仍须重跑 |
| `.venv/bin/python -m unittest tests.test_cli -v` | 当时 21 项通过，随后强化断言 | 旧用例吞掉默认工具项目目录错误；已强化断言，工具所有者正在修复 ContextVar 默认项目选择，不能保留旧绿灯作为修复证明 |
| `.venv/bin/python -m unittest tests.test_cli tests.test_safety_regressions -v` | 当时 45 项，6 个失败 | CLI 目录/轮数/审批 fixture 已修；goal 停止原因覆盖交 UI 所有者；子代理用完整请求预算后旧 12k fixture 不足以启动，已调整为可容纳首轮但不足后轮并保留实际调用次数/用量断言 |

日志：`/tmp/nailong-root-integration-20261004.log`、`/tmp/nailong-agent-integration-20261004.log`、`/tmp/nailong-cli-20261004.log`、`/tmp/nailong-cli-safety-20261004.log`。重复运行会更新同名日志；最终结果需以本文件末尾的最终复核为准。

工具窗口较早全仓有界运行：611 个用例，26 failures、2 errors，并排除一个无限等待旧 fixture；详见 `2026-10-04-runtime-tools-verification.md`。各所有者在处理新接口与旧 fixture 的兼容及实际缺陷，当前不能宣称全仓通过。

## 已定位并修复的根问题

- 多个独立 LangGraph interrupt 的审批决策必须按 interrupt ID 分组恢复；不能把首个批准广播给另一个被拒绝的操作。
- 工具观察、取消、异常和交付阶段在 TaskStore 原子身份锁下绑定本轮 task_id/revision；新任务不接收旧任务的终态事件。
- 静态审查只收集 Context 处理后、成功模型调用消费的本轮文件区间，独立核对前后指纹并记录流程证据；模型总结不产生证据。
- 子代理传入父会话 ID，完整 prepared request 在预算计量之前组装；异步命令日志保留开头和结尾，显式中间截断。
- CLI 切换项目使用 ContextVar 上下文栈，退出恢复；达到单轮上限保留会话和任务，便于核对恢复。
- 新提示词纯组装模块已经导入运行时；资料、来源匹配、配置验证和功能验收分别表达。

## 真实 API 与外部审查

Claude Code 只读审查使用 Read/Glob/Grep、safe-mode、无 MCP、无会话持久化和 1 美元预算。达到预算上限退出，实际报告约 $1.043787；没有产出结论，不能计为审查通过。结果 `/tmp/nailong-runtime-claude-review-20261004.json`。

根真实 API 冒烟在临时项目执行，限定 read/edit、精确审批、只读审查、拒绝写入、恢复和无头回答。脚本初期三次错误分别为 run_turn 的 cost_budget 参数、VerificationService 的 approval 参数名，以及把权限引擎作为验证步骤位置参数；已更正，这些错误不计为行为通过。最终五个场景全部通过，具体证据见下。

## 最终复核

- [x] 所有窗口交接与生产接口当前一致。
- [x] context/memory 的每步动态 scope、来源变化、约束保护实测。
- [x] Task 跨进程恢复、补充要求、旧证据与身份隔离实测。
- [x] 拒绝零执行、审批期间变更、并行审批分离、取消进程清理实测。
- [x] 当前功能验收和静态审查证据链、配置流程与目标覆盖分离实测。
- [x] 正常分页继续、重复无进展暂停、队列和目标停止原因一致。
- [x] 真实 API 冒烟、无头 JSON 和 TUI 两种尺寸实际渲染。
- [x] 最终全仓回归，记录准确用例数量和失败/未覆盖场景。

## 收尾发现与修复证据

1. **路径别名误拒绝及保护路径绕过。** macOS `/var` 与 `/private/var`、项目符号链接别名应落到同一项目边界；PermissionEngine 原先按词法前缀提前拒绝。现在权限、FileSession、旧 local_tools 均先保留词法保护检查，再以真实路径核对项目边界。新用例确认别名读取受同一规则约束、越界仍拒绝、别名下 `.env` 符号链接不能绕过保护。先观察到失败再修复。
2. **目录审查枚举窗口。** output 窗口独立复核发现先枚举、后取指纹可能遗漏并发新增文件。总控新增可控竞态用例，复现错误生成 reviewed；修复为先取独立基线、再枚举，末尾核对同一基线，新增文件使本轮保持 unverified。
3. **取消原因覆盖。** 已结算轮次随后在 goal_status 被取消时，目标可能已 paused，旧 update 拒绝保存取消原因。允许同会话 paused → paused 更新说明；仍不允许恢复、完成或跨会话更新已停止目标，且不再重复 record_round 计费。原安全回归通过。
4. **下一模型调用前停止重复读取。** 工具观察增加稳定参数/结果哈希、版本、布尔结果和变更状态；观察发生在工具返回图之前。服务去重观察，生命周期 middleware 在下一模型步骤前检查持久暂停和任务版本。真实 LangGraph 替身连续读取六次后暂停，第七次模型调用未发生；正常分页、版本或结果变化继续。
5. **终端项目切换污染全局目录。** Textual 与 inline `/project` 移除全局 PROJECT_ROOT 写入；新运行时及工具仍显式绑定新项目。真实 FileSession 从新目录读取，另一个默认项目目录保持原值。
6. **退出时状态动画竞态。** 730 项回归有一项偶发 NoMatches：控件已卸载而状态计时器仍运行。新增确定性卸载用例复现，改为保存状态控件引用、检查 attached、卸载时停止计时器；最终完整回归通过。

旧取消测试因没有审批桥接而永久等待 pid，现显式批准测试命令并使用有界等待及取消清理。工具哈希用例修正为包含 schema 默认参数的规范 JSON。UI 旧 fixture 按用户已批准的底部审批和折叠工具行为更新，保留拒绝零写入、显式允许、详情可展开和可点击的断言。

## 最终可重复证据

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python docs/reviews/2026-10-04-runtime-api-smoke.py
.venv/bin/python docs/reviews/2026-10-04-runtime-tui-smoke.py
git diff --check
```

完整本地回归：**731 项，59.528 秒，零失败、零错误，无排除**；执行使用 150 秒外层超时。另有 42 项 TUI 收尾聚焦回归通过，121 个项目及测试 Python 文件 ast.parse 通过，git diff --check 退出 0。最终日志 `/tmp/nailong-full-final-20261004.log`；摘要、日志哈希与生产源文件哈希保存于 `2026-10-04-runtime-final-verification.json`。

真实 API 使用当前配置的 **deepseek-flash**；三个 managed 提供商场景共享 $0.15 上限，本次估算 $0.004200924。无头普通回答是另一个请求，不计入这个 managed 数值。结果保存于 `2026-10-04-runtime-api-smoke.json`：

| 场景 | 可观察结果 |
| --- | --- |
| 阅读、精确编辑、绑定验证 | 实际修正加法；批准 edit_file 与核心检查命令；检查通过，业务验收对应当前输入，delivery=verified |
| 静态审查 | 全文实际进入成功模型输入，独立范围与版本核对后 delivery=reviewed |
| 拒绝写入 | 一次写入审批拒绝；文件不存在，证据 denied |
| 跨进程恢复 | 独立 Python 进程恢复同一 task_id、目标和两项验收；当前交付仍 verified，无重放 |
| 无头普通回答 | 退出 0、success、零工具、无旧 delivery，实际输入 9,811 / 输出 4 tokens |

真实 Textual 渲染在临时项目执行，无模型和命令调用。结果 `2026-10-04-runtime-tui-smoke.json`，SVG 位于 `docs/previews/runtime-*.svg`。对紧凑和展开两种审批，选择项、提示、输入和统计始终在屏幕内，Esc 返回 reject：

| 终端 | 紧凑审批高度 | 展开审批高度 | 输入起始行 | 统计底部 |
| --- | --- | --- | --- | --- |
| 100×32 | 9 | 18 | 27 | 32 |
| 80×24 | 9 | 13 | 19 | 24 |

SVG 仅对 Rich 导出的 CJK textLength 做显示单元宽度校正，保持原终端坐标；没有改变 Textual 渲染器。已检查 80×24 紧凑预览的实际图片。测试额外验证 long content 滚动、审批默认拒绝、暂停队列显式恢复、手工验收与任务完成、完成后的普通回答不复用旧交付报告。

## 结论边界与后续方向

本轮实现的是设计中的任务闭环，并验证上述行为。verified 表示当前输入与用户登记的验收证据吻合；用户的验证步骤覆盖声明需要用户判断，退出 0 本身不能证明功能正确。reviewed 只证明选定范围的静态输入及流程覆盖，不证明没有缺陷。

无操作系统沙箱、多用户 daemon、MCP、自动知识提取/晋升或向量检索；这些没有作为本轮实现或验收。停滞判断覆盖稳定重复读取；更广义的语义空转仍依赖轮数、调用和费用上限。输入摘要已移出事件循环，但尚未提供增量缓存。未穷尽全部文件系统竞态，也未实测其他操作系统与终端字体。
