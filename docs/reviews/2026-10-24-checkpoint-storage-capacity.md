# 实际多轮Graph/SQLite历史容量：原生探索与后续调查

phase30渲染边界修复完成四组门后，以独立丢弃式探针补上此前2400消息纯归档样本没有覆盖的真实逐回合执行。不是新的生产改动/第二次code review，也不是声称200次远端模型推理或已交付200个真实任务。

脚本 `/tmp/ignovate-runtime-history-scale.py`，父运行器 `/tmp/ignovate-run-runtime-history-scale.py`；owned temp根、最长240s、每20回合检查512MiB磁盘限额，固定offline RecordingModel、Factory→AgentService.stream_turn→Graph→SQLite真实路径，每轮一次模型边界调用并提供约14400字符助手分析，200条新持久用户要求分别追加到同一Task。128000窗口/122880输入limit，允许工具仅read_history_result/read_task_context且本探针无工具调用。模型usage是fixture数，不是远端精确token证据。

每一实际请求包含所有到此刻追加的完整要求及最初constraint尾标；每轮检查要求持久化，task_id不变、request_report估算不越limit。200回合7次L2归档索引、50/100/150/200回合关闭并重开Factory，4次逐字段比较重开前后的Task snapshot完全相等。已有9轮/19次真实SDK和实际工具/产物验证回放是另一独立证据，不混成200轮产物交付。

新phase30 wheel03f实际313安装：200边界调用/7 compaction indexes/4重开，最大input estimate122631≤122880；14.731s/peakRSS142770176bytes，peak disk521602901bytes、关闭后516930981bytes，主checkpoints.sqlite493010944bytes。loop内FD7→7；asyncio.run退出后processFD4→4/no new threads。新要求200条，加初始目标要求共201active（首版result的active_requirements=200字段指新增expected数，逐20回合日志及baseline改为准确201/另new_requirements200，不据字段别名少算原始要求）。

实际旧e403验收wheel通过--no-deps离线安装到独立target `/tmp/ignovate-history-scale-baseline313`，仍使用同一313哈希锁依赖，module origin实际在该target。完全相同探针：200调用/201active/7索引/4重开、最大122631、14.889s/peakRSS142753792bytes，peak disk521606975、关闭后516935055、主SQLite493015040bytes，processFD4→4/no threads。SQLite原生只读统计1400 checkpoints、checkpoint BLOB合计483143372bytes/最大595183，dbstat checkpoints表484970496bytes、writes表7733248bytes，其他index等约数百KiB。占用不是本次renderer新引入，两个样本近乎相同。

**已观察但未完成解决的容量问题：**200个逐回合任务导致约493MB持久checkpoint数据库，而全部历史仍应可恢复/分叉；不能因当前请求窗口有界就推断长期磁盘占用已解决。尚未把这一成本直接定级为数据丢失bug，也未实测更大回合数/实际非重复代码文本/数小时墙钟/断电/磁盘耗尽或其他平台。下一步优先检查checkpoint重复快照的存储放大、兼容全部sync/async/GUI/branch/history读取路径的无损压缩或其他有界策略；不能删checkpoint、截历史或移除要求来刷容量通过。需在真实保存/重开/分叉/崩溃恢复和旧数据兼容证据下再决定修复，不用该探索预先宣称新存储格式安全。

探针弯路明确保留：最初TaskStore.begin误传constraints导致TypeError，原API是begin后amend；仅/tmp探针修正，日志 `/tmp/ignovate-summary-bounds-history-scale-first-fixture-error.log`。下一次业务200回合全通过，但在asyncio.run内部把合法的loop默认executor asyncio_0误算Factory泄漏，末断言失败；该线程属于尚活跃loop，已改循环退出后核验资源并真正在两个对照运行结束后FD4→4/无线程。原日志 `history-scale-pre-loop-check-error.log` 保留，不归咎产品，不以更名掩盖失败。无生产/test文件修改。父TemporaryDirectory在已退出子进程后回收全部约0.5GB fixture数据；保留两日志和指标JSON。

Ruling: 该200回合证据补充真实逐回合持久化容量，不替代真实SDK/产物交付或无限稳定保证；cost: 时间/资源仅覆盖该受控样本，原始goal保持active。
Ruling: baseline同样约493MB说明重复快照成本早于本次摘要修复，作为后续独立存储调查；cost: 本轮不改变数据库/serializer，不承诺所有旧checkpoint可被任意新格式读取，全部历史保留。
Ruling: 默认executor归属在loop退出后判断，Factory只应关闭自己的资源；cost: 活跃loop期间默认worker可合法存活，不能强制销毁共享线程池换取虚假的零线程。

最终指标与两日志哈希 `/tmp/ignovate-runtime-history-scale-evidence.json`。


## Phase31 后续：默认生产实现已验收

实际新wheel 85fb1423e204fcc4f7fcdd80f8ced29b61d355f55a9f2732c0f21567275f19bc 的200回合/201active要求/7L2/4重开样本通过，1400 checkpoint完整保留并全部解码；主DB32321536bytes，相比本发现独立旧基准493015040bytes减少约93.44%，闭合后FD4→4/no新threads。未删除/裁剪历史或改写实际用户DB。收益限此高度重复样本，旧行不自动转换/VACUUM，旧reader不能读取新压缩记录，长期容量仍增长；详见 `2026-10-24-checkpoint-compression.md` 的全量、发行、review、升级和全部裁决/代价。
