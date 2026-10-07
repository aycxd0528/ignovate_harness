# ignovate harness Implementation Plan

> **For agentic workers:** Use executing-plans to implement task-by-task in the current shared checkout. Steps use checkbox syntax.

**Goal:** 统一产品名称，完成首次配置指引及推理/权限设置。

**Architecture:** 复用现有连接配置、PreferenceStore 与统一 CommandActions。新增独立的启动配置存储和 Textual 引导，运行设置通过已有队列调用共享处理器。

**Tech Stack:** Python 3.11、Textual、LangChain DeepSeek、unittest。

**Spec:** `docs/superpowers/specs/2026-10-07-ignovate-harness-design.md`

## Global Constraints

- 产品名 `ignovate harness`；主命令 `ignovate`；保留旧入口和数据路径。
- 用户连接配置权限 0600；取消不写；不自动调用模型。
- 推理档位 `default/none/low/high/max`；未知模型只用默认。
- 完全访问仅本次启动；帮我批准仅自动批准文件修改。
- 复用共享工作目录，保留其他窗口改动与 Git 索引，不提交。

## Review Focus

- 配置文件损坏或符号链接：拒绝覆盖和泄漏。
- 非 TTY、无头与帮助命令：不启动欢迎或等待输入。
- API Key 留空、取消引导：保留原配置，不回显密钥。
- 模型不支持选中推理强度：保存和运行前拒绝。
- 权限切换或切换项目：实际执行模式与显示一致。

### Task 1: 品牌及推理配置

**Files:** `config.py`、`pyproject.toml`、`nailong/core/branding.py`、`nailong/core/reasoning.py`、`nailong/core/preferences.py`、`agent.py`、现有界面品牌文案；`tests/test_harness_settings.py`。

**Interfaces:** `reasoning_options(model)` 返回允许档位；`model_reasoning_kwargs(model, effort)` 返回实际 SDK 参数；`PreferenceStore` 支持 `reasoning_effort`。

- [x] 先写品牌、档位、未知模型和偏好校验断言，运行观察失败。
- [x] 实现共享常量、请求参数映射及主/子模型构造；统一界面品牌和安装入口。
- [x] 运行相关配置、提示词和界面回归。

### Task 2: 欢迎及首次模型配置

**Files:** `nailong/core/bootstrap.py`、`ui/setup.py`、`config.py`、`main.py`；`tests/test_harness_setup.py`。

**Interfaces:** `BootstrapStore` 读取/原子保存用户连接配置及完成标记；`run_setup` 返回本次设置或取消。启动在创建 Agent 前处理引导。

- [x] 先写 0600、取消无写入、密钥保留、损坏/链接拒绝、无头不弹窗断言，运行观察失败。
- [x] 实现欢迎、连接表单和设置选择，新增 `--setup`；保留原 `.env` 启动能力。
- [x] 运行引导/CLI 回归，以临时 HOME 核对首次、再次与取消流程。

### Task 3: 运行设置与收尾

**Files:** `ui/settings_flow.py`、`ui/settings_view.py`、`ui/actions.py`、`ui/commands.py`、`ui/app.py`、`main.py`、`tui.py`、`ui/presentation.py`、`README.md`；相关测试。

**Interfaces:** 共享选择回调 `setting_dialog(kind,current,options)`；运行时 `set_reasoning` 与 `AgentService.set_permission_mode`。

- [x] 先写命令切换及执行模式同步、只读保护断言，运行观察失败。
- [x] 接入三个 UI、启动参数、状态显示和项目切换；安装本地 `ignovate` 入口。
- [x] 执行全量本地回归、SDK 请求序列化核对、100×32/80×24 实际渲染并自审，保存报告。

## 自审与进度

接口冲突已核对：连接密钥独立于非秘密 PreferenceStore；推理配置在模型构造前校验；权限只保存于运行中服务和 UI，不写用户配置。按用户已授权的“后续计划自审后直接执行”实施。

验收：995 项本地回归通过（90.673s）；真实 API 冒烟 2/2 通过；新入口已安装，100×32 与 80×24 引导布局已核对。详见 `docs/reviews/2026-10-07-ignovate-harness-acceptance.md`。
