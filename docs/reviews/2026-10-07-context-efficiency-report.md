# 上下文效率优化交付报告

日期：2026-10-07。范围：本地上下文核算、工具结果提前归档、自动压缩重试控制。

首轮优化已落地。每次调用前仍核算实际完整请求并执行硬预算检查；重复文本统计和重复归档可以复用，同一输入的无效压缩不再反复执行。没有引入额外模型调用。

## 实际改动

| 文件 | 结果 |
| --- | --- |
| `nailong/core/context.py` | ASCII 统计使用快速路径；增加按实际内容和当前工具 schema 索引的有界缓存；分类合并统计后统一取整，再应用当前校准系数。 |
| `nailong/core/context_runtime.py` | 预检查与最终检查共享运行时缓存；复用未变化工具结果的提前归档；记录无效压缩输入；增加分阶段计时和缓存计数。 |
| `nailong/core/compact.py` | 复用快速字符统计，保持原有压缩收益计算口径。 |
| `tests/test_context_efficiency.py` | 新增 18 项回归测试，覆盖缓存失效、归档恢复、预算拒绝、压缩重试和兼容边界。 |
| `scripts/benchmark_context.py` | 增加可复用的离线基准，支持载入修改前源码比较完整报表。 |
| `README.md` | 补充缓存行为、压缩策略、指标含义及基准运行说明。 |

文本缓存、schema 缓存分别最多 512 条、2,000,000 字符；提前归档复用缓存最多 256 条、2,000,000 字符。字符上限不等同于 Python 内存字节上限。缓存仅属于当前 runtime，不跨进程持久化。

自动软压缩以触发阈值的 80% 为目标，保留最近四轮；硬超限时可进一步缩小保留轮数。20% 最低压缩收益门槛保持原样。目标不保证总能达到，固定约束、当前用户输入和未完成工具调用继续受保护。

## 安全与连续性边界

- 消息 ID、任务需求版本相同，内容仍可能改变：缓存依据实际文本、参数和 schema，不依据 ID 或需求版本假定内容不变。
- 记忆、任务投影和配置继续在调用边界刷新。最终检查依据过滤后的实际工具集合、系统内容、消息和输出预算重新核算。
- 提前归档复用只减少重复保存。真正清理正文前仍通过真实归档接口检查磁盘；归档被删除可重新保存，归档损坏则拒绝清理并保留原文。
- 无效压缩的重试指纹包含完整请求视图、工具、策略和校准系数。输入变化后重新尝试；输入不变时跳过重复压缩，但仍执行硬预算拒绝。
- 硬超限且临时压缩仍失败时，指纹对应原始输入，因为该次失败不会返回 checkpoint 更新。
- 归档复用区分 JSON 的 `1`、`1.0` 和 `true`。不能深拷贝的工具附带对象可以正常归档，只放弃可选缓存；不调用未知对象的自定义相等运算。
- 动态覆盖 `tool_call_schema` 的工具、任意 callable 等没有稳定版本约定时，每次重新转换工具定义，避免复用陈旧 schema。

L1/L2 仍采用现有确定性本地压缩。这次没有新增语义摘要模型，也没有改变任务目标、证据或停止机制的数据结构。

## 离线基准结果

Python 3.11.15；每组预热 3 次、测量 21 次，取中位数。旧实现来自本轮修改前保存的三个核心文件，而非脏工作区中的 Git HEAD。基准核对旧实现、新实现和缓存路径的完整核算结果，所有比较均一致。

| 场景 | 修改前 | 修改后，不使用缓存 | 修改后，缓存预热 |
| --- | ---: | ---: | ---: |
| 10 万 ASCII 字符，100 条消息、20 个工具定义 | 1.754 ms | 0.327 ms | 0.343 ms |
| 60 万 ASCII 字符，同上 | 9.021 ms | 0.323 ms | 0.344 ms |
| 400 万 ASCII 字符，同上 | 57.261 ms | 0.331 ms | 0.375 ms |
| 60 万中文字符，同上 | 12.019 ms | 11.449 ms | 0.344 ms |

ASCII 路径本身很便宜，缓存的管理开销在这些样本里略高于直接统计。中文未命中缓存时仍需扫描内容；预热结果不能当作首次调用耗时。

包含 50 条旧工具结果、每次新增一条用户消息的“预检查＋最终请求核算”中位数：**14.150 ms → 1.456 ms，下降约 89.7%**。最后一次预检查复用了 50 条提前归档记录，新增归档数为 0。

该综合样本没有启用记忆刷新和任务投影，也没有会话日志存储、真实模型响应或工具执行。这些数据说明局部处理成本下降，不能推算整个 Agent 任务或模型响应提速。中文首次统计、真实文件刷新、超大生产负载仍需实际会话观测。

## 验证结果与证据

- 新增效率回归测试：18 项通过。
- 上下文、任务投影、记忆、归档等相关测试：**207 项通过，1.573 秒**。
- 最终全量测试：**995 项通过，85.606 秒**。该次测试拒绝非本地 socket 连接，日志记录被拦截的非本地连接尝试数为 0。
- 独立只读复审已完成，没有剩余需要先修的问题。复审发现的硬失败指纹、JSON 类型比较、不可深拷贝附带对象问题均已修复并增加回归测试。
- 测试使用隔离 pycache 路径导入当前源码，避免共享工作区旧字节码干扰。早期日志曾出现 5 项界面名称断言失败，并在修改前核心源码下复现；最终当前工作区全量验证已通过。该界面变动不属于本轮核心优化。

证据文件：

- [最终全量测试日志](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-07-context-efficiency-unittest-final.log)
- [最终相关测试日志](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-07-context-efficiency-focused-final.log)
- [基准原始 JSON](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-07-context-efficiency-benchmark.json)
- [核心源码改动补丁](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-07-context-efficiency.patch)
- [修改前后源码 SHA-256 与环境](/Users/aycxd/Desktop/LLM_project/docs/reviews/2026-10-07-context-efficiency-source-manifest.json)

相关测试可复跑：

```sh
PYTHONPATH=tests .venv/bin/python -B -X pycache_prefix=/tmp/nailong-context-clean-import-20261007 -m unittest test_context_efficiency test_context_budget test_context_runtime test_managed_context test_task_projection test_history_archive test_context_compaction test_context_memory test_memory_context test_managed_memory test_workflow_sessions_context -q
```

基准可运行 `.venv/bin/python scripts/benchmark_context.py --samples 21`；要再次比较旧实现，使用 `--baseline-dir` 提供修改前的三个核心文件。保存的补丁和源码哈希可用于确认比较版本。

## 后续优化应依据什么

`context_request.performance` 现在记录 `preflight`、`request`、运行时累计缓存计数；阶段包含记忆刷新、任务投影、策略读取、核算、归档、请求视图和压缩。失败时写入 `context_performance` 事件。计时不包含提供商请求及该报表事件写入；压缩计时包含内部核算，不能简单累加所有阶段。

下一步先用真实复杂任务收集各阶段分布、缓存命中率、压缩次数和恢复正确性。如果记忆或任务投影成为主要成本，再设计带内容版本校验的复用。当前结果不足以证明可以取消这两处刷新，也不足以证明需要直接引入模型摘要。

当前未验证真实提供商 token 数、极端生产负载和多个 OS 线程共享同一 middleware 的情况。缓存适用于现有每个 runtime 独立、正常异步调用的路径。

此前设计参考：[Claude Code 上下文处理](https://code.claude.com/docs/en/how-claude-code-works#when-context-fills-up)、[Codex 配置中的自动压缩阈值](https://learn.chatgpt.com/docs/config-file/config-reference)、[OpenAI Responses compaction](https://developers.openai.com/api/docs/guides/compaction)。本轮借鉴的是按阈值压缩与减少无效重复工作；没有将提供商专用压缩状态移植为 DeepSeek 的通用摘要格式。
