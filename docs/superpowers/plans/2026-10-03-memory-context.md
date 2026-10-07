# Memory Context Implementation Plan

> Execute in this session with test-driven-development and one final independent review. User authorized the implementation objective; preserve the current checkout and all pre-existing changes. Do not commit, push, or install dependencies.

**Goal:** Fix session isolation and visible truncation, then implement memory catalog, on-demand reads and one configurable budget; evaluate automatic extraction and vector storage afterwards.

**Spec:** `docs/superpowers/specs/2026-10-03-memory-context-design.md`

**Architecture:** Extend MemoryStore with bounded, safe document catalog/read APIs. Keep load_project_memory list-compatible, carrying snapshot diagnostics and rendered context. A per-runtime read context and model middleware enforce the shared estimate over default context plus current/historical memory results. Shared UI actions expose diagnostics and directory commands.

## Global constraints

- Work in current codex/nailong-v1 checkout because the implementation being changed is uncommitted there. No source transfer, reset, staging or commits.
- Preserve old MemoryStore editing and list iteration contracts; keep permissions and tool profile filters.
- Default memory budget 4000 estimated tokens; validate 512–16384; never count provider usage as this estimate.
- Only read theme directories and exact legacy paths; no automatic persistence or network/vector dependencies.

## Tasks

### Task 1: Isolation and explicit loading results

Files: agent.py, nailong/core/memory.py, tests/test_prompt_assembly.py, new tests/test_memory_context.py.

Interfaces: memory entries carry version/status/total_chars/loaded_chars; a list-compatible snapshot carries reports. Unbound goal does not enter a named or unnamed chat.

- [x] Run the existing unbound-goal test RED; add unnamed-chat coverage and loading status regressions for long, empty, invalid and linked files, then run RED.
- [x] Restrict target policy to an explicitly bound matching thread; return explicit loading results instead of discarding errors; retain bounded legacy reads.
- [x] Run related tests GREEN and whole suite.

### Task 2: Catalog and paginated document reads

Files: nailong/core/memory.py, tests/test_memory_context.py, .gitignore.

Interfaces: MemoryStore.catalog() metadata result; read_document(document, offset=0, limit=4000) JSON-compatible result. Logical IDs scope/filename.md. MAX memory bytes 160000, topic count 200.

- [x] Add failing discovery, frontmatter fallback, traversal/link rejection, UTF-8, file-size and complete-pagination tests.
- [x] Add safe topic path mapping and bounded discovery/read; ignore memory.local directory in Git.
- [x] Run tests GREEN.

### Task 3: Shared budget and runtime tools

Files: new nailong/core/memory_context.py, agent.py, tools.py, nailong/tools/registry.py, tests/test_memory_context.py, tests/test_prompt_assembly.py, tests/test_tool_registry.py.

Interfaces: estimate_memory_tokens(text); snapshot.rendered_context plus budget report; MemoryReadContext list/read handlers and filter_messages(messages). Runtime tools memory_list(scope='',offset=0,limit=20), memory_read(document,offset=0,limit=4000). Context and actual built tools share the same snapshot.

- [x] Add RED tests for bounded three-layer injection, metadata-only topics, Chinese text, JSON overhead, repeated/concurrent reads, historical results, actual model tool usage and profile filtering.
- [x] Implement validated configuration, shared snapshot rendering, read context and model-call filtering; wire tool schemas and real runtime handlers.
- [x] Refresh snapshot only at request construction/reload boundaries; retain existing user-file isolation in tests.
- [x] Run related tests GREEN, update intentional tool-list contracts and run whole suite.

### Task 4: User diagnostics and documentation

Files: ui/actions.py, ui/commands.py, nailong/core/context.py, agent_service.py, nailong/core/diagnostics.py, README.md, tests/test_memory_context.py, tests/test_workflow_closures.py as needed.

Interfaces: /memory list [scope], /memory read <ID> [offset]; old show/edit/reload retained. /context includes memory budget/status report.

- [x] Add RED tests for visible truncation/errors, no false stale flag, scope catalog, paginated command reads and budget reporting across shared UI actions.
- [x] Implement shared commands and doctor checks; document topic format, scope, budget configuration and explicit truncation.
- [x] Record evaluation of auto extraction/vector databases and their adoption conditions.
- [x] Run all local tests and inspect generated CLI JSON plus a fake-model multi-step runtime; final independent review, fix consequential findings with regressions, audit every spec requirement.

## Review focus

Budget overflow through historical results/JSON wrapping; symlink and path traversal; cross-scope privilege leak; silent errors versus missing files; Unicode/empty pagination/no-progress; directory and metadata truncation; runtime reload while a graph is active.

## Progress

- Baseline: 374 tests, 1 failure: test_unbound_goal_does_not_activate_named_chat_until_attached. This is the authorized first fix.
- Preflight: Task 1 snapshot metadata is consumed by Tasks 3/4; Task 2 read/catalog contracts are consumed by Tasks 3/4; signatures and scope mapping agree.
- Execution decision: implement directly in existing checkout to preserve the uncommitted code under review. Keep reviewable edits without commits; native skill bookkeeping that depends on task commits is replaced by this progress record.
- Completed Tasks 1–4: legacy context remains editable; topic directories, paginated tools, scope reports, version guards and configurable budget are integrated in shared commands and the actual LangChain runtime.
- User-authorized coordination with the context chat preserved its core selector, archive and context middleware. Both preflight and model calls now use the same bounded memory view; actual request reports include their memory snapshot. No second unbounded index is appended.
- Independent review reproduced and resolved minimum-budget Unicode-ID starvation (including unlisted IDs), diagnostic-heavy first pages, oversized heading indexes, version changes, concurrent ancestor-link swaps, and unbound-goal mutations through the actual tool. Lower-impact exact-heading clipping and filename quoting are also repaired.
- Verification: 28 memory-context tests; reviewer rerun of 62 focused tests; final full suite 497 tests / 41.722s / OK, log `/tmp/nailong-memory-acceptance-suite.log`. Syntax compilation and `git diff --check` pass. CLI JSON checks cover list/read/show/context and invalid budget doctor output without model calls.
- Automatic extraction and vector storage evaluation: `docs/design/2026-10-03-memory-storage-evaluation.md`. Both deferred until candidate approval/quality measurement justifies the additional persistence or retrieval mechanism.
- Final requirement evidence: `docs/reviews/2026-10-03-memory-context-acceptance.md`.
