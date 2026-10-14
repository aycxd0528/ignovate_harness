# Managed runtime 提示词交接

日期：2026-10-04。范围：提示词与组装辅助；不修改共享 `agent.py`、工具、状态或权限引擎，不调用真实 API，不提交。

## 交付与语义

新增 `nailong/core/prompts.py`，提供提示词常量及纯函数 `build_prompt_parts(...) -> dict[str, str]`。返回键保持 `base_system`、`fixed_memory`、`skill_catalog`，可直接替换当前 `_prompt_parts` 的组装逻辑。依赖仅 Python 标准库；不访问磁盘、store 或模型，不变更任务、目标、验收或审批状态。

保留当前输出协议、交付前核查、完整 P0–P3 审查报告和各模式限制；原 `REVIEW_POLICY` 原文保留。探索、修改、命令、委派、Skill 和记忆说明仍按实际注册工具过滤；初始化缺写入工具和计划缺提交工具时返回草稿。主代理保留传入记忆分区；子代理不带父记忆、Skill 目录或任务快照说明。未绑定目标、其他会话及非 chat 模式不带目标指令。

本轮强化：

- 最新用户补充与纠正取代冲突旧要求，未取消的有效目标和约束继续保留；暂停、取消或改换目标不能被旧任务记录抵消。
- 任务快照即使在请求末尾，也只是运行时任务资料；状态、步骤 done、验收 passed 和模型摘要不自动成为验证事实。
- `confirmed`、`observed` 和来源 SHA 匹配不证明知识正文语义、测试或功能验收；候选、过期和适用性未知的内容需核实。
- 目标创建、配置与恢复成功只表示状态操作成功；配置验证与功能验收分开。功能验收仍须当前需求、输入版本和范围的有效证据。
- 失败、拒绝、中断、未测、未知覆盖和过期证据分别呈现，允许交付未验证结果；不把免除验收当成测试通过，不代替用户确认人工验收。
- 无新信息的相同读取需要调整方法，尊重运行时暂停/停止。正常分页、新证据、结果变化、文件变化后的重读和有状态变化的轮询不误判无进展；无界轮询仍需停止。
- 恢复、中断或回退后先核对磁盘与执行结果；未知结果不盲目重放副作用，已启动操作不声称从未执行。

这些是模型行为要求，不新增硬性保障。任务身份、来源有效性、验收、权限、预算、投影大小和停止条件继续由各运行时组件执行。

## `agent.py` 的具体接线

把现有提示词常量和 `AgentProfile` 声明移至新模块后，通过导入保持 `agent.SYSTEM_PROMPT` 等兼容名称，避免旧常量在导入后再次覆盖新值：

```python
from nailong.core.prompts import (
    AgentProfile,
    SYSTEM_PROMPT, OUTPUT_POLICY, FINAL_CHECK_POLICY, REVIEW_POLICY,
    EXPLORATION_POLICY, EDIT_POLICY, COMMAND_POLICY, DELEGATION_POLICY,
    GOAL_POLICY, build_prompt_parts,
)
```

将 `AgentRuntimeFactory._prompt_parts` 的完整方法体替换为以下内容，保留现有签名及 `memory_snapshot` 参数：

```python
def _prompt_parts(self, profile, target_path, tool_names, thread_id, memory_snapshot=None):
    snapshot = memory_snapshot if memory_snapshot is not None else self._memory_snapshot
    task = self.task_store.snapshot(thread_id) if thread_id and profile != "subagent" else None
    goal = self.goal_store.active() if profile == "chat" and "update_goal" in tool_names else None
    return build_prompt_parts(
        project_root=self.settings.project_root,
        profile=profile,
        target_path=target_path,
        tool_names=tool_names,
        output_style=getattr(self, "output_style", "normal"),
        thread_id=thread_id,
        has_task_context=bool(task and task["lifecycle"] != "completed"),
        active_goal_thread_id=goal.thread_id if goal is not None else None,
        fixed_memory=snapshot.rendered_context,
        skill_catalog=self._skill_catalog,
        context_file=CONTEXT_FILE,
    )
```

`active_goal_thread_id` 只来自 `GoalStore.active()`；不要传入 paused/completed 目标或凭 latest 推断活动状态。`has_task_context` 只表示本请求的 provider 将提供同一项目、会话的任务投影，不表示内容已经验真；它应与 `_build_runtime.current_task` 的过滤条件一致。纯组装函数不重复读取或验证 store。

保留 `system_prompt()` 和 `/context` 的现有分区拼接，禁止按标题重新拆字符串。`_build_runtime` 中传给 `ContextManagerMiddleware.parts` 的调用也应传入本轮已锁定的 `snapshot`，与系统提示词及 `MemoryReadContext` 一致：

```python
parts=self._prompt_parts(profile, target_path, tool_names, thread_id, snapshot)
```

主代理和只读子代理都已经通过 `_prompt_parts` / `system_prompt` 取提示词，以上接线覆盖两者。不要在旧组装代码外另行追加新 `TASK_POLICY`、`GOAL_POLICY`、`MEMORY_POLICY` 或 `PROGRESS_POLICY`，以免重复或继续保留冲突规则。工具名称仍以 `build_tools` 经 `allowed_tools` 过滤后的结果为准。

`has_task_context` 是编译时说明开关；每次请求的任务快照仍由 `task_snapshot_provider` 更新。最新用户优先与快照资料边界在基础规则中始终生效；如果同一图可能在构建后才建立任务，不依赖该布尔值建立执行保障。

## 本地验证与实际限制

新增 `tests/test_managed_prompts.py` 的 7 项组装测试，覆盖实际工具清单及不可用操作过滤、活动目标所属命名会话、父任务说明、模式草稿回退、记忆/目录分区与子代理隔离、记忆分页能力、项目根目录与输出偏好。没有给文案逐句编写断言，也没有把提示词快照相等当成模型行为测试。

新增模块前，专属测试因 `ModuleNotFoundError: nailong.core.prompts` 失败；模块实现后 7 项通过。这是新接口的红绿记录，不是旧 runtime 行为缺陷的复现。

实际执行：

```bash
.venv/bin/python -m unittest tests.test_managed_prompts -v
```

模块交付阶段（接线前）未运行真实 API，未执行全套测试，未修改 `agent.py` 完成接线；该阶段不能据此声称新规则已进入模型请求、整个 runtime 已验证或模型遵从度已提升。总控接线后的定向检查见下节；旧轮 367 项结果不可作为本轮证据。

模块交付阶段的独立只读复核未发现可确认的问题，专属测试复核同为 7/7 通过。静态解析、无尾随空白检查、`git diff --check` 通过，并核对详细 `REVIEW_POLICY` 与接线前 `agent.py` 原文一致。该次独立复核不覆盖尚未接线的模型请求与真实模型效果。

提示词输入更完整，实际 token 用量继续由现有 `/context` 统计和预算流程判断；纯模块不自行跳过预算或丢弃约束。

## 总控接线后的定向审查与验证

总控完成生产接线后，本线程仅读取当前 `agent.py`；只修改获独占授权的 `tests/test_prompt_assembly.py` 和本交接文档，没有改生产文件。

静态核对确认：

- 旧提示词常量已移除，导入的新值没有被重复赋值覆盖；`_prompt_parts` 仅通过一个 `build_prompt_parts` 返回结果，没有追加旧任务/目标规则。
- 活动目标来自 `GoalStore.active()`，组装还检查 chat 模式、目标工具和匹配的非空会话；任务提示开关与 provider 都排除 completed 任务。TaskStore 的读取校验会话与项目身份；模型请求投影继续由 ContextManager 做身份和版本检查。
- 主图 `system_prompt`、`parts` 与 `MemoryReadContext` 共用本轮的记忆 snapshot。编译时记忆分区保持可替换位置，没有按目录标题拆分。
- 子代理组装使用 subagent 模式和实际工具，thread_id 为 None，不读父任务、不给父固定记忆/目录；子图 Context 不带 task provider 或父 checkpointer。继承父执行 thread ID 供工具执行关联使用，不因此加载父对话。
- 子图 Context 在 Accounting 前，完整输入先受上下文检查，再做预算保留；失败和中断的实际状态没有被提示词组装改写。

第一轮有界命令运行 54 个用例，出现 4 处断言失败：init/review/plan/子代理的旧清单缺少工具注册表新提供的只读 `read_tool_result`。逐项对照当前注册结果后，在原精确列表中增加该工具，没有删除工具过滤、写入/命令禁止或父记忆隔离断言；未按列表本身推断模型行为效果。

另外新增两个真实 Agent 图回归：

1. 同目录重建 factory、更新约束后，实际模型输入只有一份当前任务投影，更新后的约束可见，旧约束不从历史快照重新注入；目标说明只有一份且不进入其他会话，任务投影不写入 checkpoint。
2. 父任务、活动目标和父历史同时存在，子代理继承父执行会话 ID 时，实际请求仍只含委派问题和子代理系统输入，没有父任务投影、父目标说明、固定记忆、Skill 目录或父历史。

`test_managed_prompts + test_prompt_assembly` 的 18 项已通过；两个已有 SQLite factory 恢复/异步 rewind 用例已通过。以上使用离线记录模型，检查发送输入与状态持久化，不衡量模型语义遵从效果。
