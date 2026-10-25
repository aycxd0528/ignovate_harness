# 长任务上下文续作实施计划

> 使用executing-plans inline和TDD，沿用已授权native工作流；一次requesting-code-review独立最终复核。不提交/推送/真实模型API，保留前十四轮及用户IDE/CSS。

**Spec:** docs/superpowers/specs/2026-10-22-long-task-context-continuation.md

## 文件与接口

- `nailong/core/context_runtime.py`：默认投影目标和已知模型窗口的有界扩展，保留完整请求预算与request-only/CAS契约。
- `nailong/core/task_context.py`：如需结构化超限元数据则集中处理；直接渲染严格默认保持。
- `tests/test_long_task_continuation.py`：真实Graph/SQLite/工具/命令/Task/Goal组合、实际provider请求内容与预算拒绝。
- 既有task_projection、managed_context、history_async_context、session_budget及自然任务回归继续覆盖。

## Review Focus

1. 动态工具发现、自动L2或request-only历史压缩导致可见原文集合变化，必须重新计入全部有效要求。
2. 显式调用方字符预算与未知窗口不可被默认扩展绕过。
3. 脱敏或stage policy追加文本使实际投影超限，不能截断字段或遗漏长度检查。
4. 较大任务投影仍计入Goal/累计token reserve；实际请求不能使用旧的较小估计。
5. 重启/任务修订/旧版本请求不能重置费用、伪造要求被替代或让旧交付证据通过。

## 1. 原生反例和投影边界

- [x] 将真实已知窗口+40k不可见有效要求写入RED，确认provider未收到请求是现有固定字符上限导致。
- [x] 加入直接渲染严格、未知窗口和显式硬上限的保留测试，实际模型窗口不足时不调用provider且checkpoint原文保留。
- [x] 实现默认None/基础12000目标与最大1000000字符；只在已知模型窗口时扩展，追加stage/脱敏后仍核对实际长度。
- [x] Task投影仍request-only，所有原文、状态、约束和完整预算检查继续有效。

## 2. 真实组合长任务与恢复

- [x] 固定16轮→两次压缩→两次关闭/重开Factory/SQLite→failed build→repair→passed→Task/Goal完成的原生回归；尾部要求可归档恢复，所有身份/费用不重置。
- [x] 实际provider请求包含当前全部有效要求；完成后产物外部变化使delivery失效。
- [x] 增补自动压缩、当前消息去重后重估、累计预算与重复压缩/重启，测试针对真实缺失或错误分支。

## 3. 最终复核、安装与整体继续

- [x] requesting-code-review一次独立最终复核，真实Important一轮native RED/GREEN修复；记录裁决与限制。
- [x] 完整Python/JS/diff，实际外部pip安装wheel并核对全部发行Python内容与来源；更新PROJECT/CLI_REFERENCE/总体审查。
- [x] 只清本计划scratch，原goal active；真实工作区旧外链、有效历史容量、跨平台与更广复杂交付仍在原目标内。

## 独立最终复核

一次fresh gpt-6-astra复核精确第十四轮attested wheel→当前的生产差异；无Critical/Important，独立44项通过7.782秒。原生发现1项Minor，按executing-plans记录而不进入修复pass：request-only历史压缩重建实际投影后未同步diagnostic info，实际40623字符可显示1599、expanded=false。实际请求窗口、整体token和累计费用仍按最终消息核对。整体goal继续，下一阶段可固定该原生反例。

裁决记录：

- 保留全部有效原文，默认目标仅在已知窗口有界增长；更大的有效请求消耗更多token，未知窗口/显式硬上限严格。
- 第十五轮以SHA核对的第十四轮wheel为基线；之前已接受的未提交阶段及用户IDE/CSS不重复纳入本次独立差异复核，全量回归仍覆盖整体。代价：本轮独立审查不等于再次审查前十四轮。
- 真实提供商长期行为和Linux/Windows原生保证不从macOS离线结果推导，原goal保持active；本轮不提供跨平台或真实提供商稳定性保证。

Deferred minors：上述投影诊断偏差；不会据此缩小实际预算或省略有效要求。

## 最终验收

- 完整Python2007项，45平台跳过，其余通过，263.985秒；JS17通过，diff check干净。完整日志 `/tmp/ignovate-long-continuation-final-full-python.log`、`/tmp/ignovate-long-continuation-final-js.log`。
- 实际wheel SHA-256 `d45960666b140496db8194e56f739a1b6d3a82adb2c1e55e4acf84c5073760c7`；外部CLI help/JSON doctor/依赖smoke通过。pip --target实际安装后，在源码外运行405项原生回归，全部通过77.401秒；27个关键模块路径来自安装目录，149个发行Python文件与源码字节一致。日志 `/tmp/ignovate-long-continuation-final-installed-native.log`、`/tmp/ignovate-long-continuation-final-wheel-smoke.log`。
- finishing-a-development-branch：沿用用户既有不提交/推送授权安排，保留codex/adaptive-task-runtime和全部工作树变更，不重复询问集成。仅删除本计划scratch；此前计划和用户IDE/CSS保持。整体goal active，下一阶段先处理已确认的Minor，再继续长期容量及更广复杂交付。
