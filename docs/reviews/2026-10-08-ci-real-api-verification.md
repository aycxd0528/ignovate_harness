# CI 与真实模型 API 验证记录

日期：2026-10-08。补充此前的项目审查：启用远端 CI，并用已配置的真实模型验证关键运行链路。

## 远端 CI

已为 GitHub CLI 完成 `workflow` 授权，工作流在 `.github/workflows/tests.yml`，支持 push、pull request 与手动触发。运行完整离线测试、构建 wheel，并从源码目录之外加载发行包做 smoke；显式安装 ripgrep，保证正则搜索回归实际执行。

首轮 [GitHub Actions 运行](https://github.com/aycxd0528/ignovate_harness/actions/runs/37718961729) 对提交 `0d2693c48fd2e41681346505a29453de1dc4fb16` 验证通过：

| 环境 | 完整测试 | wheel 与安装包检查 |
| --- | --- | --- |
| Linux / Python 3.11 | 1043 项，146.016 秒，OK | 构建成功，smoke 通过 |
| Linux / Python 3.13 | 1043 项，119.380 秒，OK | 构建成功，smoke 通过 |

首轮出现旧 Actions 的 Node 20 弃用警告，因此后续使用已核对官方发布的 `actions/checkout@v7.0.1` 和 `actions/setup-python@v7.0.0`，固定 Ubuntu 24.04，并设置 15 分钟任务上限。最新提交的实际检查结果以对应 GitHub Actions 记录为准。

## 真实模型验证

使用本机配置的 `api.deepseek.com` / `deepseek-flash`，真实 SDK 请求，没有假模型。测试项目、SQLite 会话和验证命令都位于临时目录。运行时仍会读取本机已有的用户级记忆、技能与偏好，结果反映本机配置。未向 CI 配置模型密钥或自动触发付费模型请求。

可重复执行：

```bash
python scripts/smoke_real_api.py --output /tmp/ignovate-live-api.json
python scripts/smoke_real_api.py --only headless_json --output /tmp/ignovate-headless.json
```

managed 场景共用本地估价的 0.15 美元预算，每轮最多 1–5 次模型调用，每个 managed 请求最多输出 1000 tokens，关闭 SDK 自动重试，单请求超时 30 秒、单场景超时 90 秒。无头场景另限 1 个模型轮次，默认客户端仍可能重试 HTTP 请求，其费用不计入 managed 预算。

| 场景 | 实际证据 |
| --- | --- |
| 无工具回答 | 返回预期 OK，1 次模型调用，无工具调用 |
| 带 low 推理的文件读取 | read_file 读取临时 identifier，2 次模型调用，返回正确标记 |
| 编辑及绑定验证 | 读取并批准修改 calc.py，真实命令确认 add(2,3)==5，交付状态 verified |
| 只读审查 | 读取 calc.py 全文，交付状态 reviewed |
| 审批拒绝 | 写入被拒绝，目标文件未创建 |
| 无头 JSON | 退出码 0、状态 success、预期 OK、无工具，独立调用及定向补测通过 |
| 当前 API Key 未落盘 | 检查临时目录文件与脱敏报告，均未包含本次已配置的 API Key |

首次无头场景的答案期望检查未通过，当时退出码为 0、结构状态为 success；随后独立调用与脚本定向补测均返回 OK。没有修改生产代码，也不将该首次失败描述为根因已确认并修复。报告同时保留原始失败、重试与最近结果，模型遵从度不以一次成功保证。

所有已记录请求的提供商 usage 共 51,639 tokens。managed 部分费用按本地单价估算为 0.00428852 美元，不包含三次无头请求；实际账单以提供商记录为准。原始脱敏元数据见 [JSON 报告](2026-10-08-real-api-smoke.json)。报告不保存最终回答原文、推理正文或凭据。

## 范围

验证覆盖所配置模型的这些具体链路，第三方 MCP、Windows 实机、不同终端剪贴板以及所有可能的模型输出仍需各自验证。远端 README 的新增提交已保留；并行任务的 Token Weather 改动和既有 IDE 暂存文件不属于本次提交范围。
