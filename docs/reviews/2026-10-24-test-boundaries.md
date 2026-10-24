# 原生回归夹具与默认backend身份边界

## 有界设计与检查清单

上一goal turn为有效进展；当前真实生产验收wheel SHA256 `50704687774c9e903a64f946329be563f52b875e637c0b379f9b7c726f559845`。本轮是phase25/26已明确Minor的独立测试任务，不进入旧C/I pass；原goal完整保留且active。仅修改两个现有test文件，生产158Python字节全部与该wheel一致，无新生产行为/依赖/接口，不提交推送、不调用真实模型API，保留全部旧工作及用户IDE/CSS。

MCP夹具诊断`MSG_PEEK`异常使event永不set，production正确join watcher反而令测试永久等待。作者有限实际SDK探针 `/tmp/ignovate-mcp-fixture-failure-regression.py` 先RED（报告门闩被卡住，释放救援后清理），随后用try/finally确保event.set。GREEN311/313均原诊断故障正常报告、owners完成、FD7→7，无真实资源被故障注入吞掉。探针只在测试peer代理中让一次MSG_PEEK抛BlockingIOError，不改实际SDK/进程关闭；旧路径RED时在时限后只释放该测试门闩，防止演示失败遗留资源。日志 `/tmp/ignovate-test-boundaries-fixture-red.log`、`fixture-green.log`、`fixture-green313.log`（同前缀）。

backend新增3方法/6子场景：两家HTTPX × sync/async默认backend子类故意使用同module/name，但实际返回公共MockBackend的HTTP字节流；正确fallback须完成真实SDK请求/关闭、不替换原backend。两家显式AnyIOBackend另用实际loopback服务发请求并保留raw socket核验关闭，不仅检查空client。新测试在phase24已验收wheel工作区外副本3方法4error/2fail（1.286s），证明捕获原包前缀策略而非只测builder；当前合计14项6.332s通过。日志 `/tmp/ignovate-test-boundaries-backends-attested-red.log`、`green.log`（同前缀）。

- [x] 原诊断故障RED/最小finally修复GREEN311/313，原真实SDK三项不放宽断言。
- [x] 持久化同module/name子类及显式AnyIO真实请求/关闭，旧已验收wheel负对照失败。
- [x] 定向原生/Factory兼容、311/313全量及实际安装、CLI/JS/diff。
- [x] 单次fresh read-only review；0C/0I无作者补修无复审，Minor及全部Declined逐项裁决。

## 裁决

Ruling: 将两项已记录Minor作为独立测试维护任务 — 不借旧生产C/I pass夹带修改；cost: 新的测试差异审查和最终回归，不改旧报告结论。
Ruling: 诊断失败只保证释放调度门闩，不吞掉测试失败 — finally仍让实际异常进入SDK退出/测试断言；cost: 故障注入仍使原fixture报告失败，但不再用卡死代替结果。
Ruling: 默认子类用同module/name实际SDK回放，显式AnyIO使用真实loopback/socket — 让行为保护精确类身份而非只有MockBackend案例；cost: 新增3项维护及私有pool接口耦合，不把AnyIO的普通SDK关闭证明扩大成native receipt/retry保证。
Ruling: 复用已验收phase26 wheel作为发行验证对象 — 所有生产字节完全未变，重建只会引入无关元数据；cost: 明确本轮无新发行包，必须让新测试实际加载原wheel而非工作区生产模块。

基准tests不包含于wheel：从各自已冻结review差异完整恢复原文件，MCP文件SHA256与原review记录`9171f2733a48de10a21c1ae756ee834f118f67db0bc18c75606ccd77a6f48ba9`核对；backend基准来自phase25冻结新文件全量。基准目录 `/tmp/ignovate-test-boundaries-baseline`，本轮冻结差异 `/tmp/ignovate-test-boundaries-review.diff`，不是HEAD，也不冒充wheel包含tests。同期继续检查较大完整历史容量，仅作独立丢弃式原生探索，不在本轮冻结测试差异中改生产代码。

相关107项64.517s/1skip GREEN，`/tmp/ignovate-test-boundaries-targeted.log`。单次fresh review期间生产/两测试冻结；全部最终门和评审均已结束。

## 独立历史容量探索

自建临时数据2400组成套human/assistant/tool/assistant消息，分8批真实L2压缩，完整归档9595条证据，最后索引2397844字符；重开HistoryArchive后通过实际read分页400页恢复完整索引，最初要求尾部仍从原始归档可读。311源码总22.001s/分页4.146s/约319225856 bytes峰值RSS，313实际原wheel安装总19.137s/分页4.215s/343703552 bytes，实际磁盘29631748 bytes，FD均4→4。原生脚本 `/tmp/ignovate-history-capacity-probe.py`，监督器 `/tmp/ignovate-run-history-capacity-probe.py`（120s上限，独占父临时目录托管）；日志 `/tmp/ignovate-test-boundaries-history-capacity311.log`、`history-capacity313.log`（同前缀）。首次临时目录路径包含系统`/var`链接而被安全路径拒绝，改夹具Path.resolve后再测，没有放宽生产路径检查。313在/tmp启动且清空PYTHONPATH，使用同一原生probe。

这是归档/压缩/索引分页样本，不是2400次真实模型调用、SQLite整个runtime重启或无限容量/性能认证。另一项实际Factory/Graph/SQLite探索反而发现重要续作缺口：9000字符持久约束可容纳128k窗口，却因固定6000字符摘要上限不能丢弃过大的旧助手分析。两版4场景均1RED/3对照GREEN；详见 `docs/reviews/2026-10-24-constraint-compaction-capacity.md`，未修复且原goal必须继续，不以本测试任务GREEN隐去该失败。

## 单次评审与全部反馈裁决

Fresh Astra 0C/0I/1M；独立14项6.332s、诊断故障FD7→7、旧已验收wheel的3方法4error/2fail负对照均得到独立验证。原报告 `/tmp/ignovate-test-boundaries-review-final.md`，原文归档 `docs/reviews/2026-10-24-test-boundaries-review.md`。已再次确认冻结差异/两test基准/158发行Python及完整manifest，未改生产，无C/I补修无二次review。以下承接前4项Ruling，覆盖M1及全部7项Declined/considered项：

5. Ruling: 新M1为非阻塞测试清理待办，留待独立维护 — AnyIO finally中sdk.close异常会跳过server收尾；review实际执行SDK close后注入OSError，两listener/thread仍存活，探针救援全部关闭/join；cost: 当前该诊断故障会让测试残留本地server至解释器退出，明确未修，不夹带入本轮C/I pass，且不等同生产回归。证据 `/tmp/ignovate-test-boundaries-review-cleanup.py`及相邻log。
6. Ruling: D1本轮不重审旧生产生命周期，但不以scope忽略并行发现的真实生产问题 — 仅本两test增量得到review认可；长约束压缩RED已独立建调查并优先后续修复；cost: 旧取消/retry/TLS/长任务不能因本轮测试GREEN重新认证，整个goal保持active而非宣称已稳定交付。
7. Ruling: D2显式AnyIO/custom仍只承诺原SDK契约 — 正常请求/原生socket关闭不升级成默认receipt/retry保护；cost: 自定义backend故障下更强保证仍未认证，测试不暗示该结论。
8. Ruling: D3其他OS/loop/未来依赖须实机门 — 独立review在macOS311，作者另有313实际门；cost: 不能外推未执行的平台、循环或私有SDK未来版本。
9. Ruling: D4生产wheel+还原冻结test作为增量基准，旧dirty/前端/metadata/HEAD不重复背书 — tests不在wheel，历史来自原冻结diff且MCP另有旧hash；cost: Python字节相等不代表全部非Python工作区内容或可重复构建证明，用户IDE/CSS继续保留。
10. Ruling: D5故障注入探针暂不再增加永久meta-test — 两行finally已通过真实SDK注入RED/GREEN、原三测试断言保持，探针可重跑；cost: 删除finally的未来回归不会由普通成功场景直接捕获，/tmp探针是本轮证据而非保留CI覆盖。
11. Ruling: D6异步子类forwarder保留普通def返回原coroutine — 真实SDK实际await已通过，不因风格增加无意义wrapper；cost: 方法签名表面不显示async，未来协议变化须由行为回归捕获，不称其为当前兼容缺陷。
12. Ruling: D7原生socket断言不是空列表或fake成功 — 实际成功keepalive连接持有非空socket列表，subclass回放刻意验证非native契约；cost: 该证明限本机正常SDK关闭，不替代fault retry证据，mock子场景不被说成native句柄验收。

## 最终门与发行对象

| 实际范围 | 总项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2164 | 48 | 380.328s |
| Python3.13源码全量 | 2164 | 49 | 385.744s |
| 工作区外实际安装3.11 | 719 | 5 | 229.207s |
| 工作区外实际安装3.13 | 719 | 5 | 233.319s |

四组实际终态0且无ResourceWarning，日志 `/tmp/ignovate-test-boundaries-final-full311.log`、`full313.log`、`installed311.log`、`installed313.log`（同前缀）。两组158发行Python字节/45模块来源均核对，完整生产Python manifest与wheel一致。JS17/diff、原wheel实际安装CLIhelp/JSONdoctor/依赖smoke通过。发行对象仍为phase26 wheel SHA256 `50704687774c9e903a64f946329be563f52b875e637c0b379f9b7c726f559845`；本轮无新生产发行包，313精确锁与311现有环境不混称同一锁。

phase25精确身份持久覆盖Minor及phase26门闩Minor至此关闭，旧报告不回写。新增AnyIO异常清理Minor明确保留；并行原生长约束压缩RED为下一项生产修复优先级，不因有界评审通过而丢弃。全部旧工作/用户IDE/CSS保持，无真实API/提交/推送/合并，无scratch待清，原goal继续active。
