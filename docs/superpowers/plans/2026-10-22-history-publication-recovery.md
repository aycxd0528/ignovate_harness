# 历史归档发布恢复实施计划

> REQUIRED SUB-SKILL: executing-plans inline + TDD；一次requesting-code-review。沿用既有不提交/推送及离线验证安排。

**Goal:** 让历史归档失败和硬退出不持续泄漏不可达分片，同时保留所有完整历史与并发安全。
**Architecture:** 每会话目录FD绑定原生锁与有界发布journal，完整头作为提交点；完整引用图先于删除，坏图保留并警告。
**Tech Stack:** Python、native file locks、SQLite membership、unittest、原生子进程与wheel。
**Spec:** docs/superpowers/specs/2026-10-22-history-publication-recovery.md

## Global Constraints

- v1/v2/reference/完整原文/脱敏/分页/64MiB上限不变，不删完整历史。
- 保留用户IDE/CSS和当前分支，不提交/推送/真实模型API；原goal active。
- 同版并发受锁保护，旧进程升级前停写；120秒、512MiB图核验数据、64MiB路径/512MiB SQLite、最多8警告。

## Review Focus

1. 修复缺失但仍被旧完整头引用的共享分片后，当前发布失败也不能删除它。
2. 目录rename/替换及链接必须绑定同一个FD，不触碰替换目录或链接目标。
3. 多进程现代发布/collect与重复取消不得删在途数据或泄漏锁；等待时事件循环可继续。
4. 坏intent/坏头/图限额失败必须保留候选，不能用部分引用图或重置历史制造成功。
5. header提交后清理失败和硬退出不能重写已完成身份、误报记录丢失或令费用/checkpoint重置。

## Task1：原生失败与恢复协议

- [x] tests/test_history_storage.py真实头发布失败RED，保留已完成历史。
- [x] history_storage.py锁/维护/journal/引用图；history_chunks发布前header回调；HistoryArchive接入所有save和显式collect，保留缓存读取语义与目录FD。
- [x] 重复失败、legacy回收、有效历史/共享分片、坏intent/坏图/资源边界GREEN。

## Task2：交错与请求链路

- [x] 原生子进程part/head硬退出、并发publisher/collector、重复取消等待、目录替换、清理失败；先固定必要RED再实现。
- [x] 有界警告进入context_request；健康现代快速路径无全图扫描，L2与真实Graph/自动goal续作相关回归。

## Task3：最终复核与发行

- [x] 一次fresh最有能力可用reviewer，精确第十六轮attested wheel→本轮；真实C/I一次native RED/GREEN。
- [x] 全Python/JS/diff，实际外部pip --target及全部发行Python内容/模块路径，CLI smoke。
- [x] 更新PROJECT/CLI_REFERENCE/总审查与全部裁决，清自己的scratch，保留分支与active原goal。

## 独立最终复核及补修

fresh gpt-6-astra：0Critical/1Important/0Minor。独立19项执行18通过、1错误，确认同一已提交记录在INTENT清理持续失败时重试错误；没有其他具体发现。作者一轮native RED/GREEN：同一已验证提交身份可带有界警告返回；不同发布仍拒绝且不改原journal。最终相关87项通过16.416秒，日志 `/tmp/ignovate-history-storage-review-green.log`；未进行第二次复核。

当前最终实际外部pip安装467项通过90.777秒；31个关键模块来自安装目录，150个发行Python文件与源码一致。wheel SHA-256 `f680db68740a5a133433c89d5f9b10eb10a481c6db486e631a4976e86ddd9a33`，外部CLI help/JSONdoctor/依赖smoke通过。最终完整Python2030项，45项平台跳过，其余通过277.807秒；JS17与diff check通过。

## 裁决

- history独立协议复用目录/index原语，不重构已验收artifact协议；代价是两个不同journal schema分别维护。
- 同版publisher/collector串行化，升级前需停止不使用新锁的旧进程；代价是不保证新旧发布协议同时写入时安全热升级。
- 原生证实共享POSIX首次O_CREAT打开也有竞态，因此扩展file_locks修复；代价是全部POSIX锁消费者采用新的独占创建/存在时打开路径，需全量回归。
- GC先验证完整v1/v2身份与分片，头及完整体合计512MiB、路径64MiB/索引512MiB、120秒；代价是少数恢复/显式维护会读取历史正文，超限保留碎片并警告，健康现代保存仍不全扫。
- 独立复核以第十六轮attested wheel为基线，不重审前16轮与用户IDE/CSS；代价是本次审查不等于再次全项目审查，作者全量回归另提供树级证据。
- Windows/真实API仅有静态或离线证据，原goal保持active；代价是不提供本轮没有原生依据的跨平台/真实提供商长期保证。

Deferred minors：无。

## 最终验收与继续

- 最终完整日志 `/tmp/ignovate-history-storage-accepted-full-python.log`：2030项/45平台跳过，其余通过277.807秒。JS17 `/tmp/ignovate-history-storage-accepted-js.log`；diff干净。
- 最终安装/来源/CLI日志 `/tmp/ignovate-history-storage-accepted-installed-native.log`、`/tmp/ignovate-history-storage-accepted-wheel-smoke.log`。467项通过90.777秒；31模块路径、150发行Python字节一致；最终wheelSHA f680db68740a5a133433c89d5f9b10eb10a481c6db486e631a4976e86ddd9a33。
- 40轮真实并发归档、每轮8写入者、40次完整尾部读取通过，原生FD4→4；日志 `/tmp/ignovate-history-storage-fd-final-green.log`。原始5次失败探针修复后0不可达分片/0字节，旧完整历史可读，`/tmp/ignovate-history-orphan-publication-repeat-green.log`。
- 最初2029项/45skip和安装466/150（SHA ecb600b81e5e42fd0c3d25c03181f77f347eac0de569bd17851fd12083702ac6）是复核补修前证据；当前最终结果全部重跑，不能混用。
- finishing-a-development-branch：沿用用户不提交/推送安排，保留codex/adaptive-task-runtime、用户IDE/CSS和其他计划；只清本计划scratch，所有裁决已持久保存。原goal active；仍需更广复杂场景、有效历史容量和跨平台原生证据，不能据此宣布整个项目审查完成。
