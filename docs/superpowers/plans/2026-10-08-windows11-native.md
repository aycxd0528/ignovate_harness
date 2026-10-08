# Windows 11 Native Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task; independent OS subsystems use dispatching-parallel-agents. Steps use checkbox syntax for tracking.

**Goal:** Release v1.0.2 supports Windows 11 without WSL and retains `ignovate set up` automatic environment configuration.
**Architecture:** Native PowerShell bootstrap plus cross-platform file, lock and process backends; preserve POSIX behavior.
**Tech Stack:** Python 3.11–3.13, ctypes/Win32, PowerShell 5.1, uv, GitHub Actions.
**Spec:** docs/superpowers/specs/2026-10-08-windows11-native-design.md

## Global Constraints
- Windows 11 native operation, no WSL/Linux dependency or calls.
- Version 1.0.2; preserve macOS/Linux and original user changes.
- Preserve file boundaries, protected files, read-before-edit/version checking, locks and process cancellation.
- Windows installation needs no pre-existing Python, user-level installation with idempotent PATH and no configuration overwrite.

## Review Focus
- Junction or symlink ancestor replacement cannot redirect a safe read or history write.
- Multiple CLI processes cannot lose task/goal transactions or acquire the same driver lease.
- Cancellation and timeout do not leave an ordinary spawned child alive.
- Arguments with spaces, quotes, backslashes, non-ASCII paths and empty strings retain their meaning.
- Interrupted setup does not leave ready state; repeat installation preserves configuration.

### Task 1: Native filesystem and locking
- [ ] Add regression tests for native regular files, archive replay, reparse points, path replacement, task/goal concurrent transactions.
- [ ] Implement focused Windows handle-based file and lock helpers; adapt history_archive, memory, goal and task_state, retain POSIX paths.
- [ ] Root integrates safe read helper into local_tools and rejects junctions in traversal/config paths; portable pipe capture in file search belongs to Task 2.
- [ ] Run file/history/memory/task/goal tests locally; Windows CI executes real native regressions.

### Task 2: Native processes
- [ ] Add regression tests for bounded output, command timeout/cancel, parent/child cleanup, editor and hooks.
- [ ] Implement portable process helpers (Windows jobs and bounded pipe capture) and adapt processes, hooks and plan.
- [ ] Root integrates capture into local_tools and file-search pipe collection after helper signatures are agreed.
- [ ] Run covering process tests locally and native Windows CI.

### Task 3: Native installation and release
- [ ] Add a Windows installer smoke that checks local checksums, launcher cwd/arguments/exit status, PATH opt-out and setup repeat/failure.
- [ ] Replace WSL installer with Windows 11 user-level install and PowerShell launch script; automatically download pinned uv/rg and Python.
- [ ] Run actual fresh `set up --environment-only`, imports, doctor and installed-tool smoke on Windows; repair twice, without WSL.
- [ ] Bump to 1.0.2, update CI and all install documentation, including Feishu.
- [ ] Complete POSIX suite and native Windows validation, independent review, build clean tagged assets, push tag and verify published download.
