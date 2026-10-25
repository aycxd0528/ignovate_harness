# MCP 请求取消与连接清理隔离

> **For agentic workers:** Use executing-plans inline; retain prior fixes and user changes; do not commit or push.

**Goal:** 未开始的 MCP 请求取消或超时只撤回自身；已开始调用取消后等待唯一连接 owner 清理，重复控制不打断 SDK teardown；回执准确区分 started，不自动重放。

**Evidence:** 原生 stdio fixture 中 pause 正在执行，echo 确认排队后仅取消 echo，pause 却收到 mcp_disconnected、started=true，连接关闭。根因是 call_tool 的取消/超时路径无条件 _close。

**Design:** 队列保存请求身份和开始/完成状态，在 owner 调用 SDK 前同步登记开始。撤回排队项同时释放容量；活动请求关闭其捕获的原连接。关闭时立即停止 admission，同一连接只向 SDK owner 发出一次取消；每个等待者都等完整清理后传播自己的取消。SDK 上下文仍在同一 Task 进入和退出。现有工具 schema、审批与不自动重试契约保留。

**Scope:** 本地真实 MCP stdio 和 HTTP、重复取消、超时、重连及队列容量；不调用模型 API。保留 .idea 与 GUI CSS，不提交、不推送。整体项目目标仍需继续审查。

## 1. 请求隔离

- [x] 原生门闩服务：排队取消、排队超时、容量释放与无延迟副作用 RED。
- [x] 请求身份、原子 admission、完成竞态及撤回；已执行取消/超时兼容。

## 2. 清理所有权

- [x] 真正 stdio SDK 上下文中门控 teardown，复现并发关闭重复取消和调用方取消传播。
- [x] 关闭屏障、唯一 owner 取消、等待者取消后继续清理；核对多连接退出与重连。
- [x] 复核补修独立 disconnect 等锁/多控制者、原生 mcp_admitted 证据保留、SDK 自身取消在 teardown 前关闭 admission；对应新增用例均先 RED。
- [x] requesting-code-review 独立复核，修复所有 Critical/Important；最终独立 41 项及 7 个额外原生探针通过。

## 3. 验证与记录

- [x] MCP/执行门/原生队列定向、完整 Python/JS、diff check、外部 wheel 模块集成。
- [x] 更新 PROJECT、CLI_REFERENCE 和整体审查证据；保留未覆盖边界。

最终证据：Python 1864 项，45 跳过，其余通过（207.897 秒）；JS 17 项；相关组及实际安装模块各 93 项，14 平台跳过，其余通过，十个运行文件字节核对一致。wheel SHA-256 c554df233dd6f0a609f0f3b2ad29cf1f615a583f4f40cbd1dcf8fba91102fa73。独立复核三项 Important 均先 RED 后修复，最终独立 41 项和 7 个原生 probe 通过。整体目标仍 active，plain 用户停止回执、真实输入范围和长期交付链路待续；未运行真实 API、第三方 MCP、Windows 实机或长期 soak。不提交、不推送。
