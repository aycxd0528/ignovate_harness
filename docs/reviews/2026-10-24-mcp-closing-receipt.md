# MCP关闭期间最终监护结果

## 有界设计与验收清单

上一goal turn为有效进展，phase25最终实际wheel SHA256 `b37090a696f16a17ed3b934000acc860fdb54e6ec779ab9bc8b03cf1cd696894`为本轮真实基准；不以HEAD/全部dirty作为增量。目标仍是完整长任务稳定交付，本轮接续此前明确未认证的late MCP通知顺序，不改变原goal范围。保留所有前序及用户IDE/CSS，不提交推送、无真实模型API。

根因：`StdioOwnership._watch`只在connection尚未closing时发布结果；`__aexit__`先关闭健康socket再取消watcher。真实SDK/direct process均已退出、authenticated通道里EOF已可读，也可能被当作干净主动断开。新增原生回归只延后watcher调度，调用原stdio SDK、服务和双监护进程；SDK退出后MSG_PEEK核验实际通道字节（正常C/inner owner被杀则EOF），不伪造协议或close效果。2项首轮0.729s，unknown用例RED：公开status.error为空，normal对照通过；日志 `/tmp/ignovate-mcp-closing-receipt-red.log`。

修复设计：保留原SDK退出顺序，在健康通道退出前等待现有watcher收齐结果；closing状态只表示不再接纳，不代表清理核实。正常C在主动关闭时保持干净断开，缺少/损坏确认时即使已closing也报告unknown；已有closing不再次cancel SDK owner。watcher若被取消/异常不能取得结果，则保守发布unknown，仍传播owner取消、不留后台Task。

- [x] 原生SDK/实际helper退出后最终字节peek与公开status的RED，正常C对照。
- [x] 最小生产修复与新用例GREEN，原MCP/Factory/GUI相关回归。
- [x] 单次fresh read-only review，0C/0I，无作者补修无复审；全部Declined裁决和代价归档。
- [x] 311/313全量、实际wheel安装字节与来源、CLI/JS/diff及当前包长任务探针。

## 初始裁决

Ruling: 将late通知缺口作为独立有界修复，不改写phase24已结束的评审 — 原生公开状态已证实误报干净断开；cost: 新增独立回归/review/包级门。
Ruling: 接纳关闭与清理证据分别判断，unknown在closing期间仍发布，已closing不再次cancel owner — 停止接纳不证明原生进程清理；cost: 原本显示disconnected的部分故障关闭现在保持error，调用方须核对副作用。
Ruling: SDK退出后等待既有健康watcher，再释放通道；无确认或watcher取消按unknown — 不用强制取消吞掉最终证据；cost: 需等待真实SDK已关闭的本地peer完成读取，不新增虚假清理超时，未核实不报成功。

第三项原生回归刻意取消watcher后正常SDK退出，实际MSG_PEEK仍为C，但没有观察结果不能据此谎称核实。三项在phase25 attested wheel的工作区外副本运行，2fail/1正常对照通过0.965s；当前3项0.976s（311）/1.069s（313）GREEN，`/tmp/ignovate-mcp-closing-receipt-attested-red.log`、`three-green.log`、`three-green313.log`（同前缀）。相关完整99项37.592s/1skip GREEN，`/tmp/ignovate-mcp-closing-receipt-complete-targeted.log`；早先98项含首2测试也通过36.671s，但不代替最终99项。

生产修复仅`nailong/mcp/stdio_owner.py`，新测试单文件`tests/test_mcp_closing_receipt.py`；冻结review差异 `/tmp/ignovate-mcp-closing-receipt-review.diff`。`receipt`只记录当前watch结果，已closing且实际C不改具体error，unknown仍封闭接纳/置error，但不再次cancel正在退SDK的owner。`__aexit__`在原SDK结束后join既有watcher，取消/异常无法取得receipt则发布unknown，并finally关闭socket；不新增Task/loop、helper协议不变，SDK timeout不改。

作者补充丢弃式原生探索（非保留CI）：连接真实MCP并spawn有限后台写入，直接让asyncio.run返回、全局取消所有Task。311源码与313实际本轮wheel安装均owner结束、status保守unknown（watcher被取消）、FD4→4、无新线程，1.3s后后台marker不存在。日志 `/tmp/ignovate-mcp-closing-receipt-global311.log`、`global313.log`（同前缀），探针 `/tmp/ignovate-mcp-global-drain-probe.py`。不以该两次有限样本外推任意强杀/脱组/平台效果。

当前包默认HarnessChatDeepSeek/OpenAI/HTTPX2本机SSE长目标探索重跑：311源码与313实际安装均9round/19request、92累计toolresult、7次构建失败后压缩/暂停/Factory重开/恢复/修复/验证完成，费用2090tokens/$0.0007980000000000003连续；外部修改交付物降unverified，FD4→4、无新线程。日志 `/tmp/ignovate-mcp-closing-receipt-native-goal311.log`、`native-goal313.log`（同前缀）。这是有限自建服务协议证据，不冒充真实provider认证或持久CI覆盖。

## 最终包级门

| 实际范围 | 总项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2161 | 48 | 372.002s |
| Python3.13源码全量 | 2161 | 49 | 377.526s |
| 工作区外实际安装3.11 | 716 | 5 | 221.793s |
| 工作区外实际安装3.13 | 716 | 5 | 226.902s |

四组命令退出0，均无ResourceWarning；日志 `/tmp/ignovate-mcp-closing-receipt-final-full311.log`、`full313.log`、`installed311.log`、`installed313.log`（同前缀）。两组实际安装158发行Python字节与当前源码一致、45关键模块来源在安装目录；313精确发行锁77包依赖兼容，311现有OpenAI3.26.1与313锁定3.26.0不混称同一锁。JS17、diff、实际安装CLIhelp/JSONdoctor/依赖smoke通过。

wheel `/tmp/ignovate-mcp-closing-receipt-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`，SHA256 `50704687774c9e903a64f946329be563f52b875e637c0b379f9b7c726f559845`。最终生产/测试与单次冻结review差异完全相同，无C/I补修；对比两个真实wheel，158个Python文件集合一致，仅`nailong/mcp/stdio_owner.py`字节不同，所有当前Python字节与新wheel一致。四组测试数/跳过/耗时及日志哈希另保存 `/tmp/ignovate-mcp-closing-receipt-gates.json`。

## 单次评审与全部反馈裁决

Fresh Astra 0C/0I/1M，独立新增3项1.072s通过；另5项原生探针覆盖watcher异常、晚unknown+SDK次级异常、owner在join中取消、disconnect waiter取消、C保留已有具体错误，全部通过，FD7→7/0 pending Task。原报告 `/tmp/ignovate-mcp-closing-receipt-review-final.md`，原文归档 `docs/reviews/2026-10-24-mcp-closing-receipt-review.md`；探针 `/tmp/ignovate-mcp-closing-receipt-review-probe.py`及相邻log。无二次review，无production/test改写。以下承接初始3项Ruling，逐项覆盖M1与全部8项Declined：

4. Ruling: 接受M1为非阻塞夹具待办，后续独立修复诊断peek的finally门闩释放 — reviewer实际注入一次BlockingIOError，测试/watcher/owner待在门闩，手工释放后均结束；当前原生正常peek及生产故障/取消路径通过，不把测试注入问题重评成生产C/I；cost: 该诊断异常会令当前测试等待监督超时，记录明确未修，不塞入已冻结增量的C/I pass。证据 `/tmp/ignovate-mcp-closing-receipt-review-fixture-probe.py`及相邻log。
5. Ruling: D1旧GUI及其他dirty/untracked历史工作不重复独立认证 — 本轮以phase25真实验收wheel限定新增两文件；cost: 本轮review不重新背书旧阶段，也不完成整体goal。
6. Ruling: D2包级发行内容/311/313/安装/CLI/JS由作者最终实际门核验 — 不把单次局部review冒充完整交付；cost: 作者承担四组门与158字节/45来源，只有全部终态0后接受（本轮已完成）。
7. Ruling: D3其他OS和未来MCP/AnyIO须原生验证 — 当前只证明macOS POSIX实际SDK组合，313由作者运行；cost: Windows/Linux/未来私有接口不由代码检查外推，仍在整体review未认证边界。
8. Ruling: D4不把truthful unknown报告扩成独立内监护被杀/主动脱组/提权后代的强包含保证 — 进程组边界未改；cost: 这些独立故障下效果可能继续，须核对副作用，整体goal仍保留进一步证据需求。
9. Ruling: D5保留既有同用户IPC信任边界，不在本轮改握手或添加对抗安全系统 — 正常descriptor归属已检查，未执行token窃取/保留peer/伪造的对抗审计；cost: 不认证恶意同用户进程破坏通道后的真实性/有界收尾。
10. Ruling: D6未复现的grant至watcher资源耗尽交接另作startup/admission调查 — 本轮改收尾证据，不凭静态可能性改启动协议；cost: 异常blocked send后cancel且尚无watcher的细分顺序仍未认证，不能把正常立即send当所有调度证明。
11. Ruling: D7不新增私有watcher的活跃期监督器 — 无公开操作只替换/取消watcher而保留owner，本轮实际保证关闭时观察缺失保守unknown；cost: 外部直接改私有Task时不承诺立即撤销活跃连接，不能将收尾fallback当实时监督。
12. Ruling: D8原SDK退出前任意重复raw cancel及OS未收回direct process仍须独立故障证据 — 本轮独立cancel探针在新post-SDK join，作者globalcancel只覆盖一条实际顺序；cost: 不外推所有更早SDK清理中断或OS失败的收尾保证，仍依正常manager生命周期控制。

当前late receipt误报干净断开已由真实旧wheel RED/当前GREEN/完整实际门关闭；M1明确待办，phase25精确backend边界持久覆盖Minor也保持待办。原始goal active，完整历史容量、跨平台/loop/协议矩阵、历史外部链接及更广故障交错继续需要推进。不调用真实模型API、无提交/推送/合并；全部前序与用户IDE/CSS保留，无scratch待清。
