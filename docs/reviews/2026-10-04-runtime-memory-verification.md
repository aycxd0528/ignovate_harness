# 记忆来源与有效性：本轮运行验证

日期：2026-10-04。所属记忆模块的验证与实际缺陷修复已完成；项目整体仍需总控核对下述集成事项。本报告记录当前运行证据，补充 2026-10-03 仅静态检查的交接；此前 497 项通过不作为本轮证据。

本阶段修改 `nailong/core/memory_knowledge.py`、`nailong/core/memory_context.py`，新增专属 `tests/test_managed_memory.py` 和本报告。其余两个所属 memory 模块完成只读核对与回归；没有改 agent/service/context/UI/tools、其他窗口的测试或全局计划，没有提交或推送。全部测试使用临时项目和本地/fake 模型；没有真实 API 调用。

## 实际发现与修复

`SourceValidator._source()` 原先对每个成功读取的来源固定扣除 `MAX_SOURCE_BYTES + 1`，即 1 MiB 加一次探测。目录包含 10 个几字节的独立来源时，前 8 项 observed，最后 2 项因预算过早耗尽变成 unverified。

现在先预留 `min(单文件上限, 剩余额度 - 1) + 1`，成功完成安全读取后只扣实际字节数加一字节探测；失败读取保留预留扣费，因为可能已经读取过字节再遇到版本冲突。同次操作缓存不重复读取或扣费。单文件 1 MiB、总操作 8 MiB 加 8 字节、最多 64 个独立来源授权的限制仍生效，没有扩大读取边界。

红绿验证：修正两处测试 fixture 后，新文件的初始 20 tests / 1 failure，唯一失败为 `test_catalog_small_dependencies_within_actual_io_budget_remain_observed`；上述生产修复后 20 tests / 0 failures。后续增加额度精确边界、失败扣费、缓存、竞争替换、配置和刷新测试，30 tests 全部通过。进一步复现动态 scope 缺陷时，33 tests / 10 failed subtest assertions；修复后 33 tests 全通过，增加构造接口、快照隔离及版本测试后最终 37 tests 全通过。

两处 fixture 修正分别是：构造 confirmed 缺少确认声明时，显式覆盖 helper 自动填入的 `confirmed_by`；验证零来源额度时先纯解析声明，避免在权限 spy 内调用另一套自带 validator 的 `knowledge()`。这些是测试准备错误，未据此改生产代码。

## 当前覆盖

| 行为 | 实际测试证据 |
| --- | --- |
| 来源变化/删除 | 正文读取和目录从 observed 转 stale，保留确认声明，fact_verified 始终 false |
| 不自动晋升 | 匹配文件摘要、目录读取及刷新后仍为 candidate；记忆文件字节不变 |
| Read 权限 | 持久 deny/ask 不读取来源字节；default/plan/acceptEdits 的 ask 均不能静默放行；会话 grant 允许，随后 deny 覆盖 |
| 权限刷新 | 撤销会话允许后，核心、目录、历史请求视图共同变为 unverified；原历史消息及 tool_call_id 保持不变 |
| 不适用/跨项目 | 未知任务范围、范围不匹配、project_id 不匹配；用户层文件来源必须绑定项目；另一项目即使文件同名同内容仍不适用 |
| 声明边界 | 重复 YAML key、merge 隐式覆盖、非合法 mapping key、缺失/超过 8 KiB 的 frontmatter；来源/路径/引用/适用路径超限，无来源授权调用 |
| 来源 I/O 边界 | 1 MiB 精确边界和超限、累计精确额度含空文件、零额度、缓存免重复收费、失败读取保守扣费、65 个来源只检查前 64 个 |
| 来源安全 | 保护路径/穿越/绝对路径先于授权被拒绝；符号链接不返回外部摘要；读取后文件被替换则 unverified；来源正文不出现在结果中 |
| 配置失败 | 持久权限形状错误、规则错误、未知字段不读取来源；错误 callback、字符串或整数 truthy 值不能允许来源 |
| 默认兼容 | 无 knowledge 的旧正文可读且不产生来源 I/O；loader 保留 callback/task_scope，拒绝超过 128 项或越界的 scope |
| 核心/目录/历史 | 来源变更后共同显示 stale；只修改请求副本；旧正文版本保留自己的来源声明 |
| 刷新与分页 | 刷新不绑定新文档版本；改版后 next_offset 请求返回 changed；下一运行时允许读新版本 |
| 动态任务范围 | 同一上下文 src→docs→None 时核心/目录/读取/历史共同更新；provider 异常或非法范围清空旧 scope 并抛错；同步回调、范围复制、独立 store 与改版拒绝拼页 |
| 统一预算 | 512 tokens 下仍保留 epistemic 状态及有效分页进度；固定摘要加实际 JSON 结果估算不超过预算 |

## 实际命令及结果

工作目录 `/Users/aycxd/Desktop/LLM_project`，使用当前 `.venv/bin/python`。

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_managed_memory.py' -v
```

最终输出 `Ran 37 tests ... OK`，退出码 0。

本轮最初还运行现有 `test_memory_context.py`、`test_context_memory.py` 基线：46 tests，0 failures，0 errors。日志 `/tmp/nailong-managed-memory-baseline.log`。

最终聚焦回归实际命令：

```sh
.venv/bin/python - <<'PY'
import io
import unittest
from pathlib import Path
from unittest.mock import patch
patterns = (
    'test_managed_memory.py', 'test_memory_context.py', 'test_context_memory.py',
    'test_context_runtime.py', 'test_context_budget.py', 'test_permissions.py',
    'test_managed_context.py',
)
loader = unittest.TestLoader()
suite = unittest.TestSuite(loader.discover('tests', pattern=pattern) for pattern in patterns)
output = io.StringIO()
with patch('socket.socket.connect', side_effect=AssertionError('offline verification forbids network connections')):
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
log = Path('/tmp/nailong-runtime-memory-focused.log')
log.write_text(output.getvalue(), encoding='utf-8')
print(f'patterns: {patterns}')
print(f'tests={result.testsRun} failures={len(result.failures)} errors={len(result.errors)} skipped={len(result.skipped)}')
print(f'log: {log}')
for test, failure in result.failures + result.errors:
    print(str(test))
    print(failure)
raise SystemExit(not result.wasSuccessful())
PY
```

结果 `tests=145 failures=0 errors=0 skipped=0`，退出码 0。包含 context 所属的实际 fake LangGraph 接线测试，而不只是四个 memory 模块的单元测试；运行时阻止 socket connect。

较早扩大运行用同一 runner，在当时的 27 项新测试基础上加入 `test_prompt_assembly.py`：151 tests / 4 failures / 0 errors，退出码 1。日志 `/tmp/nailong-runtime-memory-regression.log`。不能把该扩大运行写成全部通过。

4 个失败断言集中在两个方法：`test_profile_capabilities_match_real_registered_tools` 的 init/review/plan 三个 subtests，以及 `test_subagent_receives_readonly_capabilities_without_parent_memory`。实际列表比旧期望多 `read_tool_result`，其他列出的能力相同。未改这些非所属测试；由总控核对新增工具的注册与提示词契约后处理期望。本窗口没有用删除失败测试或减少保护项来修复。

## root 接线的只读核对

1. 当前 `agent.py` 的 `authorize_memory_source` 每次调用读取当前 `tool_execution_context.permission_engine` 和 `permission_mode`，只把 `Decision.ALLOW` 转成 True。`reload_memory()` 保留 user_file、callback、task_scope，并让 selector 和 snapshot 使用同一个 store。
2. `_build_runtime()` 从对应 thread 的 TaskStore snapshot 注入 scope；无任务或 completed 时传 None。memory 工具、外层过滤 middleware、ContextManager 共用一个 MemoryReadContext。
3. `ContextManagerMiddleware` 的 preflight 和实际 wrap 都调用 `refresh_for_request()`，定位编译时记忆槽，更新 system text/parts/memory_budget，再过滤历史请求视图。已有 fake graph 测试验证工具步后 stale 显示、无 system/history 持久注入；请求计量与保护窗口包括刷新后的正文。

动态 scope 缺陷已按总控确认修复记忆侧接口，root 接线由总控实施，记忆窗口未改 agent/context。

```python
MemoryReadContext(snapshot, *, task_scope_provider=None)
```

可选 provider 必须同步，每次返回当前 `list[str] | tuple[str, ...] | None`。`refresh_for_request()` 在 `snapshot.refresh_metadata()` 之前取值，按 MemoryStore 相同的项目相对安全路径规则校验，最多 128 项，支持根范围 `.`，复制成 tuple 后更新 **该 snapshot.store.task_scope**。不重新创建 store、不改预算、不重绑历史 versions/initial_context，也不凭 scope 直接改 validity；来源仍经过本轮授权及摘要检查。None 表示没有当前范围，带路径约束的知识适用性未知。

provider 抛错、返回异步结果、非法形状/越界/受保护路径时，先清空旧 store.task_scope，再抛安全 `ValueError('memory_task_scope_refresh_failed')`；不返回刷新视图、不调用 refresh_metadata，也不输出 provider 的原始异常。ContextManager 现有刷新异常处理会阻止该请求。无 provider 的旧 caller 保持原行为。

总控可在 `_build_runtime()` 给每个图捕获当前 thread 的 provider：

```python
def current_memory_scope(identity=thread_id):
    task = self.task_store.snapshot(identity) if identity else None
    return task['scope'] if task and task['lifecycle'] != 'completed' else None

memory_context = MemoryReadContext(snapshot, task_scope_provider=current_memory_scope)
```

factory 每次 reload_memory 建立独立 store；callback 不应写 factory 可变的 memory_store。新接口只更新自己的 snapshot.store，专项测试用同一项目的两个独立 store 验证 src/docs/None 不相互覆盖。src→docs→None 专项同时验证核心、目录、正文及历史请求视图、不增加 1024-token memorybudget、不提升 candidate、不写文件；scope 更新与文档改版同时发生时仍返回 changed，不能拼页。

## 未覆盖与结论边界

- 这是记忆部分及相关离线回归的当前证据，不是项目全量 suite 或真实提供商请求的通过证明。没有运行真实 API、UI/CLI 冒烟、跨进程崩溃恢复或其他操作系统的 reader 分支。
- 文件变化与读取竞争覆盖的是可控的符号链接/路径替换场景，没有把所有并发文件系统行为视为已穷尽。
- 未解析 session/user reference、未实现自动记忆提取、知识晋升或向量库。confirmed 仍只是文件中的用户确认声明；observed 仍只是来源摘要匹配，均不能当作测试通过或事实正确。
- 动态 scope 的记忆接口及独立快照行为已验证；root TaskStore provider 接线、同一 fake graph 内任务 completed/amend 的端到端回归由总控核对，不能仅凭接口测试宣称接线完成。
