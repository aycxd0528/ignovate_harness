# 真实 API 冒烟测试

日期：2026-10-02。提供商主机：`api.deepseek.com`；配置模型：`deepseek-flash`。

## 结果

真实 API 连通。首轮执行 9 个场景，经过校正冒烟脚本的结果解析后，7 个通过、2 个失败。子代理能读取文件并返回正确结果，但预算和实际用量记账存在回归，不能据此宣布全部功能正常。

| 场景 | 结果 | 可观察证据 |
| --- | --- | --- |
| 普通问答与流式输出 | 通过 | 返回 `SMOKE_OK`，收到 token/final/usage，3,797 tokens |
| 只读文件工具 | 通过 | 实际调用 read_file，返回临时文件中的随机标记，8,619 tokens |
| 父代理 task 调研与子代理记账 | 失败 | task 返回正确文件标记；SDK 回调记录子代理两次响应共 2,302 tokens，但会话事件只保存估算 37 tokens，estimated=true |
| 模型主动暂停目标后的结算 | 通过 | 实际调用 update_goal(paused)，持久状态 paused、round=1；已观察用量费用 $0.000649092 已保存 |
| 收到已完成调用用量后取消目标 | 通过 | CancelledError 继续传播；paused、round=1、唯一结算 ID，费用 $0.000470916 已保存 |
| 对话回退保留已发生用量 | 通过 | 删除本轮 4 条消息，token 总数仍为 8,619；事件标记 rewound |
| 子代理调用前 token 预算 | 失败 | 额度 1,000 tokens、子代理输出配置 64，仍发生一次实际模型调用，使用 1,119 tokens；请求输出上限为 512 |
| 实际 CLI 无头 stream-json | 通过 | 子进程退出码 0，NDJSON 可解析，返回 HEADLESS_OK、usage 与 session_id，5,066 tokens |
| 新运行时恢复会话与密钥检查 | 通过 | 重新打开运行时后恢复回答；测试目录未找到配置密钥，临时输入文件内容未改变 |

首轮获得 12 次完成的模型响应（含 CLI 的 1 次），已知总用量 45,142 tokens。按照当前项目配置价格及缓存命中估算，费用约 **$0.005267256**；此数字不是提供商账单核对结果。

第二轮复测首个问答请求在 100 秒超时，没有取得模型响应，后续 API 场景未执行。未将这次超时视为通过，也无法据此判断超时请求是否产生了提供商费用。

## 失败根因与当前回归

当前 [agent.py](/Users/aycxd/Desktop/LLM_project/agent.py:118) 已不含 `ModelAccountingMiddleware`：子代理只注册 SecretRedactionMiddleware，返回纯文本，没有采集各次调用的 usage；主代理的 middleware 初始列表也为空。因此 ReadOnlyTaskRunner 进入兼容估算路径，调用前 token 预算与美元预算没有进入模型执行链。

与上轮修复前镜像比较，可以确认上述预算中间件、用量 collector 和 ChildTaskResult 接线被移除。该文件修改时间为 03:16:01，早于本轮首个真实请求（03:24:39）。本轮没有改写生产代码，以免覆盖修复后新增的有意改动；已向用户询问是否恢复接线再测。

当前工作区重新运行 `.venv/bin/python -m unittest discover -s tests -v`：**272 个用例，265 个通过、7 个失败，23.663 秒**。失败集中于模型等待中的取消预留费用、父任务取消时子代理计量，以及子代理输出/输入额度和缺失 usage 后停止调用。上一轮的 272 个全通过记录对应当时的文件版本，不能作为当前工作区通过的证据。

## 测试方法与证据

- 直接调用当前项目的 ChatDeepSeek、AgentRuntimeFactory、AgentService 和共享目标流程，未替换为模拟模型。
- SDK 回调独立记录每次完成响应的 usage，和 Agent 事件用量交叉核对。
- 通过真实 CLI 子进程验证无头事件，避免只测试内部格式化函数。
- 使用临时项目、临时检查点和独立会话，只提供测试文件；未修改用户项目文件、密钥配置或生产会话。
- 冒烟脚本初版把字符串工具结果要求为 ok=true，并漏取 headless_result.data，造成两处断言偏差。现已校正；CLI 原始真实响应已重放解析通过，子代理则继续因实际计量与 SDK 不一致而失败。

证据文件：

- [校正后的首轮结果 JSON](/tmp/nailong-real-api-smoke-20261002/verified-result.json)。
- [首轮 SDK 原始回调结果](/tmp/nailong-real-api-smoke-20261002/initial/result.json)。
- [首轮真实 CLI NDJSON](/tmp/nailong-real-api-smoke-20261002/initial/headless-corrected.ndjson)。
- [复测超时日志](/tmp/nailong-real-api-smoke-20261002/run.log)。
- [当前完整回归日志](/tmp/nailong-real-api-smoke-20261002/current-full-suite.log)。
- [手动运行脚本](/tmp/nailong-real-api-smoke-20261002/run_smoke.py)（再次运行会发出真实 API 请求）。

本轮只验证 API 和执行链路，没有进行真实终端界面视觉检查。
