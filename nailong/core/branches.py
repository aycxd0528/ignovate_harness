"""Native conversation lineage and recoverable turn boundaries.

Nodes index the original session logs/checkpoints. Shared ancestors are never
copied into child logs. GUI layout is stored in a separate database.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone


class ForkHistory:
    """Copy only archives reachable from an immutable fork boundary.

    References are reissued under the child's thread identity, including nested
    index references. No lookup permission to the source's future archive is
    granted. Missing/cyclic records fail before a branch is published.
    """
    reference = re.compile(r'hist_[a-f0-9]{64}')

    def __init__(self, archive, source, target):
        self.archive, self.source, self.target = archive, source, target
        self.mapping, self.visiting = {}, set()

    def _reference(self, reference):
        from langchain_core.messages import messages_from_dict
        if reference in self.mapping:
            return self.mapping[reference]
        if reference in self.visiting or len(self.visiting) > 100 or len(self.mapping) > 2000:
            raise ValueError('历史归档引用循环或过多，不能精确分叉。')
        self.visiting.add(reference)
        try:
            record = self.archive.load(self.source, reference)
            if record.get('archive_truncated'):
                raise ValueError('历史归档正文已截断，请基于摘要创建新会话。')
            serialized = copy.deepcopy(record['message'])
            if (serialized['data'].get('additional_kwargs') or {}).get('nailong_task_archive'):
                # Provenance is metadata, not a live reference owned by this
                # thread. Keep it outside generic archive-reference rewriting.
                archived_payload = json.loads(serialized['data']['content'])
                archived_payload.pop('fork_source_reference', None)
                serialized['data']['content'] = json.dumps(archived_payload, ensure_ascii=False)
            message = messages_from_dict([self.copy(serialized)])[0]
            if message.additional_kwargs.get('nailong_task_archive'):
                payload = json.loads(message.content)
                from nailong.core.task_context import validate_task_snapshot
                task = validate_task_snapshot(payload['task'])
                if task['thread_id'] != self.source:
                    raise ValueError('任务归档来源会话无效。')
                task['thread_id'] = self.target
                payload.update(task=task, fork_source_thread=self.source, fork_source_digest=reference.removeprefix('hist_'))
                message.content = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
            mapped = self.archive.save(self.target, message, self.copy(record.get('arguments')))
            self.mapping[reference] = mapped
            return mapped
        finally:
            self.visiting.remove(reference)

    def copy(self, value):
        from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict
        if isinstance(value, BaseMessage):
            return messages_from_dict([self.copy(message_to_dict(value))])[0]
        if isinstance(value, str):
            return self.reference.sub(lambda match: self._reference(match[0]), value)
        if isinstance(value, dict):
            return {key: self.copy(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.copy(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.copy(item) for item in value)
        return copy.deepcopy(value)


def clone_checkpoint(saver, source_thread, checkpoint_id, target_thread, *, history=None):
    from langgraph.checkpoint.base import empty_checkpoint
    config = {'configurable': {'thread_id': source_thread, 'checkpoint_ns': '', 'checkpoint_id': checkpoint_id}}
    saved = saver.get_tuple(config)
    if saved is None or saved.pending_writes:
        raise ValueError('该回合缺少完整可恢复 checkpoint。')
    checkpoint = copy.deepcopy(saved.checkpoint)
    values = checkpoint.get('channel_values', {})
    calls, results = set(), set()
    for message in values.get('messages', []):
        calls.update(str(call['id']) for call in getattr(message, 'tool_calls', []) or [])
        if getattr(message, 'type', None) == 'tool':
            results.add(str(message.tool_call_id))
    if calls - results or checkpoint.get('pending_sends'):
        raise ValueError('工具交换尚未闭合，不能精确分叉。')
    if any(key.startswith('branch:') and value for key, value in values.items()):
        raise ValueError('checkpoint 仍有未完成步骤，不能精确分叉。')
    if history is not None:
        checkpoint = history.copy(checkpoint)
    elif ForkHistory.reference.search(str(values)):
        raise ValueError('历史归档未绑定新分支，不能精确分叉。')
    fresh = empty_checkpoint()
    checkpoint.update(id=fresh['id'], ts=fresh['ts'], pending_sends=[])
    metadata = {**saved.metadata, 'source': 'update', 'parents': {}}
    target = saver.put({'configurable': {'thread_id': target_thread, 'checkpoint_ns': ''}}, checkpoint,
                       metadata, checkpoint.get('channel_versions', {}))
    return target['configurable']['checkpoint_id']


def turn_timing(events):
    explicit=next((event.get('data',{}) for event in reversed(events) if event.get('kind')=='gui_run_timing'),None)
    if explicit is not None:
        return {key:explicit.get(key) for key in ('started_at','finished_at','elapsed_ms')}
    started=next((event.get('timestamp') for event in events if event.get('kind') in {'turn_start','user'}),None)
    finished=next((event.get('timestamp') for event in reversed(events) if event.get('kind')=='final'),None)
    elapsed=None
    if started and finished:
        try:elapsed=max(0,int((datetime.fromisoformat(finished)-datetime.fromisoformat(started)).total_seconds()*1000))
        except (ValueError,TypeError):pass
    return {'started_at':started,'finished_at':finished,'elapsed_ms':elapsed}


def project_turn(node, events):
    from types import SimpleNamespace
    from nailong.core.diagnostics import usage_snapshot
    prompt, answer, tokens, tools = '', '', [], {}
    native_usage = usage_snapshot(SimpleNamespace(read_events=lambda _: events), node['thread_id'])
    from nailong.core.usage_projection import usage_projection
    usage_details = usage_projection(events)
    usage = {**native_usage, 'complete': bool(native_usage['calls']) and native_usage['usage_complete']}
    if not usage['complete']:
        usage['cost_usd'] = None
    delivery = {'status': 'unverified'}
    for event in events:
        kind, data = event.get('kind'), event.get('data', {})
        if kind == 'user':
            prompt = str(data.get('text', ''))
        elif kind == 'token':
            tokens.append(str(data.get('text', '')))
        elif kind == 'final':
            answer = str(data.get('text', ''))
            delivery = data.get('delivery') or delivery
        elif kind in {'tool_start', 'tool_end'}:
            identity = data.get('call_id') or data.get('id')
            # Unidentified results are not guessed to belong to another call.
            identity = identity or f'unknown-{len(tools)}'
            tool = tools.setdefault(identity, {'call_id': identity, 'status': 'unknown'})
            tool.update(data)
            tool['status'] = ('running' if kind == 'tool_start' else
                              'failed' if data.get('ok') is False else
                              'completed' if data.get('ok') is True else 'unknown')
        elif kind == 'verification':
            delivery = {'status': data.get('status', 'unverified'), 'verification': data}
    for tool in tools.values():
        if tool['status']=='running' and node.get('status') not in {'running','waiting_approval'}:
            tool['status']='unknown' if node.get('status')=='completed' else 'interrupted'
    return {**node, 'prompt': prompt, 'answer': answer or ''.join(tokens), 'tools': list(tools.values()),
            'usage': usage, 'usage_details': usage_details, 'delivery': delivery,
            'timing':turn_timing(events), 'recoverable': bool(node.get('checkpoint_id'))}


class BranchStore:
    def __init__(self, sessions):
        self.db = self.view = None
        try:
            self._initialize(sessions)
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise

    def _initialize(self, sessions):
        self.sessions = sessions
        sessions.ensure_directories()
        self.db = sqlite3.connect(sessions.root / 'branches.sqlite')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS branches (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, thread_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, parent_id TEXT, fork_node_id TEXT, inherited_head TEXT,
            head TEXT, archived INTEGER DEFAULT 0, is_default INTEGER DEFAULT 0,
            legacy INTEGER DEFAULT 0, created TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY, branch_id TEXT NOT NULL, parent_id TEXT, thread_id TEXT NOT NULL,
            start_index INTEGER NOT NULL, end_index INTEGER NOT NULL, checkpoint_id TEXT,
            task_snapshot TEXT, status TEXT NOT NULL, created TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, payload TEXT, result TEXT);
          CREATE TABLE IF NOT EXISTS node_history (id TEXT PRIMARY KEY, events TEXT NOT NULL);
        ''')
        if 'history_conflict' not in {row['name'] for row in self.db.execute('PRAGMA table_info(branches)')}:
            self.db.execute('ALTER TABLE branches ADD COLUMN history_conflict INTEGER DEFAULT 0')
            self.db.commit()
        if 'title_source' not in {row['name'] for row in self.db.execute('PRAGMA table_info(branches)')}:
            self.db.execute("ALTER TABLE branches ADD COLUMN title_source TEXT NOT NULL DEFAULT 'manual'")
            names=self.sessions._metadata()
            for row in self.db.execute("SELECT id,thread_id FROM branches WHERE name='新会话' AND head IS NULL AND parent_id IS NULL"):
                if row['thread_id'] not in names:self.db.execute("UPDATE branches SET title_source='auto' WHERE id=?",(row['id'],))
            self.db.commit()
        self.view = sqlite3.connect(sessions.root / 'gui-layout.sqlite')
        self.view.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        if os.name != 'nt':
            for name in ('branches.sqlite', 'gui-layout.sqlite'):
                os.chmod(sessions.root / name, 0o600)

    def close(self):
        from nailong.core.runtime_resources import raise_cleanup_errors
        errors = []
        for connection in (self.db, self.view):
            if connection is not None:
                try:
                    connection.close()
                except BaseException as error:
                    errors.append(error)
        raise_cleanup_errors(errors)

    def branch(self, identity):
        row = self.db.execute('SELECT * FROM branches WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise ValueError('分支不存在或不属于当前项目。')
        return dict(row)

    def branches(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM branches ORDER BY created')]

    def create(self, name='新会话', *, thread_id=None, conversation_id=None, parent_id=None,
               fork_node_id=None, inherited_head=None, legacy=False, identity=None):
        name = self.sessions._sanitize(str(name).strip())
        if not name or len(name) > 80:
            raise ValueError('名称须为 1–80 个字符。')
        identity = identity or uuid.uuid4().hex
        thread_id = thread_id or identity
        self.sessions.session_path(thread_id)
        with self.db:
            self.db.execute('INSERT INTO branches (id,conversation_id,thread_id,name,parent_id,fork_node_id,inherited_head,head,archived,is_default,legacy,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                            (identity, conversation_id or identity, thread_id, name, parent_id, fork_node_id,
                             inherited_head, inherited_head, 0, int(parent_id is None), int(legacy),
                             datetime.now(timezone.utc).isoformat()))
            if name=='新会话' and parent_id is None and not legacy:
                self.db.execute("UPDATE branches SET title_source='auto' WHERE id=?",(identity,))
        return self.branch(identity)

    def title_from_input(self,identity,message):
        branch=self.branch(identity)
        if branch['title_source']!='auto':return branch
        title=' '.join(str(message).split())[:80]
        if not title:return branch
        safe=self.sessions.rename(branch['thread_id'],title)
        with self.db:self.db.execute("UPDATE branches SET name=?,title_source='first_input' WHERE id=?",(safe,identity))
        return self.branch(identity)

    def create_fork(self, branch_id, node_id, name, checkpoint_id, *, before=False, thread_id=None, identity=None):
        branch = self.branch(branch_id)
        node = next((n for n in self.path(branch_id) if n['id'] == node_id), None)
        if node is None or not node['recoverable'] or node['status'] != 'completed' or not checkpoint_id:
            raise ValueError('该回合没有完整可恢复边界。')
        inherited = node['parent_id'] if before else node_id
        return self.create(name, thread_id=thread_id, conversation_id=branch['conversation_id'],
                           parent_id=branch_id, fork_node_id=node_id, inherited_head=inherited, identity=identity)

    def node(self, identity):
        row = self.db.execute('SELECT * FROM nodes WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise ValueError('来源回合缺失。')
        return dict(row)

    def commit_turn(self, branch_id, start_index, checkpoint_id, *, task_snapshot=None, status='completed', end_index=None):
        branch = self.branch(branch_id)
        events = self.sessions.read_events(branch['thread_id'])
        end_index = len(events) if end_index is None else end_index
        if type(end_index) is not int or not start_index <= end_index <= len(events):
            raise ValueError('来源回合的结束边界无效。')
        node_id = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (node_id, branch_id, branch['head'], branch['thread_id'], start_index, end_index,
                             checkpoint_id, json.dumps(task_snapshot, ensure_ascii=False), status,
                             datetime.now(timezone.utc).isoformat()))
            self.db.execute('UPDATE branches SET head=?, legacy=0 WHERE id=?', (node_id, branch_id))
            self.db.execute('INSERT INTO node_history VALUES (?,?)', (node_id, json.dumps(events[start_index:end_index], ensure_ascii=False)))
        return project_turn(self.node(node_id), events[start_index:end_index])

    def node_events(self, node):
        saved = self.db.execute('SELECT events FROM node_history WHERE id=?', (node['id'],)).fetchone()
        if saved:
            return json.loads(saved['events'])
        events = self.sessions.read_events(node['thread_id'])[node['start_index']:node['end_index']]
        with self.db:
            self.db.execute('INSERT INTO node_history VALUES (?,?)', (node['id'], json.dumps(events, ensure_ascii=False)))
        return events

    def path(self, branch_id):
        branch = self.branch(branch_id)
        result, seen = [], set()
        head = branch['head']
        while head:
            if head in seen:
                raise ValueError('会话来源关系损坏。')
            seen.add(head)
            node = self.node(head)
            result.append(project_turn(node, self.node_events(node)))
            head = node['parent_id']
        return result[::-1]

    def import_sessions(self, checkpoint_lookup=None, *, exclude=()):
        existing = {row['thread_id']: row for row in self.branches()}
        for row in self.sessions.list_sessions():
            if row['thread_id'] in exclude:
                continue
            events = self.sessions.read_events(row['thread_id'])
            if not events:
                try:
                    if not self.sessions.session_path(row['thread_id']).stat().st_size:
                        continue
                except FileNotFoundError:
                    continue
            first_input=next((event.get('data',{}).get('text','') for event in events if event.get('kind')=='user'), '')
            title=row.get('name') or ' '.join(str(first_input).split())[:80] or '历史会话'
            branch = existing.get(row['thread_id']) or self.create(title, thread_id=row['thread_id'], legacy=True)
            if branch['history_conflict']:
                continue
            events = self.sessions.read_events(branch['thread_id'])
            owned = list(self.db.execute('SELECT * FROM nodes WHERE branch_id=? ORDER BY start_index,rowid', (branch['id'],)))
            if any(self.node_events(dict(node)) != events[node['start_index']:node['end_index']] for node in owned):
                with self.db:
                    self.db.execute('UPDATE branches SET history_conflict=1 WHERE id=?', (branch['id'],))
                continue
            # A foreign tail observed before completion is replaced with a new
            # immutable node. Its old saved version remains addressable.
            if owned and owned[-1]['status'] == 'interrupted':
                tail = dict(owned[-1])
                end = next((i for i in range(tail['end_index'], len(events)) if events[i].get('kind') == 'user'), len(events))
                if end > tail['end_index']:
                    segment = events[tail['start_index']:end]
                    complete = any(event.get('kind') == 'final' for event in segment)
                    identity = uuid.uuid4().hex
                    with self.db:
                        self.db.execute('INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (identity, branch['id'], tail['parent_id'], branch['thread_id'], tail['start_index'], end,
                             None, 'null', 'completed' if complete else 'interrupted', row['updated_at']))
                        self.db.execute('INSERT INTO node_history VALUES (?,?)', (identity, json.dumps(segment, ensure_ascii=False)))
                        self.db.execute('UPDATE branches SET head=?,legacy=1 WHERE id=?', (identity, branch['id']))
                    branch['head'] = identity
                    owned.append({'end_index': end})
            indexed = max((node['end_index'] for node in owned), default=0)
            starts = [i for i, event in enumerate(events) if i >= indexed and event.get('kind') == 'user']
            for offset, start in enumerate(starts):
                end = starts[offset+1] if offset+1 < len(starts) else len(events)
                segment = events[start:end]
                complete = any(event.get('kind') == 'final' for event in segment)
                # Old/foreign logs do not carry an atomic checkpoint + task
                # binding. Keep them readable; summary starts a fresh context.
                checkpoint = None
                node_id = uuid.uuid4().hex
                with self.db:
                    self.db.execute('INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)',
                                    (node_id, branch['id'], branch['head'], branch['thread_id'], start, end,
                                     checkpoint, 'null', 'completed' if complete else 'interrupted', row['updated_at']))
                    self.db.execute('UPDATE branches SET head=?, legacy=1 WHERE id=?', (node_id, branch['id']))
                    self.db.execute('INSERT INTO node_history VALUES (?,?)', (node_id, json.dumps(segment, ensure_ascii=False)))
                branch['head'] = node_id

    def update_branch(self, identity, *, name=None, archived=None, default=False):
        branch = self.branch(identity)
        with self.db:
            if name is not None:
                safe_name = self.sessions.rename(branch['thread_id'], name)
                self.db.execute("UPDATE branches SET name=?,title_source='manual' WHERE id=?", (safe_name, identity))
            if archived is not None:
                self.db.execute('UPDATE branches SET archived=? WHERE id=?', (int(bool(archived)), identity))
            if default:
                self.db.execute('UPDATE branches SET is_default=0 WHERE conversation_id=?', (branch['conversation_id'],))
                self.db.execute('UPDATE branches SET is_default=1 WHERE id=?', (identity,))
        return self.branch(identity)

    def usage(self, conversation_id):
        nodes = {node['id']: node for branch in self.branches() if branch['conversation_id'] == conversation_id
                 for node in self.path(branch['id'])}
        return {'input_tokens': sum(n['usage']['input_tokens'] for n in nodes.values()),
                'output_tokens': sum(n['usage']['output_tokens'] for n in nodes.values()),
                'complete': bool(nodes) and all(n['usage']['complete'] for n in nodes.values())}

    def layout(self, conversation_id=None):
        key = 'layout:'+conversation_id if conversation_id else 'layout'
        row = self.view.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else {'revision': 0, 'positions': {}, 'collapsed': [], 'viewport': {'x': 40, 'y': 40, 'zoom': 1}}

    def save_layout(self, value, revision, conversation_id=None):
        encoded = json.dumps(value, allow_nan=False)
        if len(encoded) > 500_000:
            raise ValueError('地图布局过大。')
        for position in value.get('positions', {}).values():
            if not isinstance(position, dict) or any(type(position.get(k)) not in (int, float) or not math.isfinite(position[k]) for k in ('x', 'y')):
                raise ValueError('地图坐标无效。')
        viewport = value.get('viewport', {'x': 40, 'y': 40, 'zoom': 1})
        if any(type(viewport.get(k)) not in (int, float) or not math.isfinite(viewport[k]) for k in ('x', 'y', 'zoom')) or not .2 <= viewport['zoom'] <= 4:
            raise ValueError('地图视口无效。')
        with self.view:
            self.view.execute('BEGIN IMMEDIATE')
            current = self.layout(conversation_id)
            if type(revision) is not int or current['revision'] != revision:
                raise ValueError('地图布局版本冲突，请重新加载。')
            saved = {**value, 'revision': revision + 1, 'viewport': viewport}
            key = 'layout:'+conversation_id if conversation_id else 'layout'
            self.view.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key,json.dumps(saved)))
        return saved

    def draft(self, branch_id, text=None):
        self.branch(branch_id)
        if text is not None:
            if not isinstance(text, str) or len(text) > 100_000:
                raise ValueError('草稿过长。')
            with self.view:
                safe_text = text.replace(self.sessions.api_key, '[密钥已隐藏]') if self.sessions.api_key else text
                self.view.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('draft:'+branch_id, safe_text))
        row = self.view.execute('SELECT value FROM state WHERE key=?', ('draft:'+branch_id,)).fetchone()
        return row[0] if row else ''
