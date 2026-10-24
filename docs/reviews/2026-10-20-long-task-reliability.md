# 长任务可靠性审查（持续进行）

目标：review 整个项目，查找遗漏的潜在问题并修复，使复杂场景下的长任务能够稳定交付。

这份记录区分已复现并修复的问题和仍需审查的链路。测试通过仅支持其实际覆盖的行为，不能据此宣称整个项目已经完成可靠性审查。

## 已复现与修复

### 目标账本损坏会被当成没有目标

`GoalStore._load()` 原先把读取错误、无效 JSON 和错误根类型视为空账本，并跳过部分非法记录；相同目标 ID 还会互相覆盖。这样创建新目标或进行费用结算时可能覆盖原有进度与累计成本。

现在仅文件不存在时返回空账本。现有文件无法读取、格式错误、目标身份重复、多个 active 目标、非法状态/计数器/费用/引用结构均明确报错，不保存替代状态。拒绝 NaN/Infinity，保存前也校验账本并禁止非有限 JSON 数值。旧记录的可选字段仍采用 dataclass 默认值。

证据：`tests/test_goal_store_regressions.py` 新增三个用例，覆盖真实文件字节保留、读取权限失败，以及旧可选字段与费用幂等。修复前出现 13 个预期失败；修复后该文件 7 个用例通过。

### 已存在的空协调/续作记录会被覆盖

`SessionCoordination._read()` 和 `ChildContinuations._read()` 把现有 `{}` 当成首次初始化。现在保留文件并报错；子任务 worker 不启动，协调查询和变更均拒绝该记录。文件不存在仍允许正常初始化。

证据：`tests/test_adaptive_child_continuations.py` 和 `tests/test_adaptive_coordination.py` 的新增用例在修复前出现 4 个预期断言失败；修复后两个文件 13 个用例通过。

### 普通目标轮次的真实工作被计为空转

`AgentService.stream_turn()` 结算普通会话中的目标工作时固定写入 `files_changed=False, tool_calls=0`。真实连续创建两个文件也会累计两轮 idle 并暂停目标。

现在从本轮输出的原生工具事件更新调用数量和成功写入标志，普通结算和草案重新确认前结算均使用这些观察结果；轮次身份和费用幂等路径继续复用。独立协调派发也修正了相同问题，使用已有原生成功工具计数；已确认回执的恢复结算读取同一计数，旧回执缺失时按 0 处理，不推断历史进展。

证据：`test_productive_ordinary_goal_turns_do_not_trigger_idle_pause` 使用真实 runtime、临时文件和离线模型执行两轮写入；修复前 `idle_rounds=2` 失败，修复后 `tests/test_adaptive_task_flow.py` 的 25 个用例通过。

### 非空但结构残缺的续作/派发记录导致未捕获异常

真实保存的子任务记录缺少 `thread_id` 时，续作路径抛出 `KeyError`；派发缺少 `owner` 时，状态查询也抛出 `KeyError`。现在在读取入口校验核心身份、版本、状态及消费者使用的字段类型；协调记录另核对 lease 关系、权限模式、范围及依赖有效性，限制派发记录为现有 writer 支持的最多 200 条，使依赖校验有界。异常文件保持原字节，不启动执行。有效 running/interrupted/终态和可选字段缺省保持兼容。

证据：两个真实依赖阶段完成文件写入、原生回执在费用结算故障后恢复、旧回执缺工具计数时只结算一次，以及畸形记录字节保留的新增回归均先失败后通过。最终定向命令 `PYTHONPATH=tests .venv/bin/python -m unittest test_adaptive_child_continuations test_adaptive_coordination test_adaptive_recovery test_adaptive_task_flow -q`：61 个用例通过。

### 完整输入摘要的容量、一致性及取消边界

旧实现对超过 5000 个文件或 50 MiB 的项目直接拒绝生成摘要；还会漏报“前序文件已经读取后被改写”以及“扫描中新增文件”。现在保留全部原安全输入范围与生成物排除规则，使用完整流式 SHA-256、私有临时 SQLite 前后清单以及文件版本/Git 状态一致性核对。没有自动按 Gitignore 排除输入。

清单和目录队列保存在磁盘，不将大目录的全部条目装入 Python 列表。SQLite 页面缓存 2 MiB，临时元数据索引上限 512 MiB，扫描时限 120 秒；缓存最多 131072 个文件摘要、估算 128 MiB。达到资源边界或读取/索引失败会拒绝返回部分摘要并清理临时文件。POSIX 缓存以设备、inode、模式、大小、mtime 和 ctime 绑定版本；读取时尚未经过两秒静默期的摘要既不复用也不入缓存，避免粗时间戳在后续变成旧命中。Windows 使用 Win32 FILE_BASIC_INFO.ChangeTime 核对清单及读取句柄，继续完整重读。Windows 原生新增用例需在实际 Windows 环境执行，本机的接口契约模拟不能替代该验证。

`_owned_worker` 将取消事件传入扫描线程，反复取消时仍等待资源清理，但扫描在当前读取结束后停止继续读块。主代理的输入核对复用同一 helper。底层已经阻塞的文件系统调用仍需等待系统返回；其他同步调用入口的响应性仍需后续审查。

证据：新增 `tests/test_input_fingerprint.py` 的 15 个定向用例通过，包含真实原生验证执行、超容量项目后续输入变更导致目标完成被拒绝、扫描中修改及新增文件、等长恢复 mtime、缓存淘汰、临时索引耗尽清理、旧摘要排序兼容、粗时钟延迟旧命中及反复取消。旧实现先复现了三项断言失败和两项容量错误；取消先观察到停止后仍读取 9 次，修复后只完成已经在途的 1 次读取。独立复核指出的 fresh 缓存和 Windows creationtime 两项 Important 均已修复。

临时数据集实测（5002 个文件，含一个 67108864 字节稀疏文件，静默期后执行）：首次扫描 0.438 秒/读取 5002 个输入，未变更复查 0.117 秒/读取 0 个输入，等长改写并恢复 mtime 后 0.118 秒/读取 1 个输入。前两次摘要相同，第三次不同。这是该数据集的本机测量，不能推广为任意项目的性能保证。

### 子任务和协调扫描阻塞事件循环、关闭流时清理失败

子任务启动前和协调派发回执收尾原先同步扫描项目。现在通过 `_owned_worker` 只将扫描放到后台，等待期间不持任务锁；子任务在创建运行记录前重新绑定父任务 ID/修订。协调恢复保留同步 API，异步 adapter 与同步 adapter 共用恢复步骤，仅摘要观察离开原线程，状态读取和提交仍由调用者执行。扫描之后重新检查任务、控制权及 checkpoint，防止旧回执被套到已经修订的任务。

协调生成器在收到 `GeneratorExit` 后原先仍 yield 通知，导致 `async generator ignored GeneratorExit` 和后续 ContextVar 清理异常。现在关闭/取消时先恢复权限引擎、保存中断回执并幂等结算，然后直接传播中断。回执扫描中发生取消也进入同一结算路径。最终 checkpoint 读取失败、返回空值时降为 `needs_reconciliation`，保留有效 lease，不会因覆盖为 None 而令整份协调账本无法读取。已经结算到预算/轮数上限的回执可恢复完成历史，不恢复目标、不重收费。用户暂停某一个目标会话时，其他独立会话仍保持原有可执行语义。

证据：第三轮新增 10 个 core 回归，原代码下先复现事件循环无响应、过期父任务仍启动、关闭流异常、扫描中修订仍完成、checkpoint 异常漏结算及空 checkpoint 污染账本；取消收尾和已计费暂停窗口也单独验证。`PYTHONPATH=tests .venv/bin/python -m unittest test_adaptive_child_continuations test_adaptive_coordination test_adaptive_recovery test_adaptive_task_flow test_input_fingerprint -q`：86 个用例通过，17.984 秒。独立复核发现的恢复竞态与空 checkpoint 两项 Important 已修复；最终复核未发现新的 Critical/Important，补充 probe 确认状态步骤留在 owner 线程以及取消时的 generator 清理。

### GUI 和会话导出的摘要扫描阻塞控制通道

HTTP 快照、历史节点、导出以及工作控制人工验收改为在锁外等待原生摘要，SQLite 读取和状态提交保留在 GUI 事件循环。返回后核对项目/session/branch、来源节点和完整任务快照；同 revision 的状态变更也会使旧观察失效。只读快照遇到未知输入保持 unknown。具有相同生成路径排除集合的工作项共享本次快照观察。同步 Python snapshot/node API 保持原语义，直接调用仍可能阻塞调用者。

真实反馈创建和确认链路还发现了第二次同步扫描：原测试只注入 verification 模块的函数别名，并在任务变化后提前拒绝，漏掉了 FeedbackStore 内层扫描。现已覆盖两个别名及正常发布路径。私有原生 observation 绑定项目、会话存储边界、完整任务/候选、文档和该文档/锁的精确排除集合，持锁提交前再次核对；同步 FeedbackStore 默认仍自行扫描。不会把仅排除验证生成物的摘要用到反馈候选文档的不同范围。

`GUIServer.call` 超时后现在取消 loop 内尚未完成的 coroutine，后台 worker 保持取消后清理所有权。`/recap` 和 `/export` 也使用异步观察，只有扫描前后相同的验证记录才能使用该观察，旧同步 recap API 保留。

证据：新增 15 个 GUI 扫描用例，包含真实 HTTP 心跳、stop/approval 并发、项目/任务替换、同 revision 状态变更、反馈正常和恢复路径、精确排除及候选变更；先复现阻塞后通过。GUI 定向回归 181 项通过，反馈同步 API 8 项通过，超时相关 7 项通过，会话动作与相关工作流 28 项通过。最终反馈补修的独立复核因代理用量限制中止，已进行根代理逐项 diff 检查和完整回归；不将中止的复核记录为通过。

### 进程硬退出后目标美元费用和调用预留丢失

独立离线 probe 复现：普通会话、UI goal 和 headless 在 provider 开始或 usage 交付窗口硬退出后，Goal 的 round/spent/settled_round_ids 没有变化，两次重启都能再次调用 provider。SessionTokenBudget 的 pending 只能保护部分在途 token 窗口，不能替代 Goal 的美元账。

现在 `goal_costs.py` 将有界 pending 轮次保存在同一 Goal 原子文件，旧记录缺字段默认空。模型调用前持久化预留；模型中间件返回响应前，将已知实际费用立即计入 spent_usd。轮次收尾在同一事务中扣除已计入部分并清除 pending，避免双记；未知或仍在途的预留保守计费并暂停。实际费用超出预留时保留完整数额，不用预留上限裁剪已知费用。写入失败不允许继续调用 provider，在途/用量未知费用也不能完成目标。

普通会话、UI goal、headless 和独立协调全部绑定原生目标/轮次身份，子代理共享父预算。每个原生预算持有独占、非阻塞轮次租约；恢复只处理取得租约的遗留，活跃预算不被另一个 factory/进程误判为崩溃。预算从执行到整个收尾都有确定清理所有权，失败 Task 和原始 traceback 仍被保留时也不会遗留活跃租约。Factory 在创建模型和 checkpoint 资源前恢复旧费用；prepare/resume 也会核对遗留。恢复状态持久标记为待核对，明确恢复保留原目标 ID、额度、轮数和历史费用，独立新目标需要原生新任务意图。

恢复阻止调用的回归原先曾在测试夹具中提前判断 paused；移除该夹具判断后又复现 ordinary legacy 路径绕过旧费用账，现已用持久恢复标记及按当前会话独立检索修复。其他会话的 active goal 不会遮住旧会话的恢复边界；多个未能唯一匹配的旧账本会阻止自动选择预算。相同金额的并发请求有各自原生 reservation ID，重复结算拒绝并保持原字节。

证据：费用原生边界定向 24 项通过，8 个四入口 provider/usage 硬退出子场景真正执行第二次原生入口，未由夹具提前阻止；还包含活跃跨进程租约、并发相同金额、缺失/超额 usage、读写失败、畸形/超大金额、旧字段兼容、显式恢复与歧义。已注册 task 工具实际执行父/两个子代理共 5 次模型调用，在故障导致 round 收尾未写入时，立即费用和恢复均不重复收费。110 项相关工作流定向组通过。独立复核发现并修复了收尾 RuntimeError 漏 close 和 foreign active 遮蔽恢复状态两项 Important；相应最小原生 probe 最终均通过。完整回归和最终增量复核仍以本节后续验证记录为准。

### 完成检查持锁扫描、反向锁及陈旧状态提交

独立探针复现 `GoalStore.update(state='complete')` 在目标事务锁内计算摘要：后台扫描受控等待 0.3 秒时，事件循环中的 `goals.active()` 被阻塞 0.312 秒。后续回归进一步复现同 revision 的用户暂停被覆盖、验证记录被旧检查覆盖、完成后仍发布陈旧 active 派发，以及 Task 所有者持锁与完成/验证写回之间的反向锁死锁。

现在先在所有事务外取得 Goal/Task 快照及完整输入摘要，再按 Task → Goal 顺序进入短事务，比较完整状态并核对费用、验收和最新派发。等待期间的新验证保留，旧检查不会清除或代其完成。`record_verified_run` 同样统一锁顺序。派发 admission 在 Goal 事务内重读完整目标快照，和最终完成互斥，避免读取旧状态后排入新工作。

另有两个真实边界先失败：已经暂停的任务能被直接完成；原生 `update_goal` 被反复取消后仍继续读取 18 个数据块。现拒绝直接完成暂停/阻塞任务，异步工具复用 `_owned_worker` 将取消传入摘要线程，在当前读取结束后停止；提交前再次检查取消，不清除既有验证。无法强行中断操作系统内已经阻塞的文件调用。

独立复核补充两项 Important：旧版本验证晚到时清空新版本证明；目标暂停、修订为禁止接管后恢复，同 ID 的陈旧授权仍能派发。现旧验证回执失配直接拒绝，不修改新证明；派发用完整 Goal 比较拒绝修订或控制状态变化。两个真实原生 probe 均先失败后通过，最终增量复核未发现剩余 Critical/Important。

证据：新增 `tests/test_goal_completion_observation.py` 共 12 项回归，涵盖真实验证命令、原生工具、派发及子进程锁顺序，缺陷用例均有 RED/GREEN 证据。首批相关工作流 110 项通过，取消及暂停边界后工具/自适应定向 53 项通过；证明保留补修后相关组 156 项通过，授权补修后协调/恢复组 48 项通过。最终完整回归见后续验证记录。

### 子任务硬退出遗留、原生进度丢失和续作查询

临时项目原生探针先复现：`ChildContinuations.run` 的 worker 内 `os._exit(29)` 后，重启记录仍为 running，续作被拒绝且记录不更新。真实子图进一步复现：读取第一页后在下一 provider 调用硬退出，已保存 evidence 仍为空。旧实现只在开始和最终收尾写记录，没有执行所有权或逐工具进度。

现在新阶段在记录发布前取得独占非阻塞租约，并持有到整个异步执行及最终保存结束；正常、取消和异常 traceback 被保留时也确定释放。Factory 在创建模型/SQLite 资源前恢复，后续查询和执行入口也核对遗留；只有取得租约的 running 才归类 interrupted，并明确 usage_complete=false，不自动调用模型或工具、不替父费用账本重复结算。旧版没有租约的 running 不足以证明进程退出，标为 usage_unknown，不能自动续作。

原生子图通过专用工具中间件逐次保存版本、分页及结果身份元数据，后续取消或进程退出仍能取得已完成读取的位置；正常结束按调用身份合并，避免中间进度与最终回执重复。私有 ContextVar 将并行子图各自的回执绑定到其阶段。`task_status` 只查询本会话历史，提供分页和具体引用详情；查询不核对当前输入，历史资料不扩大权限。显式 task 续作继续检查父任务要求、完整输入和一次消费；续作阶段崩溃后从新引用恢复，原引用保持已消费。

真实工具边界另发现 status 统一归一化会覆盖子任务 partial/usage_unknown，过大结果裁剪还会丢 resume_reference。现保留通用工具 status 语义，新增明确的 child_status，裁剪保留阶段结果及续作标识。损坏证据字段在读取和写入前共用校验；独立复核指出的 path 列表、原生 EOF 的 content_offset=None、bypass 历史绝对路径均补实证回归，既拒绝畸形文件，也保留合法只读元数据。

扫描后检查还不足以覆盖 semaphore/共享预算等待。现在实际 worker 启动前再次核对父任务身份、要求和控制状态；队列中暂停会阻止启动，未发送的预留按已知零用量释放，重复取消仍清理并传播取消。正常进度/证据变化不会误撤销排队授权。没有跨 await 持有任务或全局记录事务锁。

证据：新增 `tests/test_child_crash_recovery.py` 20 项，覆盖真实硬退出、活跃跨进程租约、重启原生查询并从字符 40 补读到 80、并行原生读取隔离、重复取消、初始/进度/收尾失败、续作链与分页、合法/非法元数据、业务状态裁剪，以及原生子图硬退出后父美元账与会话 token 的联合恢复。后者仅结算一轮美元费用，保留 unknown token 停止状态，查询/重复恢复无 provider 调用。缺陷用例有 RED/GREEN 证据。相关组 106 项、排队补修后 45 项及最后权限投影组 71 项通过；最后新增文件 20 项通过。独立最终增量复核未发现剩余 Critical/Important；完整固定代码证据见下方。

### 大历史归档截断未传播，压缩后无法恢复完整要求

实际原生探针先复现：超过 2 MiB 的早期 HumanMessage 尾部含唯一要求，L2 仍替换原消息。查询显示 archive_truncated=true，但新摘要 index_truncated=false，模型可见摘要和正文归档均无法取得尾部要求。AI metadata、工具参数和继承引用也不能仅靠有界预览证明完整。

现在小记录保留 v1 引用，大记录使用 v2 域区分旧部分记录，以 1 MiB 内容寻址分片保存，最后原子发布清单。读取验证每片大小/摘要、完整摘要及原始消息/参数身份；并发重试不覆盖既有对象。单条完整编码记录最多 64 MiB，超限在发布前失败，保留原始消息。写失败或硬退出留下的未发布分片不成为有效引用，可由相同输入重试复用；尚未增加孤立分片回收，不声称整个存储容量无上限。

`read_history_result` 默认仍分页正文，新增 message/arguments section 以恢复完整消息元数据和参数，每页至多 6000 个 Unicode 字符。参数/标识简介的预览截短与原始归档完整性分别标记。L1、L2 和继承 summary 引用统一核对完整性，旧部分归档不能支持清除或生成冒充完整的索引；不覆盖或改写旧部分对象。旧 goal/plan 只解除 pin、保留原文，真正压缩时再进入归档及可发现索引。大记录分页当前会读取并验证整条有界记录，不声称常量内存或随机读取性能。

异步模型入口另复现事件循环被同步归档阻塞。现在预处理和请求构建通过持有清理责任的 worker 执行；取消传入分片读写，并在释放资源后传播。provider 调用、Graph checkpoint 提交保留在原生运行循环。受控门闩回归验证事件循环可继续、重复取消不遗留 worker、请求准备取消不调用 provider，以及真实 Graph checkpoint 保留完整工具原文。

独立复核另外复现三项 Important：原生入口提前删除旧 pin 绕过归档；worker 等待时 Task 暂停或修订后仍调用 provider；手动 `/compact` 继续在事件循环归档。现统一解除旧 pin 并保留正文，在原生模型记账边界于预留前及全部预算等待之后重核 Task ID/版本/停态，拒绝时释放已知未发送的预留，不计 attempts/轮次。手动压缩持原生项目执行租约、后台归档、owner 读取和提交 checkpoint，等待后发现新 checkpoint 则拒绝覆盖。取消等待 worker 清理后释放执行租约。

证据：新增完整归档 10 项、存储故障 7 项及异步责任/手动压缩 12 项，另增加 1 项原生预算压力下通过注册工具恢复大结果尾部的回归，共新增 30 项。包含两个 Context 等待阶段各自的暂停、阻塞、修订、更换任务和正常进度共 10 个原生 Graph 子场景；真实 SessionTokenBudget 预留后暂停保证零费用/零 attempts 且不产生 unknown。存储用例包含真正 os._exit 的分片/清单两个窗口、写入失败、并发幂等、中文/emoji 边界、大参数/metadata、缺片/篡改/链接/FIFO、容量限制、隐私与 v1 兼容。缺陷回归有 RED/GREEN 证据；首批 92 项、pin/兼容组 147 项及最终预算/手动组 136 项通过。独立复核和全量固定版本证据以下方最终记录为准。

### 会话队列关闭提前返回、取消输入仍执行及自依赖

真实 SessionRunner 探针先复现：close 被取消后，在 current finally 仍持有清理门闩时返回；释放门闩后 queue 仍为 1、busy=true、待执行 Future 永久 pending。原生 inline UI 的 finally 在 runner.close 之后关闭 Factory，故提前返回会破坏资源关闭顺序。另一个原生 pump 完成回调调用 resume，stop 返回时 state=paused、下一项却已在运行；取消待执行 Future 原先也仍执行其回调。

现在每个 runner 的单一 stop owner 只取消当前输入一次，所有 stop/close 等待同一清理。调用方反复取消仍先等待 owner，再传播取消。恢复、启动和重绑定检查停止屏障；旧 stop 等待者不写回新代状态。close 先拒绝提交并撤回等待 Future，最终清空队列。取消的待执行项跳过并释放容量，取消当前 Future 进入同一停止流程，后续项只能显式恢复。业务吞取消并正常返回及普通回调失败后继续下一项的既有语义保留。

独立复核另外复现三项 Important：Future 已取消但 done 回调晚于 pump 唤醒时，下一项抢先执行；当前 Future 取消后，child 清理异常无处报告；通过子 Task 等待自身 stop 仍形成不可取消环。现 pump 在交接前同步重核取消，清理错误保留在具体 pump 结果，由 wait_idle/stop/close 报告，取消本身的异常保留晚到失败作为 cause。stop owner 取出异常避免无人等待警告，但不清除可观察结果。业务 scope 用绑定当前输入身份的 ContextVar，直接、create_task、TaskGroup 和嵌套 runner 的自依赖控制均立即拒绝，旧 scope 不会误阻止新代外部控制。

新 scope 护栏与 Windows plain Ctrl+C 也有一条真实调度分支回归：Python signal handler 原先从被中断 business Task 复制上下文，外部停止被误判为自依赖。现在注册时捕获 controller Context，Windows call_soon_threadsafe 显式使用它；POSIX 保持原有注册语义。回归在不修改全局 os.name、不安装真实信号的前提下触发该分支，真实 Runner 能正常停止；这不是 Windows 实机验证。独立另跑真实 POSIX SIGINT 和重复/回调内再次触发 probe，通过且无 Context 重入错误。

证据：新增 tests/test_runner_owned_stop.py 17 项，包含真实子进程树/PID/延迟写入、Factory → AgentService → Graph → SQLite 的关闭顺序、反复/并发取消、停止与恢复交接、取消 Future 容量/绑定、错误报告、旧代上下文，以及 3 个控制方法 × 4 种直接/委托/嵌套路径共 12 个有限子进程自依赖场景。缺陷均有 RED/GREEN：首组 9 项中 5 个失败/2 个错误、容量回归 RED、交接/selfjoin 两项 5 个失败、委托/错误三项 11 个失败，信号上下文回归也先 RED；最终相关定向 104 项，3 项平台跳过，其余通过。复核初次因代理容量中止，后续完成最终增量复核，独立 5 项及 2 个额外原生 probe 通过；信号增量也独立通过，未发现剩余 Critical/Important。最终全量和发行验证以下方固定代码记录为准。

### MCP 排队取消连带断开、重复关闭及 admission 竞态

原生本地 stdio 探针先运行 pause，再把 echo 排队；仅取消未开始的 echo，却使 pause 返回 mcp_disconnected、started=true，连接关闭。排队超时存在同一问题。新请求记录保存身份、调用方取消代数、绝对截止时间及 SDK admission/完成状态；未开始请求只撤回自身并立即释放队列容量。owner 在调用 SDK 前同步检查取消和截止时间，避免事件循环来不及运行 Future/timeout callback 时仍执行已撤回或过期的请求。已交给 SDK 的请求继续按副作用未知处理，不自动重放。

真实 stdio transport 上下文门闩进一步复现并发 active cancel/disconnect 再次取消 SDK teardown、断开调用吞掉调用方取消、关闭中仍可发现工具及整体退出等待生命周期锁时取消导致资源遗留。现在同一连接只取消 owner 一次，立刻停止 admission 并撤下工具；每个等待者隔离自己的重复取消，等待 SDK 上下文原 Task 完整退出后传播。整体 aclose 先取得清理所有权并关闭全部捕获连接，再等待生命周期锁；初始化 ready 同步撤回，重连等待旧连接后也重核 shutdown，避免退出快照漏掉新进程。调用发生传输错误时同时等待自然 teardown，避免已完成错误回执先于资源关闭返回。

独立复核进一步发现单独 disconnect 在生命周期锁外尚未取得清理所有权，取消等锁或第二个控制者时仍会提前返回。现在 disconnect 在首次 await 前捕获原连接，直接共享该 owner 的清理；connect 替换前已等待旧 owner，不需要全局锁阻挡这个停止入口。执行门原有 started 属于本地处理器进入状态，不能直接信任 handler 覆盖；新增本地 mcp_admitted 分别保留 SDK 接纳证据到原生 ToolMessage、observer 及大结果摘要，缺失表示未知，不用于权限或通用完成可信度。真实 BrokenPipe 导致 SDK TaskGroup 自己取消 owner 时也有关闭遗漏；现在统一 owner stack 在 SDK __aexit__ 前停止 admission，初始化 ready 的唤醒仍保留。

证据：tests/test_mcp_cancellation.py 新增 20 项本地原生用例，含 stdio/Streamable HTTP 排队隔离、18 次撤回的容量复用、重复取消、多连接/PID 清理、初始化中退出及锁/重连竞态、原生消息/观察/大结果字段和真实断管故障。旧实现首组 3 项全部失败；关闭组 4 项全部失败，生命周期锁退出、重连、取消/截止时间 admission 的新增回归也均先 RED。复核三项 Important 的对应 RED 为 disconnect 两项、原生 metadata 四项、BrokenPipe 一项；SDK 故障门闩使用 AnyIO shield 排除 SDK 自身层级取消，确认没有额外 Task.cancel，关闭后 PID 不存在。最终相关组 93 项，14 项平台跳过，其余通过；独立最终复核 41 项及 7 个额外原生探针通过，覆盖 SDK 故障、初始化/发现自然失败及 shutdown、代次竞争与原生取消观察，未发现该增量剩余 Critical/Important。完整回归及发行验证以下方固定代码记录为准。没有调用真实模型 API、第三方 MCP 或 Windows 实机。

### 终端 Ctrl+C 过早报告停止与控制任务交接

plain 的信号 callback 原先先 create_task(runner.stop())，然后同步保存暂停并打印“当前任务已停止”。真实 run_inline → Factory → Graph → SQLite 配合离线 provider 清理门闩，已复现模型 finally 仍等待时就显示完成。保存暂停失败会带原始异常逃逸到 loop callback；停止控制失败会成为未接收的 Task 异常。两种原生故障注入均使用假密钥确认未脱敏错误能进入 loop handler。

新增 begin_stop 同步建立现有停止屏障，返回绑定原 owner 的 coroutine；async stop 保留既有入口和 None 返回。等待结果只在同一停止仍是当前 paused 代时为 true，恢复后的旧 waiter 既不停止新工作也不生成错误的成功回执。创建等待对象与真正 await 都检查自依赖，有限子进程复现了外部创建 coroutine 被交给自身业务 cleanup 后的循环（exit 73），补修后立即拒绝。

终端持有一个停止控制 Task，捕获原 service/thread/key，先显示正在停止；保存暂停和停止错误单独脱敏处理，重复中断不重复创建控制者。真实清理结束且身份仍有效时才报告完成。退出先加入控制者再关闭 Factory。复核补出的 Minor 是控制 Task 首次运行前取消：嵌套 coroutine 未被 await，正常 /exit 也被误当作 CancelledError。现由 done callback 关闭持有的 coroutine，gather 将控制者自身取消作为结果，只在退出调用者真的取消时继续传播。

证据：tests/test_terminal_stop_receipts.py 新增 10 项，包含真实 Factory/Graph/SQLite、Goal 暂停、重复信号、恢复后迟到回执、三次取消真实 UI shutdown、实际 POSIX SIGINT 子进程、执行时委托自依赖及首次运行前取消。首组 5 项全部 RED；委托自依赖和 pre-start cancel 也先 RED 后修复。修复后新模块 discover 入口 10 项通过，相关组 80 项，1 项平台跳过，其余通过。独立指定组 74 项通过/1 跳过，另有 workflow 10 项和原生 InputBroker/控制者前置取消探针；最终未发现该增量剩余 Critical/Important。首轮完整回归 1873 项有 1 个新增 SIGINT 子进程导入失败，原因是 discover 只设置父进程 sys.path；现显式传递测试路径，最终完整和发行证据以下方固定代码记录为准。

### Factory 并发初始化、失败回滚及跨入口资源归属

第十一轮原生探针确认：inline 在读取会话列表或恢复提示失败/取消时，SQLite 仍打开；Factory 首次并发 async_runtime 会开出两套 saver，setup 失败的 saver 已被发布；反复取消 aclose 会越过写入锁等待，首项预算错误会跳过后续预算和数据库。主模型切换、推理配置切换及专用子模型的 OpenAI/AsyncOpenAI 客户端没有完整归属。构造错误的 traceback 被保留时，垃圾回收无法代替主动回滚。

现在 Factory 使用独占初始化与单个关闭控制任务。取消开库先加入真实连接线程/初始化任务，再回滚；只有 setup 成功且 Factory 未关闭时才发布 saver。失败回滚的未发布连接仍被登记，重试前先完成清理，不能连续新增连接。关闭等待初始化和 checkpoint 写入，逐项释放所有资源，保留失败项供后续显式重试。SQLite 同步/异步 context 的 generator 在 finally 抛错后也会耗尽，单纯重试 context 会假成功；因此始终显式关闭保存的原生连接。已完成关闭 Task 可在其事件循环关闭后再次观察，不向已关闭 loop 注册回调。

模型创建均登记 SDK 客户端。旧主模型保留至最终关闭；专用子任务模型在成功、取消及编译失败时及时关闭，避免长期累计。共享模型的归属在创建子任务时固定，不能因之后切换主模型而提前关闭旧共享图。同步 close 拒绝异步 saver/初始化/关闭及活动 MCP；未用于异步图的 SDK 池可以同步收尾。直接调用 model 的异步 API 不属于同步资源契约，调用方仍应在原 loop 使用 aclose。

入口修复包括：inline 将清理责任提前到取得 Factory 时，并在启动、候选失败和所有退出路径收尾；TUI 的 run_async finally 与 Unmount 共用控制者，失败后允许重试，候选先准备完成再采用；legacy CLI 把会话列表、初始化和 REPL 异常退出都放在原 Runner 的 finally 内；headless 逐项收尾，关闭错误进入脱敏 error 结果，刷新失败不沿用旧交付结论。单项失败不会跳过其他 Factory。六个旧测试 fixture 原先反向登记 async/sync cleanup，因 unittest 逆序执行而先同步关闭，现调整登记顺序，保留原有断言。

GUIController 和 BranchStore 构造失败会回滚已取得的数据库，关闭首个数据库失败仍尝试其余连接。GUI 关闭控制者隔离调用方反复取消，先加入实际 Graph，再释放 Factory 和两个分支数据库。项目切换先完整采用新项目，将旧状态交给独立退休控制器；取消/失败时不丢候选或关闭新项目，失败旧资源在退出及下次切换前重试。旧密钥必须由旧 owner 在向新项目传递错误前脱敏，首次 notice 和重复清理错误均有回归。

GUIServer 关闭同步封住公共请求调度，取消并加入已准入 HTTP 请求，再关闭控制器。普通响应超时不用于资源关闭，清理失败保留原 loop 供重试；成功后才停止并加入 loop 线程。构造失败和 HTTP 绑定失败也会关闭控制器及 loop。并发 close 受同步锁保护，不能在 loop 线程自我 join。独立原生 probe 覆盖项目切换等待旧清理时 server_close 同时取消 HTTP 请求：关闭始终等待清理门闩，新旧项目四个真实 SQLite 连接最终全部关闭。

证据：新增四组 42 项原生生命周期回归，使用真实 SQLite、SDK、本地 MCP、临时文件及离线模型，无真实模型 API。构造/初始化/取消、原生 close 失败重试、入口早期异常、子模型共享、headless error、GUI 事件循环超时和绑定失败等先复现失败后通过；关闭交错有额外独立 probe。独立复核发现的问题均已补修，最后复核无剩余 Critical/Important；完整回归和安装包结果记在下节。

## 验证记录

- 开始审查时的完整 Python 离线测试：1682 个用例，44 个跳过，成功；耗时 149.698 秒。
- JavaScript 原生单元测试：`node --test tests/desktop-runtime.test.cjs tests/gui-control-map.test.mjs tests/gui-diff.test.mjs tests/gui-preferences.test.mjs`，12 个通过；`node --test tests/gui-map.test.mjs`，另 5 个通过。
- 第一批修改后的完整 Python 离线测试：1688 个用例，44 个跳过，成功；耗时 152.238 秒。后续新增修复须再次核对最终版本。
- `requesting-code-review` 独立只读复核：目标账本与普通轮次结算未发现新的 Critical/Important；38 个定向测试通过，22 个可选字段逐项或同时缺失仍兼容读取。
- 续作与协调最终 diff 的独立复核未发现新的 Critical/Important；实际执行后更新 checkpoint、释放控制、缩小范围及更换 owner/goal 重新接管，均保留可读历史。
- 最终固定代码的完整 Python 回归：`python -m unittest discover -s tests`，1696 个用例，44 个跳过，成功；耗时 154.368 秒。相对基线新增 14 个用例，多个用例包含表驱动的损坏状态子场景。`git diff --check` 通过。
- 未执行真实模型、长时间故障注入、浏览器交互、Windows 原生或安装包验证。
- 第二轮：完整 Python 离线回归 1712 个用例通过，45 个跳过，耗时 170.980 秒；输入摘要 15 个定向用例通过。wheel 使用 `pip wheel --no-build-isolation --no-deps` 构建，并通过源码目录之外的已安装 help/JSON doctor/dependency smoke；新增两个摘要模块和验证/主代理模块与当前源码字节一致，在安装目录实际调用摘要成功，无模型 API 调用。
- 第二轮最终独立复核未发现新的 Critical/Important。补充 probe 覆盖 SQL/旧遍历顺序及非法字节文件名 round-trip、四线程并发扫描与内容修改、缓存淘汰及容量记账；不代替 Windows 实机验证。
- 第三轮最终完整 Python 离线回归：1740 个用例，45 个跳过，成功，175.210 秒；JavaScript 17 个用例通过；`git diff --check` 通过。新增 28 个用例，其中 10 个 core、15 个 GUI 扫描、1 个 HTTP 超时及 2 个会话动作扫描。
- 第四轮费用恢复：完整 Python 离线回归 1765 个用例，45 个跳过，成功，183.526 秒；JavaScript 17 个用例通过；`git diff --check` 通过。相对第三轮新增 25 个用例，费用原生定向 24 项通过，另有协调异常 traceback/租约回归。独立最终增量复核未发现新的 Critical/Important。
- 第四轮 wheel：`pip wheel --no-build-isolation --no-deps` 构建成功，SHA-256 `4bc42bc6b2879823fd92597bb0b053d3af42856e47fcbcafabf946bb940b2c70`；源码目录之外 CLI help/JSON doctor/dependency smoke 通过。安装目录实际运行费用 reserve/settle/round closeout 成功，并核对八个费用/执行相关模块与当前源码字节一致；没有模型 API 调用。未执行实际 Windows、真实模型或长时间随机故障 soak。
- 第五轮完成检查：最终固定代码完整 Python 离线回归 1777 项，45 项跳过，其余通过，188.083 秒；JavaScript 17 项通过；`git diff --check` 通过。相对第四轮新增 12 项；锁顺序用例包含完成及验证写回两个子场景。独立复核两项 Important 已修复并重跑原生 probe，最终增量未发现剩余 Critical/Important。
- 第五轮 wheel：构建成功，SHA-256 `80db04c31234676fdf5345ad12e2428314db5032c7b4a2d197941a7eee97a8bc`；源码目录外 CLI smoke 通过，安装模块实际完成原生 VerificationService → 异步 update_goal → Task/Goal 完成流程，并核对三个本轮生产模块与源码字节一致；没有模型 API 调用。Windows 原生及长时间随机故障 soak 仍未执行。
- 第六轮子任务恢复：最终固定代码完整 Python 离线回归 1797 项，45 项跳过，其余通过，195.871 秒；JavaScript 17 项通过；`git diff --check` 通过。相对第五轮新增 20 项。独立复核中的损坏证据、EOF/bypass 原生兼容、队列暂停均修复并独立重跑；最后普通进度投影增量未发现新的 Critical/Important。
- 第六轮 wheel：构建成功，SHA-256 `fcdee3781de0cfdeb7091eddf1793c72cbbf25020e374bb711c119761913ec27`；源码目录外 CLI help/JSON doctor/dependency smoke 通过。以安装模块运行 4 项原生集成（测试夹具从源码 tests 读取），覆盖进程硬退出、Factory 恢复、task_status 查询/从字符 40 续读到 80、美元/token 护栏及队列暂停/正常进度，全部通过；断言模块路径位于安装目录，六个本轮生产模块与源码字节一致。没有模型 API 调用；本阶段没有 Windows 原生或长时间随机故障 soak 结果。
- 第七轮完整历史：最终固定代码完整 Python 离线回归 1827 项，45 项跳过，其余通过，195.852 秒；JavaScript 17 项通过；`git diff --check` 通过，相对第六轮新增 30 项。首轮全量发现两个旧测试仍用缩小 v1 上限来制造截断，现改为真实 legacy 部分记录夹具，保留原文保护和拒绝不完整恢复的断言。独立复核三项 Important 已修复并重跑原生边界，最终未发现剩余 Critical/Important；独立最后 5 项预算/手动压缩回归通过。
- 第七轮 wheel：构建成功，SHA-256 `ac2ff9df4f5b3ac3a0611b15d972278236f032ce67b8f7cc470895b673395ff4`；源码目录外 CLI smoke 通过。安装目录实际运行 14 项原生集成，含大历史重复 L2、完整 metadata/arguments 分页、注册工具/预算压力恢复、分片/清单硬退出、Graph 取消、两阶段 Task 变化护栏和手动压缩/新 checkpoint 保护，全部通过。断言模块路径位于安装目录，七个本轮生产模块与源码字节一致。没有模型 API 调用；Windows 原生、长期随机故障 soak、孤立分片回收及大历史分页性能仍不在本轮验证结论内。
- 第八轮队列停止/关闭：最终固定代码完整 Python 离线回归 1844 项，45 项跳过，其余通过，197.162 秒；JavaScript 17 项通过；`git diff --check` 通过，相对第七轮新增 17 项。最终相关组 104 项，3 项跳过，其余通过；独立复核 late cancel、错误保留、委托自依赖及信号上下文均修复，最终未发现剩余 Critical/Important。该轮结论不覆盖当时尚未修复的 MCP 排队取消连带断开或 plain 停止文案回执。
- 第八轮 wheel：构建成功，SHA-256 `1f82eeb6ac0f9cd5b3be9b2e6666d539cd6f3ae0f063b0733df55016a4a571db`；源码目录外 CLI help/JSON doctor/dependency smoke 通过。安装目录实际执行新增 17 项，覆盖队列取消、错误、12 个有限自依赖子进程场景、真实子进程树/延迟写入、Graph/SQLite 关闭顺序及控制上下文，全部通过；断言实际模块路径位于安装目录，六个运行文件与当前源码字节一致。没有模型 API 调用；Windows 控制分支为模拟，原生 Windows 及长时间故障 soak 未执行。

- 第九轮 MCP：最终固定代码完整 Python 离线回归 1864 项，45 项跳过，其余通过，207.897 秒；JavaScript 17 项通过；`git diff --check` 通过。相对第八轮新增 20 项。最终相关组 93 项，14 项平台跳过，其余通过；独立复核三项 Important 全部修复，最终独立 41 项与 7 个额外原生 probe 通过，未发现该增量剩余 Critical/Important。
- 第九轮 wheel：SHA-256 `c554df233dd6f0a609f0f3b2ad29cf1f615a583f4f40cbd1dcf8fba91102fa73`；源码目录外 help/JSON doctor/依赖 smoke 通过。实际安装模块的 MCP、原生 Graph、队列及工具结果组 93 项，14 项平台跳过，其余通过；模块路径来自安装目录，十个相关运行文件与最终源码字节一致。没有调用真实模型 API、第三方 MCP、Windows 实机或长期 soak；整体项目审查继续。

- 第十轮终端停止：最终固定代码完整 Python 离线回归 1874 项，45 项跳过，其余通过，213.685 秒；JavaScript 17 项通过；`git diff --check` 通过。相对第九轮新增 10 项。初次完整回归中新增 SIGINT 子进程的导入错误已修正，最终 discover 入口覆盖该用例；原生自依赖及控制者前置取消均有 RED/GREEN。独立复核无剩余 Critical/Important，1 项 Minor 已补修并独立重跑，另核对 InputBroker 的单读者与清理后输入。
- 第十轮 wheel：SHA-256 `846455396682c3bf99cac2cdfc732c46f6940fc1aa0a8883ba6a065c21e03698`；源码目录外 CLI help/JSON doctor/依赖 smoke 通过。实际安装模块的终端、Graph、队列与共享工作流组 80 项，1 项平台跳过，其余通过（8.752 秒），11 个运行文件与当前源码字节一致。实际 POSIX SIGINT、门闩与反复取消检查均在隔离测试进程/临时项目中执行，无模型 API 调用；Windows 实机、硬退出和长期 soak 仍待审。

- 第十一轮 Factory/入口生命周期：最终完整 Python 离线回归 1916 项，45 项跳过，其余通过，217.550 秒；JavaScript 17 项通过；git diff --check 通过。相对第十轮新增42项。最后旧项目重复清理错误脱敏补修也已纳入该完整回归；独立复核和额外 GUI 切换/shutdown 原生交错未发现剩余 Critical/Important。
- 第十一轮 wheel：SHA-256 `8e0b14f88e1e6bdf9fab99c5596a365a8ec79cebc2af61bd7101e1b27da54aea`；源码目录外 CLI help/JSON doctor/dependency smoke 通过。以 pip --target 实际安装 wheel，从外部工作目录运行180项 Factory/入口/GUI/TUI/CLI/MCP/队列回归，全部通过（43.363秒）；11个关键模块路径均来自安装目录，全部142个发行 Python 文件与源码字节一致。测试fixture仅通过测试namespace和tests目录加载，未把生产源码根加入搜索路径。首次安装组的fixture namespace错误已修正后重跑。无真实模型 API；Windows 原生、长期 soak、真实工作区输入范围和全面交付仍待审。
- 第十二轮输入/产物证据：最终完整Python离线回归1953项，45项跳过，其余通过，239.433秒；JavaScript17项与diff check通过。相对第十一轮新增37项。首轮1951项通过后，补入坏旧清单恢复及迟到delivery CAS两项原生RED/GREEN，再完整重跑。独立复核在本轮约定范围内未发现剩余Critical/Important，原生额外探针覆盖9份坏旧清单/8条警告上限及完整权限全部丢失后拒绝。
- 第十二轮wheel：SHA-256 `87dc2a85576de53b93d9a3dfd8478e74c7f5858a30b7acd523be879fece3af01`；外部CLI help/JSON doctor/dependency smoke通过。pip --target实际安装后，从外部目录运行262项原生/入口/GUI/Task/Goal/取消回归，全部通过，41.448秒；22个关键模块路径来自安装目录，全部145个发行Python文件与源码字节一致。首次安装组额外写入了不存在的test_session_actions选择，262项已通过但多出1个导入错误；修正选择后完整重跑262项通过。无真实模型API。实际工作区旧外部链接、失败构建/产物归属恢复、Linux/Windows原生目录证据及长期容量仍未完成。
- 第十三轮活动输出归属：最终完整Python离线回归1982项，45项跳过，其余通过，250.438秒；JavaScript17项及diff check通过。相对第十二轮新增29项。最终原生专项29项通过，12.673秒；157项相关回归在独立复核前通过。复核唯一Important（超时误登记）及作者发现的深层中文路径拒绝均先RED后修复。首次复核后全量1981项中，已加载的旧版新增夹具未解析macOS /var路径导致1个错误；修正夹具、补齐跨会话迁移两种顺序后，按最终文件重跑1982项通过。
- 第十三轮wheel：SHA-256 `f9e4b54fc3f2e55dea4b035a3705456f3e2b27eaf8a4221d0abfdba01fc897c9`；外部CLI help/JSON doctor/dependency smoke通过。pip --target实际安装后，从源码外运行291项输出恢复、命名空间、Task/Goal、GUI、入口/生命周期及取消回归，全部通过，56.481秒；24个关键模块路径来自安装目录，全部147个发行Python文件与源码字节一致。完整日志分别为 `/tmp/ignovate-output-authority-accepted-full-python.log` 与 `/tmp/ignovate-output-authority-final-installed-native.log`；无真实模型API、提交或推送。整体目标继续active。
- 第十四轮产物存储：最终完整Python1999项，45项平台跳过，其余通过，257.305秒；JS17及diff check通过。相对第十三轮新增17项。独立复核唯一Important（目录替换误删）补入两项真实rename RED后修复，相关74项通过29.577秒，额外8次目录替换回归通过且原生FD4→4。完整日志 `/tmp/ignovate-artifact-storage-final-full-python.log`，相关日志 `/tmp/ignovate-artifact-storage-review-green.log`。没有复核后第二个审查者；最终修复按原生RED/GREEN及完整回归验收。
- 第十四轮wheel：SHA-256 `1a49e0fa65caaeb354e1d23b8344c4a0f34c5220319a5cb8b8b82fd3d48b6537`；外部CLI help/JSON doctor/依赖smoke通过。实际pip --target安装后，在源码外运行308项存储/命名空间/输出归属/Task/Goal/GUI/入口/取消回归，全部通过，60.308秒；26个关键模块路径来自安装目录，全部149个发行Python文件与源码字节一致。日志 `/tmp/ignovate-artifact-storage-final-installed-native.log`、`/tmp/ignovate-artifact-storage-final-wheel-smoke.log`。无真实模型API、提交/推送或用户IDE/CSS变更；有效历史容量、跨平台原生与全面交付仍待续。

- 第十五轮完整上下文续作：完整Python2007项，45项平台跳过，其余通过，263.985秒；JS17及diff check通过。新增8项原生回归，含16轮、两次压缩、两次重开SQLite、失败构建→修复→验收→交付及外部修改后失效。独立最终复核无Critical/Important，独立44项通过7.782秒；1项Minor投影诊断偏差已记录待下一阶段处理。完整日志 `/tmp/ignovate-long-continuation-final-full-python.log`。
- 第十五轮wheel：SHA-256 `d45960666b140496db8194e56f739a1b6d3a82adb2c1e55e4acf84c5073760c7`；外部CLI smoke通过。pip --target实际安装后，源码外405项原生回归全部通过77.401秒；27个关键模块路径来自安装目录，149个发行Python文件与源码字节一致。日志 `/tmp/ignovate-long-continuation-final-installed-native.log`；无真实模型API、提交或推送，整体goal继续active。

## 第十二轮：输入命名空间与产物证据

第十一轮验收后继续原生审查，先复现以下四项，再落实专项回归：

- 当前真实工作区按既有过滤范围测量为106149个普通文件、10073654300字节；完整摘要仍拒绝应用包内部链接。抽查Python/framework链接均指向项目内存在目标。不能将Factory测试通过推广为真实工作区验证身份可用。
- 真实VerificationService/TaskStore/GoalStore成功构建bundle/app.txt后，将该生成物改写；目标仍返回goal_completed=true、任务进入completed。输入排除缺少独立产物当前身份，旧验证错误支持了已变化交付物。
- 真实构建101个生成物，返回记录为101条，持久事件只有100条；下一次验证以“生成物不能排除本次已有输入”拒绝。通用事件展示截断污染后续生成物权威。
- 原生验证要求assets空目录存在，通过后删除该目录，目标仍完成。普通文件摘要没有绑定目录命名空间。

规范与计划：[完整项目身份与交付产物证据](../superpowers/specs/2026-10-22-input-artifact-evidence.md)、[实施计划](../superpowers/plans/2026-10-22-input-artifact-evidence.md)。当前实现将输入身份升级为v2：临时SQLite队列遍历完整目录命名空间，避免Python递归深度与成串固定句柄；普通文件完整内容/权限、目录存在性/权限、内部链接原文及目标状态分别入摘要。内部缺失、循环和40跳ELOOP有明确身份；项目外和受保护目标仍拒绝覆盖。前后完整清单、原生版本和Git必须一致，旧身份自然陈旧，不清任务/费用/历史。

生成物使用固定私有边界的不可变完整清单：1MiB片段、SHA-256、64MiB总上限、完整核对和最后原子发布；Task、Goal及JSONL只保存有界引用与64项预览。Task和Goal位于不同私有边界时，先复制核对完整清单，再短事务登记。源输入与产物在同次owned snapshot观察，Goal完成、Task/AgentService交付、自动verify复用、GUI工作控制/节点、反馈、回顾和导出均读取完整证明。目录只排除已登记自身条目，未知后代/兄弟仍是输入；首次缺失的输出父目录只登记自身元数据。

独立复核推动补修了审批期间/验证预检之后新增文件升格为输出、目录rename/chmod后恢复、反馈双快照拼接、根链接再次验证、嵌套输出首次构建、坏旧清单阻塞健康新清单、迟到delivery观察抹除新验证等边界，均有原生RED/GREEN。输入稳定性另保存原生变化版本；可变产物父目录及已有祖先使用macOS原生kqueue移动/删除/属性见证。资源由run owner持有，反复取消先加入工作线程再关闭；独立连续8次取消后原生FD由7回7，关闭均在非主线程。Linux inotify分支只有静态复核，Windows没有本轮原生guard证据，未知平台保留严格版本而非省略时钟。

实际工作区再次只读观察：24.743秒、峰值RSS 80117760字节，明确拒绝历史 `dist/macos-test/.../runtime/bin/2to3` 的项目外链接；目标为已删除的旧打包临时目录。当前macOS打包器已移除这些辅助脚本，该旧产物未被修改/删除。内部应用/framework链接的原生专项已通过，但真实工作区完整身份仍是unknown；没有通过忽略dist/artifacts制造成功。

原生完整产物专项覆盖5000文件、超过1MiB清单和Task文档上限、重启复验、损坏/缺失/发布失败/容量耗尽、旧文件记录迁移、完整Task/Goal边界、实际AgentService与GUI中预览外产物变更。最终完整及发行验收另列在本轮记录中；整个目标继续active。

### 第十三轮起点：失败构建与失效产物的归属

原生同一配置命令首次创建bundle/ready后exit1，若能再次执行则exit0。首次记录为failed、started=true且有完整manifest；第二次却在预检以“生成物不能排除本次已有输入”拒绝，持久verification事件仍只有1条。复现日志 `/tmp/ignovate-failed-build-retry-probe.log`。现有“仅成功验证可授权已有输出”的规则混淆产物生成归属与验收成功，可能让长任务在首次构建失败后无法继续；下一阶段需独立的受控输出归属与失败/取消/崩溃恢复证明，不能简单接受任意失败记录或把目录前缀全部排除。

另一个原生反例：第一次成功构建包含bundle/retired.py；第二次成功构建删除它，完整新清单确认不再包含该路径；随后用户在该位置写入新源码，第三次验证仍由所有历史成功路径的并集把它当作生成物，启动后删除该源码并报告passed。日志 `/tmp/ignovate-retired-artifact-authority-probe.log` 显示first/second/third均passed、second_retired_registered=false、user_source_still_exists=false。这些是第十三轮修复前的证据；不能把第十二轮局部验收推广为整个目标完成。

## 第十三轮：活动输出归属与失败构建恢复

规范与计划：[输出活动归属、退役与失败构建恢复](../superpowers/specs/2026-10-22-output-provenance-recovery.md)、[实施计划](../superpowers/plans/2026-10-22-output-provenance-recovery.md)。上述两项先写入原生回归，首轮4项出现1个断言失败、3个预检错误，之后实现独立的活动归属；正常退出非零的构建可以合法重试，失败记录仍不能使Task或Goal完成，只有后续成功且当前证据匹配才可交付。

项目私有的 `output-authority-v1/` 采用完整不可变路径清单、小型原子head/intent和原生非阻塞文件锁。路径片段最多1MiB、完整成员最多64MiB，临时SQLite负责成员查询；元数据读写共同限于256KiB，合法深层中文项目路径已原生重启验证。每个执行的准入绑定项目、run、步骤、配置、Task版本、会话、完整作用范围和源输入版本；准入本身不提供权限。完整命令观察后才发布完成回执和新活动成员，实际范围作替换并撤销缺失成员；未执行步骤与未知兄弟/后代保持输入。已完成归属先于JSONL/Task/Goal通知发布，后续通知失败不会遗失它。

真实子进程在完整intent后、head发布前 `_exit(73)`，重启恢复已完成归属；在命令返回后、完整观察前 `_exit(74)`，新文件保持未知且拒绝自动接管。重复取消会等待持有者完成实际发布，verification仍取消且不能完成Goal；取消/超时进程未完成观察时不建立新归属。旧完成结果不能覆盖新head，同一执行的重复完成幂等。缺失head、坏活动片段、丢失原生完成key及容量耗尽均保留原状态并拒绝验证。

旧记录只在首次初始化迁移：同一会话依持久流顺序撤销旧权限，坏/失败的旧证明不能重新授予权限；完整健康后续证明可恢复独立成员。跨会话重叠范围取共同证明，原生测试覆盖两种会话插入顺序，不能以墙钟猜测。现代执行只读取完整活动快照，不反复扫描所有历史；历史损坏在实际读取/迁移时诊断，警告至多8条。这两项存储/历史读取Ruling经独立复核确认合理。

独立复核确认1项Important：仅检查started会把真实超时产生的部分文件也seal。先原生RED（1秒超时后head错误升至1），再限制超时结果不登记新生成；重配删除命令不能接管该文件。作者另复现808字节中文项目根被1KiB转义JSON读取上限拒绝，补入原生RED/GREEN及元数据发布容量保护。另一项实际发现是单步骤执行后扫描全部配置范围会接管未执行步骤中的新输入；在源码之外使用第十二轮发行wheel复现旧实现删除输入并报告passed，当前按实际步骤隔离。存储范围替换还补修了释放旧成员容量及缺失完成key的严格校验。

### 第十四轮起点：长期存储与失败清单碎片

在隔离临时项目实际执行30次1000文件构建，全部passed；第1/10/30次完整产物私有存储分别为98850/989229/2967849字节，归属存储20739/34484/65044字节。完整历史产物证明随内容变化增长，活动路径片段会复用；这项测量本身不表示已完成记录可以删除。日志 `/tmp/ignovate-output-retention-probe.log`，临时数据在测量后移除。

另以真实ManifestWriter和原生原子文件写入缩小容量上限至1.25MiB，5次各自因容量失败的观察未发布任何manifest，却各遗留1048575字节不可达part，累计5242875字节。日志 `/tmp/ignovate-orphan-artifact-probe.log`；这是第十四轮修复前的失败碎片证据。需要区分在途/已提交/历史引用，不能以删除当前、Task/Goal或历史有效证明来制造容量成功。当前机器无docker/colima/limactl，尚未取得Linux或Windows原生目录证据；可继续在本机推进存储与完整交付链路，不构成整体目标阻塞。

## 第十四轮：完整产物发布、孤片回收及目录身份

规范与计划：[产物发布与长期存储](../superpowers/specs/2026-10-22-artifact-publication-recovery.md)、[实施计划](../superpowers/plans/2026-10-22-artifact-publication-recovery.md)。真实命令的容量失败、头发布失败及源末片损坏的复制先写成3项原生RED；初次夹具的macOS /var路径和过早触发成员索引上限已调整，最终3项均因真实孤片而断言失败。实现后，观察期分片暂存于由with持有的TemporaryFile，完整核对后才进入长期目录。最多64MiB正文、1MiB单片和256KiB头；复制在完整源证明核对之后执行相同发布事务。异常、重复取消均先加入owned worker并关闭spool。

固定私有边界用原生发布锁及有界intent串行提交，完整头最后落盘。下一持有者恢复中断意图，头已提交则保留完整结果；未提交部分只在完整历史头引用图证明无引用时回收。首次升级检查旧孤片，正常现代发布不重扫全部历史。坏头、未知条目、容量或取消使引用图不完整时保留无法证明的内容，诊断至多8条并持久保存，不阻止完整健康新证明发布；代价是这些保留片段仍占空间，需修复历史图后再维护。完整历史头、Task/Goal、事件和费用不删除。

原生实际进程在首片发布后硬退出，恢复移除孤片且不重放命令；完整头发布后硬退出，恢复保留全部片段。另覆盖旧头引用的缺失片段被新事务补回后失败、真实跨进程双向复制、取消等待原生锁、源末片损坏、坏intent不重置及链接外部数据保护。两项作者边界也有RED/GREEN：完整头提交后intent清理失败不抹去完成结果；回收被坏历史图阻断时诊断不会丢失。

独立复核确认1项Important：GC的scandir使用固定fd，读写/删除却重新打开路径。真实rename替换后，旧孤片仍在，而替换目录中完整头引用的同名part被删。两项实际rename回归先RED：第二次扫描前替换目录、发布首片后替换目录；随后集中ArtifactDirectory owner使POSIX读、原子写、删除均使用同一dir_fd，结束前核对路径身份，发现替换即拒绝成功。Windows沿用全部祖先禁止写入/删除共享的原生pin，未取得本轮原生Windows证据。最后相关74项通过（29.577秒）；连续重复两项替换8次通过，原生FD由4回4。

同一5次容量失败探针修复后长期目录0文件、0字节，日志 `/tmp/ignovate-artifact-storage-repeat-green.log`。首次完整1997项/45跳过及实际安装306项/148文件均通过，但早于独立复核修复；最终已按目录owner修复后的代码重跑完整1999项、实际安装308项，结果详见本轮验收记录。有效历史未被清除；长期有效历史容量、跨平台原生和更广复杂交付仍属于整体active目标。

## 后续审查范围

费用、完成检查、子任务恢复及完整历史归档已覆盖明确复现的故障，不能据此推断全部长任务链路已经完成审查。仍需推进真实工作区链接与输入范围、存储长期容量、进程清理及更广的端到端交付场景。

MCP 排队、取消、超时和重连已在第九轮修复，终端 Ctrl+C 回执在第十轮修复；Factory 初始化/关闭失败、多个实例及各入口归属在第十一轮补修。最初 initialization_error=true / sqlite_still_open=true / context_still_owned=true 的原生 inline 失败路径已变为连接关闭的回归。真实 Windows 信号、更广 SIGTERM/硬退出交错、长期故障注入和全面交付链路仍需证据，不能从局部回归推广整体完成。

| 链路 | 需要证明的行为 | 当前状态 |
| --- | --- | --- |
| 目标与费用账本 | 损坏不清账，重复结算不收费；进程硬退出后的在途费用可核对 | 已修复损坏保护、持久预留/即时费用、原生硬退出恢复与多会话边界；更广持久化故障交错待续 |
| 子任务续作 | 父任务/输入版本绑定，取消与崩溃后有可核对状态 | 已修复租约恢复、原生读取进度、历史查询/续作链、队列暂停及正常进度兼容；更广存储故障交错待续 |
| 会话协调 | 派发身份幂等，审批后重新核对控制权，未知操作不重放，依赖有真实证据 | 已修复存储、空转计数、扫描后版本重核及关闭流/回执异常结算；更广的恢复交错及集成交付待审 |
| 任务与交付 | 所有用户要求保持有效，验收绑定当前输入，局部验证不能代表整体交付 | 已修复完成扫描持锁、验证写回反向锁、暂停/新验证覆盖及派发完成竞态；全面审查待续 |
| 上下文与归档 | 多次压缩仍能恢复要求、工具结果和版本；归档失败不静默丢数据 | 已修复完整分片归档、全类型及继承引用完整性、元数据/参数分页和异步取消；更广长期容量及端到端待续 |
| 大项目验证摘要 | 依赖与构建产物增多时仍能取得完整、可解释的输入身份 | v2目录/内部链接、完整产物清单、当前证据、审批/目录ABA、失败构建恢复及活动归属退役已补修；真实工作区旧外部打包链接及跨平台目录见证待续 |
| 进程与取消 | 超时/反复取消后回收进程、管道、预算与锁，未知副作用可追溯 | 已修复队列停止/关闭所有权、Future 交接与错误保留、自依赖拒绝及 MCP 请求/清理隔离；真实进程和 Graph/SQLite 关闭顺序已验证，更广故障交错待续 |
| GUI/CLI/headless | 队列中断、重启、显式恢复与相同目标的状态一致 | 已修复 GUI/导出扫描阻塞、旧观察绑定、HTTP 超时孤儿请求及终端停止屏障/完成回执；Factory/SDK/GUI loop 生命周期及启动/切换/退出失败归属已补修；更广端到端待续 |
| 工具、MCP、配置及发布 | 边界和并发变更有效，超时与重连可控，发行内容包含运行所需资源 | 已修复 MCP 排队撤回、绝对 admission、清理所有权与重连/退出竞态；更广配置及发布审查待续 |

大项目边界：首次测量当前工作区按 `FileSession._is_protected` 过滤后有106175个文件、11710464935字节，主要来自artifacts历史交付快照和dist发行候选。旧版摘要先遇容量上限，第二轮当时拒绝所有链接，第十二轮已覆盖内部链接并定位到旧打包产物的外部断链。因此当前真实工作区整体身份仍为unknown；后续需处理实际外部链接和产物生命周期，保留完整覆盖证明。


## 第十五轮：压缩与重启后的完整任务续作

真实长任务探针执行7轮、L2由37901降至2870估计tokens，关闭并重开Factory/SQLite后Task、Goal与费用仍保留；修复请求却因完整有效原要求重新进入请求投影而超过固定12000字符（40396），已知足够的模型窗口无法继续。原文与完整归档未丢失。证据 `/tmp/ignovate-long-composite-probe.log`。

请求中间件默认12000字符改为目标，只有已知窗口可完整重渲染到1000000字符绝对上限；显式字符上限与未知模型仍严格。有效原文不分页省略，不写入checkpoint；阶段规则、脱敏、实际完整输入、输出reserve、安全余量与累计token预算继续核对。发现配置窗口在准备与最终请求间撤回的原生反例，增加实际请求消息检查，在provider之前停止。

新增8项原生测试。16轮真实AgentService/Graph场景经过两次L2及两次Factory/SQLite重开；原任务、约束、scope、验收、归档尾部与累计费用均保留。失败构建产生已知部分输出，实际读取后修复，通过原生命令检查全部6个阶段文件并完成Task/Goal；外部修改最终CSV使delivery变为unverified。所有执行中实际模型请求均含完整有效要求。该组合对SHA核对的第十四轮wheel仍因40180>12000失败，当前实现通过；相关111项全部通过13.125秒，日志 `/tmp/ignovate-long-continuation-broad-final.log`。最终完整Python2007项/45平台跳过、JS17及diff通过；实际安装405项和全部149个发行Python来源核对通过，详见上述验收记录。

独立最终复核未发现Critical/Important。原生复核发现1项Minor：request-only历史压缩重建投影后仍报告初始info，实际40623字符会显示1599、expanded=false，预算仍按最终实际请求核对。按本轮executing-plans流程记录待下一阶段处理，未进入Minor修复pass。裁决：保持完整要求而允许已知窗口请求增长（成本是更多输入token）；使用第十四轮attested wheel界定本轮差异（不重审前十四轮）；macOS离线不能推导真实提供商长期或Linux/Windows原生保证。原goal保持active。


## 第十六轮：实际投影诊断与自动目标驱动续作

上一轮Minor由原生Graph再次RED固定：实际投影40623字符，context_request却记录1599。历史请求视图现在把实际采用请求和Task诊断一起返回；候选无收益或原始归档失败保留旧请求的诊断，不额外读取或重新渲染Task快照。新增3项原生诊断测试覆盖采用、不采用和失败；request-only投影、全部有效原文与完整预算门不变。

新增共享ui.flows.stream_goal自动9轮/19模型调用场景：7次真实配置构建失败，原生读到新的输入事实，压缩后暂停并关闭Factory/SQLite，重开后显式恢复，原生先读后写修复source，自动核心验证通过，再完成Task/Goal。每次实际请求保留完整有效原文及约束；费用按19次usage、2090实际tokens、9个唯一settled round核对，重启前后不重置，预留归零；外部修改CSV后交付unverified。两个初始夹具错误已纠正：Goal只保存成功proof，失败验证应核对真实session事件与Task失败证据；未放宽生产门。

当前完整Python2011项（45项平台跳过）其余通过271.591秒，JS17和diff通过；实际外部安装409项通过84.051秒，27个关键模块来自安装目录，全部149个发行Python文件与源码一致。wheel SHA-256 `520ef38bf21ea1bbe41752206d18818db2b05abe6058e6092ff81266796734e1`，CLI help/JSONdoctor/依赖smoke通过。日志 `/tmp/ignovate-goal-driver-final-full-python.log`、`/tmp/ignovate-goal-driver-final-installed-native.log`。第一独立审查代理gpt-6-astra因账户用量限制中止，没有审查结论；fresh gpt-6.1-sol完成首次实际独立审查，0Critical/0Important/0Minor，并自行从attested wheel重建本轮差异一致。没有复核后修复pass，当前代码与上述全量和安装证据相同。

### 下一项原生发现：历史归档失败孤片

真实HistoryArchive.save连续5次最后header发布失败后，已有1份完整归档仍可读，但遗留15个不可达histpart，10489105字节。日志 `/tmp/ignovate-history-orphan-publication-probe.log`。第十四轮只修复verification artifact存储，不能推导history archive没有相同问题；后续修复必须保留所有完整历史和并发在途对象。未清用户数据或更改归档生产代码，整体goal继续active。

第十六轮裁决完整记录见对应实施计划：传递同一快照的请求/metadata（三元组内部接口改变）、费用仅容许1e-12美元浮点舍入而精确核对调用/tokens/轮数、只对第十五轮attested wheel后的差异独立复核、astra失败后用fresh6.1-sol完成首次实际审查、历史孤片另立并发安全存储修复且不宣称整体目标完成。无deferred minor，原goal active；保留分支/工作树、不提交或推送。


## 第十七轮：历史归档发布恢复与共享锁首次创建

真实5次最后头发布失败曾留下15个histpart/10489105字节。现在每会话目录采用原生锁和有界发布意图，完整头作为提交点；普通失败回收已证明无引用的新分片，硬退出/取消后下一owner恢复。保留所有完整v1/v2历史头、原文、reference、分页和脱敏格式，不删除Task/Goal/费用或checkpoint。目录FD贯穿锁、读写、完整图和unlink，末尾重核目录身份；rename替换不操作替换目录，链接不被跟随。

回收先核对全部v1正文身份及v2完整分片/正文身份。作者额外RED发现结构合法而正文被篡改的根原先仍能授权清理，现统一使用HistoryArchive的完整验证。图核验头+正文最多512MiB，临时路径64MiB/SQLite512MiB、120秒，坏图或超限保留候选并提供最多8条警告；健康现代保存不逐次全扫。intent损坏不重置，维护警告进入真实context_request。共享缺失分片由失败的新发布恢复后仍被旧完整头引用时继续保留。

综合原生并发回归还复现首次锁文件O_CREAT竞态：有效仍链接目录中并发打开会返回ENOENT，最小150×4打开在有/无O_NONBLOCK时分别266/239次失败。独占创建、已存在时无创建打开150×4无失败；共享file_locks和历史锁均使用该路径。独立原生锁测试先RED后修复。40轮实际并发归档（每轮8writer）及40次完整尾部读取通过，FD4→4。

新增19项原生回归，含分片/头硬退出、多进程publisher/collector、重复取消锁等待、目录rename、共享分片、坏intent/图/容量和原生Graph警告。旧失败用例由要求保留孤片改为原消息保持、孤片清除且可完整重试。一次fresh gpt-6-astra复核0Critical/1Important/0Minor，确认持续journal清理失败导致同一已提交记录重试错误；作者一轮native RED/GREEN补修，仅同一已验证提交身份可带警告返回，不同发布仍拒绝且原journal不变。最终相关87项通过16.416秒，未进行第二次复核。

最终完整Python2030项，45项平台跳过，其余通过277.807秒；JS17、diff通过。实际外部pip --target安装467项通过90.777秒；31个关键模块来自安装目录，150个发行Python与源码字节一致；CLI help/JSONdoctor/依赖smoke通过。wheel SHA-256 `f680db68740a5a133433c89d5f9b10eb10a481c6db486e631a4976e86ddd9a33`。日志 `/tmp/ignovate-history-storage-accepted-full-python.log`、`/tmp/ignovate-history-storage-accepted-installed-native.log`、`/tmp/ignovate-history-storage-accepted-wheel-smoke.log`。最初2029/466证据早于Important补修，不作为最终验收。原5失败探针现在0不可达分片/0字节且旧完整归档仍可读。

裁决完整记录见本轮实施计划：history独立协议复用现有目录/index原语（两个journal schema维护）；同版并发和升级前停旧写入（无新旧协议热升级保证）；修复原生证实的共享POSIX锁竞态（所有消费者需全量回归）；完整图核验有资源边界（超限保留碎片并警告）；只独立复核精确本轮增量（不重审前16轮）；macOS离线不推导Windows原生/真实API长期保证。无deferred minor。保留原分支与用户IDE/CSS、未提交/推送或真实模型API，原goal继续active。

## 第十八轮：临时磁盘索引的硬退出回收

原生输入inventory及artifact index各写12000条有效多级路径后os._exit81，旧实现分别遗留33652736B和25432064B命名SQLite文件。现共用SQLite私有临时磁盘库，先FILE再ATTACH scratch，正常/异常/取消关闭连接，硬退出由默认VFS/系统句柄回收，不依赖下一次启动扫目录。活跃大库原生见到超过2MiB的无链接0600磁盘文件；同一硬退出用例现无命名数据库遗留，不据内部索引行数声称项目扫描性能。

scratch保持4096字节页、131072页/512MiB上限、2MiB缓存、原有deadline和取消检查。空main限制1页；作者原生RED发现旧输出迁移另建两张辅助表会绕开scratch，现统一迁入同一受限库。输入守卫同时核对Python及SQLite默认临时根，不改环境/CWD，也不读写不安全全局temp_store_directory。强制内存或编译选项不可核实则拒绝。默认内置VFS/稳定进程配置是明确边界；本机TEMP_STORE=1有原生物理证据，2/3实际编译构建未运行，force3拒绝为受控接口测试，Windows专用用例在macOS跳过。

新增15项回归，相关139项48.172秒通过（1项Windows跳过）。一次fresh gpt-6-astra精确复核四个生产文件增量，0Critical/0Important/0Minor，独立15项中14通过、1Windows跳过，另核验33.65MB临时目录权限边界；未进行复核后修复或第二次复核。完整Python2045项274.691秒，46项平台跳过其余通过；JS17、diff通过；实际外部pip --target 482项88.156秒，1Windows跳过其余通过；32关键模块来自安装目录，151发行Python字节与源码一致；CLI help/JSONdoctor/依赖smoke通过。日志`/tmp/ignovate-transient-index-final-full-python.log`、`/tmp/ignovate-transient-index-final-installed-native.log`、`/tmp/ignovate-transient-index-final-wheel-smoke.log`。wheel SHA-256 `d1c7481e83c5796c18ca01d52bacee29512cf11ca46076422ff62e465bb91973`。

全部裁决及代价见本轮实施计划：SQLite/OS私有临时生命周期（不扫删旧未知/tmp文件，依赖默认VFS）；scratch统一预算+main容量拦截（内部辅助DDL须迁移，四个生产文件）；仅默认VFS/稳定配置且不改全局临时目录（第三方/全局变更不保证）；内存强制构建拒绝（自定义构建可明确失败）；精确本轮增量复核（不代表全项目复核完成）；macOS离线证据（无Windows原生/真实模型长期保证）。无deferred minor；保留用户IDE/CSS、分支，无提交/推送或真实模型API；原goal继续active。

### 下一项原生发现：控制进程硬退出后的命令所有权

实际execute_process启动有限时命令，控制进程os._exit81时完成标记尚不存在，之后子进程仍写入标记。日志`/tmp/ignovate-process-controller-hard-exit-probe.log`：controller_exit81、command_started=true、finished_at_controller_exit=false、command_wrote_after_controller_exit=true。探针专属临时目录且命令自行结束，未删除用户数据/终止未知进程。正常取消和超时清理不能代表控制进程崩溃后的原生清理；后续需设计并修复，不能将本轮临时索引硬退出保证推广到命令进程树。真实工作区历史外部链接、长期完整历史容量与跨平台/更广交付仍在整体审查范围。

## 第十九轮：控制端崩溃后的受管命令所有权

实际OwnedProcess、capture_bytes和execute_process三个入口在控制进程os._exit81后均继续写入，原生3项RED。现由独立SID/PGID的短驻Python监护者启动真实命令；专用CLOEXEC存活管道只由控制端持写端，未放行前控制端退出不运行用户命令，启动后EOF由监护者终止自身所属组。普通同组子孙、shell/argv/stdin/Unicode和正负退出码均有原生证据；实际SIGKILL控制端也有回归。监护启动器-I/-S绝对路径，不从项目cwd加载模块；真实命令不继承私有控制与状态FD。正常结束也收回同组后台子孙；没有历史PID kill或重启扫描清理。

初始同会话组原型在控制终端读取时暂停整组，原生5.601秒RED后拒绝，保留原有独立会话。监护者是group leader，私有状态管道在原生终态后映射用户退出码，内部process.pid指owner；缺少stopping回执则清理无法核实、重复close不假成功。回执是清理意图而非对独立外力在回执与系统kill之间破坏的原子保证。其他作者原生RED还覆盖Python启动器SIGPIPE变化、reader线程构造失败漏清理、原生句柄信号误打监护者及取消+exec失败吞取消，均已补修。

验证、普通工具、钩子和异步editor经OwnedProcess边界；execute_process在所有平台共用PipeCapture，启动后台化并join取消；同步终端editor原路径保留。Windows仍走已有Job，不以macOS模拟推断本轮Windows原生通过。独立监护进程本身被外力强杀/暂停、主动setsid或变组daemon不由本轮保证，错误不支持正面交付结论，仍在原始goal范围。

一次fresh gpt-6-astra精确复核六生产文件，0Critical/2Important/0Minor：公开SIGSTOP广播会暂停监护者并阻塞EOF；asyncio.run整体关闭会直接取消创建Task，丢失之后线程创建的资源。作者一次3项原生RED→GREEN补修，STOP只暂停真实用户进程（Popen语义），监护者持续可运行；后台创建用executor Future和显式copy_context，取消仍等创建并关闭结果。相关110项48.376秒通过，3平台跳过；无第二次复核，无deferred minor。

最终完整Python2069项293.348秒，47平台跳过其余通过；JS17、diff通过。新增24项含1Windows原生skip。实际外部pip --target安装584项132.072秒，4平台跳过其余通过，39关键模块来自安装目录，153发行Python与源码字节一致；安装子进程也按实际导入runtime路径启动。CLI help/JSONdoctor/依赖smoke通过。最终wheel SHA-256 `4cd2394a7d9dc3e8838c6124273d244b659c71f298b1d288f18471e78e7ab88c`。最终日志 `/tmp/ignovate-process-crash-accepted-full-python.log`、`/tmp/ignovate-process-crash-accepted-installed-native.log`、`/tmp/ignovate-process-crash-accepted-wheel-smoke.log`；2065/2066和安装581早于Important补修，不作为最终验收。原生40个空命令测量总耗时0.763689→1.992694秒，约增30.7ms/命令，双方父FD4→4，仅本机测量而非性能保证。

全部11项裁决与代价按顺序保存在本轮计划：同会话原型被终端证据否决；不用Linux-only/历史PID方案（增加监护启动代价）；普通同组单控制端保证的边界（整体继续）；独立SID+私有退出码（内部owner PID语义）；统一六文件及读取线程（关联回归成本）；私有信号/状态IPC（维护协议）；构造失败与取消错误cause（取消优先展示）；终止回执+原生终态（独立多故障仍未知）；公开STOP只暂停用户进程（非整树冻结）；Future手动线程上下文（取消等待创建）；精确增量复核不代表全项目/Windows/真实模型长期保证。保留原分支/IDE/CSS，无提交推送/真实API；原goal继续active。

### 下一项原生发现：本地MCP服务后代的控制端崩溃遗留

真实MCPManager通过SDK stdio完成initialize和工具注册后，控制端os._exit81。普通有限时服务后代在退出时尚未写入，之后继续写入。日志 `/tmp/ignovate-mcp-controller-hard-exit-probe.log`：native_mcp_connected=true、descendant_wrote_at_controller_exit=false、descendant_wrote_after_controller_exit=true。探针只用专属临时目录和有限时子进程，均已结束，未调用真实模型/远端服务或操作未知进程。本轮OwnedProcess不覆盖SDK stdio_client；下一轮需修复该入口并保留既有SDK协议、并发取消与连接隔离。更广全局worker关闭、实际工作区外部历史链接、完整历史容量和跨平台仍待推进，不能宣称全项目目标完成。


## 第二十轮：本地 MCP 控制端崩溃及监护状态传播

真实MCPManager/SDK完成initialize和工具注册，服务工具启动普通有限时后代后，host os._exit81、host SIGKILL、SDK只kill直接进程三项原先均遗留晚写入，native RED3项5.399秒。本轮仅POSIX stdio包装为-I/-S绝对受信outer A；A先核对实际父子关系/session，再在受信coreguard加载或创建阻塞之前启动parent-watch。A持phase19 inner B的私有lifeline，B/C另有SID；host死亡由A观察，SDK只kill A也由OS关闭lifeline，B收回普通同组C及后代。SDK parser/AnyIO owner/环境过滤不变，不代理协议字节，不signal历史父PID。Windows/HTTP原路径保持。

首轮修复暴露真实SDK兼容问题：A/B持有多余stdin，服务关闭stdin后SDK看不到EPIPE。以MCP opt-in在真实C启动后释放A/B不用的0/1/2；普通guard默认不变。又以原生旧wire RED确认不能增加必选argv字段，保留旧布局，以stdio-v1:在原ignored-policy字段标记MCP资源策略；旧helper遇新tag失败关闭。共五生产文件：MCP client、新core/_mcp_stdio_owner.py、core guard/child及新mcp/stdio_owner.py。

一次fresh Astra原始四文件增量复核0C/1I/0M：独立kill B使A正确退出127，但C持有协议管道，SDK不观察A，manager仍显示connected且新echo成功。作者真实有限时C自注入RED1.619秒，增加host/A独立认证回环健康通道后GREEN0.483秒：先随机32字节token握手再放行B/SDK初始化，A只有核实guard.close后发清理回执，无回执EOF令host撤工具、封接纳并报告清理无法核实，同SDK owner只取消一次。通道仅127.0.0.1、C不继承、无磁盘socket残留；loopback不可用时不运行用户程序。原生测试补初始化前A死亡、坏token、权限拒绝、正常/未知回执、多连接、取消、argv/env/cwd、stdin/stdout关闭、FD重连基线和实际C无健康socket；作者一次fix pass，无二次review，无deferred minor。

最终完整Python2094项320.812秒，48平台跳过，其余通过；JS17/diff通过。相关186项57.308秒、2跳过；本轮新增25项含1 Windows native skip。实际外部pip --target安装639项170.684秒、5跳过，41关键模块来自安装目录，155发行Python与源码字节一致；CLI help/JSONdoctor/依赖smoke通过。最终wheel SHA256 `e5ee993852688f3d70a37ee576e01d4520796eead3925fe6dde68ec948616fd1`，最终日志 `/tmp/ignovate-mcp-crash-accepted-full-python.log`、`/tmp/ignovate-mcp-crash-accepted-installed-native.log`、`/tmp/ignovate-mcp-crash-accepted-wheel-smoke.log`。修复前2087/632及SHA03e6ac...不作最终验收。最终本机六次预热连接 .1557–.1805→.1838–.1875秒，两个监护者合计RSS37,920–38,128 KiB（约37MiB/连接），20 RPC .0110–.0117→.0112–.0117秒；另有宿主健康Task/通道，一次连接生命周期成本，不是性能/容量承诺。

全部12项Ruling按顺序和代价持久化在 `docs/superpowers/plans/2026-10-23-mcp-crash-ownership.md`：保留SDK协议（启动器维护）；双监护（约37MiB/连接）；POSIX livePPID而不kill历史PID（Windows未验）；MCP释放多余stdio（共享core回归）；兼容旧wire（新opt-in需要配对helper）；认证loopback健康sideband（本地IPC权限/额外Task）；独立B死亡不保证收回但停止接纳（存活效果未知）；主动脱组未包含（独立效果可能继续）；Windows不由macOS推导（待原生）；Linux也不由macOS推导（待原生）；不把六次样本当容量保证（高连接内存仍待验证）；增量复核不关闭整体goal（全局关闭/容量/实际工作区/平台矩阵仍待证据）。保留原分支/用户IDE/CSS，无提交/推送或真实API；原goal继续active。

### 下一项原生发现：全局事件循环关闭时异步 SQLite 开库丢失所有权

实际RuntimeResourceOwner/AsyncSqliteSaver/aiosqlite，在native sqlite3.connect中gate .2秒，asyncio.run返回时全局取消所有Task。单个caller取消对照正确等待并关闭；全局关闭却清除unpublished记录、worker在loop已关闭后返回并抛Event loop is closed，探针保留供核验的原生连接仍open。重复2次同现，worker_threads为空不能证明connection closed。日志 `/tmp/ignovate-factory-global-shutdown-probe.log`、`/tmp/ignovate-factory-global-shutdown-repeated-probe.log`、`/tmp/ignovate-factory-global-shutdown-control-probe.log`。探针刻意保留原生句柄等同调用方保留失败traceback的核验边界，不据此断言所有普通调用永久泄漏；有限时独立进程退出回收句柄，专用父临时目录自动清理，原首次私有残留也已精确清除。该生命周期竞态需下一轮修复；本轮产品代码未借机改动。原始全项目长任务稳定交付目标仍未证明。

### 第二十一轮：全局loop关闭下的SQLite原生所有权与失败重试

根因是asyncio.run直接cancel全部Task，shield不能保住开库返回值和收尾；关闭成功后清账若留在可取消外层，也会留下虚假未关闭记录。新增SQLite-only生命周期Task保护已接纳acquire/setup/discard/rollback/close，在受管成功路径内撤销发布字段；caller取消/超时仍传播且保留异常cause，不扩大到模型/MCP的SDK deadline。

一次独立Astra最终复核0C/2I/0M发现两项本轮要求内的继承SDK缺口：原生close失败时SDK finally丢连接指针，后续noop重试会假成功；真实OperationalError开库失败时stop future未join，worker会在loop关闭后回调。作者唯一RED/GREEN pass增加连接入口所有权适配，entry前持有原生连接，原worker先验证raw close，再SDK close并等待stop及实际线程结束；失败原连接/worker保留供显式retry。原SDK SQL/checkpoint、同loop、默认SQLite线程亲和保持，不改全局SDK/asyncio、不添第二loop或worker。aiosqlite限定>=0.22.1,<0.23，现有发行锁0.22.1保持。

新增17项原生/兼容测试，严格记录loop返回瞬间worker已停止，保留真实连接核验closed；actual worker内sqlite3.close失败在attested旧wheel上RED，原SDK确实丢指针并清账而原句柄仍open，当前backend GREEN。真实不存在父目录的OperationalError+SDK stop closure线程trace gate验证失败开库关闭顺序。有限探针均父临时目录托管，无真实API或GC替代证明。

最终证据：相关169/2skip72.053秒；全量2111/48skip326.392秒；JS17与diff通过；工作区外真实pip安装656/5skip177.779秒、43模块来源和157Python字节一致；安装CLI help/JSON doctor/依赖通过。wheel SHA256 `6b85f714b933ab73bb7f3d401950d0dbe8c182a3752667daac263833e8e4fd69`。完整日志、一次review报告、精确phase20增量及验收见 `docs/superpowers/plans/2026-10-23-sqlite-global-shutdown.md`；预复核2108/653结果不代替本次最终证据。

全部12项Ruling（含review全部8项Declined）与代价已按顺序持久化实施计划：SQLite-only拒cancel（原生I/O延迟无上界）；同loop/SDK逻辑（绕过custom task_factory）；entry前拥有连接+rawclose-first/workerjoin（SDK接口耦合、失败保活worker）；aiosqlite版本界限（升级须重验）；强杀不承诺优雅关闭（OS语义未验）；手工停/关loop不支持（须正常drain）；永久I/O卡死未承诺有界退出（外部终止）；不扩用SDK timeout（新增调用点须审）；自定义Task instrumentation不等价（普通Task保持）；3.13仅helper探针通过（完整3.12/3.13/Windows/Linux/uvloop待原生）；review只审增量且父执行全量（不等于整体完成、无二次review）；不建自动全局registry（调用方须显式aclose、接纳前无所有权）。无minor；保留原分支、用户IDE/CSS，无提交/推送或真实API。跨平台原生矩阵、完整历史容量、实际工作区旧外部失效链接及更广故障交错仍待推进，完整原goal保持active。


## 第二十二轮：模型 HTTP 原生连接所有权及并发清理

原生SDK先标closed/清pool后close失败、全局取消、同步TLS交接均可能使真实socket失去关闭路径。新增实例级HTTPX/HTTPX2 backend/stream所有权，在返回stream时持有实际socket，正常关闭及时撤销、失败保留供重试；非取消的淘汰失败封闭新连接准入。TLS升级计交接并串行化native指针更新；原生最终收尾由HTTP专用Task持有，不包SDK请求/read/write/connect/TLS超时，不扩用SQLite helper。异步资源关闭用executor Future等待同步HTTP，保留事件循环进度和原线程SQLite/预算语义。

一次最终独立评审0C/2I/1M，Astra用量中止后6.1-sol完成同一冻结增量评审，未复审作者补修。作者一次C/I RED/GREEN修复I1：private child释放与Factory退出共用每客户端的在途关闭/结果，sync复用真实executor Future，async首调用在既有owner Task执行并发布plain Future；所有并发等待者加入同一次实际关闭并观察原始失败。仅实际成功后清账，失败留待显式retry，任何单项错误不跳过其余SQLite/预算/模型清理。I2：native async准入从connect/TLS前开始粘性记录，同步close在修改资源前拒绝直接SDK异步使用、进行中交接及客户端关闭，真正未使用的异步SDK仍可同步收尾。

本轮31个真实socket/SDK子进程用例，含原始native close故障/全局取消、caller重复取消、TLS/legacy、read与handshake timeout、inflight connect、同步TLS交接及executor全局关闭、retired/privatechild、四项并发成功/共享失败重试。保留实际socket至loop退出后核验closed，FD4→4、无新增存活线程，不以SDKclosed/GC/进程退出证明关闭。最终四项并发测试还在独立临时wheel副本中被“独立关闭+幂等删除”突变全部触发，证明不是仅吞list.remove错误。

新增实际Python3.13精确发行锁环境时，离线cache缺cp313 pydantic-core2.46.5，按官方PyPI哈希锁补齐而未替用版本。首轮687项1失败：phase21 SQLite故障闸门把setup的Cursor.close当Connection.close，导致取消发生在真正native close准入前；3次原生诊断/对照确认。强化任务身份断言先RED，再把测试闸门限到实际sqlite3.Connection.close；SQLite产品代码与最终wheel不变。3.13对应原生用例3/3 GREEN后，完整三组验收重跑，不隐去首轮失败或沿用修正前证据。

| 最终验收 | 项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| 完整Python3.11 | 2142 | 48 | 360.415s |
| 工作区外实际pip安装Python3.11 | 687 | 5 | 212.321s |
| 工作区外实际锁定Python3.13 | 687 | 5 | 221.646s |

两组真实安装均在工作区外运行，44模块来源/158发行Python字节一致；Python3.13.13实际运行锁定OpenAI3.26.0、HTTPX0.28.1/HTTPcore1.0.9、HTTPX2/HTTPcore2 2.13.1、aiosqlite0.22.1、pydantic-core2.46.5、langchain-deepseek1.1.1。JS17/diff、实际安装CLIhelp/JSONdoctor/依赖smoke通过；无真实模型API。wheel SHA256 `5e71fd04fdf597b5137b9ef246cb0380c34f712692e9156e7354466ec5213ccc`。最终日志 `/tmp/ignovate-model-http-accepted-final-full-python.log`、`/tmp/ignovate-model-http-accepted-final-installed-native.log`、`/tmp/ignovate-model-http-accepted-final-matrix313-native.log`；预复核及错误SQLite闸门的中间结果不作最终验收。

Deferred minors：M1默认backend的包名前缀检测也接纳公共HTTPcore/HTTPcore2 MockBackend，因其无native socket导致正常关闭失败；实际OpenAI未装饰控制正常。默认Factory不选择该backend，当前无默认native资源丢失证据，保留Minor待后续兼容修复，本次C/I pass未修改。

全部21项裁决（含review全部8项Declined）和代价按时间顺序如下：

1. Ruling: track native streams at backend acquisition rather than close-time pool snapshots — requests and SDK close can detach before retry; cost: instance-level default pool backend adapter coupling and per-live-stream record, no globalpatch or protocol rewrite.
2. Ruling: protect only native HTTP final settlement in a dedicated Task, never SDK requests/connect/read/write or SQLite helper reuse — global cancellation otherwise loses cleanup Task; cost: native cleanup/connect join may delay cancellation with no bound for permanent I/O, custom task_factory not used for this final owner.
3. Ruling: seal native backend admission on a non-cancellation stream close failure — continuing new connections after unverified retirement can accumulate detached sockets; cost: failed backend requires explicit resource close/replacement instead of silently opening another socket, caller cancellation alone does not seal ordinary model reuse.
4. Ruling: count TLS upgrades as resource handoffs and serialize per-stream close/upgrade — sync SSL can detach old socket before returning new SSLSocket; cost: close waits original SDK handshake deadline/completion and needs stream locks/counter, no premature pointer/record removal.
5. Ruling: async resource close/release joins sync HTTP clients through the executor Future, not an asyncio creation Task — preserve loop progress and globalcancel join; cost: uses default executor capacity and may wait native sync TLS handoff, synchronous-only close stays blocking and SQLite/budget closure stays originalthread.
6. Ruling: decorate only supported default HTTPX/HTTPX2 pool backends, keep custom transports' existing close contract — product creates fresh default SDK clients; cost: custom/unknown external transports do not gain native guarantees, already-used default clients rejected because acquisition cannot be retroactively owned; SDK private-pool interface upgrades need native gates.
7. Ruling: complete the single frozen review with the available fallback after the primary reviewer terminated on usage limit — there was no final report to act on; cost: final judgment comes from 6.1-sol rather than Astra, supplemented by independent native probes, not another review of author fixes.
8. Ruling: share each client's in-flight close operation/result across private release and Factory shutdown — duplicate SDK calls and ownership removal can skip remaining cleanup; cost: per-active-client Future bookkeeping and joining that operation's original failure, failed clients retained for explicit retry, no historical completion cache.
9. Ruling: run the first async SDK close inline on the existing owner task and let other callers await its plain completion Future; reuse the actual executor Future for sync clients — a new ordinary SDK-close Task can lose startup during global cancellation; cost: first caller performs SDK close, Future bookkeeping must settle every BaseException, native-only cancellation protection remains in the HTTP owner.
10. Ruling: make native async admission sticky from before connect/TLS handoff and reject synchronous close before resource mutation, also reject active per-client close operations — exposed SDK use bypasses async_runtime flag; cost: even a failed/cancelled admitted async connection requires original-loop aclose; genuinely unused async clients retain synchronous-only cleanup.
11. Ruling: target the concurrency fixture gate at the exact same-client close entry rather than async-list entry — parent SDK close can yield before the child is reached; cost: fixture uses the per-client helper as the admission gate, while outcomes still inspect real sockets/SQLite/SDK and shared failures.
12. Final: Ruling: review declined unrelated accepted phases1–21 — keep exact phase21-wheel delta as this review boundary, include directly relevant inherited I1/I2, run author whole suite; cost: this review does not independently re-certify previous phases or close the whole goal.
13. Final: Ruling: review declined arbitrary custom transports' native guarantees — preserve the specified SDK close fallback contract, with M1 explicitly deferred as its known non-native-backend compatibility gap; cost: custom transports gain no native-handle guarantee and affected public MockBackends currently require a later compatibility fix.
14. Final: Ruling: review declined retroactive ownership of already-used external default SDK clients — reject before decoration because prior streams have no acquisition receipts; cost: embedding integrations must supply fresh clients and cannot import their old pool into this ownership guarantee.
15. Final: Ruling: review declined post-install transport/mount/backend mutation and shared HTTP client across independent Factories — do not claim support beyond product fresh-client instance construction; cost: integrations doing either must manage those resources themselves or establish a separate ownership contract.
16. Final: Ruling: review declined bounded shutdown for permanently stuck native connect/TLS/close — join admitted finite settlement rather than falsely clear resources on deadline; cost: permanently stuck native I/O can block cleanup and cancellation indefinitely.
17. Final: Ruling: review declined cleanup after the originating loop was discarded without aclose — require original-loop explicit aclose and fix I2's still-live-loop early rejection; cost: embedding callers remain responsible for draining that loop, with no new global registry or recovery loop.
18. Final: Ruling: review declined optional HTTP2, HTTPS-proxy TLS-in-TLS, other OS, Trio/custom loops and future SDK private interfaces — keep only demonstrated macOS asyncio/direct TLS/HTTP-forward-proxy claims, add the actual locked Python3.13 installed matrix without extrapolating beyond it; cost: h2 is absent and unrun protocols/platforms/loop/version combinations still need native certification.
19. Final: Ruling: review declined whole-project long-task stability/completion — accept only this phase's tested resource ownership after the single C/I fixpass, leave the original goal active; cost: long-history capacity, actual workspace historical dangling link, cross-platform and further fault interleavings still need work.
20. Ruling: fill the owned Python3.13 test environment using exact release-lock hashes from official PyPI after offline cache lacked pydantic-core2.46.5 cp313 — an alternate cached version would not test the shipped lock; cost: dependency downloads and an owned temporary environment, no model API/account interaction and no change to the production lock.
21. Ruling: restrict the SQLite shutdown fault gate to actual Connection.close and assert runtime:sqlite-close identity — a setup cursor close was falsely signalling admitted native resource closure, exposed reliably on3.13; cost: test fixture changes beyond the initial two-file model delta and repeat full/native installed matrices, no change to SQLite product semantics or premature-task cancellation support.

完整过程及一次review报告见本轮计划与独立review归档。原始goal继续active：跨平台/协议/事件循环矩阵、完整历史容量、实际工作区旧外部失效链接和更广故障交错仍待推进。保留codex/adaptive-task-runtime和用户IDE/CSS，无提交、推送或合并。保留本轮自建的精确锁CP313临时环境供后续矩阵，不删除用户历史构建；仅本轮scratch在归档后移除。


## 第二十三、二十四轮联合验收：GUI checkpoint 读取与 MCP 诊断

第23轮由实际Python3.13全量的数据库ResourceWarning追到GUI空会话检查：原with sqlite3.connect只结束事务，不关闭连接。真实12次读取保留原生对象核验，FD10→22且12连接仍可查询；closing后12连接均关闭、FD10→10，查询失败与BaseException均收尾，writer测试bootstrap保留事务提交。一次freshAstra0C/0I/0M，并独立14项及4项原生prune探针。作者核验review列出的未认证filesystem边界时发现dangling/cyclic checkpoint link实际导致branch被删除，重评Important后唯一RED/GREEN补修：lstat仅把真正FileNotFound视为缺库；坏链接状态无法核实时不清会话，合法链接已提交checkpoint仍保护分支，不写原链接/数据库。相关57项通过1.107s，无二次review。

第23轮完整311运行2149/48skip有1项失败，未被忽略：既有MCP内层监护者死亡测试的unknown-cleanup diagnostic偶发被SDK退出ExceptionGroup覆盖。313全量和两组安装704已通过，仍不据此标包级验收。第24轮原生追踪记录先发布“清理无法核实”再被generic覆盖；在原SDK退出后注入故障两次稳定复现。新native回归先RED，再一行fallback保留已知具体错误；接纳仍关闭，明确重连用新generation清旧错误，协议/Task/timeout不改。相关56/1skip34.204s通过。第24轮另一次freshAstra独立审这个新MCP delta，0C/0I/0M、native新用例及首次fallback/准入调用具体错误+退出故障探针通过；不是GUI作者补修复审。

### 最终联合门

| 实际命令范围 | 总项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2150 | 48 | 368.512s |
| Python3.13源码全量 | 2150 | 49 | 372.220s |
| 工作区外实际安装3.11 | 705 | 5 | 217.441s |
| 工作区外实际安装3.13 | 705 | 5 | 223.572s |

四个最终日志均无ResourceWarning；两组安装45关键模块来源、158发行Python字节与当前源码一致。JS17/diff与实际安装CLIhelp/JSONdoctor/依赖smoke通过。wheel SHA256 `c24d068c01ba9023aff237678e916c807e563247b7fefc8ea2a125ff8d2ed86a`；最终日志前缀 `/tmp/ignovate-mcp-diagnostic-final-`（full311/full313/installed311/installed313.log）。phase23中间SHA954d.../d6d...与失败全量仅保留历史证据，不能替代联合最终门。

最终源码3.11与实际安装3.13另各跑真实默认HarnessChatDeepSeek/OpenAI/HTTPX2本机SSE长目标探针：9轮/19请求、分片工具调用与reasoning/usage，7次原生构建失败后压缩、暂停、Factory/SQLite重开、恢复、读后写修复、验证并完成Task/Goal；2090tokens、费用0.0007980000000000003及9个唯一settled round连续，外部改变CSV后降为unverified，FD4→4无新增存活线程。探针 `/tmp/ignovate-native-goal-wire-discovery.py` 为丢弃式探索，最终日志 `/tmp/ignovate-native-goal-wire-final311.log` 和 `final313.log`，不冒充保留CI用例或真实云端provider认证。

### 全部裁决与代价

以下18项包含两个review全部7+5条Declined的裁决（已复现的坏链接效果被实际修复，不仅凭scope排除）：

1. Ruling: 修复只读检查的短作用域连接，复用closing而不扩建通用生命周期管理 — 原生证据指向缺少close且同类GUI读取已有实现；cost: 同步close仍在原调用线程执行，native close异常沿现有无法核实路径处理，不新增失败资源registry。
2. Ruling: 保留并核验实际连接而不依赖GC警告消失 — sqlite3事务context退出不等于句柄关闭；cost: 测试会刻意延长连接对象寿命以检查确定性关闭，FD观测限本机支持平台。
3. Ruling: 写入测试bootstrap同时保留事务context与显式连接close — closing只管理句柄而不提交INSERT，不能借资源修复改变证据；cost: 测试使用双context，生产读取仍只读、不引入提交或DDL。
4. Ruling: 将review未认证的损坏checkpoint链接重评为Important并原生修复 — dangling/cycle不是“已知无数据库”，实际prune会丢会话映射；cost: 每次检查增加lstat，无法核实的路径会保留本可为空的会话，仍不承诺对不合作外部writer的原子分类。
5. Ruling: package-wide311/313、安装、CLI、JS由作者实际门验证，独立review仅认证有界增量 — 避免重复整项目评审且不把其结论扩为release证据；cost: 包级结论必须依赖最终作者命令及输出，任何未运行项仍未证实。
6. Ruling: 不把本读取器的确定关闭扩展为所有SQLite/SDK/controller/process均无泄漏 — 此增量只更换短作用域并补未知链接保护；cost: 其他所有权路径和更广故障交错仍需独立证据，整体goal保持active。
7. Ruling: native SSE长目标探针仅作实际SDK本机协议/工具/恢复证据，不认证真实provider稳定 — 没有调用真实模型API；cost: 云端行为、延迟和长期故障仍未认证，探针保持丢弃式而不冒充CI覆盖。
8. Ruling: 保持普通closing语义，不增加本reader的native-close失败重试registry或替换close的双异常协议 — 未建立受支持native close自身失败的复现，现有同类reader也依该原语；cost: close本身失败不承诺释放，替换close与主取消同时失败可遮蔽主异常，不据此报告确定关闭。
9. Ruling: filesystem边界分开处理：已复现dangling/cycle数据风险作为Important补修；不合作writer/路径在检查后被替换的原子分类仍未认证 — project lease及原mode=ro保留但不是外部写者事务；cost: 只能声明固定坏链接安全拒绝，不能声明抵御全部外力竞态，需后续独立审计。
10. Ruling: 不在reader生命周期修复中重写branch/layout两库删除持久性协议 — 本次不删除内容证据且原序列保持；cost: 硬退出/断电跨库原子性没有新增保证，若另立要求需要故障注入和协议设计。
11. Ruling: Native证据限定本机macOS311/313，不把portable断言当作Windows/无devfd原生通过 — 实际安装和源码门会保留各自skip；cost: 其他OS的句柄/链接权限与平台路径仍需实机验收，不宣称全平台FD基线。
12. Ruling: 将全量暴露的继承MCP异常传播独立立项，不把GUI专项通过当作包级验收 — 真实unknown状态丢失会误导长任务故障恢复；cost: 新的单次MCP增量review与完整最终门，phase23保持待联合验收。
13. Ruling: 仅在通用异常处理没有已有具体错误时填充fallback — 健康通道的已知unknown优先于SDK退出的泛化异常名；cost: 不在单一error字段聚合全部次级异常，后续明确重连才创建新connection状态。
14. Ruling: 此独立review不重复GUI及其他dirty旧变更 — 两项有各自一次review，GUI作者Important补修已有native RED/GREEN；cost: 新MCP reviewer不单独认证旧行为，联合最终门仍由作者执行。
15. Ruling: package-wide验收及发行内容依作者完整311/313/安装/CLI/JS实际命令 — 一行diff的独立结论不是包级GREEN；cost: 只有最终输出确认后才联合接受，早先失败和旧hash不作为最终证据。
16. Ruling: 不把本机POSIX native回归扩为Windows/其他OS进程监护保证 — 一行fallback无新平台分支，其他监护实机未运行；cost: 新用例在非POSIX跳过，其他平台仍需原生证据。
17. Ruling: 只保证已发布具体diagnostic不被次级fallback覆盖，不改通知晚于connection.closing的既有producer策略 — 此次有原生顺序证据的是先发布再覆盖；cost: 更晚通知仍可能未发布具体unknown，不能据此宣称所有通知顺序均已解决，该边界仍在整体review范围，接纳已关闭不等于原生清理核实。
18. Ruling: 测试pre-addition副本是作者反向移除新增method的重建，不冒充wheel-attested历史 — 旧文件来自早期untracked阶段且wheel不含tests；cost: 只独立审实际新增方法和相关现行行为，不能由副本单独证明旧测试历史字节，当前product安装字节与源码仍需实际核对。

本两轮无新增deferred minor；phase22 M1公共MockBackend兼容问题仍在待办，不借本两轮C/I pass修改。未调用真实模型API、未提交/推送/合并，保留codex/adaptive-task-runtime、用户IDE/CSS和全部前序工作；无本轮scratch需要删除。原始goal继续active，后续仍需推进已列明的兼容待办、通知排序、更大历史容量、跨平台矩阵及实际工作区历史外部链接等证据；不把这些有界修复定义为完整目标已实现。


## 第二十五轮：默认模型backend识别与mixed客户端兼容

独立有界修复phase22旧M1：HTTPcore/HTTPcore2公共MockBackend与默认native backend共享包名，实际SDK请求/关闭被误接管并抛native socket无法核实。以真实默认SyncBackend/AutoBackend类身份取代package前缀；全部pool先检查资格，随后才拒绝全默认已用client，避免warm default + custom mount在fallback前被误拒绝。混合客户端完整保留SDK契约，不部分装饰；默认native所有权、deadline、关闭重试及已用纯默认拒绝保持。

新增8方法/16实际OpenAI+两家HTTPX sync/async子场景：首4方法8错误RED，warm mixed另2方法4错误RED，保留2个纯默认已用对照；相关72项35.053s GREEN含原31 native。一次fresh Astra0C/0I/1M，独立8/16及6项类身份补充探针通过；无作者C/I补修、无二次review。新Minor仅为将同module/name子类和显式AnyIO检查纳入持久回归，当前正确行为已通过补充探针，留待独立测试扩充。

| 最终实际范围 | 总项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| 3.11源码全量 | 2158 | 48 | 370.983s |
| 3.13源码全量 | 2158 | 49 | 375.507s |
| 工作区外安装3.11 | 713 | 5 | 219.880s |
| 工作区外安装3.13 | 713 | 5 | 226.090s |

四组退出0且无ResourceWarning，158Python字节/45module origins两组均核对；JS17/diff及实际安装CLIhelp/JSONdoctor/依赖通过。3.11现有OpenAI3.26.1、3.13精确发行锁3.26.0且77包依赖兼容。新wheel SHA256 `b37090a696f16a17ed3b934000acc860fdb54e6ec779ab9bc8b03cf1cd696894`，`/tmp/ignovate-model-backend-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`；日志同前缀 `full311/full313/installed311/installed313.log`。3.11源码与3.13实际安装另重跑默认HarnessChatDeepSeek本机SSE探针，9round19request/92toolresult、7次失败后压缩暂停重开恢复修复验证交付、2090tokens/$0.0007980000000000003、外部修改降unverified；FD4→4无新线程，无真实provider请求。

### 全部13项裁决和代价（含review全部9条Declined）

1. Ruling: 为已记录M1另立有界实现任务，不塞回旧C/I pass — 该兼容错误已有实际SDK复现，原goal要求继续修复；cost: 另付独立测试/review/全量验收，不改旧轮历史评审结论。

2. Ruling: 默认backend按实际类身份识别而非package/name字符串 — 公共MockBackend与native类共享package，子类或自定义实现不应被默默接管；cost: SDK私有默认类路径耦合，未来版本须原生验证，显式AnyIO/Trio等custom backend沿SDK原契约而不获得native保证。

3. Ruling: mixed mounts整客户端预检后再装饰 — 返回fallback前改写部分pool会留下未登记native owner；cost: 一处custom mount会令该客户端全部沿原SDK关闭契约，不提供混合native保护承诺。

4. 4. Ruling: 接受本轮M1为非阻塞覆盖待办，留到独立测试扩充 — 当前精确类身份行为已被review真实SDK探针验证，缺陷未复现；cost: 未来改写predicate时，保留CI尚未直接保护同module/name子类和显式AnyIO边界，本轮不借C/I pass追加Minor。

5. 5. Ruling: Declined1旧dirty生产变化与历史评审不在此次冻结增量中重新认证 — 实际基准是上轮已验收wheel，保留所有旧工作；cost: 本轮独立结论只及两文件，不能据此关闭整个goal。

6. 6. Ruling: Declined2接管后的mount/backend热变更继续不支持 — established owner的既有early return不构成动态接管协议；cost: 做此类外部变更的集成方须自行管理，新的pool不自动获得native保证。

7. 7. Ruling: Declined3跨独立Factory共享client/transport继续不承诺独立生命周期 — wrapper重用不是多租户引用计数；cost: 共享集成须提供独立所有权契约，否则不能从本轮推导隔离关闭。

8. 8. Ruling: Declined4并发request与首次接管不支持 — 产品在发请求之前构造并接管fresh client，资格检查/装饰不加通用事务锁；cost: 外部接管方必须先停止请求，竞态首次接管仍未认证。

9. 9. Ruling: Declined5任意内部monkeypatch不由精确类身份背书 — 本轮识别真实默认类，不认证被替换的方法/AutoBackend delegate；cost: 修改私有实现的调用方可能破坏native契约，须自管或另证，不把同一type当作完整实现证明。

10. 10. Ruling: Declined6自定义async SDK使用后仍须原loop调用aclose — custom fallback没有默认native owner的started信号，本轮不扩建独立活动跟踪；cost: 自定义async直接使用后同步factory.close不具默认native准入guard保证，测试实际使用aclose，不宣称该边界已解决。

11. 11. Ruling: Declined7混合/显式custom客户端保留整个SDK关闭契约 — 正常warm socket关闭已实际验证，但不借此承诺失败重试receipt；cost: SDK在故障下丢失底层句柄的更强保护不适用于fallback客户端。

12. 12. Ruling: Declined8未来SDK私有路径、Trio及其他OS须各自原生门 — 当前两家实际默认类身份已验证，路径缺失则fallback；cost: 升级可能失去native保护，未运行平台/loop不由macOS asyncio两版本外推。3.11当前OpenAI3.26.1，3.13实际精确发行锁OpenAI3.26.0，两者不混称同一锁。

13. 13. Ruling: Declined9包级/实际安装验收由作者最终门负责 — reviewer只独立认证有界增量并读取相关72项记录；cost: 必须等完整311/313、安装来源/字节、CLI/JS和命令退出后才接受，不以独立评审结论代替交付证据。

完整有界brief与最终门：`docs/reviews/2026-10-24-model-backend-compatibility.md`；原文独立review归档：`docs/reviews/2026-10-24-model-backend-compatibility-review.md`。phase22旧兼容M1关闭，不回写旧C/I pass或历史报告；本轮新M1覆盖待办仍明确保留。无真实API/提交/推送/合并，所有前序/用户IDE/CSS保留，无scratch待清。原goal继续active：晚通知、容量、跨平台/loop/协议、历史外部链接与更广故障交错尚待证据，未宣称整项目长任务稳定交付已全面认证。


## 第二十六轮：MCP关闭期间最终监护证据

真实SDK/双监护进程重现：健康watcher延后到SDK direct process退出之后，通道实际EOF无C确认，但closing状态抑制通知且aexit取消watcher，公开status.error为空。新3项用例在phase25 attested wheel工作区外副本2fail/1正常控制（.965s）；当前311 .976s/313 1.069s通过。仅改stdio_owner：记录receipt，closing仍发布unknown但不再次cancel owner，SDK退出后join既有watcher再关闭socket，无结果保守unknown。正常C主动断开不报错，已有具体error仍保留；SDK/Task/协议/timeout不扩改。

相关99项37.592s/1skip；单次fresh Astra0C/0I/1M，独立3项1.072s及5种原生错误/取消/诊断保留探针通过，FD7→7/0pending；无作者C/I补修，无复审。M1仅为新测试夹具诊断MSG_PEEK若抛异常可能不释放event、令测试等待监督超时，实际注入复现且手动释放后收尾，明确保留独立夹具修复待办，不冒充已修。当前正常原生测试未观察到自发peek异常。

| 最终实际范围 | 总项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| 3.11源码全量 | 2161 | 48 | 372.002s |
| 3.13源码全量 | 2161 | 49 | 377.526s |
| 工作区外安装3.11 | 716 | 5 | 221.793s |
| 工作区外安装3.13 | 716 | 5 | 226.902s |

四组终态0且无ResourceWarning，158发行Python字节/45module origins两组均核对；前后wheel仅stdio_owner.py生产字节不同，冻结review增量与最终一致。JS17/diff/实际安装CLIhelp/JSONdoctor/依赖通过，313发行锁77包兼容。wheel SHA256 `50704687774c9e903a64f946329be563f52b875e637c0b379f9b7c726f559845`，`/tmp/ignovate-mcp-closing-receipt-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`。四组日志同前缀 `full311/full313/installed311/installed313.log`；数目/耗时/日志哈希清单 `/tmp/ignovate-mcp-closing-receipt-gates.json`。

作者实际asyncio.run全局取消探索在311源码/313安装均owner结束、status保守unknown、FD4→4、0新线程、有限背景后代无后续写入。当前包默认HarnessChatDeepSeek本机SSE长目标9round19request/92toolresult、7次失败后压缩暂停重开恢复修复验证交付、2090tokens/$0.0007980000000000003、外部修改降unverified，FD4→4/无新线程两组通过。探索非持久CI/真实provider/无限容量承诺。

### 全部12项裁决与代价（含review全部8项Declined）

1. Ruling: 将late通知缺口作为独立有界修复，不改写phase24已结束的评审 — 原生公开状态已证实误报干净断开；cost: 新增独立回归/review/包级门。

2. Ruling: 接纳关闭与清理证据分别判断，unknown在closing期间仍发布，已closing不再次cancel owner — 停止接纳不证明原生进程清理；cost: 原本显示disconnected的部分故障关闭现在保持error，调用方须核对副作用。

3. Ruling: SDK退出后等待既有健康watcher，再释放通道；无确认或watcher取消按unknown — 不用强制取消吞掉最终证据；cost: 需等待真实SDK已关闭的本地peer完成读取，不新增虚假清理超时，未核实不报成功。

4. 4. Ruling: 接受M1为非阻塞夹具待办，后续独立修复诊断peek的finally门闩释放 — reviewer实际注入一次BlockingIOError，测试/watcher/owner待在门闩，手工释放后均结束；当前原生正常peek及生产故障/取消路径通过，不把测试注入问题重评成生产C/I；cost: 该诊断异常会令当前测试等待监督超时，记录明确未修，不塞入已冻结增量的C/I pass。证据 `/tmp/ignovate-mcp-closing-receipt-review-fixture-probe.py`及相邻log。

5. 5. Ruling: D1旧GUI及其他dirty/untracked历史工作不重复独立认证 — 本轮以phase25真实验收wheel限定新增两文件；cost: 本轮review不重新背书旧阶段，也不完成整体goal。

6. 6. Ruling: D2包级发行内容/311/313/安装/CLI/JS由作者最终实际门核验 — 不把单次局部review冒充完整交付；cost: 作者承担四组门与158字节/45来源，只有全部终态0后接受（本轮已完成）。

7. 7. Ruling: D3其他OS和未来MCP/AnyIO须原生验证 — 当前只证明macOS POSIX实际SDK组合，313由作者运行；cost: Windows/Linux/未来私有接口不由代码检查外推，仍在整体review未认证边界。

8. 8. Ruling: D4不把truthful unknown报告扩成独立内监护被杀/主动脱组/提权后代的强包含保证 — 进程组边界未改；cost: 这些独立故障下效果可能继续，须核对副作用，整体goal仍保留进一步证据需求。

9. 9. Ruling: D5保留既有同用户IPC信任边界，不在本轮改握手或添加对抗安全系统 — 正常descriptor归属已检查，未执行token窃取/保留peer/伪造的对抗审计；cost: 不认证恶意同用户进程破坏通道后的真实性/有界收尾。

10. 10. Ruling: D6未复现的grant至watcher资源耗尽交接另作startup/admission调查 — 本轮改收尾证据，不凭静态可能性改启动协议；cost: 异常blocked send后cancel且尚无watcher的细分顺序仍未认证，不能把正常立即send当所有调度证明。

11. 11. Ruling: D7不新增私有watcher的活跃期监督器 — 无公开操作只替换/取消watcher而保留owner，本轮实际保证关闭时观察缺失保守unknown；cost: 外部直接改私有Task时不承诺立即撤销活跃连接，不能将收尾fallback当实时监督。

12. 12. Ruling: D8原SDK退出前任意重复raw cancel及OS未收回direct process仍须独立故障证据 — 本轮独立cancel探针在新post-SDK join，作者globalcancel只覆盖一条实际顺序；cost: 不外推所有更早SDK清理中断或OS失败的收尾保证，仍依正常manager生命周期控制。

完整记录 `docs/reviews/2026-10-24-mcp-closing-receipt.md`，原文review `docs/reviews/2026-10-24-mcp-closing-receipt-review.md`。原goal继续active，M1夹具异常释放和phase25身份边界持久覆盖待办均保留；更大历史容量、跨平台/loop/协议、工作区历史外部链接与故障交错仍待推进。无真实API、无提交推送合并，全部旧工作/用户IDE/CSS保留，无scratch待清。


## 第二十七轮：回归夹具收尾与backend身份覆盖，及容量新发现

独立测试维护任务关闭phase25/26两个Minor：MCP诊断MSG_PEEK失败时finally始终释放调度event，实际SDK探针先RED（救援清理后报门闩卡住）、后GREEN311/313均FD7→7/owners完成，原失败仍由测试报告；新增3方法6子场景持久保护同module/name默认backend子类实际SDK请求/关闭，显式AnyIO另经真实loopback持有raw socket核验关闭。新backend测试在旧已验收phase24 wheel独立4error/2fail、当前相关14项6.332s GREEN，定向107项64.517s/1skip。生产158Python字节及完整manifest与phase26 wheel完全一致，不新建发行包。

单次fresh Astra0C/0I/1M，独立14项、故障门闩FD7→7、旧wheel负对照及字节来源全部核验，无作者补修无复审。新Minor是AnyIO测试finally中SDK close异常会跳过server清理；实际注入两个listeners/threads存活，probe救援全部关闭，明确保留独立后续维护，不等同生产回归。

| 最终实际范围 | 总项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| 3.11源码全量 | 2164 | 48 | 380.328s |
| 3.13源码全量 | 2164 | 49 | 385.744s |
| 工作区外安装3.11 | 719 | 5 | 229.207s |
| 工作区外安装3.13 | 719 | 5 | 233.319s |

四组终态0/无ResourceWarning，158字节/45module origins两组核对，JS17/diff/实际安装CLIhelp/JSONdoctor/依赖通过。复用wheel SHA256 `50704687774c9e903a64f946329be563f52b875e637c0b379f9b7c726f559845`，日志前缀 `/tmp/ignovate-test-boundaries-final-`，门与日志哈希 `/tmp/ignovate-test-boundaries-gates.json`。测试基准从旧冻结完整diff重建，不伪称wheel包含tests或以HEAD为基准。

独立丢弃式容量探索：2400组成套消息8次L2、9595证据、2397844字符索引重开后400页完整恢复，最初要求尾部不丢；31122.001s/分页4.146s/峰值319225856bytes，313安装19.137s/4.215s/343703552bytes，磁盘29631748bytes，FD4→4。这是归档/压缩/分页样本，不是2400次模型调用或无限规模保证。初始自建路径经系统/var链接被安全拒绝，修正夹具resolve而未放宽生产。

**新的实际生产RED不能被上述GREEN掩盖：**9000字符持续约束足够放入128k窗口，历史助手分析变大后却被固定6000字符摘要上限阻断L2，实际provider0调用、230159估算超过122880输入预算。311源码及313实际安装各4场景1RED/3对照GREEN：短历史可用、不含约束词可压缩、只在探针扩大摘要至20k也可调用且完整约束仍在请求。优先下一项生产修复，不能建议用户删约束或仅换大模型代替解决。详见 `docs/reviews/2026-10-24-constraint-compaction-capacity.md`，脚本 `/tmp/ignovate-history-constraints-discovery.py`，最终精确日志同phase前缀 `history-constraints311-final.log`、`history-constraints313-final.log`。

### 全部12项裁决与代价（含review全部7项考虑/排除项）

1. Ruling: 将两项已记录Minor作为独立测试维护任务 — 不借旧生产C/I pass夹带修改；cost: 新的测试差异审查和最终回归，不改旧报告结论。

2. Ruling: 诊断失败只保证释放调度门闩，不吞掉测试失败 — finally仍让实际异常进入SDK退出/测试断言；cost: 故障注入仍使原fixture报告失败，但不再用卡死代替结果。

3. Ruling: 默认子类用同module/name实际SDK回放，显式AnyIO使用真实loopback/socket — 让行为保护精确类身份而非只有MockBackend案例；cost: 新增3项维护及私有pool接口耦合，不把AnyIO的普通SDK关闭证明扩大成native receipt/retry保证。

4. Ruling: 复用已验收phase26 wheel作为发行验证对象 — 所有生产字节完全未变，重建只会引入无关元数据；cost: 明确本轮无新发行包，必须让新测试实际加载原wheel而非工作区生产模块。

5. 5. Ruling: 新M1为非阻塞测试清理待办，留待独立维护 — AnyIO finally中sdk.close异常会跳过server收尾；review实际执行SDK close后注入OSError，两listener/thread仍存活，探针救援全部关闭/join；cost: 当前该诊断故障会让测试残留本地server至解释器退出，明确未修，不夹带入本轮C/I pass，且不等同生产回归。证据 `/tmp/ignovate-test-boundaries-review-cleanup.py`及相邻log。

6. 6. Ruling: D1本轮不重审旧生产生命周期，但不以scope忽略并行发现的真实生产问题 — 仅本两test增量得到review认可；长约束压缩RED已独立建调查并优先后续修复；cost: 旧取消/retry/TLS/长任务不能因本轮测试GREEN重新认证，整个goal保持active而非宣称已稳定交付。

7. 7. Ruling: D2显式AnyIO/custom仍只承诺原SDK契约 — 正常请求/原生socket关闭不升级成默认receipt/retry保护；cost: 自定义backend故障下更强保证仍未认证，测试不暗示该结论。

8. 8. Ruling: D3其他OS/loop/未来依赖须实机门 — 独立review在macOS311，作者另有313实际门；cost: 不能外推未执行的平台、循环或私有SDK未来版本。

9. 9. Ruling: D4生产wheel+还原冻结test作为增量基准，旧dirty/前端/metadata/HEAD不重复背书 — tests不在wheel，历史来自原冻结diff且MCP另有旧hash；cost: Python字节相等不代表全部非Python工作区内容或可重复构建证明，用户IDE/CSS继续保留。

10. 10. Ruling: D5故障注入探针暂不再增加永久meta-test — 两行finally已通过真实SDK注入RED/GREEN、原三测试断言保持，探针可重跑；cost: 删除finally的未来回归不会由普通成功场景直接捕获，/tmp探针是本轮证据而非保留CI覆盖。

11. 11. Ruling: D6异步子类forwarder保留普通def返回原coroutine — 真实SDK实际await已通过，不因风格增加无意义wrapper；cost: 方法签名表面不显示async，未来协议变化须由行为回归捕获，不称其为当前兼容缺陷。

12. 12. Ruling: D7原生socket断言不是空列表或fake成功 — 实际成功keepalive连接持有非空socket列表，subclass回放刻意验证非native契约；cost: 该证明限本机正常SDK关闭，不替代fault retry证据，mock子场景不被说成native句柄验收。

完整记录 `docs/reviews/2026-10-24-test-boundaries.md`，原文review `docs/reviews/2026-10-24-test-boundaries-review.md`。原goal继续active：新长约束生产缺口优先，其后AnyIO测试故障清理Minor及其他已列平台/协议/历史边界仍待推进。无真实API/提交推送合并，保留全部旧工作和用户IDE/CSS，无scratch待清。


## 第二十八轮：长约束与历史压缩容量修复（与phase29联合验收）

修复phase27发现的实际生产RED：9000字符持久约束足够容纳128k窗口，但旧900000字符已完成分析因固定6000摘要上限不能L2，provider0/估算230159>122880。新增共享已知budget候选容量、默认/unknown显式限制保留，完整约束/archive/index保留，自动preflight/request-only/manual三个入口一致；扩展标记持久化，窗口移除在checkpoint/preflight之后仍拒绝，最终完整system/tools/framing/output reserve/校准准入不放宽。外部Factory缺少model/root回退原6000契约，旧legacy3测试原样通过。仅compact/context_runtime/agent_service三生产文件+新test10方法。

8项先RED4fail2error，修复后100项9.185s GREEN；初版完整两版各2174/1error发现旧外部Factory适配器问题，修正后311全量2174/48skip371.815s、安装两版732/5skip219.877/232.940s通过。旧313环境35 distributions依赖文件/RECORD缺失无法验收，原因未知；保留场景、核对owned PID/PPID后停止无效全量终态241，两个import失败终态1另保留。新.codex/tmp313由相同哈希锁离线重建76/5405、装app后77/5615文件全部校验，重装/最终也全完整；该证据不证明旧原因。

单次完成fresh fallback6.1-sol review 0C0I1M，首Astra用量限制无最终报告，因此不是复审。独立13测试.758s+4原生Graph/SQLite probes.324s；作者唯一C/I pass无需修改、不复审。M1明确延期：final banner/double-newline导致6000→6001（旧wheel byte-identical）、1000000→1000001，直接边界可多几十字符；不截约束且final token gate仍有效，后续精确格式容量独立修。

新313源码全量2174/49skip382.085s暴露原生GUI历史地图追问偶发false-busy，旧已验收507包受控3测试同1error/2GREEN证明早于phase28；不将这一轮刷为GREEN。6c候选wheel未单独接受，而在phase29独立修复后由最终联合e403包接受。

### 全部12项裁决与代价（含review四项Declined）

1. Ruling: 允许已知预算下的有界摘要表示扩展，而不增大真实模型输入预算 — 固定6000字符不是provider容量；cost: checkpoint摘要可变大，名义1000000字符另有M1格式误差，完整输入仍可能拒绝，不能把4字符/token换算当精确token保证。

2. Ruling: default/显式容量与unknown保持严格原契约，扩展结果增加持久标记并在窗口失去核实时拒绝 — 不能仅凭先前known配置继续推断容量；cost: 配置被移除后需恢复可核实窗口才能继续该扩展历史，旧未标记summary不由长度被追溯拒绝。

3. Ruling: 自动preflight、request-only及手动compact共用同一容量计算 — 不能只让单个自动入口恢复而手动仍卡住；cost: 三个既有生产文件和一个私有检查helper扩展，manual本身不发provider请求，后续真实调用仍重核窗口。

4. Ruling: 原始持续约束/完整archive与索引保留，不使用ellipsis或删除旧要求换取通过 — 已确认9000字符要求可容纳当前窗口；cost: 归档I/O/真正超资源或完整输入预算时仍拒绝，原始checkpoint/要求保留，不承诺无限容量。

5. Ruling: 外部Factory缺少模型/窗口元数据时保留原手动compact契约 — 完整311/313各2174项暴露同一既有test_v1_features.CompactionTests原生兼容回归，不能删除或改造旧fixture绕过；cost: 无模型元数据的适配器仍只用6000字符摘要，不获得已知窗口扩展，必须重跑最终包门。

6. Ruling: 首位Astra reviewer用量限制终止且无最终报告，改由可用6.1-sol完成同一增量的唯一最终评审 — 不是已完成评审后的复审；cost: 独立结论来自fallback模型，保留初版冻结diff和失败状态，并把随后全量发现的adapter修正纳入新的最终冻结差异。

7. Ruling: 新313环境从相同发行哈希锁离线重建并以RECORD逐文件核验，不用缺失依赖的运行作为产品验收 — 旧环境35个distribution的源码/RECORD缺失，原因未确定，不能归因产品、uv或reviewer；cost: 保留旧环境/失败日志与法证JSON、新增隔离目录与完整重跑时间。新安装76依赖5405文件、装app后77项5615文件全部存在且哈希匹配，app安装未在该新环境复现缺失；该结果不证明旧缺失原因。

8. Ruling: M1按非阻塞字符计数精度问题明确延期，不并入本轮C/I pass — renderer先按单换行计算，再加入完成banner并双换行join；公开6000→6001（旧wheel字节完全相同）、1000000→1000001，直接renderer边界还可多几十字符；cost: `summary_max_chars`及名义1M不是严格最终文本长度上限，完整最终token gate仍有效。后续独立边界修复须计算最终格式/固定标题，省可选段或拒绝L2，绝不截断完整约束；当前10项只覆盖明显超限，不声称精确边界已修。证据 `/tmp/ignovate-constraint-capacity-review-probes.py`、相邻log及原文报告。

9. Ruling: D1本增量review不认证整个goal、旧阶段或其他平台，原goal保持active — 本轮有限真实长任务/源码与安装门各自负责其实际范围；cost: 未执行的平台/loop/未来SDK/更大历史仍需原生证据，本轮通过不能替代项目整体长期稳定交付证明。

10. Ruling: D2环境缺失成因与修复验收由作者承担，reviewer只读边界保留 — reviewer明确未修改/调查旧313环境，不能以其311探针当313验收；cost: 作者须独立保留失败、法证和重建后完整终态（详第7项），新环境成功不把未知原因改写为已定位。

11. Ruling: D3采用已授权离线/有限loopback边界，继续现有带校准估算器契约 — 无真实远端模型API，实际OpenAI/HTTPX SDK仍在线缆上发送/解析但服务端usage为夹具数据；cost: 不提供远端精确token/真实服务长期稳定保证，4字符/token仅摘要候选容量，完整system/tools/framing/output reserve和校准决定最终准入。

12. Ruling: D4保留最终请求policy读取处重核的既有边界，不新增配置与网络的原子事务 — reviewer在preflight后变unknown/更小窗口/提高校准均验证provider0；cost: 最后读取完成之后到network I/O之间的并发配置变更不由本补丁原子化，不能把before-boundary故障证明外推到该窗口。


完整记录 `docs/reviews/2026-10-24-constraint-compaction-capacity.md`，原文review `docs/reviews/2026-10-24-constraint-compaction-capacity-review.md`。最终共同门见下一轮，不重复计成两套独立全量。


## 第二十九轮：GUI已完成执行到地图续作的交接

phase28最终全量中的真实新失败单独定位：run completed/actual execute Task.done()为真，但await该Task的pump尚未清current_task；map入口及下游fork各自以truthy误拒绝。新增真实Factory/Graph/SQLite受控调度3项，在507旧wheel/当前source/6c313实装均1RED/2GREEN；最初fixture gate释放晚于close造成teardown等待，核对owned PID后143停止并保留记录，随后先释放gate再nativeclose才取得真实RED。只改map guard的首次69项仍被下游fork拒绝，补齐两处后69项4.648s GREEN。未修改原失败测试的等待方式。

只改controller两处准入为Task存在且not done；map queue保护、active owner、completed/recoverable checkpoint、request稳定去重、完整fork history与真实project lease保持。单次fresh独立review0C0I0M，8项1.596s/额外3个真实lease/publicfork/current-head探针.646s通过；作者唯一C/I pass无需修改、不复审。review明确6c是已review但未独立全门接受的精确候选基准，最终联合包相对旧已接受507仅四生产文件，各有冻结增量审查。

### 全部8项裁决与代价（含review四组Declined）

1. Ruling: 本问题作为独立GUI生产增量，不能把旧问题归因phase28或宣称该全量绿色 — 旧wheel负对照相同RED；cost: 多一个单独审查和最终联合发行包门，保留phase28失败日志。

2. Ruling: 只在map→fork路径按真正Task.done准入，仍保留queue/active Task/项目lease/精确checkpoint保护 — 同步fork不会与未结束的_execute并行；cost: 其他设置/项目切换等保守busy窗口不在本轮放宽，不能将此局部修复外推为全GUI交接重构。

3. Ruling: 受控调度放大真实done-owner窗口而不stub历史/模型服务/clone或延长任意sleep — 可以确定性复现偶发原生失败；cost: test pump有小段复制，须维护与生产调度结构一致，门闩必须先于controller.close释放；绝不修改旧测试等待条件以掩盖产品误判。

4. Ruling: 最终验收使用联合新wheel和两版全量/外安装/完整字节来源 — phase28原6c wheel及其three-file+test独立结论保持，GUI增量基准明确为候选；cost: 全量与安装重跑时间，任何新失败继续调查，不仅重跑单个用例。

5. Ruling: D1其余controller项目切换/configuration/test_connection/reasoning/accept_plan保守busy边界独立延期 — 不把仅map/fork准入改动外推为所有状态转换；cost: 完成到pump清引用的短窗口仍可能要求这些控制重试，后续须分别核验旧factory清理/新项目状态/队列语义，尤其不能提前清pointer破坏project switch归属。

6. Ruling: D2模型选择/provider/plugin mutation及catalog刷新/work-control的相同保守窗口独立延期 — 这些操作有不同lease/version/资源更新契约；cost: 极短暂busy拒绝或catalog延迟仍存在，当前不认证所有GUI动作一致性，后续若共用idle判断须覆盖每个变更的安全条件。

7. Ruling: D3不为消除test pump复制而重构生产pump或加测试hook — 测试只在await实际execute结束之后延迟retirement，现有pump生产逻辑未改且有效；cost: test_gui_map_handoff.py复制的调度骨架须随未来pump维护，当前释放gate早于close，不以生产表面扩张换取一次性测试便利。

8. Ruling: D4保留phase28独立review及其M1/四项Declined，不把GUI评审冒充旧dirty/包装/源码安装验收 — 最终联合门须与当前e403 wheel一致；cost: 作者承担四组完整实际门和158字节/46来源核验，无追加review，未通过时不能发布完成结论。


### 两项修复的最终共同验收

| 最终联合实际门 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| 3.11源码全量 | 2177 | 48 | 375.875s |
| 3.13源码全量 | 2177 | 49 | 380.450s |
| 工作区外安装3.11 | 748 | 5 | 228.751s |
| 工作区外安装3.13 | 748 | 5 | 232.895s |

最终接受的是联合wheel `/tmp/ignovate-map-handoff-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`，SHA256 `e40302a5913b4d905b1ea2a8946f15bf8aca8cdc095143b53b032c93cc84a7e3`。6c容量候选wheel独立保留，曾在full313暴露既有GUI失败，不能将其改写为全门通过。联合wheel对507旧验收wheel仅四个生产文件改变；phase28的三文件+新test冻结差异、phase29 controller+newtest冻结差异各自已独立review，review后均无作者C/I代码改动。158生产Python逐字节与当前source一致，两版外安装各46个真实module origins核对；GUI相对6c只有controller改变，未重写phase28三文件或其测试。

JS17/实际安装CLIhelp/JSONdoctor/dependency check/git diff --check全部通过，四组无ResourceWarning（不称所有warning为空，313既有editor-terminal测试pty.fork会发多线程DeprecationWarning；搜索确认调用位于tests/test_editor_terminal.py，未据此声称产品deadlock）。门与所有日志哈希 `/tmp/ignovate-map-handoff-final-gates.json`，字节来源 `/tmp/ignovate-map-handoff-final-attestation.json`。外安装311实际本地SDK OpenAI3.26.1，313实际完整发行哈希锁OpenAI3.26.0，未伪称两个环境完全同依赖。

联合最终包的实际默认SDK SSE长约束2请求跨close/reopen：全文9000约束保留，旧900000分析不入请求，输入8225/8248≤122880，FD4→4/errors空。另9轮/19请求/92tool-result复杂goal，verified后外部改动变unverified、实际compacted=true、全部脚本耗尽、FD4→4/no new threads/errors。有限127.0.0.1夹具无真实远端API，不代表远端真实token质量/计费。对应final-native-constraints313与final-native-goal313日志为最终e403实装；原3118k输入证明对应未再改变的相同phase28模块字节。

旧损坏环境/失败原日志/首次fixture错误与部分修正失败均保留；新313依赖/app安装及reinstall完整性未复现缺失，最终77 distributions/5615文件RECORD/hash审计issues空（final-integrity313.json）。缺失原因仍未知，未归咎产品或工具，也未隐瞒旧运行以SIGTERM结束的241/fixture143。

本轮无真实API/提交/推送/合并；保留全部旧工作与用户IDE/CSS。两项既有Minor（phase27 AnyIO close异常可跳过fixture server清理、phase28摘要精确字符上限）仍明确延期；其他GUI控制的保守忙碌窗口、实际远端/其他OS-loop/未来SDK/更大容量及最后policy read之后配置并发边界未被本轮认证。原始goal保持active。本轮两项生产缺口完成联合验收，不将阶段完成写作整体长期稳定目标已达成。

完整GUI记录 `docs/reviews/2026-10-24-gui-map-handoff.md`，原文review `docs/reviews/2026-10-24-gui-map-handoff-review.md`。目标继续active，下一步已记录的Minor与其他控制/平台/容量边界仍保留。

最终冻结核验 `/tmp/ignovate-map-handoff-final-freeze-audit.json`：与507验收wheel完整manifest路径相同，仅四个生产Python及对应dist-info/RECORD内容改变；158生产文件与当前source相同，两份review冻结diff与当前source/tests均精确相同。非Python资源未因本轮改变。


## 第三十轮：摘要最终格式严格容量与真实逐回合容量探索

关闭phase28 M1：原renderer把未加banner的单换行prefix当容量，最终双换行文本可6000→6001/1M→1M+1。现在以完整固定格式精确计数，constraints/index不截断；必要格式不足保留原历史。可选段标题/列表/节选/遗漏通知/分隔符一起计数，只回退可选条目并重算遗漏数量。不改state/archive/gain/pin-tool边界/三入口窗口计算/最终request预算。只改compact.py一个函数和新test_compact_summary_bounds.py6方法。

311 source/e403313实际安装各6方法8个真实RED（.071/.079s）；修复后111相关5.110s GREEN。单次fresh6.1-sol xhigh只读review0C/0I/1Minor，独立6项.062s、10states/69892容量检查.931s及实际accepted public边界、19081仅提示场景、整体state/可选顺序/遗漏数量通过。作者唯一C/I pass无需代码修改，无复审。新M1是永久optional测试缺少保留/排序/准确数量断言，独立仅返回必要前缀mutation仍通过.001s；现产品通过独立探针，覆盖缺口明确延期，不夹入旧C/I pass。

### 初始全部5项裁决及代价

1. Ruling: 固定最终格式必须完整容纳，否则保留原历史 — 一字符超限也不截约束或恢复index；cost: 过去返回略超限summary的边界输入现在可能拒绝L2，完整原文与最终request gate仍保留。
2. Ruling: 可选段按标题/双换行/节选/遗漏通知整体精确计数，撤回条目时更新遗漏数量 — 不用尾部截断制造假完整，不把遗漏数量当已渲染条目；cost: 极小剩余容量可能只显示遗漏提示或不显示可选段，原始数据仍在state/archive，可检索index始终是必要格式的一部分。
3. Ruling: 保留原三入口容量/unknown和绝对1M契约，最终模型预算独立核验 — 本轮仅把名义字符上限变成最终文本严格上限；cost: 字符不是字节或远端精确token，取消旧200字符安全余量后可选文本数量略变化，gain/final完整预算仍可拒绝。
4. Ruling: 用独立最终格式和公开6000/20000/1M两侧边界作持久回归，同时真实archive恢复 — 不是只测private helper或始终拒绝；cost: fixture包含固定界面标题和143字符格式常量，未来正文格式改变时须同步维护边界期望，其他历史稳定性靠完整相关/发行门。
5. Ruling: 本轮只处理生产格式边界，不夹带AnyIO测试close异常清理或其他GUI控制更改 — 与最终可靠长任务相关的已知Minor分别留有证据；cost: phase27 AnyIO fixture close失败仍可能跳过server关闭，其他GUI短暂保守busy及平台/远端更大容量边界继续未认证，不因本小修复宣称整体完成。


### 最终逐项采纳全部16项considered和8项Declined裁决/代价

C1. Ruling: Accept the exact final-format measurement and refusal if it cannot fit — Final mandatory formatting, including banner and double joins; exact independent prefix and adjacent-fit probes; cost: Previously tolerated formatting excess now conservatively refuses L2; originals remain available.

C2. Ruling: Accept fixed-format overhead as part of the same character capacity — Empty-state/tiny capacity and Unicode; new direct exact-fit tests passed; cost: A capacity below even the fixed banner/title cannot produce a summary.

C3. Ruling: Accept the existing public defaults, integer validation, and 1M ceiling with exact output enforcement — Public default/explicit 6000, 20000, and absolute 1000000 boundaries; actual baseline comparisons; cost: Capacity remains Python character count, not bytes, graphemes, or provider tokens.

C4. Ruling: Accept refusal instead of trimming protected content — Complete constraints, first goal excerpt, and recovery index remain mandatory; renderer reads without mutating state; cost: The existing first-goal excerpt is still limited to 800 characters; a large mandatory block can prevent L2 even if optional text is empty.

C5. Ruling: Accept unchanged nondestructive L2 refusal behavior — Public L2 overflow retains original messages/content/metadata in the new tests and independent probe; cost: This is not a rollback promise for a separately successful L1/pin operation in an `all` call; that accepted behavior is unchanged.

C6. Ruling: Accept the existing request-first/reverse-other-items policy and fixed section budgets — Optional section order and excerpt budgets; independently checked across 10 states and generous output equal to baseline; cost: Fitting more optional material after removing the old 200-character reserve can change a candidate's gain and which later sections fit.

C7. Ruling: Accept conservative whole-entry retraction and recomputation — Accurate omission count after retraction, omission-only sections, and complete section omission; independent exhaustive probe passed; cost: The renderer can leave spare room or show only an omission notice instead of retrying a shorter excerpt; no maximal-fill promise is introduced.

C8. Ruling: Accept a rendering-only change with no state/metadata schema change — Optional state immutability and full saved constraints/evidence; read surrounding `_summary_state` and metadata construction; cost: Complete metadata can remain larger than rendered text; this patch supplies no new persisted-state size guarantee.

C9. Ruling: Accept unchanged complete-snapshot and discoverable-index semantics — Real archive index and original assistant analysis recovery; new real `HistoryArchive` test passed; cost: The archive must remain available for later recovery; optional visible excerpts are not substitutes for originals.

C10. Ruling: Keep fail-closed completeness checks and existing immutable snapshot publication — Archive-truncated refusal and archive publication before a possible renderer refusal; surrounding code inspected; cost: A refused compaction may already have saved recoverable snapshots/index records; this is pre-existing nondestructive I/O, not an original-history deletion.

C11. Ruling: Retain the accepted algorithms unchanged — Existing pin/tool exchange boundaries, L1 behavior, hard/soft gain checks; compact.py surrounding implementation inspected; cost: A newly longer optional candidate can still fail the unchanged soft gain gate; a hard call still uses its existing gain policy.

C12. Ruling: Retain the accepted phase28/29 contracts; exact production-byte inventory shows these files are unchanged — Unknown/known-window helper, three runtime entry points, expanded marker, complete request token gates; cost: Strict character fit is candidate construction only; actual complete-request admission may still reject it; broader runtime acceptance belongs to the final author gates.

C13. Ruling: Keep that banner mandatory in the exact calculation — Fixed completion banner still describes historical assertions as unverified; cost: Its character overhead is charged even for an otherwise empty summary.

C14. Ruling: Accept the explicit fixture as a valid regression tradeoff — Independent fixed-format expectations and the public fixture's documented 143-character maintenance constant; cost: Future intentional wording/format changes must update the fixture; dynamic generous-render extraction in other cases alone is not the independent oracle.

C15. Ruling: Record/defer Minor M1 — Permanent optional test can pass after all optional content is discarded; cost: Current correctness is independently checked; retention/order/count semantics lack the proposed focused permanent assertion.

C16. Ruling: Accept the local bounded rendering work — Extra optional-section reconstruction/retraction work; cost: Existing small per-section excerpt budgets bound the candidate entry list; no complete long-run performance certification is inferred.

D1. Ruling: 作者采纳以下独立范围裁决及代价：**D1 — Whole long-task goal, previous accepted phases, and unrelated dirty files:** this review authenticates only the wheel-to-frozen rendering delta. **作者裁决:** preserve prior acceptance evidence and keep the overall goal active. **Cost:** this result cannot certify all earlier lifecycle, history, GUI, frontend, or IDE work.

D2. Ruling: 作者采纳以下独立范围裁决及代价：**D2 — Final distribution/full-matrix acceptance:** the author owns the new wheel, full 3.11/3.13 runs, actual installed package tests, 158-file/46-origin reconciliation, CLI/JS/diff checks, and finite SDK loopbacks; duplicating them was expressly excluded. **作者裁决:** require those final actual outputs before release acceptance. **Cost:** focused review success does not mean those gates are complete, green, or reproducible.

D3. Ruling: 作者采纳以下独立范围裁决及代价：**D3 — Real remote-provider behavior and exact remote tokenization:** real model APIs/external network were prohibited. **作者裁决:** retain the calibrated complete-request estimator and the authorized offline/loopback acceptance boundary. **Cost:** no cloud model reliability, latency, token-count, or service-capacity guarantee is supplied here.

D4. Ruling: 作者采纳以下独立范围裁决及代价：**D4 — Other platforms, alternate loops, and future dependency/private SDK interfaces:** no platform branch or dependency changed in this delta and no such native matrix was run. **作者裁决:** keep those claims limited to their existing actual evidence. **Cost:** source portability and this 3.11 review do not certify unexecuted OS/loop/SDK combinations.

D5. Ruling: 作者采纳以下独立范围裁决及代价：**D5 — Atomic configuration mutation after the final policy read:** this renderer change does not alter the accepted runtime revalidation boundary or introduce an atomic config/network snapshot. **作者裁决:** retain that boundary rather than attribute a new guarantee to exact character sizing. **Cost:** concurrency after the last policy read remains outside this patch's certification.

D6. Ruling: 作者采纳以下独立范围裁决及代价：**D6 — Phase27 AnyIO fixture cleanup when `sdk.close()` itself fails:** the separately evidenced deferred Minor is in a different test increment and is unchanged. **作者裁决:** keep its explicit independent deferred disposition. **Cost:** that diagnostic failure path can still skip fixture server cleanup; this is not repaired or recertified by the summary patch.

D7. Ruling: 作者采纳以下独立范围裁决及代价：**D7 — Other GUI commands' conservative idle gap:** the previous map/fork fix and other controller actions are unchanged and not exercised by this renderer review. **作者裁决:** retain their recorded separate scope/deferment. **Cost:** those actions can still briefly return busy until owner retirement; this review supplies no broader GUI admission guarantee.

D8. Ruling: 作者采纳以下独立范围裁决及代价：**D8 — Arbitrarily large or indefinitely growing history/state/archive capacity:** probes cover the specified 1M rendered limit and bounded representative states, not unlimited persistent histories. **作者裁决:** keep the existing archive/state contracts and separate long-run capacity work. **Cost:** complete metadata/archive growth, all possible historical corruption, and indefinite resource scaling are not newly certified by a strict rendered-text limit.

### 最终门、字节与原生行为

| 最终实际范围 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| 3.11源码全量 | 2183 | 48 | 375.109s |
| 3.13源码全量 | 2183 | 49 | 379.159s |
| 工作区外安装3.11 | 754 | 5 | 227.430s |
| 工作区外安装3.13 | 754 | 5 | 231.648s |

最终wheel `/tmp/ignovate-summary-bounds-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl` SHA256 `03f19c3fc4a04a20ac05039d1d0175f5430ef241d5a3da15d3a481c343762993`。158生产Python与当前source逐字节相同、两版外安装46 module origins核验。相对e403完整manifest路径和其余所有内容相同，仅compact.py和对应dist-info/RECORD内容改变；单生产函数+新test精确匹配唯一review冻结差异，review后无生产/测试编辑。字节/最终freeze审计 `/tmp/ignovate-summary-bounds-final-attestation.json`、`final-freeze-audit.json`，所有门日志与哈希 `/tmp/ignovate-summary-bounds-final-gates.json`。此处都同前缀 `/tmp/ignovate-summary-bounds-`。

JS17/diffcheck/实际安装CLIhelp+JSONdoctor+dependency smoke通过。313最终77 distributions/5615文件RECORD/hash审计issues空，未重建/改发行依赖版本；311本地OpenAI3.26.1、313实际发行锁3.26.0，不伪称两套依赖一致。313既有editor-terminal fixture的forkpty DeprecationWarning不计为产品deadlock，也不说所有warning为空。

实际默认SDK loopback SSE：311source及313最终实装各2请求跨Factory关闭/重开，完整9000约束保留、旧900000分析不入请求，输入估算8221/8243及8225/8248≤122880，FD4→4/errors空；313最终包另9轮/19请求/92tool-result，实际compacted=true，verified后外部改动变unverified、脚本全部耗尽、FD4→4/no新threads/errors。对应final-native-constraints311/313、final-native-goal313日志。usage仍是有限fixture数据，无真实远端API/真实计费/模型推理质量声明。



### 额外原生容量发现及全部3项裁决

实际200逐回合AgentService/Graph/SQLite，200新增完整要求（加最初目标共201active）、200离线模型边界调用、7次L2/4重开，所有要求每次实际可见且Task重开逐字段相等；最大input122631≤122880。当前03f31314.731s/RSS142770176bytes/FD4→4无线程，旧e403 target31314.889s/RSS142753792bytes/FD4→4无线程，非远端推理/SDK200轮交付。父进程240s/512MiB限额，owned大临时目录均在子结束后删除，保留指标/日志。

**新的容量重点：**current/baseline主checkpoint SQLite约493MB，关闭后总约517MB、peak约522MB；旧对照只读统计1400 checkpoint rows、483143372bytes blobs/checkpoint表约485MB。不是本摘要改动新引入，也不能把这一受控样本通过当无限长任务磁盘规模保证。后续须独立原生证明无损降低重复快照存储放大的路径，覆盖sync/async/GUI/branch/history读取和旧格式恢复，不删除历史或擅改serializer。详见 `docs/reviews/2026-10-24-checkpoint-storage-capacity.md`，`/tmp/ignovate-runtime-history-scale-evidence.json`。

S1. Ruling: 200逐回合证据只补充真实持久化容量，不替代实际SDK/产物/无限稳定证明；cost: 限该受控离线样本，原goal继续active。

S2. Ruling: 对照同样约493MB的存储放大另作优先调查，不归因renderer或删checkpoint刷容量；cost: 现SQLite/serializer未改，所有历史保留，更大尺度/无损存储优化仍未认证。

S3. Ruling: 默认executor在loop退出后判断归属，Factory关闭自己资源；cost: 活跃loop默认线程可合法存在，不能关共享池制造零线程假象；保留原begin参数/活跃loop线程两次探针夹具失败，不当产品缺陷。

原始goal保持active。下一步真实持久化容量优先；AnyIO fixture故障释放与本轮M1永久覆盖、其他已记录平台/协议/远端/配置边界继续待办。生产修复及本阶段最终门完成，不将阶段通过当整个目标完成。无真实API/提交/推送合并，用户IDE/CSS/所有前序工作保留。详细brief `docs/reviews/2026-10-24-summary-rendering-bounds.md`，review原文 `docs/reviews/2026-10-24-summary-rendering-bounds-review.md`。


## Phase31: checkpoint lossless compression

Accepted baseline: phase30 wheel 03f19c3fc4a04a20ac05039d1d0175f5430ef241d5a3da15d3a481c343762993. New accepted wheel: 85fb1423e204fcc4f7fcdd80f8ced29b61d355f55a9f2732c0f21567275f19bc. All phase31 rulings and costs are recorded verbatim below; the original goal remains active. Full brief: `docs/reviews/2026-10-24-checkpoint-compression.md`.

## 初始裁决与代价

1. Ruling: 保留所有checkpoint及原serializer的完整字节，只对收益足够的新大正文压缩 — 不删除中间快照或裁剪历史换取规模；cost: 增加有界CPU/临时缓冲，非重复/高熵数据可不压缩，长期数据总量仍增长。
2. Ruling: 所有项目默认sync/async/GUI读取共用codec，显式caller serde不替换 — 防止关闭模型后的lazy GUI或旧CLI导入误判历史丢失；cost: 新压缩记录需要新reader，旧程序/默认标准SDK不可直接读，升级须停止共享DB旧进程；custom serde不自动享受压缩。
3. Ruling: 旧记录只读兼容，不做自动批量转换/VACUUM — 混合记录可恢复/分叉且旧BLOB逐字节保持；cost: 已有数据库占用不自动缩小，当前优化降低后续增长，不假称修复了所有旧空间。
4. Ruling: 压缩解码严格有界、完整校验并保守拒绝未知/坏格式 — 不从损坏记录/日志补造状态，保留原SQL/原JsonPlus信任边界；cost: 不能读取超64MiB的新压缩封包或未来未知版本；原生大记录回退保持旧契约，显式错误仍需用户修复数据/安装正确reader。
5. Ruling: 连接生命周期与失败归属不因codec改写，压缩异常在SQL发布前传播 — 原全局取消/原生close重试仍有效；cost: 内存/压缩失败仍会阻断本次保存，不吞失败继续宣称持久完成，格式优化不提供卡死IO/全局资源上限保证。
6. Ruling: 接受内部有版本存储编码的局部适配与旧reader兼容成本，写明实际升级边界 — 当前goal要求稳定复杂长任务，原生200回合证据足以支持无损降低存储放大的工作；cost: 不做伪装成标准msgpack的不可读压缩，不保证旧程序读取新封包，当前未部署或修改实际用户数据库。
7. Ruling: 以03f完整生产wheel限定变更、文档仅新增两段/章节 — tests/docs不在wheel，文档原文从已观察的改动前内容和精确移除新增块还原保存；cost: 不以HEAD或重建文档baseline给前序dirty内容背书，全部旧工作继续保留。

- [x] 原生存储放大定位与无损prototype 200回合/1400完整读取。
- [x] 持久RED、默认全读取入口与bounded codec、117项相关GREEN。
- [x] 当前精确冻结增量唯一fresh只读review、作者唯一C/I pass、全部Minor/Declined裁决。
- [x] 159生产Python/48实际来源、新包两版全量和外安装/CLI/JS、真正新实现200回合与原生SDK长任务/旧数据/分叉/取消验收。

## 唯一最终审查及作者裁决

唯一fresh reviewer `/root/checkpoint_compression_review` 完成冻结8路径的只读审查，0 Critical / 0 Important / 0 Minor。原文归档 `docs/reviews/2026-10-24-checkpoint-compression-review.md`，SHA256 `6f6bbacd9292240de231c2bb67622984762f5c29c814fea0ea2141f0984deef5`；冻结diff SHA256 `e0929e47863b17d661e5bc43b79342e6893b768398ae259718c51d0f1f011a41`。作者已阅读完整报告，核对实现/原生saver/实装来源并完成唯一C/I pass：没有C/I需要改动，不制造修复，不再review；冻结后生产、测试和两份用户文档均未改变。review不替代下述作者发行门。

审查者独立16项 .216s；另实际64MiB边界及64MiB+1 native回退、412截断/尾随拒绝、8MiB bomb声明1byte仅77543bytes额外Python decode峰值、large bytearray、legacy→async→sync混合/parent/pending/旧行逐字节相等、async checkpoint及混合pending-batch MemoryError无SQL发布。原生未适配reader能读旧行、不能读新行亦独立复现；这是接受的升级/回退代价，不记成透明兼容。以上额外内存数仅decode探针，不是全进程上限。

### 全部14项 considered 的最终 Ruling 与代价

C1. Ruling: 采纳原始序列化字节无损保存，不删除中间checkpoint或裁剪消息；cost: 历史仍增长，每个合格记录增加临时缓冲与有限压缩工作。

C2. Ruling: 采纳精确16KiB阈值、至少1/8正文收益、原类型及>64MiB原生回退；cost: 小记录/高熵/超限记录可完全没有空间收益，收益条件只计正文。

C3. Ruling: 采纳所有默认sync/async/GUI reader共享codec及显式custom serde契约；cost: custom不自动压缩，选用原生serde的caller不能读已经压缩的行。

C4. Ruling: 采纳明确的新reader兼读旧数据、旧reader不支持新记录的升级契约；cost: 升级须停止共享DB的旧进程，rollback旧软件或未适配外部reader不能读新行，无自动反向转换。

C5. Ruling: 采纳原schema/metadata/父关系/历史数量和旧行字节不变；cost: 旧DB不会自动变小，本轮只减少后续合格新行增长。

C6. Ruling: 采纳新family版本/header/规范长度/类型/完整流/尾随的严格拒绝与GUI保守continuation；cost: 损坏和未来格式拒绝读取，不伪造状态或用日志假恢复。

C7. Ruling: 采纳压缩异常在SQL发布前传播，sync/async/pending批次不部分发布；cost: 本次保存仍会中断，async可沿用原生行为先初始化schema，不能当保存成功。

C8. Ruling: 采纳原连接锁、native worker、retryable close和取消归属不变；cost: serializer压缩同步占用调用线程/loop，不新增硬取消、IO时限或总资源保证。

C9. Ruling: 采纳readonly history仍mode=ro/is_setup=True、不新增DDL以及lazy GUI完整回答；cost: controller其他fallback保留其既有open/setup行为，本轮未重设计。

C10. Ruling: 采纳原JsonPlus构造/信任策略、不增加pickle及类型权限；cost: 恢复的原字节和旧行仍沿用安装SDK的信任边界，本轮不是敌对数据全面审计。

C11. Ruling: 采纳解压原始字节上限，不混同为对象或进程内存上限；cost: 原生对象物化可多分配内存，旧超大native记录仍沿用原权限/容量。

C12. Ruling: 采纳样本性收益和有限单记录工作，不承诺无限容量/固定延迟；cost: 高熵/小行仍增长，同步压缩可短暂占用loop，未来真实负载profile可另调优。

C13. Ruling: 采纳package finder/默认子类/原生contextmanager cls构造，并以作者最终发行门补齐；cost: focused review不能替代两版实装/全量/平台门，最终实证范围见后文。

C14. Ruling: 采纳03f wheel精确增量及两文档新增块基准，不以HEAD替代；cost: 本轮不为前序dirty内容、旧文档所有声明背书。

### 全部5项 Declined 的最终 Ruling 与代价

D1. Ruling: 作者承担并完成2199源码/796外安装两版本、CLI/JS/SDK/200回合最终门，review不重复；cost: 单独review仍不是整个goal或发布完成证据。

D2. Ruling: 延后自动转换/VACUUM/历史删除/retention/rollback transcoder，避免扩大已冻结存储改动；cost: 旧文件仍大，旧reader仍不读新行，需要合适reader。

D3. Ruling: 保持有限离线样本与继承原生行为的证据边界；cost: 不保证无限session、高熵表现、磁盘满/断电/平台crash、全局资源上限或真实远端服务行为。

D4. Ruling: 其他并发/UI/早前dirty变更/原JsonPlus信任仅检查本轮触及路径的新增回归；cost: 本review不重新认证整个项目、此前文档或全部敌对数据处理。

D5. Ruling: 延后存储认证/加密签名，zlib只做流完整性和意外损坏检查；cost: 人为改写为合法payload仍受既有本地DB/JsonPlus信任边界约束。

## 最终发行与实际长任务证据

| 实际最终范围 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2199 | 48 | 372.760s |
| Python3.13源码全量 | 2199 | 49 | 376.663s |
| 工作区外安装3.11 | 796 | 5 | 226.604s |
| 工作区外安装3.13 | 796 | 5 | 231.830s |

上述四个实际终端句柄63310/43307/15276/26839均读取terminal exit0，不因等待窗口超时重跑。JS17、真实安装CLI help/JSON doctor/dependency smoke、git diff --check均exit0。新wheel `/tmp/ignovate-checkpoint-compression-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`，SHA256 `85fb1423e204fcc4f7fcdd80f8ced29b61d355f55a9f2732c0f21567275f19bc`；159生产Python全部与source逐字节相同，两版实际48 module origins，03f到新包仅5个生产路径与对应RECORD变化。最终冻结8路径diff再次逐字节一致，review后无实现/测试/产品文档改写。完整日志/哈希索引 `/tmp/ignovate-checkpoint-compression-final-gates.json`，attestation/freeze-audit同前缀。最终313 RECORD检查77 distributions/5616files/issues空；311本地OpenAI3.26.1、313实际锁3.26.0，未改发行依赖，既有313forkpty DeprecationWarning不称产品deadlock或全warning为空；四组无ResourceWarning。

实际默认SDK的有限loopback SSE：两版各2请求跨Factory关闭/重开，完整9000字符要求保留、旧900000分析归档，input8221/8243及8225/8248≤122880，FD4→4/errors空。313新实装另9rounds/19requests/92tool-results，实际compacted=true，verified后外部改产物变unverified，fixture脚本全部耗尽，FD4→4/no新threads/errors空。不是实际远端API/真实usage或200轮SDK推理。

最终生产实现200回合没有prototype注入或全局patch：实际新wheel313安装来源的Factory→AgentService→Graph→SQLite，200个离线RecordingModel边界、200新增要求加最初目标共201active，每次实际请求完整保留；7L2，50/100/150/200处共4次关闭/重开，每次Task完整快照相等，maxinput122631≤122880。1400行全部保留并再次原生解码、checkpoint/config id逐一相符，其中1396压缩+4原生，checkpoint BLOB总23948733bytes/max34097；主DB32321536bytes，相对旧独立基准493015040bytes减少约93.44%。peak owned disk60279824bytes/关闭后56241568bytes/RSS142114816bytes，业务循环15.073s（不含后置1400行遍历审计，旧独立基准14.889s不是严格性能对照）。实际进程loop结束后FD4→4/post_loop_new_threads空；活跃loop默认executor曾存在不当泄漏。父限240s/512MiB，owned大目录子退出后删除，终端90053 exit0；日志 `/tmp/ignovate-checkpoint-compression-final-runtime-scale313.log`。高重复fixture不能外推任意真实内容压缩率、无限长度、远端模型质量或全局资源上限。

本阶段接受以上初始1–7、considered C1–C14和Declined D1–D5的全部决定及代价。存储放大已在真实默认实现中无损缓解，旧库不自动收缩、旧reader限制和规模边界保留。phase27 AnyIO fixture在sdk.close异常后的server清理与phase30可选summary永久语义覆盖Minor仍有独立记录，本轮未夹带修复；其他GUI短busy/平台/远端/配置最终read后的并发边界继续未认证。当前goal保持active，不将本阶段成功等同整个目标完成；无真实API、提交/推送/合并、部署或实际用户数据库迁移，用户IDE/CSS和全部前序工作保留。


## Phase32: review-gap test maintenance

Independent test maintenance; all production bytes remain accepted phase31 wheel85fb. Full brief: `docs/reviews/2026-10-24-review-gap-tests.md`. All initial, considered and Declined rulings/costs follow verbatim. Original goal remains active.

## 初始裁决与代价

1. Ruling: AnyIO fixture以nested finally在SDK close异常后仍关闭/join它拥有的server，原close异常继续传播；cost: 不吞诊断失败，也不新增server.shutdown自身异常/任意取消的通用保证，生产生命周期未变。
2. Ruling: 永久回归保留真实两家SDK请求和正常native close，只在最终冗余close注入失败，先查实际listener/thread再救援；cost: 依赖当前SDK client类型module及测试fixture接口，未来合法API改动须维护，不模拟真实provider故障。
3. Ruling: 永久摘要断言保护全部可选段的不同文本/顺序与真实回撤/遗漏数量，原生产算法保持；cost: literal标题/148边界与既有格式耦合，未来有意格式改动需维护；不是最大填满/所有容量的永久穷举保证。
4. Ruling: 复用phase31完整wheel并核对所有159Python及48实际来源，test差异以修改前完整文本冻结，作者承担最终2202源码/799实装两版本门；cost: 本轮无新发行包或新产品能力，test/文档不在wheel，不以focused或本阶段为整个goal完成证明。

## 检查清单

- [x] 两项旧Minor根因、真实AnyIO RED和摘要mutation有效性。
- [x] 最小fixture修改及两个新增summary方法，20项相关GREEN。
- [x] 冻结两test的唯一完成fresh只读review、作者唯一C/I pass及所有disposition/cost。
- [x] 原wheel完整字节/实际来源、两版2202源码/799外安装、CLI/JS/diff和最终环境审计。

## 唯一完成的最终审查与作者一次裁决

Primary `/root/review_gap_tests_review` 在产出报告前因usage limit中止、无报告，不记通过。随后唯一fallback `/root/review_gap_tests_fallback` 完成只读审查：0 Critical / 0 Important / 0 Minor。报告原文归档 `docs/reviews/2026-10-24-review-gap-tests-review.md`，SHA256 `a78d038786aca9dddba73fcc6583f1522bf54364b2369b637c5789e3acc67912`。review独立20项6.936s、saved old fixture替换回当前回归得到两个预期FAIL/无ERROR/救援完成、六个renderer mutant全部被断言捕获；冻结diff和两个before文本哈希及159生产字节均独立核对。

作者已完整阅读报告并核对真实fixture/SDK/renderer与最终源/包来源，完成唯一C/I pass：无C/I需修改，不制造修复、不复审；冻结后两测试与生产均未改变。作者明确维持初始R1–R4的全部决定及代价，并将报告全部considered/Declined逐项裁决如下。

### 全部10项 considered 的 Ruling 与代价

C1. Ruling: 接受普通server清理成功时原最终SDK close异常继续传播；cost: 若清理本身也失败，不承诺原异常仍为primary。

C2. Ruling: 接受真实listener关闭与serving thread join的观测；cost: 该fixture保证限其已启动且普通shutdown成功的本地server，不是通用server回收器。

C3. Ruling: 接受httpx/httpx2实际SDK两家回放；cost: 不外推其他OS/loop/未来SDK私有接口。

C4. Ruling: 保留首次真实native close、只在已closed后的冗余close注入；cost: 本回归不新增首次SDK close失败/retry或TLS/provider错误的认证。

C5. Ruling: 接受资源断言在owned rescue之前，旧fixture失败仍被报告；cost: rescue仅为测试独占资源防污染，不是生产重试机制，也不吞原断言。

C6. Ruling: 接受所有可选段不同文本、段顺序、请求早→晚及其他新→旧的literal保护；cost: 标题/格式有意变更时需维护期望，不扩大为任意信息分配最优策略。

C7. Ruling: 接受148边界实际回撤条目后遗漏计数2→3；cost: 精确148依赖当前固定格式，未来格式变更须重新手算，不用UUT计算oracle。

C8. Ruling: 接受147边界仅显示遗漏4的断言；cost: 保留没有可见条目的遗漏通知，并不保证填满剩余空间。

C9. Ruling: 接受更小余量时整体省略可选段，必要前缀完整；cost: 容量不足可不显示任何可选内容，完整资料仍依赖既有state/archive契约。

C10. Ruling: 接受generous和三边界后的输入state逐字相同；cost: renderer级检查不能替代完整state构造/归档/运行时的原生验收。

### 全部6项 Declined 的 Ruling 与代价

D1. Ruling: 接受本轮仅针对冗余SDK close失败、普通server cleanup的边界；cost: 任意server.shutdown/close/join自身失败或反复取消时不新增完整清理/primary异常承诺。

D2. Ruling: 保留既有生产取消、首个native close、TLS/provider故障的独立证据，不由本测试维护重新认证；cost: 本review不提供这些故障的新结论。

D3. Ruling: 接受当前client module身份与直接调用fixture无需setup/teardown的耦合；cost: 将来SDK或fixture新增合法生命周期步骤时必须相应维护meta回归。

D4. Ruling: 接受目标永久边界及六种mutation，不新增所有容量穷举、最大packing、Unicode截断或section公平性策略；cost: 这些属性不能由本轮测试证明。

D5. Ruling: 完整summary-state、archive及更广长任务生命周期继续由其既有验收支撑；cost: renderer测试通过不能替代全部历史/归档/goal行为审计。

D6. Ruling: 作者承担并完成下述source/实装/CLI/JS等最终门，review不重复，整个goal另审计；cost: 单独review只批准两test增量，不证明全目标或发行部署完成。

## 最终门、失败归因及字节

| 实际最终范围 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2202 | 48 | 362.321s |
| Python3.13源码全量 | 2202 | 49 | 366.678s |
| 工作区外安装3.11 | 799 | 5 | 229.297s |
| 工作区外安装3.13 | 799 | 5 | 233.743s |

以上实际句柄55421/52307/57029/97922均读取terminal exit0；JS17、实际CLI help/JSON doctor/dependency smoke及git diff --check通过。159生产Python与39GUI资源全部逐字节相等，两版48实际module origins保留核验；使用原85fb wheel，不重建发行包。最终两test diff仍等于冻结SHA256 `53920c1c887f16b2d34f80ecd9c3d98595873d187a9e01263c5ba2c3d69a205a`，before文本哈希均相同。313最终77 distributions/5616files RECORD/hash issues空。全部门与完整日志哈希 `/tmp/ignovate-review-gap-tests-final-gates.json`；311本地OpenAI3.26.1与313发行锁3.26.0不伪称同依赖。无ResourceWarning不等于所有warning为空，既有forkpty DeprecationWarning与故障注入诊断保留。

首次两个source全量确实各3FAIL、非通过：`test_small_layout_keeps_fields_and_keyboard_help_visible`、`test_unknown_model_resets_manual_reasoning_to_default`、`test_welcome_starts_at_left_and_keyboard_flow_never_saves_early`。启动命令把DEEPSEEK_BASE_URL/MODEL显式置空而非移除，SetupApp因此正确出现空地址/模型的无效配置，三个用例假设的默认表单不成立。实际已验收wheel313的同三测试，env unset→3GREEN/exit0，blank→同3FAIL/exit1；独立review检查对照日志亦确认。没有修改产品/断言掩盖，原失败保留 `/tmp/ignovate-review-gap-tests-initial-full311/313.log`，对照 `/tmp/ignovate-review-gap-welcome-env-{unset,blank,control}.log`，正确runner只移除3变量后完整重跑，最终结果见表。不是因为等待窗口超时重启仍活的任务。

生产没有变化，phase31的实际SDK 9轮/19请求/92tool-result和200轮/1400恢复等证据继续归该阶段，不伪称本轮重新执行。两项已记录测试Minor至此关闭。原goal仍active；后续真实浏览器验收与整体完成审计见 `2026-10-24-project-acceptance-audit.md`，不能把本轮测试维护等同整个项目稳定性已完成。无真实API、提交/推送、部署或实际用户数据库改写，所有前序工作及用户IDE/CSS保留。


## Phase33: GUI task source, explicit control identity and lazy locking

# GUI 项目任务来源与控制身份：浏览器发现、修复与最终验收

上一goal turn完成phase32两项测试Minor、两版2202源码/799实装、唯一完成review与字节门，属于有效进展。原目标继续active。本轮从真实浏览器验收发现多会话任务来源问题，再以真实状态复现同修订号替换任务的控制身份缺口。实际生产基准为phase31/32已验收wheel SHA256 `85fb1423e204fcc4f7fcdd80f8ced29b61d355f55a9f2732c0f21567275f19bc`；修复已有GUI投影和控制提交，不放宽真实来源path/版本检查，不更改TaskStore权威记录、budget/权限或数据库schema。

## 浏览器证据与错误归因排除

使用现有Chrome155.0.8059.85及缓存Playwright1.64.0-alpha-1790635538000，无下载/依赖改动。自有临时profile/项目/配置、随机127.0.0.1端口，页面非本服务origin请求一律abort；记录blocked列表，实际各场景空。Python313真实site-packages中的agent/controller运行现有离线模型fixture，实际文件/命令/SQLite/HTTP均保持原生，非真实远端模型API。父运行器的浏览器子进程上限120s、启动20s、服务关闭15s；不把其他容量探针的240s/512MiB说成本轮资源额度。每组记录controller/agent来源、页面/命令/点击/snapshot、服务后代进程、退出/owned目录/FD/线程；临时数据组退出后删除。脚本均在/tmp，未操纵用户已有浏览器profile/tab。

丢弃式运行器 `/tmp/ignovate-run-browser-acceptance.py`、`ignovate-browser-fixture-wrapper.py`、`ignovate-browser-acceptance-run.cjs`。既有CJS只在进程内把固定8767 origin换成该组随机端口，所有行为断言保持；截图重定向到 owned `/tmp/ignovate-browser-acceptance-evidence/`，保留源码SHA。自建lazy-long场景先写入真实压缩checkpoint、关闭原Factory，再以service=None的GUI重开；禁止构造模型，浏览器全文逐字（仅规范空白）比对4000段正文+原始头尾及刷新前后长度，不只检查末尾。

首次chat-1启动读取使用TextIO.readline加select，第二行已在Python缓冲区而FD未ready，运行器错过SERVER行；修正为os.read整块拆行，未改产品。chat-2/3/4在第三次消息等待超时；实际pointerdown/up/click均已是“停止”，API确实为stop，后端保留消息草稿。旧测试只等SQLite节点，不等UI snapshot/React commit，Playwright持有的同一DOM按钮已变为stop。增加显式可见完成、无live turn、发送按钮状态条件后chat-5通过，XSS/3回合/分叉/刷新/窄布局原断言不删。没有将此改成产品发送/停止策略修复。

初次资源计数4→5不能直接归因GUI泄漏：两版纯标准库 `socket.getfqdn('127.0.0.1')` 均独立造成4→5，随后8次ThreadingHTTPServer创建关闭都保持5。最终先记录该冷初始化，再以5为基线检查GUI自有资源返回5、无新增Python线程/服务后代进程。日志 `/tmp/ignovate-browser-resolver-control311.log`、`...313.log`；没有强关这个原生名称解析初始化产生的进程级FD，也不伪称冷启动4→4。诊断使用lsof/weak socket记录，最终通过组不再替换socket类。

旧85fb实装的tools-2、conversation-1、map-live-1、lazy-long-3真实浏览器通过：并行命令/停止、批准/文件产物/刷新、地图运行中拖动/真实文件diff/刷新不重放，以及86KB级压缩checkpoint在关闭模型后浏览器恢复完整28060字符（4000段正文）/刷新不新建run。均FD5→5/无线程/服务无后代、owned目录空。它们是旧基准发现门；新包最终组另行记录，不能据此宣称新包全部已验收。

## 真实产品根因与有界修复

work-control-1在关联空会话后无法进入反馈；进一步work-control-2从真实阶段回执打开owner会话，保存仍失败：“请选择真实来源回合”。HTTP证据明确是owner的node_id搭配关联空会话branch_id，后端正确拒绝。TaskStore._project会按thread ID排序associated_thread_ids；GUI projection原先直接取第一个关联branch当来源。因此随机ID决定路由，空关联可能顶替仍持有该任务的真实owner。task_summary仍显示owner线程，路由却指另一个分支，来源不一致。

修复在`nailong/gui/work_control.py:projection`：当原生owner仍在此任务associated集合且GUI分支存在时优先选owner；owner已开始独立新任务、不再绑定原任务，或owner分支不可用时，保持既有已知有效association回退；没有已知分支仍None。不改变记录/来源node/任务revision，也不放宽_feedback_source对真实path与task身份的检查。任务与attention使用同一正确route。

中间曾在CJS补“空关联会话一律拒绝反馈”的诊断断言；这过强，因为控制项若提供了真实owner来源可以合法整理反馈，已完整撤回，`gui-work-control.spec.cjs`最终逐字节恢复原SHA `c062e52ed4476f41f094643998dc0ffdb146f7cdb10e0a6ff68e89b6612aec7e`。该诊断不是最终改动，不通过改变合法反馈契约刷GREEN。

新增两个永久Python用例：`z-owner`与`a-linked`强制错误排序，实际TaskStore/BranchStore/异步snapshot检查任务与人工attention均指真实owner，并用投影route成功提出带原生node引用的反馈；同时覆盖只给linked分支/无可用分支的保守回退。第二例owner新建独立任务后，旧任务仍路由有效linked，set_wait只修改旧任务，不污染新owner任务，不启动模型。首个真实RED .065s为a-linked≠z-owner。补control时误把可缺省wait_condition当必有键产生KeyError，修正测试为.get；初次74相关组因此1ERROR，日志保留不称通过。最终两个方法在实际已安装85fb313独立1FAIL/1control GREEN .103s（不是fixture ERROR），相关74项5.634s GREEN。

## 冻结与初始裁决

冻结3路径：生产`nailong/gui/work_control.py`，测试`tests/test_gui_work_control.py`与`tests/gui-browser.spec.cjs`。生产基准来自85fb wheel，两test修改前全文位于 `/tmp/ignovate-gui-task-source-baselines/`；不以HEAD/所有dirty为基准。联合diff `/tmp/ignovate-gui-task-source-review.diff` SHA256 `69d5adb40850ec2ea05bd8731e9f37bfac802bba91a1ba458b7e0a1e44f4884d`。联合新wheel `/tmp/ignovate-gui-task-source-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl` SHA256 `34c5832dd0d71a6b5e5d542e9c4a70c37435725b4d3dc771fad4f4ee5113c276`，完整manifest与旧包相同，仅work_control.py及RECORD内容变更；159Python/39GUI资源均与当前source相同。313离线no-deps实装后77 distributions/5616files RECORD/hash issues空。最初仅routing的32a5 diff及1c3 wheel在review派发前因下面的新真实缺口而扩充，旧证据保存为route-only-review.diff、route-only-attestation.json、route-only-wheel，不把中间候选当联合最终验收；当时唯一最终review尚未派发，现已完成，见下方处置。无实际用户DB改写、真实API、提交/推送/部署，用户IDE/CSS及全部前序工作保留。

R1. Ruling: 原生owner仅在仍绑定该任务且分支存在时优先，否则保留有效association/None回退；cost: 确定来源路由会与过去按字典序偶然选择不同，owner移到新任务时不能按旧owner存储身份强行路由。

R2. Ruling: 项目任务与attention共享route，canonical任务记录、权限、费用及控制后端不变；cost: 来源选择修复不扩成控制权转移，也不修其他GUI命令短暂保守busy窗口。

R3. Ruling: 反馈继续要求真实来源path、任务身份/revision和当前输入，不伪造空会话来源；cost: 未知/过期来源仍拒绝，不能仅因存在别的association就宣称有有效反馈证据。

R4. Ruling: 浏览器测试等可见完成再点击发送/停止共用按钮，原XSS/分叉/刷新断言保持；cost: 依赖当前DOM结构/状态，不新增产品发送按钮策略或任意UI竞态保证。

R5. Ruling: 使用实际隔离Chrome/实装Python/原生工具并单独记录纯stdlib名称解析冷FD，再检查自有资源回落；cost: 本机缓存浏览器/解释器与离线fixture边界，保留进程级冷FD，不外推其他OS/真实模型质量/无限session或全部系统资源上限。

R6. Ruling: 以85fb精确联合3路径增量做唯一完成fresh review，作者一次C/I pass并承担最终新包2206源码/803实装两版门及六组浏览器；cost: tests不在wheel，focused/字节一致/本阶段不能替代整个goal完成审计，不为其他dirty内容背书。

## 联合发现：同修订号的替换任务不能接收旧控制请求

在首次routing候选尚未review或全量验收时，进一步审查同一work_control提交路径发现：payload携带task_id但后端未核对。真实状态回归用旧Task A和新Task B相同revision、相同人工验收ID和合法B来源，旧A的控制请求在set_wait、clear_wait、confirm_acceptance、feedback_stage、feedback_confirm五个子场景均被接受，既可能改B状态，也能生成/确认B反馈。2方法.295s为5个预期断言FAIL/无fixture ERROR，历史反馈撤销control GREEN，日志 `/tmp/ignovate-gui-task-source-identity-red.log`；实际已安装仅routing的1c3候选四方法同样5FAIL/其他3control GREEN，日志 `route-only313-identity-red.log`，不是仅从代码猜测。

联合修复仍只在原模块：_check_control_task在异步扫描之前和最终同步提交时检查当前任务及显式task_id，五类当前任务动作不接受其他身份。set_wait/clear_wait复用TaskStore.bound_task持有同一原生任务/版本锁再调用原有可重入mutator，堵住检查后/写入前换绑的窗口，不在持锁期间await。其他三类原有bound_task与完整观察比较保持。显式link_task的target身份、readonly status与按feedback记录id/revision撤销历史经验不套用当前任务ID检查；owner换新任务后仍可撤销已确认的历史反馈。

新增两个永久方法保护五类同revision拒绝后的Task/session事件/feedback完整保持，以及已确认历史反馈在owner新任务后的合法撤销。四个新方法连同原组共76项5.823s GREEN。原TaskID缺省的旧Python调用继续针对当时真实当前任务并受revision/native bound锁保护；显式提供错误/空/None身份保守拒绝，不从revision相同推断同一任务。联合最终包/浏览器/完整门以34c5及69d5身份为准，之前final33标签属于1c3 routing候选，不冒充联合结果。

R7. Ruling: 五类当前任务控制同时核对显式task_id和原有各自版本契约，在扫描前和提交时拒绝错配；cost: 旧/空的显式身份会报错，caller需要刷新，未提供身份的旧Python接口仍保留当前任务语义，不把它升级为陈旧UI授权。

R8. Ruling: 等待变更复用现有原生bound_task锁，确认/反馈保留原绑定；历史反馈撤销和显式关联目标按其记录身份继续工作；cost: 短同步文件事务仍受系统IO时延约束，不新增硬取消/无限时限承诺，也不把历史撤销错误地绑定到新任务。

- [x] 原生浏览器发现、点击/API/纯stdlib对照、两类确定性RED/旧包负对照与76相关GREEN。
- [x] 3路径冻结、新包精确manifest/字节和313真实安装完整性。
- [x] 唯一fresh完成review、作者一次C/I pass及全部considered/Declined决定和代价。
- [x] 最终远端整合包两版2214源码/807实装、六组真实浏览器、CLI/JS/字节/环境验收；旧2206/803为I1前中间门。

## 唯一审查与作者一次 C/I 处置

唯一 reviewer `/root/gui_task_source_review` 在用户中断后续完原审查，没有新增第二次 review。原文 `2026-10-24-gui-task-source-review.md`，SHA256 `ef377f485e0e281049b359c2d78d08f0be095f2fcb7a0ccbc36d1a7a40773399`。结果 0 Critical/P0、1 Important/P1、0 Minor。独立76项7.610s、15个显式无效身份拒绝、2个同修订号换绑间隙拒绝通过。

I1为既有 lazy service=None 控制死锁，85fb与34c5均有原生有界子进程复现：外层TaskStore A持bound_task，最终_execute新建B后snapshot等待A文件锁。作者完整阅读报告并执行唯一C/I pass：扫描时捕获的tasks显式传入_execute，保留完整状态比较及原生可重入锁，不await持锁、不加载模型。永久子进程回归依次真实人工验收、反馈candidate到confirmed，检查service仍None/runs空。旧实现最终oracle在30.012s超时FAIL；新实现0.302s GREEN。初稿把不存在的user_decisions当结果字段，尚未走到即被死锁挡住；最终RED前已更正为原生manual passed evidence，初稿日志保留，不以错误字段刷通过。无二次review。

### 全部审查裁决与代价

- Ruling R1至R8：逐项采纳原初八条及reviewer同名建议，代价维持上文。R6最终候选因I1与远端整合更新；69d5/34c5仍是独立审查对象，新包须新验收，不冒充reviewer重审。R8补充同实例捕获/传递，同步IO时延边界保留。
- Ruling C1：任务与反馈记录revision分别使用；cost：调用方须提供对应记录版本，数值相等不代表同一身份。
- Ruling C2：保留扫描后project/branch/node/完整task/native绑定比较；cost：并发变化可保守拒绝、要求刷新，不保证外部项目不变。
- Ruling C3：接受等待写入bound_task及两个独立间隙探针；cost：真实换绑后旧请求须刷新重试。
- Ruling C4/I1：不排除已复现lazy缺陷，完成上述一次修复；cost：新增实例参数和有界回归、重建包并重跑门，无第二次独立审查。
- Ruling D1：不自动搜索或重建反馈来源节点，保留显式来源校验；cost：仍可能须选择合法来源或显式关联，不是历史修复功能。
- Ruling D2：保持发送/停止产品策略，测试等待可见idle；cost：不保证任意时序，断言耦合当前DOM契约。
- Ruling D3：保持本机离线有限容量、浏览器和资源证据边界；cost：不认证无限会话、其他OS/runtime、真实模型或全部系统资源上限，不把纯resolver冷FD当GUI泄漏。
- Ruling D4：独立审查仅三路径；cost：旧dirty工作、GitHub整合与全目标由作者另做实际门，本报告不能单独支持整体完成或发布。

### 中间门与远端整合

I1修复前34c5两版源码2206项各48/49skip、393.255/398.275s；两版实装803项各5skip、241.516/246.301s，实际句柄terminal0。六组combined33浏览器均terminal0。这些是中间证据，不能当作I1后最终修复验收。

用户新授权完成后提交GitHub。fetch发现旧分支落后远端且PR3已合并；原生create_worktree创建隔离目录 `/Users/aycxd/.codex/worktrees/9eec/LLM_project`，以origin/main `4260136` 新建 `codex/long-task-reliability`。独立临时index导出188个已授权路径，原工作区index/IDE/CSS不变。三方整合三处冲突：CLI保留远端Windows记录和新增checkpoint说明；empty_conversations保留closing+lstat；测试保留远端连接释放回归、显式commit及全部新故障回归。自动整合保留远端stdin DEVNULL及Windows GUI入口/runtime_sources。六个新增Python文件仅清理空白EOF。最终包沿用main的1.2.3版本，无tag/发布/部署。整合后最终门另列，不用整合前结果顶替。

## 最终提交版本的实际验收

最终整合wheel SHA256 `25e5d283e1c266f6c231285c869a16056a4401003a7a461027601f5b0caad919`，不是独立审查前34c5或首次整合1214。相对34c5，生产额外变化为作者I1修复、远端已有stdin DEVNULL/Windows GUI入口/runtime_sources，以及排除用户CSS；版本保持main的1.2.3。159Python与39GUI资产均与最终提交工作树逐字节一致。

| 最终范围 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2214 | 49 | 413.133s |
| Python3.13源码全量 | 2214 | 50 | 416.471s |
| 工作区外安装3.11 | 807 | 5 | 249.343s |
| 工作区外安装3.13 | 807 | 5 | 248.517s |

实际句柄23117/31790/62523/15718均读取terminal exit0。两实装各48个关键模块来源为安装目录；313按原锁环境77 distributions/5616 files逐项RECORD/hash无问题。311 OpenAI3.26.1与313锁3.26.0不混称同一依赖。19项JS、desktop check含Windows打包脚本语法、最终wheel CLI help/JSON doctor/dependency smoke及diff检查均通过。单独误用仅.cjs glob的首个JS命令只运行5项，不冒充19；随后明确.cjs和.mjs联合运行19项exit0。

最终六组Chrome验收标签github34：chat、work-control、lazy-long、tools、conversation、map-live，均使用最终实际313安装包，spec来自整合工作树；服务和浏览器exit0、FD5到5、无新增线程、无服务后代/自有目录遗留、无非服务请求、无剩余browser。lazy-long在关闭Factory/模型后全文28060字符、4000段正文和头尾独立比对及刷新均通过。其他场景覆盖真实来源反馈candidate/confirm/revoke、工具停止、审批产物、地图diff与刷新不重放；不是远端模型质量保证。

完整最终日志/哈希/浏览器结果与实际终态记录 `2026-10-24-github-validation.json`，原始日志 `/tmp/ignovate-github-final-*`、浏览器证据 `/tmp/ignovate-browser-acceptance-evidence/*-github34/`。新增lazy回归将原始2206/803以及整合前2213源码候选结果区分，不拿中间门替代最终。全部初始R1至R8、review C1至C4/D1至D4及代价均保留，I1关闭，无第二次review。当前已确认未修复P0为0，三项GUI P1至此修复并完成本地验收；原整个项目goal继续active，平台/远端/任意故障等边界不变。用户已授权下一步GitHub提交；本地验证不等于已合并、发布或部署。
