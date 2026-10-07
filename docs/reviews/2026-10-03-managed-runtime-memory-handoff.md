# Task 3：记忆来源与有效性交接

状态：所属记忆模块已实施，等待总控接入请求刷新、会话 Read 权限及任务 scope；本轮未做运行验证。

依据：`docs/superpowers/specs/2026-10-03-managed-task-runtime-design.md` 与对应实施计划。用户要求按所有权并行，保留现有改动，不提交/推送。本轮明确不新增或运行测试、不调用真实 API。此限制优先于技能的 TDD/测试完成步骤；本交接只记录代码自审和 AST 静态核对，不能沿用上一轮 497 项通过来证明这些新增代码。

## 所属修改

- `nailong/core/memory_knowledge.py`：新增有界声明解析、作用域判定、来源授权与指纹校验。
- `nailong/core/memory.py`：目录、分页、核心快照增加来源/有效性字段；只读字节读取复用描述符路径保护并检查读取期间及路径替换版本变化。
- `nailong/core/memory_selection.py`：核心与标题索引带相同知识状态，无效声明不加载正文。
- `nailong/core/memory_context.py`：目录、读取和历史请求视图保留有效性；可显式刷新固定摘要；预算压缩不能去掉重要知识状态再保留正文。
- 本交接文件。

未修改 agent.py、service、registry/tools.py、ui/*、context_runtime.py、测试文件、全局计划或 README。

## 可选 frontmatter 契约

```yaml
---
description: 构建约定及来源
knowledge:
  type: project_fact            # preference | project_fact | experience
  state: candidate              # 默认 candidate；确认声明才可写 confirmed
  origin: runtime               # user | runtime | model | unknown，默认 unknown
  # confirmed_by: user          # state=confirmed 时必需；只是文件中的确认声明
  applicability:
    scope: project              # 必须等于文档存储层 user/project/local
    paths: [src]                # 可选，最多16个项目相对路径，按目录包含关系匹配
    # project_id: "<64 hex>"    # MemoryStore.project_id；用户层文件来源必须绑定该ID
  sources:
    - kind: file
      path: src/build.py
      sha256: "<实际原始文件字节的64位十六进制SHA-256>"
    - kind: reference
      ref: session:thread-id#event:event-id
---
# 构建约定
正文……
```

上例 digest 是说明性占位符，直接复制会被判为无效，必须换成实际已观察文件摘要。新模块不计算并保存候选文档、不自动写入/晋升、不解析远端引用，也未引入向量库。

无 `knowledge` 的旧文档保持正文、原有 ID/分页和编辑 API；新增 `knowledge_state=legacy`、`validity=unverified`。只有 description 的原 frontmatter 仍兼容。声明字段、类型、来源路径和摘要严格校验；重复 YAML 键、混合/未知 knowledge 字段、无边界或过长来源明确无效。YAML 合并引起冲突也按重复键处理，避免隐式覆盖来源。

引用只接受 `session:<id>#event:<id>` 或 `user:<id>`，id 为1–80个 ASCII 字母、数字、下划线或连字符；不接受 URL、认证参数或自由文本作为引用，不联网解析。引用自身保持 unverified。

## 结果字段与语义

`catalog().documents[*]`、`read_document()`、`read_section()`、核心 reports 增加：

| 字段 | 语义 |
| --- | --- |
| metadata_status | absent / valid / invalid |
| knowledge_type | preference / project_fact / experience，旧文档为 null |
| knowledge_state | legacy / candidate / confirmed；不会因来源匹配自动晋升 |
| origin, confirmed_by | 文件中的来源/确认声明；confirmed_by=user 不是独立审批证据 |
| confirmation_is_declaration | confirmed 仅来自声明，不能当作实际用户审批记录 |
| validity | observed / unverified / stale |
| applicable | true / false / null；null 表示缺少任务 scope，false 表示项目或路径不适用 |
| applicability | scope、paths、project_id 的有界声明 |
| sources | file 的预期/current SHA-256 与状态/安全原因码；reference 保持未解析 |
| knowledge_issues | 有界的安全原因码，不输出原始异常、来源正文或密钥 |
| fact_verified | 始终 false：来源摘要匹配不能证明正文语义、测试结果或验收覆盖 |
| evidence_basis | file_sha256 / none，不能提升为 test/build/run evidence |

observed 表示声明的文件依赖均匹配且当前作用域适用；文件删除或摘要变化为 stale；没有文件来源、只含未解析引用、Read 拒绝/等待审批、任务范围未知、读取变化/超限则 unverified。确认状态与有效性互相独立；confirmed 也可以 stale。

无效声明在 catalog 为 status=error；read_document 返回 ok=false、memory_metadata_invalid，不返回正文。`read(scope)`/编辑保留取回原文以便用户修正的旧行为，不应用该正文到核心。

## 安全与边界

来源最多8项、路径最多512字符、引用最多180字符、适用路径最多16项。单来源最多1 MiB，单操作最坏读取8 MiB加8字节探测，最多64个独立来源权限检查；目录和历史视图的同次检查共享缓存，下一次操作不复用旧有效性。

来源只访问当前 project_root 的相对普通文件；禁止绝对路径、穿越、反斜线、符号链接与现有保护路径，同时拒绝 .ssh、私钥文件、credentials.json、secrets.json、.pem/.key。硬边界先于授权回调，ALLOW 无法放行这些路径。当前模型 API key 会在记忆内容中隐藏，包含该 key 的来源路径/引用和主题 ID 被拒绝或隐藏。来源正文只供哈希，绝不进入目录、提示词或工具结果。

默认来源授权复用持久 PermissionEngine 的 Read 判断，ASK/DENY 都不读取。权限配置格式/规则无效、加载环境缺少授权依赖或回调失败时返回未验证，不静默放行。总控应注入当前会话/模式的同步 ALLOW-only 回调，覆盖会话规则。

用户层 knowledge 如果包含项目文件来源，必须给 applicability.project_id，否则判为无效，防止用户知识在不同项目靠同名路径误匹配。

## 实际接口与总控接线

现有必需签名不变。新增可选参数与方法：

```python
MemoryStore(project_root, *, user_file=None, api_key='',
            source_authorizer=None, task_scope=None)
load_project_memory(project_root, *, user_file=None, api_key='', max_chars=40000,
                    source_authorizer=None, task_scope=None) -> MemorySnapshot
MemoryStore.project_id -> str
MemorySnapshot.refresh_metadata() -> str
MemoryReadContext.refresh_for_request() -> dict
# dict: initial_context, context, report
```

`source_authorizer(relative_path) -> bool` 必须同步，只返回实际 `Decision.ALLOW` 对应的 True。来源读取不代用户弹审批，也不允许用 truthy 的 `"ask"` 代替 True。`task_scope` 为最多128项项目相对路径，`.` 表示当前项目；缺省 None 时带路径限制的知识保持适用性未知。

初始化/重载/每轮 snapshot 时保留这两个参数，例如：

```python
memory_store = MemoryStore(
    settings.project_root, api_key=settings.api_key,
    source_authorizer=lambda relative: permission_engine.decide_action(
        'read_file', {'path': relative}, profile='chat', mode=permission_mode,
    ).decision == Decision.ALLOW,
    task_scope=task_snapshot['scope'] if task_snapshot else None,
)
```

使用原有 selector 创建 MemorySnapshot 时必须使用同一个 memory_store。重载不要重新构造一个丢掉 callback/scope 的 store。当前 factory 的 load_project_memory 与 core selector 结构由总控统一保留和接线。

每步模型调用前，请总控同步刷新固定记忆及 allowance，不能只改 allowance 留旧 system text：

```python
view = memory_context.refresh_for_request()
system = request.system_message
if memory_context.initial_context:
    system = system.model_copy(update={
        'content': system.content.replace(memory_context.initial_context, view['context'], 1)
    })
request = request.override(
    system_message=system,
    messages=memory_context.filter_messages(request.messages),
)
```

`initial_context` 在同一个 runtime 不变，避免第二次刷新无法匹配编译时的原 system prompt。refresh 不重绑已读文档版本、不换正文、不自动确认；文档自身变化把旧核心标为 stale，显式读取仍按原有 changed 契约拒绝拼接版本。来源文件变化只更新有效性，下一轮完整 snapshot 再读取新文档。

ContextManagerMiddleware 的 preflight/system parts/report 必须同时使用 view 的固定正文和 report，保留其现有 request_filter/requested_output/memory_budget 参数。若暂不接显式 hook，旧 caller 的固定文本/预算仍保持一致，不会因为历史过滤自动调整额度；工具读取与历史结果会更新有效性，但固定核心在本轮内仍是建立时视图。这是待总控完成的必需整合点。

`filter_messages()` 会在请求副本中重新核对历史 memory_read/read_memory/memory_list 的元数据；过期版本只重新标为 stale，保留原正文、原声明来源与 tool IDs，不把新文档来源附到旧正文，也不改持久记录。预算缩短 sources/applicability 等细节时设置 metadata_truncated，保留 metadata_status、knowledge_state/type、validity、applicable、fact_verified；装不下安全状态则返回预算错误且不保留正文。

不需要新增必需工具参数。请总控更新工具说明和 `/memory show/list/read`、`/context` 显示上述状态；`已加载` 是加载状态，不能显示成“已验证事实”。Delivery 不得把 observed、confirmed 或 source SHA 相等转换为 verified/test passed。

## 自审与未运行验证

静态自审：解析前完整声明校验；无效/受保护路径零来源读取；读取前 Read ALLOW 判断；描述符和文件版本检查；同次来源 I/O 上限；默认兼容；来源字节不序列化；候选不晋升；目录/核心/读取状态字段一致；历史只改请求副本；预算元数据精简保留有效性。

四个修改模块使用 `ast.parse` 做语法核对，未 import 或执行新模块业务方法；语法检查结果仅证明 Python 可解析。没有新增/运行任何测试，也没有真实 API 调用。没有运行 CLI 或编译/启动应用作为变相测试。

待后续明确测试授权：旧无元数据兼容；observed→stale 来源变化/删除；Read deny/ask 与会话规则；protected/symlink/并发替换；候选/确认声明；重复键/嵌套 alias/超限；项目 ID 和 task scope；目录/正文/核心/历史一致；512/4000 预算及大来源细节；默认缺依赖 doctor；总控接线的 preflight 与真实模型请求使用同一刷新视图。未运行这些验证，不能宣称行为或集成已验证。
