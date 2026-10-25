# 完整产物发布与回收实施计划

> 使用executing-plans inline、TDD原生RED/GREEN；一次requesting-code-review独立最终复核。沿用整体修复授权与native执行，不重新请求许可；不提交/推送/真实模型API，保留用户IDE/CSS及前十三轮工作。

**Spec:** docs/superpowers/specs/2026-10-22-artifact-publication-recovery.md

**目标：** 完整观察失败、复制中断和发布硬退出均不持续遗留未追踪part，且不删除有效证明或扩大产物权限。

## 文件与接口

- 新建 `nailong/core/artifact_storage.py`：固定边界发布锁、有界intent与恢复、完整引用图及孤片维护。
- 新建 `nailong/core/artifact_directory.py`：独立复核推动的目录owner，POSIX事务读/原子写/删除使用同一dir_fd，返回前核对路径身份；Windows沿用原生祖先pin。
- 修改 `verification_artifacts.py`：ManifestWriter的TemporaryFile owner、256KiB头上限、完整暂存复制；保持v1引用。
- 修改 `verification.py::_snapshot_outputs`：with/finally归属；维护诊断保留有界记录。
- 新建 `tests/test_artifact_storage.py`：原生命令/存储/硬退出/竞争/取消及复制回归；已有产物/归属测试继续有效。

## Review Focus

1. 新事务补回旧完整头缺失的片段之后失败，不能误删被旧头引用的修复片段。
2. 头已经成功发布但intent清理失败或进程硬退出，恢复不能回滚完整证明。
3. 维护遇坏历史头或容量/取消，不能用部分引用图删除；现代健康证明仍可发布。
4. 双向跨边界复制与多进程发布，不能锁反序死锁或删除在途数据。
5. 私有目录被替换/出现链接或陌生条目，不能越界读取/删除。

## 1. 固定失败边界与观察owner

- [x] 原生RED：真实构建容量失败不留part；实际头发布失败不留新孤片；最后一片损坏的跨边界复制不留半份证明。
- [x] 实现ManifestWriter with/close、TemporaryFile暂存，生产扫描finally关闭；头与片段大小有界，未知观察不发布。
- [x] 原生文件句柄/取消及完整manifest现有测试通过。

## 2. 受控发布、崩溃恢复及旧孤片

- [x] 原生RED：真实进程在片段/完整头发布后退出，下一发布恢复；成功证明共享片段、恢复幂等、损坏intent不清账。
- [x] 实现 `publish_manifest(boundary, header, spool, parts, publish)`：同边界原生锁、先intent、最后头，失败与重启恢复；parts为有界(offset,id,size)序列。
- [x] 实现 `collect_orphan_parts(boundary)`：完整磁盘引用图后才清理合法孤片；首次迁移一次，正常发布不重扫历史。坏图保留并输出有界诊断。
- [x] 覆盖旧头引用的缺失片段、跨进程锁、取消、链接/陌生条目及容量保护。

## 3. 跨边界复制与实际长任务链路

- [x] 复制先完整核对源再发布目标，失败/重复/同边界及相反方向跨进程复制原生通过。
- [x] 重复容量/存储失败不持续增长；命令失败→成功、Task/Goal/GUI/CLI/headless交付当前证据继续有效。
- [x] requesting-code-review独立复核，实际Important一轮native RED/GREEN修复；记录裁决与限制。

## 4. 最终验收与继续整体目标

- [x] 完整Python、JS、diff check；实际外部pip安装wheel，运行存储/归属/入口复杂回归并核对全部发行Python文件。
- [x] 更新PROJECT/CLI_REFERENCE/总体审查与本计划最终证据；只清理本计划scratch。
- [x] 原目标保持active；有效历史容量、真实旧外链、跨平台和全面交付继续审查。

## 最终验收与裁决

- 最终新增17项原生存储测试，相关74项通过29.577秒；完整Python1999项，45项平台跳过，其余通过257.305秒；JS17和diff check通过。实际5次容量失败从5242875字节遗留降为0长期文件/0字节。
- 一次独立最终复核仅发现1项Important：固定scandir fd却重新打开路径执行读写/删除。两个实际目录rename测试RED后，增加ArtifactDirectory owner，使POSIX读/原子写/删除使用同一dir_fd并在返回前核对路径身份；Windows沿用祖先原生pin。重复两项测试共8次通过，原生FD4→4；没有延后Minor。
- 实际外部pip安装wheel后308项全部通过60.308秒，26个关键模块来自安装目录，149个发行Python文件与源码字节一致；外部CLI help/JSON doctor/依赖smoke通过。wheel SHA256 `1a49e0fa65caaeb354e1d23b8344c4a0f34c5220319a5cb8b8b82fd3d48b6537`。
- 证据：`/tmp/ignovate-artifact-storage-red.log`、`/tmp/ignovate-artifact-storage-recovery-legacy-red.log`、`/tmp/ignovate-artifact-storage-boundary-red.log`、`/tmp/ignovate-artifact-storage-review-red.log`、`/tmp/ignovate-artifact-storage-review-green.log`、`/tmp/ignovate-artifact-storage-final-full-python.log`、`/tmp/ignovate-artifact-storage-final-installed-native.log`、`/tmp/ignovate-artifact-storage-directory-fd.log`。
- 最终验收覆盖独立复核修复后的代码；早期1997项/306安装项只是中间结果。没有真实模型API、提交或推送，既有用户IDE/CSS和前十三轮保留。

### 实施裁决（按发生顺序）

1. Ruling: 保留v1引用，使用有界匿名TemporaryFile加小型串行intent，避免v2引用迁移和每次构建扫描全部历史。代价：成功发布期间需要暂存与长期存储两份空间，完整历史仍增长。
2. Ruling: 坏/不完整历史引用图保留候选孤片并报告持久诊断，健康完整新证明仍可发布。代价：保留片段占用空间，需修复历史图后再次维护。
3. Final Ruling: 完整有效历史保留；完整头是根，不在孤片回收中删除。代价：完整历史仍需后续容量与归档策略，原目标active。
4. Final Ruling: 未取得Windows原生竞争证据，沿用现有原生pin而不宣传已原生验证。代价：Windows平台行为仍未证明，macOS结果不能推广。
5. Final Ruling: 本次独立复核以前十三轮已验收代码为基线，没有重审所有旧阶段；完整回归仍覆盖整个项目。代价：更广潜在问题继续在原目标中审查，不能由本阶段标记整个项目完成。
