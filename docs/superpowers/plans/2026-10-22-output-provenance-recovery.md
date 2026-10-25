# 输出归属与构建恢复实施计划

> Use executing-plans inline, systematic native RED/GREEN and one independent final reviewer. No implementation subagents, commits, pushes or model API. Preserve prior phases and user .idea/CSS.

**Spec:** docs/superpowers/specs/2026-10-22-output-provenance-recovery.md

## 1. 固定反例与归属调用图

- [x] 将failed-build重试与retired同名源码删除两个原生探针写入回归并先RED；覆盖真实VerificationService/Task/Goal/SessionStore。
- [x] 核对全部已有输出授权来源、项目执行lease、状态提交顺序、跨会话/旧记录迁移；明确私有账本格式和完整声明范围存储，拒绝历史路径并集及墙钟排序推断。

## 2. 完整活动归属存储

- [x] 原生存储RED：重复提交、发布中断、坏/缺manifest、坏账本、容量、跨进程竞争、嵌套/重叠范围与已退役同名文件。
- [x] 实现固定私有边界、完整回执引用、有界磁盘成员与原子替换/退役。损坏保留原状态；旧证明不复活退役权限。
- [x] 原生旧记录/重启迁移，保持第十二轮健康新证明可用、坏旧证明只诊断、覆盖不足拒绝及旧单一文件回执可重验的安全契约。

## 3. 执行与失败/取消恢复

- [x] 命令真实结束且完整输出观察成功后发布生成归属，与verification passed分开；首次失败不阻塞合法重试。
- [x] 输出登记先于可失败的JSONL/Task/Goal通知，完成证明不能因迟到/失败写回遗失；旧身份不能覆盖新版本。
- [x] 原生取消/重复取消/真实子进程退出/存储失败探针；未知副作用不自动接管输入或重放命令，资源全部由owner加入并关闭。
- [x] 保持当前产物变化使交付失效，失败证据不能通过Goal/Task；成功重试才进入原有完成检查。

## 4. 验收与继续整体审查

- [x] 原生复杂多轮：失败→修复→成功、生成集合增删、退役→用户同名源码、重启/跨会话、超过100/1MiB清单及GUI/CLI/headless。
- [x] requesting-code-review独立复核，所有实际Important都补native RED/GREEN。
- [x] 完整Python/JS、diff check、外部实际wheel安装与来源/内容核对；更新PROJECT/CLI_REFERENCE/总体审查。
- [x] 继续原目标active；实际外部链接、Linux/Windows目录原生、长期容量和全面交付仍需推进。

## 起点

第十二轮最终1953项Python/45跳过、JS17、实际安装262项全部通过，145个发行Python文件一致。wheel SHA256 87dc2a85576de53b93d9a3dfd8478e74c7f5858a30b7acd523be879fece3af01。两个新反例已先原生RED并修复，下面的最终验收记录区分本阶段与整体目标。

## 最终验收

- 原生专项29项通过（12.673秒）；完整Python1982项，45项平台跳过，其余通过（250.438秒）；JS17项与diff check通过。日志 `/tmp/ignovate-output-provenance-final-native.log`、`/tmp/ignovate-output-authority-accepted-full-python.log`、`/tmp/ignovate-output-authority-final-js.log`。
- 独立最终复核确认1项Important、无Critical或Minor：超时started被登记完整归属。真实1秒超时回归RED后修复；作者另发现合法深层中文项目路径被1KiB元数据上限拒绝，两项原生RED后GREEN。首次额外容量测试的macOS /var路径夹具错误已修正，最终全量重跑覆盖，不作为生产故障结论。
- 实际pip --target安装的wheel在源码外291项通过（56.481秒），24个关键模块来自安装目录，全部147个发行Python文件与源码字节一致。SHA256 `f9e4b54fc3f2e55dea4b035a3705456f3e2b27eaf8a4221d0abfdba01fc897c9`，外部CLI help/JSON doctor/依赖smoke通过。日志 `/tmp/ignovate-output-authority-final-installed-native.log`、`/tmp/ignovate-output-authority-wheel-smoke.log`。
- 无真实模型API、提交或推送；用户IDE/CSS及既有各轮改动保留。总目标active，本计划只完成本阶段。长期容量新探针确认容量失败留下不可达part，已在总体审查列为下一阶段待修；Linux/Windows原生、实际工作区旧外部链接及全面交付仍未被本阶段证明。

## 实施裁决

1. Ruling: 使用有界不可变成员快照、小型原子head/intent及非阻塞项目锁，临时SQLite负责成员查询。完整intent可恢复，admission本身无权限。代价：私有存储损坏会变成unknown并拒绝验证，不自动重置。
2. Ruling: 现代执行只读取完整活动快照，避免每次重扫全部历史；历史损坏在实际读取/迁移时诊断。代价：已被取代的历史回执之后损坏，不会在无关的新构建中主动报告。两项裁决经独立复核；没有延后Minor。
