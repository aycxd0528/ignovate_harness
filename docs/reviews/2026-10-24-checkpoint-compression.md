# Checkpoint 无损压缩：phase31 修复与验收完成

上一goal turn为有效进展：phase30严格摘要容量修复、四组完整门、200逐回合Graph/SQLite样本与旧包对照，发现1400 checkpoints约493MB持久占用。本轮基准是实际已验收03f wheel：`/tmp/ignovate-summary-bounds-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`，SHA256 `03f19c3fc4a04a20ac05039d1d0175f5430ef241d5a3da15d3a481c343762993`。不用HEAD/全部dirty作增量，不改用户IDE/CSS，无真实API/提交/推送/用户数据库迁移，原goal保持active。

## 定位与丢弃式实验

实际SDK SqliteSaver/AsyncSqliteSaver每次put完整序列化checkpoint；200回合保存1400份完整状态，旧样本483143372bytes checkpoint BLOB、主DB493015040bytes。installed JsonPlusSerializer没有压缩分支。项目所有实际反序列化入口为agent同步、OwnedAsync、GUI controller两处fallback和GUI history只读入口；其余表计数/引用不解析正文。caller显式提供的checkpointer/serde仍由caller决定。

只在/tmp子进程注入prototype serializer，标准库zlib3压缩原始serializer字节，16KiB–64MiB且节省至少1/8才写带版本类型，未修改checkout/dependency。相同200回合保留201active要求、7次L2/4重开、1400 checkpoint全部再次解码，DB32370688bytes、checkpoint BLOB24027921bytes、peak owned disk60337151bytes、关闭后56290679bytes；3207次序列化原始490469217bytes/存储30317449bytes、1403次选用压缩。loop退出后FD4→4/no新threads。业务回合计时14.804s不包含后置1400条遍历审计，不能当全审计墙钟或非重复真实模型性能保证。样本高度重复，收益不可外推所有数据。原型不是最终生产验收。原脚本与日志 `/tmp/ignovate-checkpoint-compression-{prototype,scale,prototype313.log}` 及父运行器留在/tmp。

## 现有链路内的有界设计

在现有SerializerProtocol的(type,bytes)接口内增加默认codec，不改数据库schema、checkpoint数量/id/父关系/metadata和pending writes。新`checkpoint_serde.CheckpointSerializer`继承原JsonPlusSerializer；在原始msgpack/bytes/bytearray至少16384且不超过67108864字节、level3压缩节省至少1/8时，返回`nailong.zlib.v1:<原类型>:<原字节数>`与压缩正文，否则保持原编码。编码失败传播，发生在SQL发布前；旧checkpoint保留。

解码兼读全部旧格式。新family严格核对版本、最长64字符header、规范ASCII整数、原类型白名单、0..64MiB长度和压缩体上限；最多解压声明长度+1，精确核对长度/eof/unused_data/unconsumed_tail，拒绝截断、checksum损坏、拼接、尾随与输出超限。未知编码转ValueError，让GUI readiness保守拒绝，不凭历史日志伪造可续作。64MiB只约束新压缩记录；旧大记录保持原有SDK读写能力。原JsonPlus类型安全/信任边界沿用，不增加pickle或任意constructor权限。

`RuntimeSqliteSaver`同步子类和既有`OwnedAsyncSqliteSaver`默认用新codec，显式serde原样传入；原生连接/worker、关闭重试、全局取消归属完全保持。agent默认sync import与GUI两fallback/history只读import改用该sync子类，GUI readonly仍is_setup=True、不DDL/不开模型。全流程不全局patch SDK；prototype的patch仅属于已退出探针。

这是已读清的现有序列化链路局部适配，使用当前goal对可逆代码修复及隔离离线验证的授权执行；没有对实际用户数据库改写或部署。格式兼容与升级边界在PROJECT/CLI_REFERENCE正文明确：新程序可读旧数据，旧程序/未适配标准SDK不能读新压缩记录；升级需停止共享DB旧进程，外部reader用RuntimeSqliteSaver或显式CheckpointSerializer。旧行不自动改写/VACUUM，旧文件不会自动缩小，后续新行受益。

## 持久测试与当前证据

新增`tests/test_checkpoint_compression.py`。首次fixture把既有helper误写log_turn（正确import_log）导致import时AttributeError，未运行产品，日志first-fixture-error保留；修正后生产修改前14方法5个原生未压缩FAIL/6个新codec未实现ERROR，3旧契约control GREEN，.200s。当前完整16方法在实际03f313外安装为5FAIL/7新功能ERROR、4control GREEN，.256s。不是删旧测试或stub存储后端刷通过。

覆盖完整AI/Tool/usage/reasoning/ID字节恢复、旧JSON/msgpack/bytes、threshold/随机不可压缩native回退、损坏/checksum/尾随/拼接/未知v2/不规范Unicode整数/超长header/解压bomb、最大记录fallback、同步旧新混合/父关系/全历史、OwnedAsync读旧和sync新/写新/pending writes、显式custom serde保持、压缩MemoryError不发布新行/不覆盖旧行、实际lazy GUI全文/原生只读continuation和未知v9拒绝、精确历史clone不继承source未来内容。首次115项12.579s，补header/mixed检查116项12.529s，完整117项12.575s GREEN；日志前缀`/tmp/ignovate-checkpoint-compression-`。

文档首次shell heredoc收到非UTF8源编码错误，Python编译前退出、未写文件；改为apply_patch写入，修正CLI标题/EOF空行后diffcheck通过。生产测试不因这一文档工具失败被改写。

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
