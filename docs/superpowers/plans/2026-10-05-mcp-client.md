# MCP Client Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Connect external MCP tools through `/mcp` and the existing approval/execution flow.

**Architecture:** Project-scoped configuration, an owned asyncio connection task per server, and dynamic ExecutionTool adapters. Shared command actions manage connections; AgentRuntimeFactory owns shutdown and registers only chat tools.

**Tech Stack:** Python 3.11+, MCP Python SDK stable v1, LangChain, asyncio, unittest.

**Spec:** `docs/superpowers/specs/2026-10-05-mcp-client-design.md`

## Global Constraints

- `.nailong/mcp.json`, `mcpServers`, atomic 0600 writes, no shell, no startup auto-connect.
- `mcp__<server>__<tool>`; chat only; remote annotations cannot bypass ASK.
- SDK context enter/exit in one task; bounded queues and request timeouts; close on cancellation, no call replay.
- Credentials via `${ENV_NAME}` references only; no DeepSeek credential inheritance.
- Preserve existing uncommitted project files. Work in the current checkout because almost all executable source is untracked and a worktree would omit it. Do not stage or commit unrelated files.

## Review Focus

- Remote `path` arguments must work without weakening native file boundaries.
- Tool-name collisions and nested JSON Schema must preserve original remote call semantics.
- Service cancellation must leave no orphan subprocess or stale tools.
- Missing credentials and malformed configuration must not leak values or overwrite files.
- Agent schemas and context accounting must include the same connected tools.

### Task 1: Configuration

**Files:** create `nailong/mcp/config.py`, `tests/test_mcp_config.py`.
**Interfaces:** `MCPConfigStore(root).list_servers()/add(name, definition)/remove(name)`; validated definitions and `${ENV_NAME}` resolver.

- [x] Write tests for persistence, invalid definitions, protected paths, and credential references; run and observe missing implementation failures.
- [x] Implement validation and atomic store using existing safe config helpers.
- [x] Run `python -m unittest discover -s tests -p 'test_mcp_config.py'`; expect PASS.

### Task 2: SDK Connection Owner

**Files:** create `nailong/mcp/client.py`, `tests/test_mcp_client.py`, local server fixtures; modify `requirements.txt`.
**Interfaces:** `MCPManager(store, api_key).connect(name)/disconnect(name)/call_tool(server, tool, arguments)/aclose()`, cached connected descriptors and status.

- [x] Write real local stdio/HTTP tests for discovery, call results, error results, cleanup, timeout and cancellation; observe RED.
- [x] Install SDK in the project venv; implement transport contexts owned by one long-running task and credential redaction.
- [x] Run the client tests; expect PASS without model API calls.

### Task 3: Approval and Runtime

**Files:** create `nailong/mcp/tools.py`, `tests/test_mcp_tools.py`; modify `tools.py`, `agent.py`, `nailong/core/permissions.py`, `nailong/core/prompts.py`.
**Interfaces:** dynamic ToolSpec objects consumed by `build_tools(..., mcp_manager=None)`; factory owns `mcp_manager`.

- [x] Test refusal prevents remote effects, approval allows nested params, remote path semantics, local path refusal, scope restrictions and factory schemas; observe RED.
- [x] Implement JSON Schema adapter, namespace mapping, chat-only registry and factory cleanup.
- [x] Run MCP and existing permission/tool/runtime tests; inspect failures against the baseline.

### Task 4: Shared Commands and Delivery

**Files:** create `ui/mcp_flow.py`, `tests/test_mcp_commands.py`; modify `ui/commands.py`, `ui/actions.py`, `README.md`.
**Interfaces:** async command flow returns existing `CommandResult`; no model request for management commands.

- [x] Test add/list/connect/tools/disconnect/remove, usage errors, plan refusal and Textual command dispatch; observe RED.
- [x] Register command with shared actions and help; document both transports and credential setup.
- [x] Run MCP tests and `python -m unittest discover -s tests`; inspect complete log and real local smoke outcomes.

## Execution Record

User instruction “确认设计，开始实行” authorizes continuing implementation in this chat. No extra approval gate for routine reversible steps. Baseline suite started before changes.

Implementation uses `mcp>=1.30,<2.0` and `jsonschema>=4.20,<5.0`. The final MCP suite covers 32 cases, including real stdio and local Streamable HTTP servers, cancellation/timeout cleanup, an offline full AgentService approval/resume round, and Textual project-switch process cleanup. Native permission regression tests also pass.

Independent review found four issues: project-switch cleanup, public tool-name collisions, unsupported anchor-schema conversion, and credentials in JSON keys. Each was fixed and verified by regression tests; the reviewer confirmed those fixes. MCP grants now preserve case-sensitive tool identities.

Plain CLI smoke exercised add → connect → tools → disconnect → remove → list without a model request. Full-suite outcomes and the original baseline failures are recorded in `docs/reviews/2026-10-05-mcp-client-acceptance.md`. No Git staging or commit was performed because unrelated staged and untracked project work is present.
