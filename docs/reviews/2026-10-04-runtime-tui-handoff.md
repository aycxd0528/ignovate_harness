# 2026-10-04 TUI 接管与检查记录

本次接管 `tui.py` 和 `ui/*` 的视觉、交互及集成检查。已阅读当前代码中的交付报告、`task_paused` 输入队列暂停、`/task` 命令和记忆有效性显示；后续界面调整需保留这些行为。`main.py`、`headless.py`、`agent.py`、`agent_service.py` 和运行时模块由其他工作流维护，本次未编辑。

## 用户本轮反馈

1. 审批弹窗有大量空白，占据整个视觉焦点，希望改用其他呈现方式。
2. 每一步工具调用和输出逐条展示，导致对话很长，需参考所附 Claude Code 截图的折叠布局。
3. `/help` 混合命令和 Skill 长说明，需分类、对齐并采用标签页。
4. 就绪状态点不够绿，成功色整体过淡。

已核对总控对话中人类用户“当前文件夹下还有四个对话窗口，你直接指挥他们完成任务吧”的授权及本轮实施指令，现在执行上述方案，无需重复批准。审批、折叠工具行、帮助页、颜色调整正在实施。

## 总控追加回归（实施中）

已复现 GoalExecutionSafetyTests 两项失败；`ui/flows.drive_goal` 已修改验收检查仅在最新目标仍 active 且无已有 stop_reason 时暂停，避免覆盖模型的“需要用户确认”。goal_prompt 已明确缺验证/缺目标覆盖应保持未验证并暂停，单个命令成功不替代目标验收。

**需要核心维护者协调：** 结算后 goal_status 的用户取消，目前调用 `GoalStore.update(state='paused', summary='目标运行已取消。')`；`nailong/core/goal.py` 拒绝对已经 paused 的目标更新，导致已有缺验收暂停原因无法保存为取消。需要核心提供幂等暂停/允许 paused → paused 更新原因的 API；本 UI 任务不会直接改核心或绕过存储锁、重复 record_round 计费。复现命令为 `PYTHONPATH=tests .venv/bin/python -m unittest test_safety_regressions.GoalExecutionSafetyTests.test_model_pausing_goal_still_settles_the_entire_round test_safety_regressions.GoalExecutionSafetyTests.test_cancel_during_settled_goal_status_pauses_without_double_charging -q`。

## 实际证据

```bash
PYTHONPATH=tests .venv/bin/python -m unittest \
  test_tui test_execution_display test_workflow_ui -q
```

53 项通过，耗时 18.197 秒。覆盖现有审批决定、Esc 拒绝、计划审批、输入队列、Ctrl+C 停止后队列恢复、窄屏、历史重新换行、主题切换、流式工具顺序、工具详情焦点及详细模式统计。

另外使用本地模拟操作调用真实 Textual 渲染器，验证了两种尺寸。命令未执行，未调用真实 API，项目路径与偏好均使用临时目录。

| 窗口 | 审批框宽高 | 审批内容 | 内置帮助 |
|---|---|---|---|
| 100×32 | 84×21 | 7 行 | 35 行 |
| 80×24 | 66×15 | 7 行 | 35 行 |

两个尺寸均通过 Esc 返回 `reject`。当前审批框 CSS 固定为 80% 高度；`_wait_screen` 还会覆盖审批框原有颜色。工具结束时立即向 RichLog 追加每条结果；`/help` 使用 `controller.specs()`，会追加 Skill 的原始长描述。

成功色为 `#9cc6a0`。本地工具进程设置了 `NO_COLOR=1`，因此此环境中状态点的 Rich 样式会关闭；不能据此认定用户截图所在终端也设置了该变量。颜色修正需要同时验证彩色与 NO_COLOR 模式。

## 待确认的布局方向

- 审批放到输入区上方，按内容展开。命令、目录、变更预览以可读字段展示，完整内容可展开；使用紧凑的键盘选择保留拒绝、仅本次允许及合法的本会话允许决定。
- 工具过程默认按阶段汇总成一行；失败与超时保留醒目提示，点击或 Ctrl+O 展开过程，`/tools` 保留具体详情。
- 帮助采用常规、命令、自定义命令和 Skills 标签页；命令按用途分组，名称与简短说明对齐，完整用法按需查看。
- 提高成功绿色的饱和度，核对就绪状态点与成功记录的最终渲染；继续保留浅色、ASCII 与 NO_COLOR 的适配。

## 未覆盖

本轮尚未验证新布局；尚未新增普通问答不显示旧交付报告、任务报告状态一致性、`task_paused` 事件与 `/task` 各操作的专门 TUI 集成用例。真实模型、真实命令和其他终端平台未测试。此前界面测试通过不能替代这些新验收项。
