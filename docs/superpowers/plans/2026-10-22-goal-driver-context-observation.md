# 自动目标续作与实际任务投影诊断实施计划

> REQUIRED SUB-SKILL: executing-plans inline + TDD；一次独立最终requesting-code-review。沿用用户不提交/推送、offline验证安排。

**Goal:** 修复实际投影诊断，补齐共享自动目标驱动器的长期续作证据。
**Architecture:** 历史视图函数返回被采用请求及其对应Task info，避免重新取快照；原生自动driver使用确定性模型但实际工具/命令/SQLite。
**Tech Stack:** Python unittest、LangGraph、SQLite、原生命令与wheel。
**Spec:** docs/superpowers/specs/2026-10-22-goal-driver-context-observation.md

## Global Constraints

- 不省略有效原文，不绕过字符/窗口/费用/token/CAS、读前写与任务范围门。
- 保持原分支及用户IDE/CSS；不提交/推送/真实模型API；原goal active。

## Review Focus

1. 归档失败或候选无收益不能留下未采用的Task诊断。
2. 候选投影的task_id/revision/lifecycle和正文必须来自同一快照，不额外重读导致TOCTOU。
3. 最终实际窗口/累计预算仍按实际消息，不依赖diagnostic characters。
4. 自动goal循环暂停、恢复及重开不能漏记/重记usage、轮数、美元reserve或丢有效要求。
5. 原生自动验收不得把失败构建/旧proof误报已交付，外部产物变化仍失效。

## Task1：诊断与实际消息一致

- [x] 在tests/test_long_task_continuation.py加入真实Graph两轮可见长要求→request-only压缩测试，断言actual projection长度与expanded和持久事件一致；看RED。
- [x] context_runtime.py::_historical_request_view与_build_request传递被采用的info，不额外渲染；保留所有早退和失败分支。
- [x] 候选无收益/归档不可用回归保留旧info；相关模块GREEN。

## Task2：自动驱动原生长任务

- [x] tests/long_goal_driver_scenario.py及tests/test_long_goal_driver.py，真实共享stream_goal自动多轮/压缩/暂停/关闭/显式resume/失败后修复/验收/完成；费用、轮数、全部有效要求/Task身份/完整归档保留，产物变更失效。
- [x] 若原生暴露生产缺陷，先RED与根因再做最小修复；正确安全门问题只修夹具并记录。

## Task3：独立审查与发行验收

- [x] 一次fresh最有能力reviewer，范围SHA核对第十五轮wheel→本轮；Critical/Important一轮native RED/GREEN，Minor留记录。
- [x] Python全量、JS、diff、实际pip --target外部安装来源及字节核对、CLI smoke；更新总体审查记录。
- [x] 持久记录所有裁决，只删除本计划scratch；保持分支与原goal active。

## 最终验收

- 原生诊断RED1599≠40623后GREEN；采用/无收益/原始归档失败均与真实消息一致。相关87项通过19.083秒。
- 自动共享driver9轮19次模型调用、7次真实构建失败、压缩/暂停/重开SQLite/显式恢复/修复/自动验证/TaskGoal完成；费用按19次usage、2090实际tokens与9个唯一settled round核对，全部预留归零。交付物变化后unverified。
- 完整Python2011项，45项平台跳过，其余通过271.591秒；JS17及diff通过。日志 `/tmp/ignovate-goal-driver-final-full-python.log`、`/tmp/ignovate-goal-driver-final-js.log`。
- 实际pip --target外部安装409项通过84.051秒；27模块路径来自安装目录，149个发行Python文件与源码字节一致。wheelSHA `520ef38bf21ea1bbe41752206d18818db2b05abe6058e6092ff81266796734e1`；CLI help/JSONdoctor/依赖smoke通过。日志 `/tmp/ignovate-goal-driver-final-installed-native.log`、`/tmp/ignovate-goal-driver-final-wheel-smoke.log`。
- 原定gpt-6-astra因账户用量上限终止，没有复核结论；fresh gpt-6.1-sol完成首次实际独立复核，0Critical/0Important/0Minor，并自行从attested wheel重建本轮差异。没有第二次已完成审查，没有修复pass。

## 裁决与保留项

- 内部历史视图返回request/historyinfo/taskinfo三元组，只传递已采用同一快照的metadata，避免额外读取导致版本/正文不一致；代价是内部helper返回签名变化，唯一调用方已更新。
- 美元费用允许绝对1e-12浮点舍入，模型usage次数、实际2090tokens与9个settled round必须精确；代价是容许亚皮美元的舍入，不容许漏记或重记调用。
- 审查基线为SHA核对的第十五轮wheel，不是含15轮未提交代码与用户修改的HEAD；代价是本次独立审查不重复评估前十五轮，全量回归仍覆盖整体。
- astra无审查产出后采用fresh6.1-sol完成同一次独立审查；代价是本轮没有原定astra结论，实际得到备用模型的独立结论。
- 新发现的历史归档孤片单独进入并发安全的存储修复，仍属于原active目标；元数据修复不混入未经验证的删除逻辑。代价是该已知问题尚未修复，不能宣布整个项目目标完成。真实提供商行为仍无API证据。

Deferred minors：无。

finishing-a-development-branch：沿用既有用户不提交/推送安排，保留分支与工作树，不重复询问集成。仅删除本计划scratch；下一项是5次失败归档留下15分片10489105字节的历史发布恢复问题。
