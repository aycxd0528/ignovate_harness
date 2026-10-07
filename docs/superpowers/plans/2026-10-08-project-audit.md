# ignovate harness 项目审查与发布计划

> **For agentic workers:** 使用 systematic-debugging、test-driven-development、verification-before-completion 逐项执行；最终使用 requesting-code-review 做独立审查。

**Goal:** 审查现有项目，修复真实缺陷和发布问题，通过本地验证后更新指定飞书说明文档，并提交至用户指定 GitHub 仓库。

**Architecture:** 保持当前 Python CLI、LangGraph 运行时及三种终端界面的结构；只在复现问题对应的边界修复。源码检查与实际 wheel 安装验证共同作为发布依据。

**Tech Stack:** Python >=3.11、unittest、setuptools、LangChain/LangGraph、Textual、MCP。

**Spec:** 用户 2026-10-08 的审查、修复、说明文档及仓库提交要求；现有 README.md 的产品行为约定。

## Global Constraints

- 保持 ignovate 与 nailong 入口及现有数据格式兼容。
- 不提交密钥、用户配置、会话数据、IDE 配置或私人评估产物。
- 不强制推送；保留远端已有 MIT LICENSE 与本地历史。
- 用户已授权修复、飞书文档写入与仓库提交，按此范围连续执行。

## Review Focus

- 源码可运行而 wheel 缺少文件：安装后的 doctor 必须返回结构化报告。
- 非法连接参数：doctor 和实际启动的校验规则必须一致，不能泄漏密钥。
- 权限、只读模式和文件版本检查：有副作用行为不能越过对应限制。
- 目标恢复和交付：没有证据时不能报告任务完成。
- MCP 生命周期和 UI 切换：失败、取消与恢复应保留正确状态。

## Task 1: 基线审查

- [x] 检查本地状态、远端仓库和飞书目标。
- [x] 运行现有完整测试：1018 tests，99.304s，OK。
- [x] 实际构建 wheel 并复现安装后的 doctor 崩溃。
- [x] 完成权限、运行时、MCP/UI 的独立只读审查并整合证据。

## Task 2: 诊断与发布修复

**Files:** nailong/core/diagnostics.py、tests/test_workflow_diagnostics.py、pyproject.toml、.gitignore。
**Interfaces:** 保持 diagnose(project_root, *, env=None) 返回报告格式。

- [x] 补充已安装环境缺少源码 requirements.txt 的失败测试。
- [x] 诊断优先读取可用源码声明，安装环境读取 distribution 的 Requires-Dist。
- [x] 为非法连接参数补充回归测试，复用 validate_connection。
- [x] 定向测试、实际 wheel smoke 验证和完整测试通过。
- [x] 整理发布元数据、CI 和忽略规则。

## Task 3: 审查发现修复

- [x] 对每项高/中严重性发现记录触发条件和根因。
- [x] 先运行失败回归，再做最小修复并验证。
- [x] 记录低严重性建议和未验证边界。

## Task 4: 文档与交付

**Files:** docs/PROJECT.md、docs/reviews/2026-10-08-project-audit.md、README.md。

- [x] 生成项目说明：定位、架构、安装、配置、使用、权限、开发、验证和已知限制。
- [x] 完成独立最终审查、全量测试与 wheel 安装 smoke：1043 tests，99.744s，OK。
- [x] 写入指定飞书空白文档，并 fetch 检查内容：revision 5，全文一致。
- [x] 精确选择提交文件并检查敏感内容；与远端现有 MIT LICENSE 内容一致。

最终交付需推送并核对远端提交，保留两侧历史；实际提交与交付链接由本次任务的最终回复确认。当前凭据缺少 workflow 写权限，CI 模板保留在 docs/ci/，远端 CI 尚未启用。
