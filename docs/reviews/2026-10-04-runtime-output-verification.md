# Runtime output 验证与接线契约

状态：收集器实现与 29 项专项测试已完成；全量本地回归进行中。共享接线仍由总控整合。本轮不调用真实 API，不修改其他所有者文件。

## 收集器接口（总控可据此接线）

```python
from nailong.core.review_evidence import ReviewCoverageCollector

collector = ReviewCoverageCollector(
    selected_paths,                         # 明确枚举的项目内文件，不接受目录或通配范围
    fingerprint_before=independent_before,
    selection_complete=True,                # 枚举超限、截断、漏项时必须为 False
    skipped_paths=(),
    excluded_call_ids=previous_read_call_ids, # 本轮开始前历史 read_file 的 tool_call_id
)

# Context 变换之后、Accounting 之外的 middleware：只在 handler 成功返回后调用。
collector.consume_request(actual_request.messages)

result = collector.finish(
    fingerprint_after=independent_after,
    completed=True,                         # 正常结束 review 模型流程；取消/异常必须为 False
    task_revision=pinned_task_revision,
    evidence_id=runtime_review_evidence_id,
    artifact_ref=runtime_review_artifact_ref,
)
```

`result` 为普通 JSON-compatible dict，包含 `status`（reviewed/unverified）、`coverage`、`paths`、`files`、`reasons`、`evidence`。只有完整、当前、正常结束的静态审查输入与流程覆盖才生成 Evidence v1；缺口时 evidence 为 None。证据不证明代码无缺陷，不来源于模型“没问题”等总结。

调用方必须为每轮新建收集器，把既有历史 read_file 调用 ID 排除；旧 ToolMessage 即使出现在成功请求中，也不能冒充本轮当前文件。必须独立绑定并核对 task_id/revision 和前后真实输入指纹，保留选定范围、skip/截断事实。收集器不读取磁盘、不执行命令、不导入 TaskStore；不负责认证输入 dict 的来源。

总控只在结果含 evidence 且绑定身份仍匹配时，关联 `review:scope` 验收；不能用未核实的模型答案、工具的累积 `read_complete` 标记或退出 0 代替本模块计算的实际消费区间。分页的 `truncated=true` 只表示本页未到全文末尾，可以由后续同版本分页补齐；`result_truncated` 表示工具结果又被裁剪，不能接受为完整页。

## 实际实现与专项验证

新增 `nailong/core/review_evidence.py` 与 `tests/test_managed_delivery.py`；已有 delivery.py 本轮未改。模块只使用 json、pathlib、threading，保持纯内存，不保存原始正文或模型结论。

先编写专项测试并观察缺少收集器时的失败，再实现；首轮 21 项通过。增加真实 FileSession 的 Unicode 换行与单行字符分页、缺 offset/error status、text-block envelope、空范围与未知前指纹等场景后 27 项通过。脱敏扩张场景先观察到错误 reviewed，再修正，当前 29 项通过。

实际命令：`.venv/bin/python -m unittest discover -s tests -p test_managed_delivery.py -v`，结果 `Ran 29 tests ... OK`。其中 6 项覆盖 delivery 的 model/unknown/stale/partial、配置与目标分离、denied/failed、用户 waiver、纯函数及渲染边界；其余覆盖实际消费区间、去重、版本、范围和流程条件。

脱敏没有可靠的源字符映射，因此出现 `[密钥已隐藏]` 或明确 `content_redacted/content_transformed` 的分页不计入覆盖。原文中恰好含此标记也保守处理；不能因为替换长度碰巧一样就推断原文已完整消费。失败或裁剪页贡献零覆盖，后续全新成功调用可以重读；版本/同版本总长发生冲突则本轮不生成证据。普通分页 truncated 可被后续同版本分页补齐。

未授权真实 API 调用，所有模型边界的验证使用本地数据/已有 fake model；专项绿色不能代替完整运行时接线通过。

## Directory coverage 口径与当前剩余发现

只在运行时完整枚举允许范围、选定不超过 20 个明确文件、无跳过/截断、每个文件实际消费完整、前后独立输入未变且任务身份/版本未变时，总控才可将 Evidence.paths 从文件集合扩展到对应目录 scope（包括 `.`）。这表示定义范围内静态输入及流程覆盖，不表示代码无缺陷，也不涵盖受保护文件。collector 自身只返回具体文件，不做目录推断；缺口情况下不得扩展。

### [P1] 基线指纹目前晚于文件枚举，目录扩展存在遗漏窗口

当前 `_prepare_review` 先调用 `select_review_files`，再在创建 collector 时取得 `fingerprint_before`。具体触发：目录枚举完成后、获取这个基线之前，另一进程新增 `new.py`；之后项目保持不变。选定集合没有 new.py，基线却已经包含它；选定旧文件全部消费后，前后指纹相同，`_finish_review` 会把路径扩展为目录 scope，从而冒称完整目录输入覆盖。

建议：先独立取得指纹，再枚举文件，末尾与同一基线比较；或者在最终核对时重枚举并比较范围身份。不能只在枚举后的某个时刻取得指纹。TaskStore.record_review 仍须原子检查 task_id/revision，不能把范围判定交给模型。

## 先前六项审查的最新只读复核

- 第 1 项：服务已使用独立 latest_request；人工新输入追加历史并增加 revision，自动 Goal 轮次复用当前要求。静态调用链已修正。
- 第 2 项：验证进入协调临界区后重核 task_id/revision、配置、输入与权限，变化时 started=False。静态调用链已修正。
- 第 3 项：中断状态更新已移到 TaskStore.record_verification 的同身份/当前 revision 分支，旧任务不会被 unbound reconcile 污染。静态调用链已修正。
- 第 4 项：Goal 记录并复用缓存时都检查绑定 task_id/revision。静态调用链已修正。
- 第 5 项：新增 `/task bind` 由用户声明配置步骤及路径映射，要求类型一致、增加 revision；实际运行后按这个声明关联业务验收。review 的真实消费收集器已提供，原子录入和流程端到端结果待总控完成。目录枚举窗口见上项。
- 第 6 项：服务仅在 participating_task 存在时输出 delivery；headless 按参与任务或显式状态/验证请求刷新。静态调用链已修正。

以上“修正”仅指此次源文件静态追踪，不据此宣称共享代码测试全部通过。

## 全量回归

已启动 `.venv/bin/python -m unittest discover -s tests`，日志 `/tmp/nailong-output-suite-20261004.log`。工作区并行变化期间已有共享组件失败，最终数量和名称将在结束后补充；本轮不抢改共享文件或其他测试。
