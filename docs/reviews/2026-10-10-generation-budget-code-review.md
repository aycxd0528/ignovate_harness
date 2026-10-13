# 生成额度、截断恢复与累计预算独立代码复审

审查日期：2026-10-10。基线：`cd2bd720396a3eac31c0107f809bbbf5eab4daac`。对象为该基线上的未提交修改及新增模块；实施者在审查过程中继续修复，因此以下结论针对最终刷新后的文件。审查没有调用模型 API、没有联网、没有修改实现或测试；唯一写入是本报告。

## 结论

最终复审未发现仍需阻止合入的 P1/P2 问题。最初六项问题，以及复审发现的最后调用与工具调用组合、usage 重复、空白续写和空 JSON 账本绕过，均已在当前代码中处理。仍有一项 P3 流式显示建议，见下文。

这是代码审查与离线协议/纯内存探针结论，不代替实施者的完整测试结果。为遵守只读限制，本审查没有运行会建立临时项目、会话、账本或检查点的完整测试套件。

## 已修复项及复核依据

1. **预算加载异常后的 ContextVar 清理。** [agent_service.py:1183](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/agent_service.py:1183) 现先加载会话预算，再设置各 ContextVar；配置无效、账本损坏、锁冲突等异常不再发生在已设置变量而尚未进入 finally 的区间。正常执行及异常执行的 reset 位于同一 finally 中，关闭图流后再清理上下文。

2. **子代理截断工具调用及截断正文。** [agent.py:492](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/agent.py:492) 的子代理安装独立生成保护，重试数为 0；截断响应在进入图工具路由前被拒绝。捕获后返回显式 `budget_failure`，包括 `scope=subagent`、`reason=output_truncated`，没有把截断正文当作完整研究结果。已读取新增子代理及主代理测试；主代理测试检查工具启动事件和目标文件不存在。

3. **空续写、空白续写及异常结束不能标记恢复。** [generation.py:134](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/nailong/core/generation.py:134) 现要求允许的结束原因、有可显示正文或合法工具调用，并拒绝 invalid_tool_calls。`body_nonempty` 使用 `text.strip()`。纯内存复核 `length("partial") → stop(" \n ")` 得到 `ModelOutputTruncated`、`recovery_failure=empty_continuation`，不再出现“模型返回了空回答”但 `recovered=True` 的状态。这也避免该恢复路径继续调用正常完成的审查证据插入逻辑。

4. **续写无法进行保留原响应诊断。** [generation.py:149](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/nailong/core/generation.py:149) 保存上一条截断响应。纯内存复核第二次 handler 抛 `RuntimeError("offline-provider-failure")`，得到带原 `finish_reason=length` 和失败类型/原因的 `ModelOutputTruncated`。第二次抛 `SessionTokenBudgetExceeded` 时保留该预算异常，并在 diagnostics 加入 `truncated_response`。服务的两个停止分支输出可见 final、diagnostics 和 `delivery.status=unverified`。

5. **最后一次调用与截断工具调用的组合。** [agent.py:123](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/agent.py:123) 的 accounting 检查现在允许 length 响应交给生成保护；不会先抛通用轮次异常而遮蔽截断状态。纯内存组合探针设置 `TurnModelBudget(1)`，提供商返回合法 read_file 调用且 `finish_reason=length`，结果为 `ModelOutputTruncated`，只有一次调用、一个待发 usage，usage 总量为 4196。服务最后调用异常的 completed_response 分支现按 call_id 去重，避免先 drain 后再次按 message_id 重记同一次 usage。已读取对应新测试。

6. **结算等待所有预算完成。** [agent.py:221](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/agent.py:221) 使用 `gather(..., return_exceptions=True)`，所有结算结束后才传播错误。纯内存模拟一个结算立即抛错、另一结算等待事件：等待事件释放前 accounting 任务保持未完成；释放后第二结算完成，再传播首个错误。取消仍使用 shield 等待结算，避免预算 hold 留在后台与工厂 close 竞争。

7. **结算异常后的实际 usage 不再丢失。** [agent.py:202](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/agent.py:202) 在结算前 `_publish` 完整响应，并将 call_id、usage 和输出诊断入列。生成保护通过 `nailong_response_observed` 避免重复记录；主代理待发队列与子代理 collector 分开。预算停止分支能够 drain 已知提供商 usage。正常恢复、再次截断、累计额度拒绝继续调用、最后调用工具响应均使用同一 call_id 去重路径。

8. **恢复既有会话预算时不能把空 JSON 账本当成不存在。** [agent.py:437](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/agent.py:437) 使用 `previous or ledger.exists()` 区分“不存在账本”与“存在但内容为空”。现有 `{}` 缺少 limit，不会返回无预算运行；会进入严格验证并拒绝。账本模块还验证 schema、整数额度、累计用量、pending、停止状态；恢复未结算 hold 时保守计入 spent 并禁止继续调用；持有进程锁直到 close，原子发布账本。

## 实际 SDK 协议与额度

实际 `agent.ChatDeepSeek` 是 [HarnessChatDeepSeek](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/nailong/core/model.py:6)，不是原生 SDK 类。纯内存真实 Harness payload 探针确认 assistant 行仍包含 `reasoning_content="provider thought"`，且 `max_tokens=32768`。本审查最初针对原生 langchain-deepseek 丢该字段的报警已明确撤回，不应计入缺陷。

默认 max/high allowance、项目 generation 覆盖、模型与 reasoning 切换、已知上下文的显式裁剪均已读取实现与新增测试。累计预算对实际准备的系统、工具和消息输入进行上界预留；不足时拒绝整次请求，而非偷偷降低完整输出 allowance。主代理和子代理继承同一会话预算；预算存在时实际 DeepSeek SDK 重试禁用，避免同一 hold 内的不可见第二次提供商尝试。

## 剩余 P3 建议

**真实流式 replacement 缺少显示边界。** [agent_service.py:1232](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/agent_service.py:1232) 会先发出第一次不完整正文的 token，再发续写后的完整 replacement；[tui.py:1511](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/tui.py:1511) 在 final 到达前持续拼接。最终 final 文本会纠正为完整回答，但直播阶段可能显示旧片段加重写正文。建议以后增加真实 fake-streaming 回归并用 attempt/replacement 事件明确显示边界。当前 FakeMessagesListChatModel 回归主要证明最终结果、调用次数与usage，不能证明逐 chunk 的显示行为；该项不影响本次截断工具禁止执行和预算正确性结论。

## 验证范围

- 全量读取当前 agent.py、agent_service.py diff、generation.py、cumulative_budget.py、上下文额度变更，以及新增/调整测试；同时检查实际模型包装器、配置读取、账本锁、原子文件访问、usage collector、子代理预算和审查证据调用链。
- 纯内存探针使用项目现有 Python 3.11 环境，并设置 `PYTHONDONTWRITEBYTECODE=1`；覆盖实际 Harness payload、空白续写、提供商异常续写、会话预算拒绝续写、最后调用截断工具响应、失败结算等待。
- `git diff --check` 无输出。
- 没有运行真实模型或网络请求；没有执行会写临时项目/会话的完整测试。真实提供商行为及 Windows 运行验证仍以实施者测试结果为准。

## 追加复审：累计预算压力下的请求视图 L1 压缩

本段复核真实 API 工程冒烟发现预算压力后新增的 [context_runtime.py:505](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/nailong/core/context_runtime.py:505) 逻辑及 `tests/test_budget_context.py`。真实 API 冒烟由实施者执行、单独归档；本审查没有调用 API，也不把这些工程冒烟纳入 A–F 测评排名。

**结论：未发现新增 P1/P2 阻塞项。** 该分支只在累计预算存在且未停止、完整请求与当前输出 cap 无法放入余额、archive/thread 存在、实际请求工具列表仍包含 read_history_result 时尝试 L1。最后一次 summary-only 请求已移除工具，因此不会产生无法通过当次工具集合恢复的新引用。候选必须同时降低 `_input_bound` 并使完整既有输出 cap 能放入余额才被采用；accounting 此后仍重新执行原子预算准入，并发预留改变余额也不会绕过预算。

归档与保护链已读取：

- [compact.py:106](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/nailong/core/compact.py:106) 保护最后一次工具 exchange，以及任何尚未收齐工具结果的 exchange 的全部 call IDs。较早完成的只读结果在 `_save` 成功返回之后才以 `model_copy` 替换；archive 失败不会发布该候选。
- [history_archive.py:273](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/nailong/core/history_archive.py:273) 保存不可变内容引用并原子发布，已有引用也会读取验证，不能仅靠内存缓存假定文件仍存在。归档通过同一 thread 的 read_history_result 分页恢复。归档保留原有大小/脱敏限制；正常工具结果有自己的响应上限，本次修改没有扩大这些限制。
- 已标记 `nailong_tool_cleared` 的结果不会被重复清理或把摘要重新归档为原始正文，其既有 `nailong_tool_reference` 保留。这一分支不修复此前已经丢失或损坏的历史归档；恢复工具仍会明确返回失败，不能据此宣称完整证据。
- `_build_request` 仅 override 请求消息，没有返回 checkpoint 更新。系统消息、普通用户要求和 AI reasoning_content 保留；L1 仍沿用既有 obsolete goal/plan pin 归一化规则。该保证针对新增预算压力分支，既有 before_model 的正常上下文阈值压缩仍可按原规则更新检查点。
- 压缩后的实际消息进入 ReviewInputMiddleware；被替换为摘要的文件分页不会冒充完整审查输入。输出 cap、工具模式以及原始 request 的消息对象未被修改。

额外纯内存探针使用无磁盘写入的 archive 替身保存完整消息，构造一个较早完成的 30,000 字符 read_file 结果，以及尚未完成的并行 p1/p2 exchange（已收到 p1 的 5,000 字符结果）。预算 12,000、输出 cap 1,000：输入上界从 39,483 降到 9,833；原 request 与 deepcopy 相等；p1 结果保持原样；AI reasoning_content 保持原样；归档中的旧正文仍为 30,000 字符。再次准备已压缩请求，消息及 reference 不变，保存次数仍为 1。未执行会写临时目录的三项新增测试，但已完整读取其断言；`git diff --check` 无输出。

建议以后补充“较早未完成并行 exchange、已有 cleared reference、候选虽变小但仍不足以放入预算、归档失败”的预算分支回归。前两个已通过本次纯内存保护/幂等探针与现有 compact 实现核对；这些是测试覆盖建议，不是发现的阻塞缺陷。

## 最后一轮追加复审：原生 SDK 输入计量、操作员覆盖及截断归档保护

本轮针对仍未提交、基线仍为 `cd2bd720396a3eac31c0107f809bbbf5eab4daac` 的最新修改。**结论：本轮发现一项 P2 归档可恢复性边界，实施者已修复并经复核；当前没有剩余 P1/P2 阻塞项。** 既有 P3 流式显示建议保持不变。

### 原生 SDK 输入边界

[budgets.py:81](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/nailong/core/budgets.py:81) 对实际 HarnessChatDeepSeek 使用它的 `_get_request_payload`，同样插入 system message、转换工具 schema，并以真实消息和工具行计算 UTF-8 字节上界。该分支排除 SDK 没有发送的 response_metadata/内部计量字段，同时保留 Harness 补回的 required reasoning_content；普通旧假请求继续使用原始 fallback，`getattr(request, 'model', None)` 不要求旧假请求必须有 model。

独立执行了不写临时目录的 `PYTHONDONTWRITEBYTECODE=1 /Users/aycxd/Desktop/LLM_project/.venv/bin/python -m unittest discover -s tests -p test_native_budget_bound.py -v`：2 项通过，未发送网络请求。另一个纯内存探针同时含 system、工具、50,000 字符内部 metadata 和 required reasoning：`_input_bound=1414`，与实际 Harness payload 中 messages/tools 的同一字节计算结果完全相等；reasoning 字段存在。无 model 的旧 SimpleNamespace 请求得到边界 1167，没有 AttributeError。已完整读取新增 MockTransport/SSE 协议测试；它们验证实际 SDK 续写字段、逐调用 usage 与累计账本，以及有预算时关闭实际客户端重试，而不调用外部 API。

### 操作员会话额度与持久化

[agent.py:303](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/agent.py:303) 的 `session_token_limit` 是调用者显式传入的正整数覆盖，在构造开始时拒绝 bool、零及其他无效类型；不会向被测项目写预算配置。[agent.py:431](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/agent.py:431) 使用该覆盖优先于项目值；仍禁止更改已持有会话的 limit，并由既有账本的严格恢复检查拒绝重设额度。配置移除后的既有 `{}` 账本仍会进入验证失败，不能通过空 dict 分支变成无预算运行。已读取操作员覆盖、移除配置后的空账本、重启累计用量及 ContextVar 清理回归；没有发现新的绕过或项目写入路径。

### 本轮发现并修复的 P2：不能用截断归档替换唯一原文

修复前，新预算压力分支只要求 `archive.save` 成功、压缩变小且完整 cap 能 fit。实际 HistoryArchive 会在超过其大小上限时返回带 `archive_truncated=True` 的引用；因此旧的/导入的大 ToolMessage 状态可能被替换为无法恢复尾部唯一信息的引用。原 checkpoint 尚有原文，故不是永久磁盘丢失；问题在于当次提供商视图不能通过 advertised 恢复工具取得完整原文。普通新工具响应另有 16,000 字符上限，该边界不会由通常大小的新结果触发，不能据此夸大为普通冒烟都丢失数据。

最新 [context_runtime.py:524](/Users/aycxd/.codex/worktrees/ignovate-generation-recovery/LLM_project/nailong/core/context_runtime.py:524) 读取候选消息中的每个 tool reference，要求归档没有 archive_truncated 才允许应用候选。新引用以及已有 cleared reference 均纳入检查；不完整时保留原请求，并记录 `applied=False, reason=archive_truncated`。checkpoint 仍不修改。缺失/不可安全读取的归档会抛出而停止请求，不会应用未经验证的候选。

独立纯内存探针使用实际 HistoryArchive._record/read 算法，仅把持久化 save/load 替换成内存字典，在内存中暂将 MAX_ARCHIVE_BYTES 设为 4096；旧结果为 30,000 字符加 `UNIQUE_TAIL`，另有受保护的最新结果。实际归档为 truncated，归档正文不含 UNIQUE_TAIL；最新 `_prepare_request` 保留原 messages，提供商请求仍含 UNIQUE_TAIL，记录 applied=False 和 archive_truncated，完整请求加 cap 为 37,365，随后将由原预算准入拒绝。没有把尾部丢失的候选交给提供商。已完整读取使用真实磁盘 HistoryArchive 同语义上限的新增失败回归，但为只读限制没有执行会写临时目录的该套测试。`git diff --check` 无输出。

真实 API R1 工程验证在 120k conservative admission 前停止，尚未交付，不能作为任务成功、官方分数改善或 A–F 排名改善的证据。本报告只评价实现与离线复核；工程冒烟的完整结论仍应由单独归档报告如实说明。
