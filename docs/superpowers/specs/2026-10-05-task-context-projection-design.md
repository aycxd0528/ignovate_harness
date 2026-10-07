# 有界任务投影与历史恢复

用户已评审方向并于 2026-10-05 要求开始实现。本轮实施第一阶段：要求来源与明确替代关系、精简请求投影、可靠分页恢复。沿用会话中计划自审后执行的偏好，保留工作区，不提交、推送或移动工作树。

## 目标与边界

长任务不能仅因累计证据元数据超过 12,000 字符而无法继续。完整任务仍由 TaskStore 持久保存，delivery 始终检查完整记录。有效目标、范围、硬约束、当前要求、未完成步骤和全部验收定义继续保护；超过保护预算明确拒绝。

本轮不实施模型摘要、语义检索、文件/符号工作集合、校准策略或新的自动验收。模型没有替代要求或修改任务事实的工具。

## 要求记录

snapshot v1 可选 requirements 列表：每条包含 id、text、source='user'、history_index、introduced_revision、status（active/superseded）、superseded_by（ID 或 None）。history_index 精确对应不可删除的 request_history；每条历史恰好一条要求记录。ID 使用 r000001 等稳定序号。

新用户要求默认追加为 active；“继续”等控制输入不新增。旧 v1 没有 requirements 时，按全部已知历史生成 active 记录，不猜测替代关系。只有用户命令 /task replace ID 新要求 调用 TaskStore.replace_requirement，才将指定 active 要求标为 superseded，追加替代记录、提高 revision 并使旧证据失效。若被替代文本恰好是任务 objective，同步更新 objective；独立 constraints 与验收不会被隐式修改。/task requirements 显示记录 ID 与状态。

校验来源、版本、完整历史映射和替代目标；不得改写原文、形成循环、指向自己或未知记录。替代目标必须在更高需求版本中引入。

## 完整恢复

TaskContextHistory 使用已有 HistoryArchive 保存完整的、脱敏任务快照，返回不可变 hist_ 引用。保存后验证可读、完整和身份匹配，截断/损坏/失败不允许生成精简投影。HistoryArchive 增加内部完整 load 接口，并对未截断记录核对内容寻址身份。

read_task_context(reference, section='index', record_id='', query='', offset=0, max_chars=4000) 是只读工具；section 为 index/requirements/steps/acceptance/evidence，按稳定引用分页，max_chars 不超过 6000。索引提供记录类型、ID 和有界描述；可按 ID 或字面关键词查询。返回项目/会话/任务身份、需求版本和 historical 标记；不重放命令，不把历史升级为当前证据，不接受磁盘路径。

## 精简投影

仅在当前运行时确有 read_task_context 工具且完整归档保存成功时启用；未启用/被 allowed_tools 排除则保持原完整投影。

每次保留 objective、scope、constraints、全部 active 要求原文和来源索引、最新输入、生命周期/阶段、修改路径、待验证、阻塞、所有未完成步骤、其已完成依赖，以及全部验收定义/状态/用户绑定/证据 ID。已完成步骤以数量概况表示；superseded 要求原文在归档可恢复。

证据保留全量来源/状态/覆盖/有效性计数、完整恢复引用及最多 6 条工作明细，优先未通过验收的关联证据、当前失败和最近当前版本证据。其余明确报告省略数量。passed 状态及精简展示不代表已经核对当前磁盘。

投影不写入 checkpoint；两层预算及工具交换边界保持原规则。版本签名包含要求记录，历史仍保持追加前缀。最终验收输入仍是完整 TaskStore snapshot。

## 验证

测试新增要求默认有效、控制输入不退役要求、显式替代和恢复、旧 v1、非法来源/替代链、原记录不变、30 步长任务继续、真硬约束超限、归档失败/截断/损坏、分页版本一致、跨会话/项目拒绝、工具过滤时回退、实际同步/异步请求与 checkpoint、完整 delivery 不变。最后运行完整本地 unittest discover，模型用离线替身。
