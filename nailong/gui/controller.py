"""Async GUI commands; all agent execution goes through AgentService."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

from nailong.core.branches import BranchStore, clone_checkpoint
from nailong.core.sessions import ProjectSessionStore


class GUIController:
    def __init__(self, project, *, sessions=None, service=None):
        from config import select_project_root
        self.project = select_project_root(str(project))
        self.sessions = sessions or ProjectSessionStore(self.project)
        self.branches = BranchStore(self.sessions)
        self.service = service
        self.factory = getattr(service, 'runtime_factory', None)
        self.runs, self.approvals = {}, {}
        self.queue = []
        self.paused = False
        self.worker = None
        self.current_task = None
        self.events_log = []
        self.sequence = 0
        self.plans = {}
        self.branch_permissions = {}
        self.branch_modes = {}
        self.error = ''
        self.last_history_sync = 0
        self.command_lock = asyncio.Lock()
        self.branches.db.execute('''CREATE TABLE IF NOT EXISTS runs
            (id TEXT PRIMARY KEY, branch_id TEXT, status TEXT, payload TEXT, start_index INTEGER, node_id TEXT)''')
        self.branches.db.execute('''CREATE TABLE IF NOT EXISTS map_requests
            (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, branch_id TEXT NOT NULL, thread_id TEXT NOT NULL)''')
        self.branches.db.execute('''CREATE TABLE IF NOT EXISTS gui_branch_modes
            (branch_id TEXT PRIMARY KEY REFERENCES branches(id), mode TEXT NOT NULL)''')
        self.branches.db.commit()
        self.branch_modes = {row['branch_id']: row['mode'] for row in
                             self.branches.db.execute('SELECT * FROM gui_branch_modes')
                             if row['mode'] in {'default', 'acceptEdits', 'bypassPermissions'}}
        for row in self.branches.db.execute('SELECT * FROM runs ORDER BY rowid'):
            run = dict(row)
            run['payload'] = json.loads(run['payload'])
            run['message'] = run['payload'].get('message', '')
            run['plan_id'] = run['payload'].get('_gui_plan_id')
            run['error'] = run['payload'].get('_gui_error')
            for key in ('queued_at','started_at','finished_at','elapsed_ms','tools','activity','goal','progress','continuation'):
                run[key]=run['payload'].get('_gui_'+key)
            run['tools']=run.get('tools') or []
            if run['status'] in {'queued', 'running', 'waiting_approval', 'stopping'}:
                run['status'] = 'interrupted'
                run['finished_at']=datetime.now(timezone.utc).isoformat()
                for tool in run['tools']:
                    if tool.get('status')=='running':tool['status']='interrupted'
            self.runs[run['id']] = run
        self._recover_runs()
        self._sync_history()

    def _recover_runs(self):
        for run in self.runs.values():
            if run['status'] == 'interrupted' and run['node_id'] is None and run['start_index'] is not None:
                branch = self.branches.branch(run['branch_id'])
                saved = self.branches.db.execute('SELECT id FROM nodes WHERE branch_id=? AND start_index=?',
                    (branch['id'], run['start_index'])).fetchall()
                if len(saved) == 1:
                    run['node_id'] = saved[0]['id']
                elif len(saved) > 1:
                    raise ValueError('恢复运行存在多个来源回合，请核对。')
                elif len(self.sessions.read_events(branch['thread_id'])) > run['start_index']:
                    events = self.sessions.read_events(branch['thread_id'])
                    start = run['start_index']; end = start
                    if events[start].get('run_id') == run['id']:
                        while end < len(events) and events[end].get('run_id') == run['id']:
                            end += 1
                    else:
                        # Legacy ownership is uncertain. Preserve one bounded
                        # historical turn, never certify the later session tail.
                        end = len(events); seen_user = False
                        for i in range(start, len(events)):
                            kind = events[i].get('kind')
                            if i > start and (kind == 'turn_start' or kind == 'user' and seen_user):
                                end = i; break
                            seen_user = seen_user or kind == 'user'
                    node = self.branches.commit_turn(branch['id'], start, None, status='interrupted', end_index=end)
                    run['node_id'] = node['id']
                self._save_run(run)
            if run['status'] in {'completed', 'failed', 'stopped', 'interrupted'}:
                from nailong.gui.work_control import closeout
                closeout(self, run, self.branches.branch(run['branch_id']))

    def safe(self, value):
        secret = self.sessions.api_key
        if isinstance(value, str):
            return value.replace(secret, '[密钥已隐藏]') if secret else value
        if isinstance(value, dict):
            return {key: self.safe(item) for key, item in value.items() if key not in {'api_key', 'DEEPSEEK_API_KEY'}}
        if isinstance(value, (list, tuple)):
            return [self.safe(item) for item in value]
        return value

    def emit(self, kind, data, run=None):
        if run and kind in {'goal_control', 'goal_progress'}:
            run['continuation' if kind == 'goal_control' else 'progress'] = self.safe(data)
            self._save_run(run)
        if run and kind in {'status','tool_start','tool_end'}:
            if kind=='status':run['activity']=data.get('status')
            else:
                tools=run.setdefault('tools',[]);identity=data.get('call_id') or data.get('id') or 'unknown-'+str(len(tools))
                tool=next((tool for tool in tools if tool.get('call_id')==identity),None)
                if tool is None:
                    tool={'call_id':identity};tools.append(tool)
                    if len(tools)>128:tools.pop(0)
                tool.update(self.safe(data));tool['status']='running' if kind=='tool_start' else 'failed' if data.get('ok') is False else 'completed' if data.get('ok') is True else 'unknown'
                run['activity']='tools' if kind=='tool_start' else 'thinking'
            self._save_run(run)
        self.sequence += 1
        event = {'sequence': self.sequence, 'kind': kind, 'data': self.safe(data),
                 'project_id': self.sessions.root.name,
                 'run_id': run['id'] if run else None, 'branch_id': run['branch_id'] if run else None}
        self.events_log.append(event)
        self.events_log = self.events_log[-4000:]

    def events(self, after=0):
        if time.monotonic()-self.last_history_sync > 1:
            if self._sync_history():
                self.emit('history_updated', {})
        reset = after > self.sequence or (self.events_log and after < self.events_log[0]['sequence'] - 1)
        return {'sequence': self.sequence, 'reset': bool(reset),
                'events': [e for e in self.events_log if e['sequence'] > after] if not reset else []}

    def _checkpoint(self, thread_id):
        saver = getattr(self.factory, 'checkpointer', None)
        if saver is None:
            # Read existing DB without loading a model or making a request.
            if not self.sessions.database_path.exists():
                return None
            from langgraph.checkpoint.sqlite import SqliteSaver
            with SqliteSaver.from_conn_string(str(self.sessions.database_path)) as source:
                saved = source.get_tuple({'configurable': {'thread_id': thread_id, 'checkpoint_ns': ''}})
        else:
            saved = saver.get_tuple({'configurable': {'thread_id': thread_id, 'checkpoint_ns': ''}})
        return saved.checkpoint['id'] if saved and not saved.pending_writes else None

    def ensure_service(self):
        if self.service is not None:
            return self.service
        from agent import AgentRuntimeFactory
        from agent_service import AgentService
        from config import load_settings
        settings = replace(load_settings(), project_root=self.project)
        self.sessions.api_key = settings.api_key
        self.factory = AgentRuntimeFactory(settings, session_store=self.sessions)
        self.service = AgentService(self.factory, api_key=settings.api_key)
        # Shared project memory is read-only to exploratory GUI branches.
        execution = self.factory.tool_execution_context
        native_pre_tool = execution.pre_tool_hook
        def guarded(action):
            args = action.get('args') or {}
            path = str(args.get('path', '')).replace('\\', '/')
            memory_path = path.endswith('/context.md') or path == '.nailong/context.md' or '/.nailong/memory' in '/'+path
            if action.get('name') in {'write_file', 'edit_file'} and memory_path:
                return {'blocked': True, 'reason': 'GUI 分支不能直接提升共享记忆，请保留在分支草稿中。'}
            return native_pre_tool(action)
        execution.pre_tool_hook = guarded
        return self.service

    def configuration(self):
        from config import load_settings
        from nailong.core.reasoning import reasoning_options
        try:
            settings = getattr(self.factory, 'settings', None)
            if settings is None:
                from nailong.core.preferences import PreferenceStore
                settings = load_settings()
                preferences = PreferenceStore(self.project)
                preferences.default_reasoning = settings.reasoning_effort
                current = preferences.effective(default_model=settings.environment_model or settings.model,
                                                cli=settings.cli_preferences)
                settings = replace(settings, project_root=self.project, model=current['model'],
                                   reasoning_effort=current['reasoning_effort'])
            model = settings.model
            from nailong.gui.controls import model_preferences
            catalog = model_preferences(self).effective()
            self.sessions.api_key = getattr(settings, 'api_key', self.sessions.api_key)
            return {'configured': True, 'model': model, 'api_base': settings.api_base,
                    'model_name': catalog['model_name'],
                    'models': [{'name': name, **value} for name, value in catalog['models'].items()],
                    'reasoning_effort': getattr(settings, 'reasoning_effort', 'default'),
                    'reasoning_options': reasoning_options(model),
                    'permission_mode': getattr(self.service, 'permission_mode', 'default')}
        except (ValueError, OSError) as error:
            return {'configured': False, 'model': '', 'api_base': 'https://api.deepseek.com',
                    'reasoning_effort': 'default', 'reasoning_options': reasoning_options(''),
                    'permission_mode': 'default', 'error': self.safe(str(error))}

    def _sync_history(self):
        # Read external CLI history only when the shared workspace lease is free.
        from nailong.core.project_execution import project_execution
        try:
            with project_execution(self.sessions):
                before = self.branches.db.total_changes
                active = {self.branches.branch(run['branch_id'])['thread_id'] for run in self.runs.values()
                          if run['status'] in {'queued', 'running', 'waiting_approval', 'stopping'}}
                self.branches.import_sessions(exclude=active)
                return self.branches.db.total_changes != before
        except BlockingIOError:
            return False
        finally:
            self.last_history_sync = time.monotonic()

    def snapshot(self):
        self._sync_history()
        configuration = self.configuration()
        from nailong.gui.settings import GUISettings
        try:
            gui_settings = GUISettings(self).preferences()
        except (ValueError, OSError) as error:
            gui_settings = {'preferences': {}, 'saved_keys': [], 'error': self.safe(str(error))}
        from nailong.core.context import configured_context_window
        window = configured_context_window(configuration['model'], self.project) if configuration['configured'] else None
        branches = self.branches.branches()
        paths = {branch['id']: self.branches.path(branch['id']) for branch in branches}
        from nailong.gui.presentation import artifact_cards
        nodes = {n['id']: {**n, 'task_snapshot': None,'artifacts':artifact_cards(n)} for path in paths.values() for n in path}
        for branch in branches:
            path = paths[branch['id']]
            branch['updated_at'] = path[-1]['created'] if path else branch['created']
            branch['recoverable'] = not branch['history_conflict'] and (not branch['head'] and not branch['legacy'] or bool(path and path[-1]['recoverable']))
            branch['draft'] = self.branches.draft(branch['id'])
            branch['node_ids'] = [n['id'] for n in path]
            branch['summary'] = path[-1]['answer'][:140] if path else ''
            branch['permission_mode'] = self.branch_modes.get(branch['id'], 'default')
            branch['weather'] = self._weather(path, window)
        from nailong.gui.work_control import projection, task_summary
        for node in nodes.values():
            saved = self.branches.node(node['id']).get('task_snapshot')
            node['task_summary'] = task_summary(json.loads(saved)) if saved else None
            source_run = next((run for run in self.runs.values() if run.get('node_id') == node['id']), None)
            if source_run:
                node['progress'] = source_run.get('progress')
                node['continuation'] = source_run.get('continuation')
        control = projection(self, branches)
        return self.safe({'schema_version': 1, 'sequence': self.sequence,
                         'project': {'id': self.sessions.root.name, 'path': str(self.project), 'name': self.project.name},
                         'configuration': configuration, 'gui_settings': gui_settings,
                         'branches': branches, 'nodes': list(nodes.values()),
                         'runs': [self._public_run(run) for run in self.runs.values()], 'paused': self.paused,
                         'approvals': [a['public'] for a in self.approvals.values()], 'layout': self.branches.layout(),
                         'layouts': {b['conversation_id']: self.branches.layout(b['conversation_id']) for b in branches},
                         'control': control, 'error': self.error, 'kernel_version': '1.1.0'})

    def _weather(self, path, window):
        from ui.presentation import SessionMetrics
        from ui.token_weather import render_token_weather
        events = []
        for node in path:
            source = self.branches.node_events(node)
            if any(event.get('kind') == 'verification' for event in source):
                continue  # Local verification does not consume model context.
            main = [event for event in source if event.get('kind') == 'turn_start' or
                    event.get('kind') in {'usage', 'usage_missing'} and event.get('data', {}).get('scope', 'main') == 'main']
            if not any(event.get('kind') == 'turn_start' for event in main):
                events.append({'kind': 'turn_start', 'data': {'visible': True}})
            events.extend(main)
        metrics = SessionMetrics.from_events(events)
        current = metrics.last_input_tokens
        percent = current / window * 100 if current is not None and window else 0 if not path else None
        level = ('unknown' if percent is None else 'clear' if percent < 30 else
                 'cloudy' if percent < 50 else 'showers' if percent < 80 else 'storm')
        text = render_token_weather(metrics, context_window=window, width=160, show_empty=True).plain
        return {'level': level, 'text': text, 'last_input_tokens': current,
                'context_window': window, 'percent': percent, 'history': metrics.context_history}

    def _public_run(self, run):
        result={key: run.get(key) for key in ('id', 'branch_id', 'status', 'node_id', 'message', 'error', 'plan_id',
                                             'queued_at','started_at','finished_at','elapsed_ms','activity','tools','goal','progress','continuation')}
        if run.get('_started_monotonic') is not None and run['status'] in {'running','waiting_approval','stopping'}:
            result['elapsed_ms']=int((time.monotonic()-run['_started_monotonic'])*1000)
        result['map_origin'] = run['payload'].get('_gui_map_origin')
        result['request_id'] = run['payload'].get('request_id')
        return result

    def _save_run(self, run):
        for key in ('plan_id', 'error','queued_at','started_at','finished_at','elapsed_ms','tools','activity','goal','progress','continuation'):
            if run.get(key) is not None:
                run['payload']['_gui_'+key] = run[key]
            else:
                run['payload'].pop('_gui_'+key, None)
        with self.branches.db:
            self.branches.db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)',
                                    (run['id'], run['branch_id'], run['status'], json.dumps(self.safe(run['payload'])),
                                     run.get('start_index'), run.get('node_id')))

    async def command(self, name, payload):
        async with self.command_lock:
            return await self._command_unlocked(name, payload)

    def _set_branch_mode(self, identity, mode):
        with self.branches.db:
            self.branches.db.execute('INSERT OR REPLACE INTO gui_branch_modes VALUES (?,?)', (identity, mode))
        self.branch_modes[identity] = mode

    async def _command_unlocked(self, name, payload):
        if not isinstance(payload, dict):
            raise ValueError('命令参数必须是对象。')
        if payload.get('project_id', self.sessions.root.name) != self.sessions.root.name:
            raise ValueError('项目已切换，请重新加载后再操作。')
        if name == 'work_control':
            from nailong.gui.work_control import execute
            return await execute(self, payload)
        if name in {'settings', 'settings_update', 'provider'}:
            from nailong.gui.settings import GUISettings
            settings = GUISettings(self)
            if name == 'settings':
                return settings.catalog()
            if name == 'settings_update':
                result = settings.update(payload.get('changes', {}))
            else:
                result = await settings.provider(payload)
            self.emit('settings_updated', {})
            return result
        if name in {'plugins', 'plugin'}:
            from nailong.gui.plugins import GUIPlugins
            plugins = GUIPlugins(self)
            return plugins.catalog() if name == 'plugins' else await plugins.mutate(payload)
        if name == 'create':
            from nailong.gui.settings import GUISettings
            mode = GUISettings(self).preferences()['preferences']['default_permission']
            branch = self.branches.create(payload.get('name', '新会话'))
            self._set_branch_mode(branch['id'], mode)
            return branch
        if name == 'model':
            from nailong.gui.controls import change_model
            return change_model(self, payload)
        if name == 'workspaces':
            from nailong.gui.controls import workspace_catalog
            return self.safe(workspace_catalog(self, payload.get('paths', [])))
        if name == 'open_project':
            if self.current_task or self.queue:
                raise ValueError('请先停止运行并移除队列，再切换项目。')
            candidate = GUIController(payload['path'])
            sequence = self.sequence
            command_lock = self.command_lock
            await self.close()
            self.__dict__.update(candidate.__dict__)
            self.command_lock = command_lock
            self.sequence = sequence
            self.emit('project_opened', {'path': str(self.project)})
            return self.snapshot()
        if name in {'send', 'verify'}:
            return self._enqueue(payload, verify=name == 'verify')
        if name == 'map_send':
            return await self._map_send(payload)
        if name == 'trace':
            from nailong.gui.trace import branch_trace
            return branch_trace(self, payload['branch_id'], payload.get('limit', 80))
        if name == 'draft':
            return {'text': self.branches.draft(payload['branch_id'], payload['text'])}
        if name == 'layout':
            conversation = payload.get('conversation_id')
            if conversation and not any(b['conversation_id'] == conversation for b in self.branches.branches()):
                raise ValueError('会话不属于当前项目。')
            return self.branches.save_layout(payload['layout'], payload['revision'], conversation)
        if name == 'permission':
            from nailong.core.reasoning import PERMISSION_OPTIONS
            branch = self.branches.branch(payload['branch_id'])
            mode = payload.get('mode')
            if mode not in {row[0] for row in PERMISSION_OPTIONS}:
                raise ValueError('权限设置无效。')
            if any(run['branch_id'] == branch['id'] and run['status'] in {'queued', 'running', 'waiting_approval', 'stopping'} for run in self.runs.values()):
                raise ValueError('请等待该分支运行或排队任务结束后修改权限。')
            self._set_branch_mode(branch['id'], mode)
            return {'permission_mode': mode}
        if name == 'branch':
            identity = payload['branch_id']
            if payload.get('archived') and any(run['branch_id'] == identity and run['status'] in {'queued', 'running', 'waiting_approval', 'stopping'} for run in self.runs.values()):
                raise ValueError('请先停止该分支的运行再归档。')
            return self.branches.update_branch(identity, name=payload.get('name'), archived=payload.get('archived'), default=payload.get('default', False))
        if name == 'approve':
            action = self.approvals.get(payload['action_id'])
            if action is None or action['public']['run_id'] != payload.get('run_id') or action['future'].done():
                raise ValueError('审批已过期或不属于该运行。')
            from nailong.core.permissions import ApprovalDecision
            decision = payload.get('decision')
            if decision not in {'approve_once', 'approve_session', 'reject'}:
                raise ValueError('审批决定无效。')
            action['future'].set_result(ApprovalDecision(decision))
            self.approvals.pop(payload['action_id'])
            return {'accepted': True}
        if name == 'stop':
            run = self.runs.get(payload['run_id'])
            if not run:
                raise ValueError('运行不存在。')
            self.paused = True
            if run['status'] == 'queued':
                self.queue.remove(run['id'])
                run['status'] = 'stopped'
                self._save_run(run)
            elif self.current_task and run['status'] in {'running', 'waiting_approval'}:
                run['status'] = 'stopping'
                self.emit('run', self._public_run(run), run)
                self.current_task.cancel()
                try:
                    await self.current_task
                except asyncio.CancelledError:
                    pass
            return self._public_run(run)
        if name == 'resume_queue':
            self.paused = False
            self._start_worker()
            return {'resumed': True}
        if name == 'resume_run':
            run = self.runs.get(payload['run_id'])
            if not run or run['status'] not in {'interrupted', 'failed', 'stopped'}:
                raise ValueError('该运行不能恢复。')
            if run['payload'].get('profile') == 'goal' and run.get('goal'):
                return self._enqueue({'branch_id':run['branch_id'], 'message':run['payload']['message'],
                    'profile':'goal','goal_id':run['goal']['id'],'goal_limits':run['payload']['goal_limits'],
                    'request_id':payload['request_id']})
            if run['start_index'] is None:
                return self._enqueue({**run['payload'], 'request_id': payload['request_id']}, verify=run['payload'].get('verify', False))
            return self._enqueue({'branch_id': run['branch_id'], 'message': '继续上次任务。请先核对已完成操作和当前工作区，不重复执行未知结果的副作用。\n原任务：'+run['payload']['message'],
                                  'request_id': payload['request_id']}, verify=run['payload'].get('verify', False))
        if name == 'fork':
            return await self._fork(payload)
        if name == 'compare':
            left, right = self.branches.branch(payload['left']), self.branches.branch(payload['right'])
            if left['conversation_id'] != right['conversation_id']:
                raise ValueError('只能比较同会话的两条分支。')
            a, b = self.branches.path(left['id']), self.branches.path(right['id'])
            common = 0
            for na, nb in zip(a, b):
                if na['id'] != nb['id']:
                    break
                common += 1
            return self.safe({'shared_count': common, 'left': a[common:], 'right': b[common:],
                              'usage': self.branches.usage(left['conversation_id']), 'workspace': '当前共享工作区；没有历史分支文件快照'})
        if name == 'node':
            return self._node_detail(payload['node_id'])
        if name == 'export':
            branch = self.branches.branch(payload['branch_id'])
            return {'filename': 'ignovate-'+branch['id'][:8]+'.json', 'text': json.dumps(self.safe({'project': str(self.project), 'branch': branch,
                    'path': [self._node_detail(n['id']) for n in self.branches.path(branch['id'])]}), ensure_ascii=False, indent=2)}
        if name == 'configuration':
            if self.current_task or self.queue:
                raise ValueError('请先完成或停止运行再修改模型。')
            from nailong.core.bootstrap import BootstrapStore
            from nailong.core.preferences import PreferenceStore, read_config, atomic_json
            from nailong.core.connection import validate_connection
            from nailong.core.reasoning import model_reasoning_kwargs
            store = BootstrapStore()
            previous = store.read()
            key = payload.get('api_key', '').strip() or self.sessions.api_key or previous.get('provider', {}).get('api_key', '')
            api_base, model, _ = validate_connection(payload['api_base'], payload['model'], key)
            effort = payload.get('reasoning_effort', 'default')
            model_reasoning_kwargs(model, effort)
            # Reconcile the composer override with the newly selected connection.
            # Validate both project fields together: the old model may not support
            # the new effort (or the old effort may not support the new model).
            preferences = PreferenceStore(self.project)
            local = read_config(preferences.local_path, self.project)
            local.update(model='default', reasoning_effort=effort)
            preferences.effective(default_model=model, _replacement=(preferences.local_path, local))
            saved = store.save(api_base, model, payload.get('api_key', ''), effort, existing_key=self.sessions.api_key)
            atomic_json(preferences.local_path, local, self.project)
            await self._close_factory()
            self.sessions.api_key = saved['provider']['api_key']
            return self.configuration()
        if name == 'test_connection':
            if self.current_task or self.queue:
                raise ValueError('请在项目空闲时测试连接。')
            service = self.ensure_service()
            from nailong.core.project_execution import project_execution
            with project_execution(self.sessions):
                answer = await self.factory.model.ainvoke('Reply with OK. Do not call tools.')
            return {'ok': bool(answer.content), 'notice': '本次测试已产生模型请求，可能计费。'}
        if name == 'reasoning_options':
            from nailong.core.reasoning import reasoning_options
            return reasoning_options(payload.get('model', ''))
        if name == 'reasoning':
            if self.current_task or self.queue:
                raise ValueError('请先完成或停止运行并移除队列，再修改推理等级。')
            configuration = self.configuration()
            if not configuration['configured'] or payload.get('model') != configuration['model']:
                raise ValueError('模型已更换，请重新打开推理设置。')
            from nailong.core.reasoning import model_reasoning_kwargs
            effort = payload.get('effort')
            model_reasoning_kwargs(configuration['model'], effort)
            self.ensure_service()
            if self.factory.settings.model != configuration['model']:
                raise ValueError('模型已更换，请重新打开推理设置。')
            updated = self.factory.preferences.set('reasoning_effort', effort)
            self.factory.set_reasoning(updated['reasoning_effort'])
            configuration = self.configuration()
            self.emit('configuration_updated', configuration)
            return configuration
        if name == 'accept_plan':
            run = self.runs.get(payload['run_id'])
            if not run or not run.get('plan_id'):
                raise ValueError('没有待批准计划。')
            self.ensure_service()
            if self.current_task:
                raise ValueError('请等待当前运行结束后批准计划。')
            if self.factory.plan_store.get(run['plan_id']) is None:
                run['plan_id'] = self.factory.plan_store.stage(self._node_detail(run['node_id'])['answer'])
            from nailong.core.project_execution import project_execution
            with project_execution(self.sessions):
                approved = self.factory.plan_store.approve(run.pop('plan_id'))
            self._save_run(run)
            return self._enqueue({'branch_id': run['branch_id'], 'message': '按已批准的计划实施：\n'+approved.markdown,
                                  'request_id': payload['request_id'], 'pin_message': 'approved_plan'})
        if name == 'workspace':
            from nailong.core.git_changes import GitChanges
            changes = GitChanges(self.project, api_key=self.sessions.api_key).select()
            return self.safe({'label': '当前工作区', 'base': changes.base, 'truncated': changes.truncated,
                              'files': [{'path': c.path, 'status': c.status, 'patch': c.patch} for c in changes.changes], 'skipped': list(changes.skipped)})
        if name == 'diagnostics':
            from nailong.core.diagnostics import diagnose
            return self.safe(diagnose(self.project))
        if name == 'file':
            from nailong.tools.files import FileSession
            from nailong.core.permissions import PermissionEngine, Decision
            decision = PermissionEngine(self.project).decide_action('read_file', {'path': payload['path']})
            if decision.decision != Decision.ALLOW:
                raise ValueError('该文件不允许直接预览，请通过内核审批读取。')
            return self.safe(FileSession(self.project).read_file(payload['path'], max_chars=16000))
        if name == 'folder':
            from nailong.tools.files import FileSession
            from nailong.core.permissions import PermissionEngine, Decision
            path = payload.get('path', '.')
            if not isinstance(path, str) or PermissionEngine(self.project).decide_action('read_file', {'path': path}).decision != Decision.ALLOW:
                raise ValueError('该文件夹不允许直接引用。')
            session = FileSession(self.project)
            if not session.resolve(path).is_dir():
                raise ValueError('请选择项目内文件夹。')
            return self.safe(session.list_files(path, limit=100))
        if name == 'capabilities':
            service = self.ensure_service()
            return self.safe({'skills': self.factory.skill_registry.catalog(), 'memory': self.factory.memory_store.catalog(),
                              'mcp': {name: self.factory.mcp_manager.status(name) for name in self.factory.mcp_manager.store.list_servers()},
                              'context': service.get_context_summary(self.branches.branch(payload['branch_id'])['thread_id'])})
        raise ValueError('不支持的命令。')

    @staticmethod
    def _validate_input(payload, *, verify=False):
        identity = payload.get('request_id')
        if not isinstance(identity, str) or not 1 <= len(identity) <= 128:
            raise ValueError('请求须有稳定 request_id。')
        message = payload.get('message', '运行配置的验证步骤' if verify else '')
        if not isinstance(message, str) or not message.strip() or len(message) > 100_000:
            raise ValueError('输入须为 1–100000 个字符。')
        profile = payload.get('profile', 'chat')
        if profile not in {'chat', 'plan', 'review', 'goal'}:
            raise ValueError('运行模式无效。')
        return identity, message, profile

    async def _map_send(self, payload):
        identity, message, _ = self._validate_input(payload)
        encoded = json.dumps({'operation': 'map_send', 'payload': payload}, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        existing = self.branches.db.execute('SELECT payload,result FROM requests WHERE id=?', (identity,)).fetchone()
        if existing:
            if existing['payload'] != fingerprint:
                raise ValueError('请求 ID 已用于不同输入。')
            return json.loads(existing['result'])
        if self.current_task or self.queue:
            raise ValueError('请等待当前运行与队列结束，再从地图继续。')
        branch = self.branches.branch(payload['branch_id'])
        kind = payload.get('kind')
        if kind not in {'root', 'continue', 'fork'}:
            raise ValueError('地图追问类型无效。')
        from nailong.core.reasoning import PERMISSION_OPTIONS
        mode = payload.get('permission_mode')
        if mode is not None and mode not in {option[0] for option in PERMISSION_OPTIONS}:
            raise ValueError('权限设置无效。')
        needs_fork = False
        if kind == 'root':
            if branch['head'] or branch['parent_id']:
                raise ValueError('该会话已有回合，请从回合卡片继续。')
        else:
            node = next((node for node in self.branches.path(branch['id']) if node['id'] == payload.get('node_id')), None)
            if not node or not node['recoverable'] or node['status'] != 'completed':
                raise ValueError('该回合无法精确恢复，请选择可恢复回合。')
            # A historical follow-up must never inherit later content. Native
            # checkpoint fork supplies the exact boundary before enqueueing.
            needs_fork = kind == 'fork' or branch['head'] != node['id']
        intent = self.branches.db.execute('SELECT * FROM map_requests WHERE id=?', (identity,)).fetchone()
        if intent and intent['fingerprint'] != fingerprint:
            raise ValueError('请求 ID 已用于不同输入。')
        if not intent:
            target_id = uuid.uuid4().hex if needs_fork else branch['id']
            target_thread = uuid.uuid4().hex if needs_fork else branch['thread_id']
            # Publish the chosen child BEFORE checkpoint copy/branch creation.
            # Both an enqueue failure and a process restart reuse this identity.
            with self.branches.db:
                self.branches.db.execute('INSERT INTO map_requests VALUES (?,?,?,?)',
                                        (identity, fingerprint, target_id, target_thread))
            intent = self.branches.db.execute('SELECT * FROM map_requests WHERE id=?', (identity,)).fetchone()
        if intent['branch_id'] != branch['id']:
            target = self.branches.db.execute('SELECT * FROM branches WHERE id=?', (intent['branch_id'],)).fetchone()
            if target:
                branch = dict(target)
            else:
                branch = await self._fork({'branch_id': branch['id'], 'node_id': node['id'],
                    'name': ' '.join(message.split())[:60]}, target_branch_id=intent['branch_id'], target_thread_id=intent['thread_id'])
        elif needs_fork:
            raise ValueError('来源路径已变化，请重新从该回合开始追问。')
        result = self._enqueue({**payload, 'branch_id': branch['id'],
            '_gui_map_origin': {'node_id': payload.get('node_id'), 'kind': kind}}, request_fingerprint=fingerprint,
                               permission_override=mode)
        if mode is not None:
            self._set_branch_mode(branch['id'], mode)
        return result

    def _enqueue(self, payload, *, verify=False, request_fingerprint=None, permission_override=None):
        identity = payload.get('request_id')
        if not isinstance(identity, str) or not 1 <= len(identity) <= 128:
            raise ValueError('请求须有稳定 request_id。')
        encoded = json.dumps({'operation': 'verify' if verify else 'send', 'payload': payload}, sort_keys=True, ensure_ascii=False)
        fingerprint = request_fingerprint or hashlib.sha256(encoded.encode()).hexdigest()
        existing = self.branches.db.execute('SELECT payload,result FROM requests WHERE id=?', (identity,)).fetchone()
        if existing:
            if existing['payload'] != fingerprint:
                raise ValueError('请求 ID 已用于不同输入。')
            return json.loads(existing['result'])
        branch = self.branches.branch(payload['branch_id'])
        if branch['history_conflict']:
            raise ValueError('CLI 已回退或改写该会话；已保留 GUI 历史，请基于摘要创建新会话。')
        if branch['archived']:
            raise ValueError('请先取消归档。')
        if branch['legacy'] and (not branch['head'] or not self.branches.path(branch['id'])[-1]['recoverable']):
            # Interrupted new runs may continue explicitly; imported logs remain read-only.
            if branch['legacy']:
                raise ValueError('旧会话仅可查看，请基于摘要创建新会话。')
        message = payload.get('message', '运行配置的验证步骤' if verify else '')
        if not isinstance(message, str) or not message.strip() or len(message) > 100_000:
            raise ValueError('输入须为 1–100000 个字符。')
        profile = payload.get('profile', 'chat')
        if profile not in {'chat', 'plan', 'review', 'goal'}:
            raise ValueError('运行模式无效。')
        self.ensure_service()
        if profile == 'goal':
            from nailong.gui.controls import validate_goal
            payload = {**payload, 'goal_limits': validate_goal(self.factory, payload.get('goal_limits', {}))}
            if self.factory.goal_store.active() or any(self.runs[id]['payload'].get('profile')=='goal' for id in self.queue):
                raise ValueError('已有进行中的目标，请先完成或暂停它。')
            if payload.get('goal_id'):
                goal = self.factory.goal_store.get(payload['goal_id'])
                if not goal or goal.thread_id != branch['thread_id'] or goal.state != 'paused':
                    raise ValueError('目标无法在该会话恢复。')
                reason = self.factory.goal_store._limit_reason(goal)
                if reason:
                    raise ValueError(reason + ' 请创建新目标。')
        if not verify:self.branches.title_from_input(branch['id'],message)
        run_id = uuid.uuid4().hex
        run = {'id': run_id, 'branch_id': branch['id'], 'status': 'queued', 'message': self.safe(message),
               'queued_at':datetime.now(timezone.utc).isoformat(),'tools':[],
               'payload': {**payload, 'message': message, 'verify': verify,
                           'permission_mode': permission_override or self.branch_modes.get(branch['id'], 'default')}, 'start_index': None, 'node_id': None}
        result = {'run_id': run_id, 'status': 'queued', 'branch_id': branch['id']}
        with self.branches.db:
            self.branches.db.execute('INSERT INTO requests VALUES (?,?,?)', (identity, fingerprint, json.dumps(result)))
            self._save_run(run)
        self.runs[run_id] = run
        self.queue.append(run_id)
        self.branches.draft(branch['id'], '')
        self.emit('run', self._public_run(run), run)
        self._start_worker()
        return result

    def _start_worker(self):
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._pump())

    async def _pump(self):
        while self.queue and not self.paused:
            run = self.runs[self.queue.pop(0)]
            self.current_task = asyncio.create_task(self._execute(run))
            try:
                await self.current_task
            except asyncio.CancelledError:
                pass
            finally:
                self.current_task = None

    async def _approval(self, run, action, index, total):
        identity = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        public = {'action_id': identity, 'run_id': run['id'], 'branch_id': run['branch_id'],
                  'action': self.safe(action), 'index': index, 'total': total}
        self.approvals[identity] = {'future': future, 'public': public}
        run['status'] = 'waiting_approval'
        self.emit('approval', public, run)
        try:
            return await future
        finally:
            self.approvals.pop(identity, None)
            if run['status'] != 'stopping':
                run['status'] = 'running'

    async def _execute(self, run):
        from nailong.core.project_execution import project_execution
        try:
            with project_execution(self.sessions):
                # Check for CLI additions after enqueue and before model input.
                self.branches.import_sessions()
                branch = self.branches.branch(run['branch_id'])
                if branch['legacy'] or branch['history_conflict']:
                    raise ValueError('CLI 已更新该会话；请查看同步的历史并基于摘要新建。')
                with self.sessions.run_scope(run['id'], branch['thread_id']):
                    await self._execute_owned(run)
        except (BlockingIOError, ValueError) as error:
            run.update(status='failed', error='当前项目被另一 CLI / GUI 运行占用。请等待后显式恢复。' if isinstance(error, BlockingIOError) else str(error))
            self.paused = True
            run['finished_at']=datetime.now(timezone.utc).isoformat()
            self._save_run(run)
            self.emit('run', self._public_run(run), run)

    async def _execute_owned(self, run):
        branch = self.branches.branch(run['branch_id'])
        if hasattr(self.service, 'set_permission_mode'):
            self.service.set_permission_mode(run['payload'].get('permission_mode', 'default'))
        if hasattr(self.service, 'permission_engine'):
            from nailong.core.permissions import PermissionEngine
            engine = self.branch_permissions.setdefault(branch['id'], PermissionEngine(self.project))
            self.service.permission_engine = engine
            execution = getattr(self.factory, 'tool_execution_context', None)
            if execution is not None:
                execution.permission_engine = engine
        run['status'] = 'running'
        run['started_at']=datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        run['_started_monotonic']=time.monotonic()
        run['start_index'] = len(self.sessions.read_events(branch['thread_id']))
        from nailong.core.control_ledger import ControlLedger
        ControlLedger(self.sessions).begin(run['id'], thread_id=branch['thread_id'], request_id=run['payload']['request_id'])
        self._save_run(run)
        self.emit('run', self._public_run(run), run)
        checkpoint = None
        try:
            payload = run['payload']
            approval = lambda action, index=1, total=1: self._approval(run, action, index, total)
            if payload['verify']:
                from nailong.core.project_execution import project_execution
                from nailong.core.verification import VerificationService
                verifier = VerificationService(self.project, self.sessions, api_key=self.sessions.api_key, task_store=self.service.task_store)
                with project_execution(self.sessions):
                    self.sessions.append_event(branch['thread_id'], 'user', {'text': payload['message']})
                    result = await verifier.run(branch['thread_id'], approval=approval,
                        emit=lambda event: self.emit(event.kind, event.data, run), permission_engine=self.service.permission_engine,
                        permission_mode=self.service.permission_mode)
                    self.sessions.append_event(branch['thread_id'], 'final', {'text': json.dumps(self.safe(result), ensure_ascii=False)})
                    self.emit('final', {'text': json.dumps(self.safe(result), ensure_ascii=False)}, run)
            elif payload.get('profile') == 'goal':
                from ui.flows import drive_goal
                from nailong.core.costs import CostEstimator
                from dataclasses import asdict
                if payload.get('goal_id'):
                    resumed, reason, goal = self.factory.goal_store.resume(payload['goal_id'], thread_id=branch['thread_id'])
                    if not resumed:
                        raise ValueError(reason)
                else:
                    goal = self.factory.goal_store.create(payload['message'], thread_id=branch['thread_id'], **payload['goal_limits'])
                async def goal_event(event):
                    data = dict(event.data)
                    if event.kind == 'goal_status':
                        data['goal'] = asdict(data['goal'])
                        run['goal'] = data['goal']
                    self.emit(event.kind, data, run)
                await drive_goal(self.service, self.factory, self.factory.goal_store,
                    CostEstimator(self.factory.settings.model, self.project), goal,
                    thread_id=branch['thread_id'], emit=goal_event, approval=approval,
                    history_display=payload['message'])
            else:
                async for event in self.service.stream_turn(payload['message'], {'configurable': {'thread_id': branch['thread_id']}},
                        profile=payload.get('profile', 'chat'), approval_handler=approval,
                        pin_message=payload.get('pin_message', False), history_display=payload['message']):
                    self.emit(event.kind, event.data, run)
            run['status'] = 'completed'
            checkpoint = self._checkpoint(branch['thread_id'])
        except BlockingIOError:
            run.update(status='failed', error='当前项目被另一 CLI / GUI 运行占用。请等待后显式恢复。')
            self.paused = True
        except asyncio.CancelledError:
            run['status'] = 'stopped'
            raise
        except Exception as error:
            run.update(status='failed', error=self.safe(str(error)))
            self.paused = True
        finally:
            if run['payload'].get('profile') == 'goal':
                from dataclasses import asdict
                goal = self.factory.goal_store.latest()
                if goal and goal.thread_id == branch['thread_id']:
                    run['goal'] = asdict(goal)
            run['elapsed_ms']=int((time.monotonic()-run['_started_monotonic'])*1000)
            run['finished_at']=datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            for tool in run.get('tools',[]):
                if tool.get('status')=='running':tool['status']='unknown' if run['status']=='completed' else 'interrupted'
            if len(self.sessions.read_events(branch['thread_id'])) > run['start_index']:
                self.sessions.append_event(branch['thread_id'],'gui_run_timing',
                    {key:run[key] for key in ('started_at','finished_at','elapsed_ms')})
                task = self.service.task_store.snapshot(branch['thread_id']) if self.service.task_store else None
                node = self.branches.commit_turn(branch['id'], run['start_index'], checkpoint,
                                                task_snapshot=task, status=run['status'])
                run['node_id'] = node['id']
                if run['status'] == 'completed' and run['payload'].get('profile') == 'plan' and hasattr(self.factory, 'plan_store'):
                    run['plan_id'] = self.factory.plan_store.stage(node['answer'])
            from nailong.gui.work_control import closeout
            closeout(self, run, branch)
            self._save_run(run)
            self.emit('run', self._public_run(run), run)

    def _node_detail(self, node_id):
        node = self.branches.node(node_id)
        events = self.branches.node_events(node)
        from nailong.core.branches import project_turn
        result = project_turn(node, events)
        result.pop('task_snapshot', None)
        saved = None
        if node['checkpoint_id']:
            config = {'configurable': {'thread_id': node['thread_id'], 'checkpoint_ns': '', 'checkpoint_id': node['checkpoint_id']}}
            saver = getattr(self.factory, 'checkpointer', None)
            if saver:
                saved = saver.get_tuple(config)
            elif self.sessions.database_path.exists():
                from langgraph.checkpoint.sqlite import SqliteSaver
                with SqliteSaver.from_conn_string(str(self.sessions.database_path)) as source:
                    saved = source.get_tuple(config)
            if saved:
                for message in reversed(saved.checkpoint['channel_values'].get('messages', [])):
                    if getattr(message, 'type', '') == 'ai' and message.content and not getattr(message, 'tool_calls', None):
                        full = str(message.content)
                        if len(result['answer']) >= 12000 and self.safe(full).startswith(result['answer']):
                            result['answer'] = full
                        break
        verification = result['delivery'].get('verification')
        if verification and verification.get('status') == 'passed':
            from nailong.core.verification import input_fingerprint
            try:
                current = input_fingerprint(self.project, verification.get('generated_paths', []))
                if current != verification.get('input_after'):
                    result['delivery']['status'] = 'stale'
            except (ValueError, OSError):
                result['delivery']['status'] = 'unknown'
        from nailong.gui.presentation import artifact_cards
        return self.safe({**result,'artifacts':artifact_cards(result), 'events': events})

    async def _fork(self, payload, *, target_branch_id=None, target_thread_id=None):
        branch = self.branches.branch(payload['branch_id'])
        path = self.branches.path(branch['id'])
        node = next((n for n in path if n['id'] == payload['node_id']), None)
        if node is None or not node['recoverable'] or node['status'] != 'completed':
            raise ValueError('该回合不能精确恢复，请选择更早完整回合。')
        self.ensure_service()
        if self.current_task:
            raise ValueError('请等待当前项目运行结束后分叉。')
        before = bool(payload.get('before'))
        ancestor = self.branches.node(node['parent_id']) if before and node['parent_id'] else None
        source = ancestor if before else node
        target_thread = target_thread_id or uuid.uuid4().hex
        from nailong.core.project_execution import project_execution
        with project_execution(self.sessions):
            from nailong.core.branches import ForkHistory
            history = ForkHistory(self.factory.history_archive, source['thread_id'], target_thread) if source else None
            task = history.copy(json.loads(source['task_snapshot'])) if source and source.get('task_snapshot') else None
            if source:
                if not source['checkpoint_id']:
                    raise ValueError('该边界缺少可恢复 checkpoint。')
                reference = clone_checkpoint(self.factory.checkpointer, source['thread_id'], source['checkpoint_id'], target_thread, history=history)
            else:
                reference = 'empty-boundary'
            if task and self.service.task_store:
                task.update(thread_id=target_thread, task_id=uuid.uuid4().hex, revision=task['revision']+1, lifecycle='paused',
                            blockers=['从历史回合创建；继续前核对当前工作区和历史证据。'])
                for item in task['acceptance']:
                    if item['status'] == 'passed':
                        item['status'] = 'stale'
                with self.service.task_store._locked(target_thread):
                    self.service.task_store._write(target_thread, task)
                    self.service.task_store._read(target_thread)
            child = self.branches.create_fork(branch['id'], node['id'], payload.get('name', '备选方案'), reference, before=before, thread_id=target_thread, identity=target_branch_id)
        return child

    async def _close_factory(self):
        if self.factory:
            close = getattr(self.factory, 'aclose', None)
            if close:
                await close()
            elif getattr(self.factory, 'close', None):
                self.factory.close()
        self.factory = self.service = None
        self.branch_permissions.clear()

    async def close(self):
        self.paused = True
        if self.current_task:
            self.current_task.cancel()
        if self.worker:
            await self.worker
        await self._close_factory()
        self.branches.close()
