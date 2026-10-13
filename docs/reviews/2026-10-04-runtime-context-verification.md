# 当前任务上下文：2026-10-04 验证记录

本分工的 28 项专项测试及包含它们的 139 项关联回归通过。模型响应使用离线 FakeMessagesListChatModel；实际执行 AgentRuntimeFactory、LangGraph、SQLite checkpoint、ContextManagerMiddleware、MemoryReadContext 和预算代码，没有调用真实 API。整体项目验收由总控汇总，本记录不能替代完整项目验证。

## 所属改动与修复

所属源码为 `nailong/core/task_context.py`、`nailong/core/context_runtime.py`、`nailong/core/context.py`；本阶段新增专属 `tests/test_managed_context.py`。保留其他所有者的改动，没有修改 agent/service、TaskStore、memory 模块、UI 或其他测试；没有提交、推送或移动工作树。

测试实际复现并修复了三处问题：

1. **新 revision 可丢失中间要求。** 原先只阻止旧 revision 和同版本的要求变更，提高版本后仍可删除或改写 request_history。现对同一 task_id 校验已见历史必须保持为新历史的完整前缀；显式新任务使用新 task_id。
2. **实际无系统消息仍统计编译提示。** 原先 _report 把显式 None 与未传参数混用。现以哨兵区分：preflight 未传参数时使用编译提示，实际请求为 None 时按真实空系统消息计入预算。
3. **同版本可改写验收定义或用户验证绑定。** 要求签名原先只含目标、范围、约束和要求历史。现包含验收 ID、说明、类型、required 和 verification_binding；验收状态及证据关联属于执行进度，仍允许在同版本刷新。

其余验证覆盖此前实现的完整任务保护、请求尾部投影、动态固定记忆槽位及互斥预算分类，没有因通过测试扩大执行权限。

## 命令与实际结果

在 `/Users/aycxd/Desktop/LLM_project` 执行：

```sh
.venv/bin/python -m unittest tests.test_managed_context -v
```

最终结果：`Ran 28 tests ... OK`，退出码 0；日志 `/tmp/nailong-managed-context-final.log`。

```sh
.venv/bin/python -m unittest \
  tests.test_managed_context tests.test_context_runtime tests.test_context_budget \
  tests.test_context_compaction tests.test_context_memory tests.test_history_archive \
  tests.test_memory_context tests.test_workflow_accounting -v
```

最终结果：`Ran 139 tests ... OK`，退出码 0；日志 `/tmp/nailong-managed-context-final-regression.log`。这是明确列出的关联模块集合，未运行完整 discover。

修复前证据：

| 日志 | 结果 | 说明 |
| --- | --- | --- |
| `/tmp/nailong-managed-context-red-confirmed.log` | 24 tests，2 failures | 中间要求被删除未被拒绝；实际空系统消息被计入 8,000 字符 |
| `/tmp/nailong-managed-context-acceptance-red.log` | 2 tests，3 个子案例失败 | 同版本改写验收说明、required 或用户绑定均未被拒绝；提高版本及进度刷新案例通过 |

另对三个所属源码与专属测试用 ast.parse 解析，4 个文件无语法错误、无行尾空白；git diff --check 退出 0。所属文件为未跟踪文件，不能只依赖 git diff 检查，故另做源码行尾检查。

## 可观察场景覆盖

| 场景 | 本次观察 |
| --- | --- |
| 完整要求、约束、未完成步骤、验收与用户绑定 | 渲染保留原文；None 与旧 v1 明确处理；不修改输入对象 |
| 保护字段过长 | 明确拒绝，要求及验收不会被静默截断 |
| 稳定投影 | 时间、重复计数等不进入正文；重复包装只留下一个任务尾部 |
| 版本与身份 | 同版本新要求拒绝、执行状态刷新允许；旧版本拒绝；提高版本仍保持完整历史；跨项目/会话拒绝 |
| 证据可信性 | model 声明、未知覆盖、旧版本、旧指纹、缺失关联均可见，不伪称当前磁盘验证通过 |
| 压缩与窗口 | 压缩后的真实请求仍保留任务约束；任务计入完整窗口，放不下时拒绝；压缩状态更新不含任务投影 |
| 工具边界 | 未完成并行交换拒绝且原消息保持不变；实际工具循环中的完整交换正常继续 |
| 动态固定记忆 | 空初始槽位可重复替换及空正文往返；重复片段按槽位定位；未知/损坏布局拒绝；最新正文计入硬预算 |
| 当步刷新 | preflight 与 wrap 使用当步刷新结果；报告深复制；真实工具循环后元数据变为 stale，旧正文不重复注入 |
| 同步/异步真实图 | 两种执行路径均有当前任务尾部，SQLite checkpoint 均无投影；普通问答无任务尾部 |
| token/费用预算 | 真实 accounting 在投影后预留；任务进入 SharedTokenBudget 与 GoalCostBudget；固定测试单价和 100/5 使用量结算为 $0.000105，无剩余预留 |

## 关联记忆模块的失败记录

扩大范围曾执行上述模块加 tests.test_managed_memory：`Ran 157 tests ... FAILED (failures=3)`，日志 `/tmp/nailong-managed-context-regression.log`。单独运行 `.venv/bin/python -m unittest tests.test_managed_memory -v` 同样得到 20 tests、3 failures，日志 `/tmp/nailong-managed-memory-isolation.log`；并非必须先运行上下文测试才出现。

具体失败项：

- `test_catalog_small_dependencies_within_actual_io_budget_remain_observed`：10 个小依赖中最后两个返回 unverified，预期全部 observed。
- `test_invalid_shapes_and_excessive_metadata_do_not_read_dependencies`，state=confirmed 子案例：预期不授权来源，但调用了一次 authorize_source。
- `test_source_size_and_operation_limits_are_explicit`：预期限额检查前不授权来源，但调用了一次 authorize_source。

记忆所有者仍在并行执行其授权验证；本分工没有改写其源码或测试。这里保留该时点的失败证据，由所有者/总控修复后重新运行并更新整体验收结果。139 项关联回归绿灯不能掩盖这三项失败，也不能据此宣称整个项目已通过。

## 接线核对与边界

当前 agent.py 已传入当前任务 provider 和与外层 MemoryContextMiddleware 相同的 memory_request_context。实际顺序为 MemoryContext → ContextManager → ReviewInput → ModelAccounting → SecretRedaction；预算测试验证 accounting 看到了任务正文。completed gating 由 agent provider 负责，context 不隐式选择别的任务。

- provider 同步、绑定当前项目/会话；版本与历史缓存只在本 middleware 实例内有效。跨进程恢复与持久化防回退属于 TaskStore/workflow，本次没有用局部缓存冒充其验证。
- task_context 只校验快照结构和已见版本；指纹相同不代表已重新读取当前磁盘，实际证据与 delivery 判定由所属模块负责。
- 固定记忆刷新依赖 MemoryReadContext 的当步视图，不重绑分页版本、不提升候选知识；文件之间没有新增原子事务。
- Context 报告中的输出预留是窗口层上界，内层 accounting 可再缩小 cap；预算拒绝后的准备记录不能解释为提供商已经收到请求。
- 12,000 字符任务保护上限仍会拒绝过大的完整任务；尚未实现大任务索引的恢复引用。提高上限仍受完整模型窗口约束。
- 没有真实提供商调用、CLI/TUI 操作、跨进程 TaskStore 恢复或整项目 discover。真实 tokenizer 与提供商接受尾部消息的差异需要总控后续限定冒烟验证。

此前静态阶段的接口说明见 `docs/reviews/2026-10-03-managed-runtime-context-handoff.md`；本记录更新其运行验证结论。
