# 完整输入与产物证据实施计划

> **For agentic workers:** Use executing-plans inline and TDD; one fresh independent final reviewer. Preserve all earlier changes and user .idea/CSS. No commit/push or real model API.

**Goal:** 复杂项目能取得完整输入身份，验证后的真实产物变化不能继续支持交付，多次大构建与重启不丢输出清单。

**Spec:** docs/superpowers/specs/2026-10-22-input-artifact-evidence.md。

**Architecture:** typed namespace snapshot + private immutable output manifests + current-evidence observation consumed across Task/Goal/UI. Disk inventory and chunked proof keep memory and persisted task metadata bounded. Actual files/commands/stores establish RED before each production change.

## 1. 固定复现与当前调用图

- [x] 将四个原生探针写成回归：产物改写后Goal拒绝完成、101输出再次验证、空目录删除失效、真实内部应用链接可观察；先运行RED。
- [x] 列出所有生成路径与当前输入判断的消费者和存储边界；确认Task/Goal不同私有目录的复制/登记顺序，明确身份版本与旧记录迁移。

## 2. 完整命名空间观察

**Files:** nailong/core/input_snapshot.py、snapshot_inventory.py、snapshot_links.py、verification_directories.py；tests/test_input_fingerprint.py及新专项文件。

- [x] 原生RED覆盖文件/目录/链接类型和权限、空目录、多跳内部链接、循环/缺失、项目外/保护范围逃逸、缓存置换、扫描中变化及反复取消。
- [x] 实现有界磁盘清单、类型域和目录元数据；以当前清单解析内部链接，不无界跟随文件系统。保留固定句柄和前后版本检查。
- [x] 验证普通源文件及配置覆盖、旧身份陈旧行为、元数据上限/取消和事件循环响应性。

## 3. 完整产物清单与生产者

**Files:** 新 nailong/core/verification_artifacts.py（或等价单一职责模块）、verification.py、sessions.py仅必要的预览语义；专项原生测试。

- [x] RED：101/大清单重启、多次生成、缺失/损坏/截断片段、清单发布中断、输出枚举取消、输入伪装为生成物。
- [x] 实现私有固定边界、1MiB片段/完整校验/原子清单、有界引用与明确预览；所有物理I/O由owned worker执行。
- [x] VerificationService登记输入与产物身份，成功记录后的完整清单作为后续生成物排除权威；已知目录后新增文件仍使输入变化。
- [x] 只将真实生产者发布的完整内容登记到Task/Goal私有边界，失败保留旧证据且不报告通过；不让大型复制进入状态锁。

## 4. 全部当前证据消费者

**Files:** task_state.py、goal.py、agent_service.py、delivery相关入口、gui/work_control.py、gui/controller.py、session_actions.py、feedback相关观察；相应原生集成测试。

- [x] RED/GREEN：产物修改/删除/权限变化之后，Task/Goal完成、自动verify复用、GUI人工决定/节点、recap/export都不能保留旧正面结论。
- [x] 读取旧记录与原生重启，清单缺失/损坏一律未知；不重置任务、历史或费用。
- [x] 对共享GUI观察绑定完整清单身份，等待后保留原任务/来源CAS；验证反复取消与真实worker清理。

## 5. 完整验收与继续审查

- [x] 在当前真实工作区运行完整身份观察并记录覆盖、边界和耗时，不通过自动全局忽略制造成功。
- [x] requesting-code-review独立复核；每个实际发现均native RED/GREEN。
- [x] 完整Python/JS、diff check、wheel外部实际安装与原生链路；更新PROJECT/CLI_REFERENCE/总体审查。
- [x] 保留原目标active直到全面完成；继续长期存储与复杂交付场景，原生Windows证据另行核对。

## Ledger

- Factory阶段最终1916项Python/45跳过、JS17、实际安装180项已通过，142个发行Python文件一致。本阶段首次四个探针改变了下一步动作：范围包含目录、链接、完整输出清单及产物当前身份，不能只解除链接报错。
- 先完成调用图与格式设计，再实现；上述输出清单边界、版本和事务规则为约束，具体存储格式须由原生测试证明。现有fixture只做定位参考，不把字典假证据当成原生覆盖。

## 最终验收

- 完整1953项Python / 45跳过，其余通过（239.433s），JS17通过，diff check通过。
- 实际pip target安装262项通过（41.448s），22模块来源与145发行Python文件一致；wheel SHA256 87dc2a85576de53b93d9a3dfd8478e74c7f5858a30b7acd523be879fece3af01。
- 实际工作区24.743s / RSS80117760字节，历史2to3外部断链使覆盖unknown，未扩大忽略范围。
- 独立native复核通过本阶段反例；Linux/Windows目录见证未原生，保守counter边界保留。
- 继续原目标：首次失败构建不能重试、已退役输出同名新源码被历史路径并集接管已原生复现；进入独立输出归属/退役/恢复阶段，不将局部验收标为整个目标完成。
