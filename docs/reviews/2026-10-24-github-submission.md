# 长任务可靠性修复：GitHub 提交记录

## 范围

用户于2026-10-24要求先审查未解决P0、列清单，再在当前修复完成后提交GitHub。当前已确认未修复P0为0；三项GUI P1均已修复并完成下方本地验收。清单见 `2026-10-24-open-p0-checklist.md`；完整历史与各轮裁决见 `2026-10-20-long-task-reliability.md`。

提交包含此前已授权的目标/费用与子任务恢复、完整历史与上下文容量、产物及输入身份、原生资源所有权与关闭、checkpoint无损压缩，以及当前GUI来源/任务身份/lazy控制修复和回归。保留用户原工作区IDE暂存与CSS修改，未纳入提交。不会将本次提交描述为整个长期审查目标已经完成。

## 整合

- 原工作区HEAD为 `6a53e7d`；远端旧分支已推进，PR3已合并。
- 原生隔离工作区 `/Users/aycxd/.codex/worktrees/9eec/LLM_project`，分支 `codex/long-task-reliability`，基于 `origin/main` 的 `4260136927f1ee8d1a8b2f3437e417fe083be3c0`。
- 三方冲突在CLI文档、空会话检查及对应测试解决，保留远端Windows修复与本次保护。未force push、未改写远端历史。
- 最终包使用main既有1.2.3版本；没有创建tag、发布release、合并PR、部署或迁移真实用户数据库。

## 最终候选

Wheel SHA256：`25e5d283e1c266f6c231285c869a16056a4401003a7a461027601f5b0caad919`。

159个生产Python与39个GUI静态资源逐字节匹配提交工作树。相对独立审查的34c5候选，额外生产变化仅为作者一次I1修复、远端已有process stdin DEVNULL、GUI Windows入口/runtime_sources及排除用户CSS；版本元数据随main保持1.2.3。独立报告没有重审这些整合变化，作者另做最终源码、实装、浏览器和发行门。

## 验收状态

全部最终命令已读取terminal exit0，未使用中间34c5/1214候选结果顶替。

| 最终范围 | 项数 | 平台skip | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2214 | 49 | 413.133s |
| Python3.13源码全量 | 2214 | 50 | 416.471s |
| 工作区外实装3.11 | 807 | 5 | 249.343s |
| 工作区外实装3.13 | 807 | 5 | 248.517s |

每版实装均核验48个关键模块来自安装目录。全部日志SHA256与浏览器结果记录在 `2026-10-24-github-validation.json`。独立审查0C/1I/0M；I1已由作者一次C/I pass修复、原生超时RED到完整lazy人工验收/反馈GREEN，无二次review。所有初始、considered和Declined裁决及代价已在GUI brief和主记录逐项处置。本地提交门至此通过；GitHub推送与PR提交身份以分支/PR历史为准。

已完成的最终候选检查：19项JS、desktop check含Windows打包脚本语法、真实wheel安装help/JSON doctor/dependency smoke、六组隔离Chrome浏览器。六组服务和浏览器均exit0、FD5到5、无新增线程、无遗留服务后代或自有临时目录；页面非本服务请求为0，浏览器全部关闭。冷名称解析FD单独对照，不宣称冷启动4到4。313安装77 distributions/5616 files RECORD与哈希无问题。

## 保留边界

当前最终执行环境为macOS、Python3.11/3.13和Chrome155离线原生fixture；不外推其他平台/loop/真实远端模型SLA。新压缩记录需要新版reader，升级应停旧实例；不自动重写旧库或VACUUM。未知外部链接/输入或失效产物仍fail closed，不删证据制造完成。其他具体限制和所有Declined的代价保持各轮原文。
