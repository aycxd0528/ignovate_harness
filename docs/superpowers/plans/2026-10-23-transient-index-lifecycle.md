# 临时磁盘索引生命周期实施计划

> REQUIRED SUB-SKILL: executing-plans inline + TDD；一次requesting-code-review。沿用offline验证、不提交/推送安排。

**Goal:** 输入与产物磁盘索引在进程硬退出后不遗留命名大文件，保留完整数据与资源边界。
**Architecture:** 共用ephemeral_sqlite.py，先配置FILE再ATTACH私有临时scratch；只在scratch创建表和索引，默认VFS+OS负责文件生命周期。
**Tech Stack:** Python sqlite3/OS、unittest、原生子进程、wheel。
**Spec:** docs/superpowers/specs/2026-10-23-transient-index-lifecycle.md

## Global Constraints

- 不改变输入范围/生成物所有权/完整历史与TaskGoal费用，不删/tmp未知数据。
- 4096字节页、131072页/512MiB、2MiB缓存、64MiB产物路径、120秒取消/deadline保持；禁内存回退与强制TEMP_STORE=3。
- 默认内置VFS与稳定进程配置；不设置全局临时目录/环境/CWD；输入临时根仍在项目外。
- 保留用户IDE/CSS、原分支；无提交/推送/真实模型API，原goal active。

## Review Focus

1. 配置FILE之前不能创建实际scratch；TEMP_STORE=2默认内存和3强制内存不能绕过磁盘/缓存预算。
2. CREATE TABLE/INDEX和max_page_count/cache_size必须指向scratch；不能仅限制空main而数据落在无界库中。
3. SQLite临时根与Python根不同（SQLITE_TMPDIR、Windows TMP/TEMP）时仍不污染项目输入，拒绝无法验证的配置且不改全局状态。
4. 连接创建、PRAGMA/DDL/取消/消费者yield异常必须关闭实际连接，不依赖TemporaryDirectory析构或垃圾回收。
5. 硬退出无finally时应无命名大索引残留，彼此连接不共享数据，旧原文/证据/费用不被伪造或删减。

## Task1：原生泄漏与磁盘生命周期

- [x] tests/test_transient_index_lifecycle.py两个真实消费者子进程硬退出RED，使用有效多级相对路径。
- [x] ephemeral_sqlite.py原生临时scratch/策略/位置守卫；snapshot_inventory、verification_artifacts及output_migration DDL/页数/缓存迁移。
- [x] 活跃磁盘fd/0600/大库体积、main空/scratch配置、容量/错误/连接隔离GREEN；旧容量用例改验证真实connection关闭而非必须创建命名目录。

## Task2：配置和运行链路

- [x] Python根/SQLITE_TMPDIR根在项目内拒绝、强制内存构建拒绝、deadline/取消清理、正常结束闭连接。
- [x] 真实完整扫描、artifact/storage/historyGC、Graph及自动goal续作相关139项回归48.172秒通过（1项Windows原生跳过）；本机物理存储和硬退出为macOS原生，compile3仅接口守卫测试。

## Task3：独立复核与发行验收

- [x] 一次fresh gpt-6-astra reviewer，精确第十七轮attested wheel→本轮四个生产文件；0Critical/0Important/0Minor，无复核后修复或第二次复核。独立15项中14通过、1Windows原生跳过，另有33.65MB临时目录权限边界原生核验。
- [x] 完整Python2045项274.691秒，46项平台跳过其余通过；JS17、diff通过；实际外部pip --target 482项88.156秒，1项Windows原生跳过其余通过；32关键模块来自安装目录，151发行Python字节一致，CLI smoke通过。
- [x] 更新PROJECT/CLI_REFERENCE/整体审查与全部裁决；只清自己的scratch，不提交/推送，原goal active。

## 最终验证证据

- 原生硬退出RED：`/tmp/ignovate-transient-index-first-red.log`，各12000有效多级路径，exit81后输入索引遗留33652736B、产物索引25432064B。
- main容量及辅助迁移RED：`/tmp/ignovate-transient-index-main-cap-red.log`、`/tmp/ignovate-transient-index-migration-red.log`。配置/原生生命周期/关联扫描和自动goal139项GREEN：`/tmp/ignovate-transient-index-native-all.log`。
- 完整Python：`/tmp/ignovate-transient-index-final-full-python.log`；JS：`/tmp/ignovate-transient-index-final-js.log`；安装：`/tmp/ignovate-transient-index-final-installed-native.log`；CLI help/JSONdoctor/依赖smoke：`/tmp/ignovate-transient-index-final-wheel-smoke.log`。
- wheel `/tmp/ignovate-transient-index-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`，SHA-256 `d1c7481e83c5796c18ca01d52bacee29512cf11ca46076422ff62e465bb91973`。最终再次核对当前源码151发行Python一致。独立复核后生产代码无修改，未进行第二次复核。

原始goal的下一项原生发现：实际execute_process启动命令后，控制Python进程os._exit81，命令随后仍写入标记；日志`/tmp/ignovate-process-controller-hard-exit-probe.log`，finished_at_controller_exit=false、command_wrote_after_controller_exit=true。探针仅使用专属临时目录、有限时命令自行结束，无用户进程或数据删除；本轮没有修改生产进程生命周期，需单独设计崩溃所有权边界并修复。此发现及真实工作区历史外部打包链接、长期历史容量和跨平台证据继续保留在整体goal范围。

## 实施裁决与代价

1. 使用SQLite内置私有临时磁盘/VFS和OS句柄生命周期，不扫描删除未知旧/tmp文件。代价：依赖默认内置VFS，旧版本已经留下的未知数据库不会自动回收；平台行为需分别原生证明。
2. FILE之后才ATTACH承载数据的scratch，数据及旧输出迁移辅助表统一受512MiB页数上限与2MiB缓存约束；空main限制1页，误用未指定库的DDL会明确失败。代价：四个生产文件改变内部schema归属，所有连接消费者需回归；真实迁移RED暴露两张遗漏辅助表后已补齐。
3. 不读写线程不安全的全局temp_store_directory，不修改进程环境/CWD；核对默认Unix候选或Windows GetTempPathW，并保留Python临时根守卫。代价：只保证默认内置VFS及稳定进程配置，不承诺第三方VFS或别处修改SQLite全局临时目录的配置。
4. TEMP_STORE=3或无法核实编译选项时拒绝，不回退无界内存。代价：自定义仅内存构建无法使用完整验证；实际本机TEMP_STORE=1已原生测量，2/3构建仅策略/接口证据，未冒充实际编译构建测试。
5. 只复核第十七轮attested wheel后的精确增量，避免重审已验收17轮；完整Python/JS/安装验收另行覆盖关联消费者。代价：本次独立意见不代表整个项目全面审查完成。
6. macOS原生硬退出/物理落盘和离线真实调用链是本轮证据；Windows用例保留平台跳过，未调用真实模型API。代价：没有Windows原生或线上长期运行保证，原始goal继续推进。

无deferred minor。各裁决和验证记录复制到本计划及整体审查后，才删除本轮专属SDD scratch；保留原分支、用户IDE/CSS，不提交/推送。
