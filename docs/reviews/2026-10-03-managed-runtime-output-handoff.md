# Task 5：结构化交付证据交接

日期：2026-10-03。状态：实现已写入并完成本次代码自审与静态语法/接口核对；未运行行为验证。总控接线及下述跨组件问题仍由对应所有者处理。

## 所有权与实际文件

本任务只新增 `nailong/core/delivery.py` 与本交接文件。没有修改 agent.py、agent_service.py、TaskStore、GoalStore、tools 或 UI，没有新增/运行测试、调用真实 API、提交、推送或移动工作树。

Ruling：本轮按用户分配与 spec 明确的验证限制执行，只做代码自审与静态核对；替代 executing-plans 的测试、提交与 worktree 步骤。本文件作为 Task 5 实施记录，不修改总控的共享计划或 ledger。

## 接口

```python
build_delivery_report(
    snapshot: dict | None,
    *,
    current_input_fingerprint: str | None = None,
) -> dict

render_delivery_report(report: dict) -> str
```

纯函数：不执行命令、不读写文件、不导入运行时/框架模块、不读取归档、不调用模型、不修改输入 snapshot。仅依赖标准库。

报告包含 spec 必需字段 `status/satisfied/pending/failed/stale/reasons`，另包含 `schema_version/waived/pending_verification/blockers/incomplete_steps/configured_verification/goal_coverage`，有效快照还带 `task_id/thread_id/project_root/revision/lifecycle`。验收分类数组中的每项有 `id/description/kind/required/status/evidence_ids/reasons`。记录 `waived` 的用户免除单独列示，不混入 `satisfied`。

## 判定约定

- 调用方必须传 TaskStore 拥有并已核对身份的 snapshot；不能直接传模型提案或外部工具正文。`source=runtime/user` 是生产方内部标记，不是本函数可认证的签名。Evidence v1 没有必需的任务/会话/项目字段，归属依赖其容器；生产方若额外提供这些身份，必须与 snapshot 一致。
- 当前指纹来自运行时对**当前输入**的独立观察，不能从模型字符串或持久快照中的旧 `input_fingerprint` 冒充。默认 None 不回退旧值；None、空白、unknown/missing/unavailable/none/null/n/a/?/未知/未获取 等未知占位不视为匹配。指纹为不透明的非空字符串，未额外限制某一种摘要算法。
- 正向验收要求其声明为 `passed`，显式关联唯一证据 ID，可信 source、当前 `task_revision`、当前输入指纹、完整 coverage、适用路径与匹配的 kind，并具有非空且非未知的 summary。只成功执行操作不会自动把 `pending` 变为通过；summary 不用于语义猜测验收匹配，exit_code 不用于猜测覆盖。
- `test/build/run/review/manual` 验收匹配同名 Evidence kind。`static` 使用实际 `review` 或 `manual` 结论证据；`read/edit` 仅证明观察或写入成功，不能当成结果正确性的证据。这是为 Evidence v1 没有 static kind 作的保守适配。
- 验收项 v1 没有自己的路径字段，因此每个必需验收使用整个任务 `scope` 与 `changed_paths` 核对。`scope=['.']` 表示整个项目；目录声明不能由一个子文件的成功推断为完整覆盖。多个各自 complete 的证据可以联合覆盖多个具体路径；partial/unknown 不能拼成完整通过。修改路径在任务范围外直接降为 unverified，不默默扩大任务范围。
- 当前可信、适用范围内的 failed 证据，在 complete 或 partial 覆盖下可以报告失败；未知范围不推测失败覆盖。denied/interrupted 明确说明未完成验证，不变成测试失败或验证通过，即使没有采集输入指纹也保留拒绝/中断的说明。
- `waived` 需要关联当前、适用且 complete 的 user/manual/passed 证据，表示用户的明确决定；单纯模型 waiver 或运行时自行 waiver 不接受。全部验收免除不等于验证通过。
- 过期版本/输入证据不支持当前通过；仍声明 stale 的验收不会被本函数自行改写。已链接的当前失败与通过同时存在时保留失败，不在没有时间/顺序契约时猜测哪个结果较新；生产方应把 `evidence_ids` 设置为当前判断的实际关联证据。
- 总控补充后，按 TaskStore 的保留 ID 命名空间 `verify:<步骤名>` 区分配置条件与功能目标，分别返回 configured_verification/goal_coverage 的 status 与各分类 ID 列表。配置流程通过但没有可信的用户目标必需验收，顶层仍是 unverified。只把验收描述中出现“测试”或 evidence.artifact_ref 中出现 verification 当成业务覆盖是不可靠的，因此不做此类文字推断。总控应保留 verify: 命名空间仅用于配置条件；若用户目标本来就是“执行某流程”，需要显式的目标验收，而非隐式猜测其与配置步骤等价。
- 整体状态优先级：明确阻塞（含 blocked 步骤） -> 必需验收可信失败 -> 必需 pending/stale 或未完成/无效步骤时 unverified -> 全部必需验收满足，且用户目标有必需证据时，功能 test/build/run/manual 为 verified，纯 static/review 为 reviewed。可选未满足项始终展示，但不冒充必需验收。只有配置验收、没有验收或只有免除时不升级 verified。
- steps 中的 todo/doing/blocked 与无效步骤以 incomplete_steps 单独展示，保留 id/title/state/dependencies；done 项不用逐项重述。不展示原始重复计数。request_history 由 TaskStore/context 保存和投影，本函数只判定当前 revision 的验收，不能从旧用户要求自行创造新的验收项。
- `pending_verification` 原样单独保留；功能目标即使已有 passed 证据，仍有这些未解决说明时不提升顶层 verified。静态 reviewed 保留未运行测试的说明，不把它自动变成强制测试；必须阻止静态审查完成的事项应表示为 required 验收项。`blockers` 或 blocked 生命周期产生 blocked，仍保留分项结果。

渲染输出中文状态、验收分类与原因，清理终端控制字符，不展示 shell 参数、原始工具结果或 evidence.summary/artifact_ref。用户验收描述和身份的 API-key 脱敏仍由既有 session/TaskStore/服务层负责。`render_delivery_report` 只负责展示 build 返回的报告，不能给任意外部 dict 认证。

## 总控接线建议

```python
from nailong.core.delivery import build_delivery_report, render_delivery_report

snapshot = task_store.snapshot(thread_id)
# fingerprint 必须为本轮重核输入的结果；无法完整核实时显式用 None。
report = build_delivery_report(snapshot, current_input_fingerprint=fingerprint)
text = render_delivery_report(report)
session_store.append_event(thread_id, 'delivery', report)
# 使用既有安全序列化/脱敏输出 report；不要接受模型自己生成的 delivery 状态。
```

此片段未运行。总控负责在权限、生成物排除规则、恢复和当前版本一致性校验都完成后提供 fingerprint，负责 goal/服务/UI 接线。不要把整体 verified 等同于“所有可选项目或所有代码均无问题”。

## 跨组件只读核对发现（不抢改其他所有者文件）

以下按当前实施中的文件静态阅读记录，文件仍可能由总控或其他所有者继续修正：

1. `TaskStore.record_evidence` 目前只在 passed 分支把 ID 关联到验收；failed/denied/interrupted 仅改 criterion.status，不关联该证据。delivery 没有可信引用就不会把失败声明当成已证实失败，也无法显示关联拒绝原因。建议所有 outcome 都保存当前关联引用；失败语义由证据判定，不能仅改 status。
2. `TaskStore.record_evidence` 目前要求 `evidence.kind == criterion.kind`；static 验收永远不能满足，因为 Evidence v1 没有 static kind。总控需要采用上述 static -> review/manual 的映射，或只为静态审查建立 kind=review 的验收。
3. `TaskStore.set_state(lifecycle='completed')` 当前把已保存的 task.input_fingerprint 作为 current 传入 delivery；磁盘外部改动如果未先 reconcile，会把旧证据和旧指纹互相比成一致。建议完成入口接收或重新获取独立当前指纹，无法取得则拒绝 verified 完成。
4. 同一 set_state 调用中目前先评估完成，再设置新 blockers；清除历史阻塞的参数尚未作用就会得到 blocked。建议在可信当前候选状态上做一次评估，明确新 blockers 与生命周期变更的顺序。
5. `TaskStore.record_verification` 对非整体验证 passed 的记录把 coverage 统一为 unknown；例如某个配置步骤有实际失败记录，也不会形成“已知子范围”的失败证据。需要依据真正执行/观察和路径范围记录 partial 或 complete 的失败覆盖，而非从退出码或 record.status 臆测覆盖。
6. 工具观察回调的 `paths/coverage/input_fingerprint` 必须以实际观察为准，普通 read/edit/run_command 不能凭操作成功填成验收 complete。TaskStore 负责显式 acceptance 关联；服务不应把工具执行日志或模型最终回答直接升级为 review/manual 的 runtime 验收事实。
7. 当前 `AgentService._begin_task(profile='review')` 没有创建静态审查验收，`delivery_report` 也只在快照已有保存指纹时重核；新只读审查因此没有验收和当前指纹，只会得到 unverified。需要为实际审查范围创建 review/static 验收，采集当前输入及真正的完整审查覆盖；不能把模型“未发现问题”改标 runtime 成功。reviewed 表示已完成静态审查覆盖，不保证代码无缺陷。
8. `TaskStore.record_verification` 目前从整体验证 status=passed 与摘要相等，就给每个步骤 paths=整个 task.scope、coverage=complete。输入未变化和步骤执行成功本身并不证明业务验收或所有任务路径被该步骤覆盖。configured 验收只定义流程执行条件，需明确此层报告只是对应必需验收；若要声明用户目标已验收，必须另有匹配目标的真实覆盖证据或显式用户决定，不能仅靠全套命令退出 0。

## 静态核对与未验证事项

- 已使用 `ast.parse` 读取源文件做语法解析，未导入或执行 delivery 模块。
- 最终源文件静态解析得到两个要求的公开接口（build 的 keyword-only 参数为 current_input_fingerprint，render 的参数为 report），依赖仅为 __future__/collections/pathlib；没有运行时 I/O 或外部服务调用。
- 逐分支自审 source、重复 ID、类型/版本、未知指纹、路径边界、完整/部分覆盖、失败冲突、拒绝、中断、waiver、空验收、可选项与静态 reviewed 的保守降级路径。
- 没有运行函数行为样例、自动化测试、真实 API 或端到端接线验证；不能声称测试通过或运行行为已经验证。
- 后续获得授权后，应验证模型自报、unknown==unknown、外部修改、旧 revision、漏关联、partial、denied、static/review、waiver、重复 ID、无验收、可选失败、目录路径覆盖及不同身份等场景；此处仅列待验证条件，没有新增测试文件。
