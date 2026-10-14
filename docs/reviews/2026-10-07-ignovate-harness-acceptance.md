# ignovate harness 启动与运行设置验收

日期：2026-10-07。实现位置：`/Users/aycxd/Desktop/LLM_project`，当前共享工作目录；未提交或修改 Git 索引。

## 交付内容

1. 产品展示名称改为 **ignovate harness**，安装包为 `ignovate-harness`，主命令为 `ignovate`。保留旧 `nailong` 入口、内部 Python 包及 `.nailong` 会话数据。
2. 首次交互启动显示欢迎页和模型配置指引。提供 API 地址、模型 ID、隐藏的 API Key、推理强度和本次权限；`ignovate --setup` 可以重开。取消不保存，也不创建 Agent。非 TTY 和无头模式不弹出引导。
3. 用户连接配置原子保存在 `~/.ignovate/config.json`（0600）。密钥是本地明文配置，表单不预填、不回显；已有密钥留空保留。配置优先于旧 `.env`，不写项目或会话日志。
4. `/reasoning` 与 `--reasoning-effort` 支持模型默认、关闭、低、高、最高（`default/none/low/high/max`）。已确认的 DeepSeek V4 模型映射到真实请求参数；未知模型仅开放默认。主、子代理共享设置，强度不改变工具调用轮数上限。
5. `/permissions` 在 Textual、inline 和 plain 中使用统一选择逻辑，实际执行模式和显示同步，切换项目保留本次模式。

| 权限 | 内部模式 | 行为 |
| --- | --- | --- |
| 请求批准 | `default` | 按现有审批规则处理修改和命令 |
| 帮我批准 | `acceptEdits` | 项目内文件修改自动批准，命令仍需批准 |
| 完全访问权限 | `bypassPermissions` | 任意文件路径与网络目标，跳过逐项审批 |

权限只在本次进程生效，不保存到用户配置；计划、审查和只读子代理仍执行各自的只读限制。

## 关键实现检查

- 模型与推理档位组合在保存前校验，不支持的组合不修改偏好。
- CLI 的显式权限与推理值在引导中锁定；引导确认的模型与推理选择在本次启动覆盖旧项目偏好。
- 配置损坏或符号链接不被静默覆盖；隐藏输入、取消、留空保留密钥均有回归用例。
- SDK 适配保留上一轮工具调用所需的 `reasoning_content` 请求字段；不把该字段写入本验收记录。
- 保留原来的 `agent.ChatDeepSeek` 构造测试接点，回归使用本地模拟，不意外调用真实 API。

参数依据：[DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 验证结果

### 本地回归

命令：`.venv/bin/python -m unittest discover -s tests -q`

结果：**995 tests，90.673s，OK**。完整输出：[`2026-10-07-ignovate-unittest.log`](2026-10-07-ignovate-unittest.log)。日志包含 Textual/asyncio 的慢任务诊断提示，最终测试结果为通过。

新增专项：品牌与入口、SDK 参数序列化、工具回复的推理字段、偏好覆盖与未知模型校验、三档权限同步、欢迎取消、密钥留空、0600 权限、损坏/符号链接拒绝、配置诊断、CLI 与无头引导边界。

### 真实 API

结果：**2/2 通过**。使用项目现有连接配置，在临时项目中执行；没有修改用户的欢迎完成标记。

| 推理设置 | 行为 | 模型调用数 | Token | 结果 |
| --- | --- | --- | --- | --- |
| `low` | 读取文件并回答校验标记 | 2 | 21,287 | 通过 |
| `none` | 不调用工具，直接回答校验标记 | 1 | 6,153 | 通过 |

脚本：[`2026-10-07-ignovate-api-smoke.py`](2026-10-07-ignovate-api-smoke.py)。结果：[`2026-10-07-ignovate-api-smoke.json`](2026-10-07-ignovate-api-smoke.json)。`high/max` 已验证请求序列化，本轮未做真实 API 调用。

### 界面和命令

- 已安装 `.venv/bin/ignovate` 及 `~/.local/bin/ignovate` 本机入口，从 `/tmp` 执行 `ignovate --help` 成功。
- Textual 欢迎与配置表单在 **100×32、80×24** 实际布局中核对：保存按钮位于视口，密钥输入在初始可见区域，窄屏表单可滚动。布局记录：[`2026-10-07-ignovate-render.json`](2026-10-07-ignovate-render.json)。
- 预览位于 `docs/previews/ignovate-welcome-*.svg` 和 `docs/previews/ignovate-setup-*.svg`。SVG 在 QuickLook 栅格化时出现中文字形重叠，不作为终端字体显示效果的验收依据；实际 Textual 控件布局与交互用例通过。

## 使用

在要处理的项目目录运行：

```bash
ignovate
```

重开欢迎与模型配置：

```bash
ignovate --setup
```

会话内选择：

```text
/reasoning
/permissions
```

启动参数示例：

```bash
ignovate --reasoning-effort high --permission-mode acceptEdits
ignovate --reasoning-effort max --dangerously-skip-permissions
```
