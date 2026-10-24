"""Discard unused GUI placeholders without touching conversation evidence."""
from __future__ import annotations

import sqlite3
from contextlib import closing


def empty_branch_ids(controller):
    store, sessions = controller.branches, controller.sessions
    branches = store.branches()
    protected = {row[0] for row in store.db.execute('SELECT branch_id FROM runs')}
    protected.update(row[0] for row in store.db.execute('SELECT branch_id FROM nodes'))
    protected.update(row[0] for row in store.db.execute('SELECT branch_id FROM map_requests'))
    protected.update(b['parent_id'] for b in branches if b['parent_id'])
    try:
        from nailong.gui.work_control import task_store
        task_threads = set()
        for task in task_store(controller).list_work_items():
            task_threads.update(task.get('associated_thread_ids') or [task['thread_id']])
            task_threads.add(task['thread_id'])
        checkpoint_threads = set()
        # A broken checkpoint link is unknown evidence, not an absent database.
        try:
            sessions.database_path.lstat()
        except FileNotFoundError:
            pass
        else:
            with closing(sqlite3.connect(sessions.database_path.as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoints'").fetchone():
                    checkpoint_threads.update(row[0] for row in db.execute('SELECT DISTINCT thread_id FROM checkpoints'))
    except (ValueError, OSError, sqlite3.Error):
        # Uncertain task/checkpoint state must never make a conversation disposable.
        return []
    result = []
    for branch in branches:
        if (branch['id'] in protected or branch['thread_id'] in task_threads | checkpoint_threads
                or any(branch[key] for key in ('head', 'parent_id', 'fork_node_id', 'inherited_head', 'history_conflict'))
                or store.draft(branch['id']).strip()):
            continue
        path = sessions.session_path(branch['thread_id'])
        # Keep even malformed/non-indexed evidence; an unreadable log is not empty.
        try:
            if path.exists() and path.stat().st_size:
                continue
        except OSError:
            continue
        result.append(branch['id'])
    return result


def prune_empty(controller, payload):
    keep = payload.get('keep_branch_ids', [])
    candidates = payload.get('branch_ids')
    for value in (keep, candidates):
        if value is not None and (not isinstance(value, list) or len(value) > 10000
                                  or any(not isinstance(item, str) for item in value)):
            raise ValueError('待清理的会话列表无效。')
    from nailong.core.project_execution import project_execution
    try:
        with project_execution(controller.sessions):
            identities = [identity for identity in empty_branch_ids(controller)
                          if identity not in keep and (candidates is None or identity in candidates)]
            with controller.branches.db:
                for identity in identities:
                    controller.branches.db.execute('DELETE FROM gui_branch_modes WHERE branch_id=?', (identity,))
                    controller.branches.db.execute('DELETE FROM branches WHERE id=?', (identity,))
                    controller.branch_modes.pop(identity, None)
            with controller.branches.view:
                for identity in identities:
                    controller.branches.view.execute('DELETE FROM state WHERE key=?', ('draft:'+identity,))
            if identities:
                controller.emit('branches_pruned', {'branch_ids': identities})
            return {'removed': identities}
    except BlockingIOError:
        return {'removed': [], 'retry': True}
