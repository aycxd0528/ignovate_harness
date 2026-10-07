"""Persist safe context changes before every model call and record actual input."""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
from time import perf_counter
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage, ToolMessage, message_to_dict
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from nailong.core.compact import compact_messages, normalize_pins
from nailong.core.context import RequestEstimateCache, configured_context_window, context_budget, request_report
from nailong.core.usage import message_usage
from nailong.core.task_context import TaskContextError, render_task_context, validate_task_snapshot


_TASK_MESSAGE_ID = 'nailong-current-task-projection-v1'
_TASK_MESSAGE_NAME = 'nailong_task_context'
_MEMORY_PROJECTION_KEY = 'nailong_memory_projection_v1'
_UNSET_SYSTEM_MESSAGE = object()


def _same_archive_value(left,right):
    """Deep equality with JSON value types preserved (1 != true)."""
    if type(left) is not type(right):
        return False
    if isinstance(left,dict):
        return len(left)==len(right) and all(
            _same_archive_value(key,other_key) and _same_archive_value(value,other_value)
            for (key,value),(other_key,other_value) in zip(left.items(),right.items()))
    if isinstance(left,(list,tuple)):
        return len(left)==len(right) and all(_same_archive_value(a,b) for a,b in zip(left,right))
    return left==right if type(left) in (str,int,float,bool,type(None)) else False


class ContextLimitExceeded(RuntimeError):
    """Protected input cannot fit the configured model context window."""


class ContextManagerMiddleware(AgentMiddleware):
    def __init__(self,settings,tools,system_prompt,profile,thread_id,store,archive,reports,calibration,parts=None,goal_store=None,request_filter=None,requested_output=4096,memory_budget=None,task_snapshot_provider=None,task_context_max_chars=12000,memory_request_context=None,task_history=None):
        super().__init__()
        self.settings=settings; self.tools=tools; self.system_prompt=system_prompt
        self.profile=profile; self.thread_id=thread_id; self.store=store; self.archive=archive
        self.reports=reports; self.calibration=calibration; self.parts=parts or {}; self.goal_store=goal_store
        self.request_filter=request_filter
        self.requested_output=requested_output or 4096
        self.memory_budget=memory_budget
        self.memory_request_context=memory_request_context
        self._compiled_system_prompt=system_prompt
        self._compiled_parts=dict(self.parts)
        self._memory_initial_context=None
        self._memory_slot=None
        self._memory_latest_context=None
        # A synchronous provider(thread_id) -> snapshot | None; TaskStore is
        # intentionally not imported here. Request-only projections are never
        # returned from before_model as checkpoint state.
        self.task_snapshot_provider=task_snapshot_provider
        self.task_context_max_chars=task_context_max_chars
        self.task_history=task_history
        self._task_versions={}
        self.provider=urlsplit(settings.api_base).hostname or settings.api_base
        self.compaction=None
        self._estimate_cache=RequestEstimateCache()
        self._archived_tools=OrderedDict()
        self._archived_characters=0
        self._last_compaction_failure=None
        self._preflight_view=[]
        self._metrics=self._new_metrics()
        self._preflight_performance=None

    @staticmethod
    def _new_metrics():
        return {'durations_ms':{},'archive_saved':0,'archive_reused':0,
                'compaction_attempts':0,'compaction_skipped':False}

    @contextmanager
    def _measure(self,phase):
        started=perf_counter()
        try:
            yield
        finally:
            durations=self._metrics['durations_ms']
            durations[phase]=durations.get(phase,0.0)+(perf_counter()-started)*1000

    def _policy(self,requested_output=4096):
        from nailong.core.preferences import read_config
        root=self.settings.project_root.resolve()
        config=read_config(root/'.nailong/settings.json',root)
        options=config.get('context',{})
        threshold=options.get('soft_threshold_tokens',150000) if isinstance(options,dict) else 150000
        if not isinstance(threshold,int) or isinstance(threshold,bool) or threshold<1: threshold=150000
        return context_budget(configured_context_window(self.settings.model,self.settings.project_root),requested_output,threshold)

    def _report(self,messages,system_message=_UNSET_SYSTEM_MESSAGE,tools=None):
        if system_message is _UNSET_SYSTEM_MESSAGE:
            system_message=SystemMessage(content=self.system_prompt)
        with self._measure('estimate'):
            return request_report(messages,system_message=system_message,
                tools=self.tools if tools is None else tools,parts=self.parts,
                factor=self.calibration.factor_for(self.settings.model,self.provider),cache=self._estimate_cache)

    def _record(self,kind,data):
        if self.store is not None and self.thread_id:
            self.store.append_event(self.thread_id,kind,data)

    def _refresh_memory_view(self):
        """Refresh once per preflight/wrap, retaining the compiled memory slot."""
        if self.memory_request_context is None:
            return None
        try:
            view=self.memory_request_context.refresh_for_request()
            if (not isinstance(view,dict) or not isinstance(view.get('initial_context'),str)
                    or not isinstance(view.get('context'),str) or not isinstance(view.get('report'),dict)):
                raise ValueError('记忆刷新必须返回 initial_context/context 文本和 report 对象。')
            initial=view['initial_context']; current=view['context']
            compiled=self._compiled_system_prompt
            if not isinstance(compiled,str):
                raise ValueError('动态记忆刷新需要编译时文本系统提示。')
            if self._memory_initial_context is None:
                fixed=self._compiled_parts.get('fixed_memory')
                if fixed is not None and fixed!=initial:
                    raise ValueError('记忆初始正文与编译时 fixed_memory 不匹配。')
                base=self._compiled_parts.get('base_system')
                catalog=self._compiled_parts.get('skill_catalog','')
                if isinstance(base,str) and isinstance(catalog,str) and compiled==base+initial+catalog:
                    slot=(len(base),len(initial))
                elif initial and compiled.count(initial)==1:
                    slot=(compiled.index(initial),len(initial))
                elif not initial and base is None:
                    # No explicit assembly parts: use one fixed tail slot.
                    slot=(len(compiled),0)
                else:
                    raise ValueError('编译时记忆正文没有唯一可替换位置。')
                self._memory_initial_context=initial
                self._memory_slot=slot
                self._memory_latest_context=initial
            elif initial!=self._memory_initial_context:
                raise ValueError('同一运行时的编译初始记忆不能变化；请重建运行时。')
            start,length=self._memory_slot
            previous=self._memory_latest_context
            prior_prompt=self.system_prompt
            refreshed=compiled[:start]+current+compiled[start+length:]
            report=deepcopy(view['report'])
        except Exception as error:
            self._record('context_memory_error',{'reason':'refresh_failed','error_type':type(error).__name__})
            raise ContextLimitExceeded('固定记忆无法安全刷新；未调用提供商。') from error
        self.system_prompt=refreshed
        self.parts={**self.parts,'fixed_memory':current}
        self.memory_budget=report
        self._memory_latest_context=current
        return {'initial_context':initial,'context':current,'report':report,
                'previous_context':previous,'previous_prompt':prior_prompt}

    def _rewrite_memory_system(self,request,view):
        """Replace exactly one owned region; never use replace('', text)."""
        message=request.system_message
        content='' if message is None else message if isinstance(message,str) else message.content
        if not isinstance(content,str):
            raise ContextLimitExceeded('固定记忆刷新无法定位非文本系统提示，未调用提供商。')
        metadata={} if message is None or isinstance(message,str) else dict(message.additional_kwargs or {})
        slot_start,slot_length=self._memory_slot
        identity=self._compiled_system_prompt+'\0'+str(slot_start)+':'+str(slot_length)+'\0'+view['initial_context']
        anchor=hashlib.sha256(identity.encode('utf-8')).hexdigest()
        marker=metadata.get(_MEMORY_PROJECTION_KEY)
        if marker is not None:
            if (not isinstance(marker,dict) or marker.get('anchor')!=anchor
                    or type(marker.get('start')) is not int or type(marker.get('length')) is not int
                    or marker['start']<0 or marker['length']<0
                    or marker['start']+marker['length']>len(content)):
                raise ContextLimitExceeded('请求中的记忆投影位置已改变，不能重复追加或覆盖未知正文。')
            start,length=marker['start'],marker['length']
            if hashlib.sha256(content[start:start+length].encode('utf-8')).hexdigest()!=marker.get('content_sha256'):
                raise ContextLimitExceeded('请求中的记忆投影版本不匹配，未替换未知正文。')
        elif content==self._compiled_system_prompt:
            start,length=self._memory_slot
        elif content==self.system_prompt:
            start=self._memory_slot[0]; length=len(view['context'])
        elif content==view['previous_prompt']:
            start=self._memory_slot[0]; length=len(view['previous_context'])
        elif view['initial_context'] and content.count(view['initial_context'])==1:
            start=content.index(view['initial_context']); length=len(view['initial_context'])
        else:
            # Especially for an empty initial body, an unknown system layout
            # has no reliable insertion point. Reject instead of duplicating.
            raise ContextLimitExceeded('实际系统提示中没有唯一的编译记忆槽位，未追加记忆正文。')
        current=view['context']
        rewritten=content[:start]+current+content[start+length:]
        metadata[_MEMORY_PROJECTION_KEY]={'anchor':anchor,'start':start,'length':len(current),
            'content_sha256':hashlib.sha256(current.encode('utf-8')).hexdigest()}
        if message is None and not rewritten:
            system=None
        elif message is None or isinstance(message,str):
            system=SystemMessage(content=rewritten,additional_kwargs=metadata)
        else:
            system=message.model_copy(update={'content':rewritten,'additional_kwargs':metadata})
        return request.override(system_message=system)

    def _filter_request_messages(self,messages):
        # This object owns both refreshed availability and read-result metadata.
        if self.memory_request_context is not None:
            return self.memory_request_context.filter_messages(messages)
        return self.request_filter(messages) if self.request_filter else messages

    def _task_projection(self,tools=None):
        if self.task_snapshot_provider is None:
            return None,None
        if not self.thread_id:
            raise ContextLimitExceeded('任务上下文投影需要绑定会话；不能使用未绑定快照。')
        try:
            source=self.task_snapshot_provider(self.thread_id)
        except Exception as error:
            self._record('task_context_error',{'reason':'provider_failed','error_type':type(error).__name__})
            raise ContextLimitExceeded('读取当前任务快照失败；未调用提供商。') from error
        if source is None:
            return None,None
        try:
            task=validate_task_snapshot(source)
            if '\0' in task['project_root']:
                raise TaskContextError('任务项目根目录包含无效字符。')
            project=Path(task['project_root'])
            if task['thread_id']!=self.thread_id or not project.is_absolute() or project.resolve()!=self.settings.project_root.resolve():
                raise TaskContextError('当前任务快照不属于调用会话或项目。')
            # Same-revision execution progress may change, but requirements
            # cannot roll back or change without a new requirements revision.
            requirement_fields={key:task[key] for key in ('objective','scope','constraints','request_history')}
            if 'requirements' in task:
                requirement_fields['requirements']=task['requirements']
            requirement_fields['acceptance']=[{
                key:row.get(key) for key in ('id','description','kind','required','verification_binding')
            } for row in task['acceptance']]
            requirements=json.dumps(requirement_fields,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            fingerprint=hashlib.sha256(requirements.encode('utf-8')).hexdigest()
            previous=self._task_versions.get(task['task_id'])
            continuation=task['latest_request'].strip().rstrip('。.!！').casefold() in {'继续','请继续','开始','开始修改','继续修改','continue'}
            same_revision=previous is not None and task['revision']==previous[0]
            if previous and (task['revision']<previous[0] or (same_revision and
                    (fingerprint!=previous[1] or (task['latest_request']!=previous[2] and not continuation)))):
                raise TaskContextError('任务需求版本回退或同版本要求发生变化；请先更新任务状态。')
            if previous and task['request_history'][:len(previous[3])]!=list(previous[3]):
                raise TaskContextError('同一任务的完整用户要求历史被删除或改写；请保留历史或显式开始新任务。')
            available=self.tools if tools is None else tools
            names={tool.get('function',tool).get('name') if isinstance(tool,dict) else tool.name for tool in available}
            reference=None
            if self.task_history is not None and 'read_task_context' in names:
                reference=self.task_history.save(task)
            text=render_task_context(task,self.task_context_max_chars,history_reference=reference)
            secret=getattr(self.settings,'api_key','')
            if secret:
                text=text.replace(secret,'[密钥已隐藏]')
            if len(text)>self.task_context_max_chars:
                raise TaskContextError('脱敏后的任务保护字段超过投影字符预算，未截断当前要求。')
        except (TaskContextError,OSError,RuntimeError) as error:
            self._record('task_context_error',{'reason':str(error),'profile':self.profile})
            raise ContextLimitExceeded('当前任务上下文无法安全投影：'+str(error)) from error
        self._task_versions[task['task_id']]=(task['revision'],fingerprint,task['latest_request'],tuple(task['request_history']))
        info={key:task[key] for key in ('task_id','revision','phase','lifecycle')}
        if secret:
            info['task_id']=info['task_id'].replace(secret,'[密钥已隐藏]')
        info.update(projected=True,characters=len(text))
        if reference is not None:
            info['history_reference']=reference
        message=HumanMessage(id=_TASK_MESSAGE_ID,name=_TASK_MESSAGE_NAME,content=text,
            additional_kwargs={'nailong_task_context':1})
        return message,info

    @staticmethod
    def _assert_completed_tool_boundary(messages):
        """Appending a human projection must not split an outstanding exchange."""
        from nailong.core.context import _tool_calls
        pending=set(); function=None
        for message in messages:
            role=getattr(message,'type','')
            if role=='tool':
                identity=getattr(message,'tool_call_id',None)
                if identity not in pending:
                    raise TaskContextError('任务投影前存在无法配对的工具结果。')
                pending.remove(identity)
                continue
            if role=='function':
                if not function or getattr(message,'name',None)!=function:
                    raise TaskContextError('任务投影前存在无法配对的旧式函数结果。')
                function=None
                continue
            if pending or function:
                raise TaskContextError('任务投影前的工具交换尚未完成；保留原历史，先完成或明确取消调用。')
            if role=='ai':
                try:
                    calls=_tool_calls(message)
                except (TypeError,AttributeError) as error:
                    raise TaskContextError('任务投影前的工具调用结构无效。') from error
                if not isinstance(calls,list) or any(not isinstance(call,dict) for call in calls):
                    raise TaskContextError('任务投影前的工具调用结构无效。')
                identities=[call.get('id') for call in calls]
                if any(not isinstance(identity,str) or not identity for identity in identities) or len(set(identities))!=len(identities):
                    raise TaskContextError('任务投影前的工具调用 ID 缺失或重复。')
                pending.update(identities)
                legacy=(getattr(message,'additional_kwargs',{}) or {}).get('function_call')
                if legacy:
                    if calls or not isinstance(legacy,dict) or not isinstance(legacy.get('name'),str) or not legacy['name']:
                        raise TaskContextError('任务投影前的旧式函数调用结构无效。')
                    function=legacy['name']
        if pending or function:
            raise TaskContextError('任务投影不能插入尚未完成的工具交换；未调用提供商。')

    def _task_request_view(self,messages,projection):
        if self.task_snapshot_provider is None:
            return list(messages)
        # Only our reserved request-only message is replaced. Persisted graph
        # history is not edited; repeated wrapping cannot accumulate snapshots.
        view=[m for m in messages if not (
            getattr(m,'id',None)==_TASK_MESSAGE_ID and getattr(m,'name',None)==_TASK_MESSAGE_NAME and
            (getattr(m,'additional_kwargs',{}) or {}).get('nailong_task_context')==1)]
        if projection is not None:
            try:
                self._assert_completed_tool_boundary(view)
            except TaskContextError as error:
                self._record('task_context_error',{'reason':str(error),'profile':self.profile})
                raise ContextLimitExceeded(str(error)) from error
            view.append(projection)
        return view

    def _preflight_report(self,messages,task_projection=None,memory_view=None):
        # Match the outer memory middleware's actual request view. Its bounded
        # reads need not destructively replace the persistent originals.
        if self.memory_request_context is not None and memory_view is None:
            self._refresh_memory_view()
        with self._measure('request_view'):
            view=self._filter_request_messages(messages)
            self._preflight_view=self._task_request_view(view,task_projection)
        return self._report(self._preflight_view)

    def _archive_tool(self,message,arguments):
        """Reuse eager preservation only; compaction still revalidates on disk.

        No cached reference is used to clear history. compact_messages calls the
        real archive again before replacing an original with a reference.
        """
        key=(message.id,message.tool_call_id)
        serialized=message_to_dict(message)
        previous=self._archived_tools.get(key)
        if previous is not None and _same_archive_value(previous[0],serialized) and _same_archive_value(previous[1],arguments):
            self._archived_tools.move_to_end(key)
            self._metrics['archive_reused']+=1
            return
        self.archive.save(self.thread_id,message,arguments=arguments)
        self._metrics['archive_saved']+=1
        size=len(json.dumps([serialized,arguments],ensure_ascii=False,default=str))
        if previous is not None:
            self._archived_characters-=self._archived_tools.pop(key)[2]
        if size>2_000_000:
            return
        try:
            saved=(deepcopy(serialized),deepcopy(arguments),size)
        except Exception:
            # Cache support is optional. A valid ToolMessage may carry an
            # artifact that can be archived as text but cannot be deep-copied.
            return
        while self._archived_tools and (len(self._archived_tools)>=256 or self._archived_characters+size>2_000_000):
            _,entry=self._archived_tools.popitem(last=False)
            self._archived_characters-=entry[2]
        self._archived_tools[key]=saved
        self._archived_characters+=size

    def _compaction_key(self,policy):
        payload={'messages':[message_to_dict(message) for message in self._preflight_view],
                 'system':self.system_prompt,'parts':self.parts,'policy':policy,
                 'tools':[self._estimate_cache.definition_text(tool) for tool in self.tools],
                 'factor':self.calibration.factor_for(self.settings.model,self.provider),
                 'model':self.settings.model,'provider':self.provider,'archive':self.archive is not None}
        return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str).encode('utf-8')).hexdigest()

    def before_model(self,state,runtime):
        self._metrics=self._new_metrics()
        self.compaction=None
        started=perf_counter()
        failed=True
        try:
            result=self._before_model(state,runtime)
            failed=False
            return result
        finally:
            self._metrics['total_ms']=(perf_counter()-started)*1000
            self._preflight_performance=deepcopy(self._metrics)
            if failed:
                self._record('context_performance',{'stage':'preflight','failed':True,**self._metrics})

    def _before_model(self,state,runtime):
        with self._measure('memory_refresh'):
            memory_view=self._refresh_memory_view()
        with self._measure('task_projection'):
            task_projection,_=self._task_projection()
        original=list(state.get('messages',[])); messages,removed=normalize_pins(original)
        changed=bool(removed)
        goal=self.goal_store.active() if self.goal_store is not None else None
        active_goal=goal is not None and goal.thread_id==self.thread_id
        if not active_goal:
            released=[]
            for message in messages:
                if (message.additional_kwargs or {}).get('nailong_pin')=='goal':
                    metadata=dict(message.additional_kwargs); metadata.pop('nailong_pin',None)
                    message=message.model_copy(update={'additional_kwargs':metadata}); changed=True
                released.append(message)
            messages=released
        # Preserve completed originals before they are cleared. Tool calls are
        # recoverable even after a short failure is folded into an older turn.
        if self.archive is not None and self.thread_id:
            with self._measure('archive'):
                args={call['id']:call.get('args',{}) for m in messages for call in getattr(m,'tool_calls',[]) or []}
                for m in messages:
                    if isinstance(m,ToolMessage) and not m.additional_kwargs.get('nailong_tool_cleared'):
                        self._archive_tool(m,args.get(m.tool_call_id))
        with self._measure('policy'):
            policy=self._policy(self.requested_output)
        report=self._preflight_report(messages,task_projection,memory_view)
        hard=policy['input_limit'] is not None and report['estimated_tokens']>policy['input_limit']
        if report['estimated_tokens']>policy['trigger_tokens']:
            before=report['estimated_tokens']; levels=[]
            with self._measure('compaction'):
                key=self._compaction_key(policy)
                if key==self._last_compaction_failure:
                    self._metrics['compaction_skipped']=True
                else:
                    target=max(1,policy['trigger_tokens']*4//5)
                    for level,keep in [('L1',4),('L2',4),('L2',2),('L2',1)]:
                        # Soft compaction retains four recent turns. A hard
                        # overflow may reduce that protected recent window.
                        if level=='L2' and not hard and keep<4: break
                        self._metrics['compaction_attempts']+=1
                        result=compact_messages(messages,keep_turns=keep,level=level,hard=hard,archive=self.archive,thread_id=self.thread_id)
                        if result.compacted:
                            messages=result.messages; changed=True; levels.append(result.level)
                            report=self._preflight_report(messages,task_projection,memory_view)
                        hard=policy['input_limit'] is not None and report['estimated_tokens']>policy['input_limit']
                        if not hard and report['estimated_tokens']<=target: break
                    # A hard failure returns no checkpoint update, so the next
                    # call sees the original input, not this temporary result.
                    self._last_compaction_failure=(key if hard else self._compaction_key(policy)) if report['estimated_tokens']>policy['trigger_tokens'] else None
            if levels:
                self.compaction={'reason':'hard_window' if before> (policy['input_limit'] or float('inf')) else 'soft_threshold',
                    'level':'+'.join(levels),'before_tokens':before,'after_tokens':report['estimated_tokens'],
                    'gain':max(0,(before-report['estimated_tokens'])/max(1,before))}
                self._record('compact',self.compaction)
        else:
            self._last_compaction_failure=None
        if policy['input_limit'] is not None and report['estimated_tokens']>policy['input_limit']:
            # No provider invocation, current user content and protected state
            # are kept in the checkpoint for explicit recovery.
            self._record('context_limit',{'estimated_tokens':report['estimated_tokens'],**policy})
            raise ContextLimitExceeded(f"完整输入约 {report['estimated_tokens']} tokens，超过当前输入预算 {policy['input_limit']}；固定约束、当前输入或未完成工具调用无法安全压缩。请减少输入、精简固定记忆或选择更大窗口模型。")
        if changed:
            return {'messages':[RemoveMessage(id=REMOVE_ALL_MESSAGES),*messages]}
        return None

    async def abefore_model(self,state,runtime):
        return self.before_model(state,runtime)

    def _prepare_request(self,request):
        self._metrics=self._new_metrics()
        started=perf_counter()
        try:
            request,snapshot=self._build_request(request)
        except BaseException:
            self._metrics['total_ms']=(perf_counter()-started)*1000
            self._record('context_performance',{'stage':'request','failed':True,**self._metrics})
            raise
        self._metrics['total_ms']=(perf_counter()-started)*1000
        snapshot['performance']={'preflight':deepcopy(self._preflight_performance),
                                 'request':deepcopy(self._metrics),'cache':self._estimate_cache.snapshot()}
        if self.thread_id and self.profile!='subagent': self.reports[self.thread_id]=snapshot
        self._record('context_request',snapshot)
        return request,snapshot

    def _build_request(self,request):
        with self._measure('memory_refresh'):
            memory_view=self._refresh_memory_view()
        if memory_view is not None:
            request=self._rewrite_memory_system(request,memory_view)
            request=request.override(messages=self._filter_request_messages(request.messages))
        with self._measure('task_projection'):
            task_projection,task_info=self._task_projection(request.tools)
        if self.task_snapshot_provider is not None:
            request=request.override(messages=self._task_request_view(request.messages,task_projection))
        if self.profile != 'subagent':
            from nailong.core.budgets import active_turn_budget
            budget = active_turn_budget.get()
            if budget is not None:
                request = budget.prepare(request)
        maximum=int(request.model_settings.get('max_tokens') or getattr(request.model,'max_tokens',None) or 4096)
        with self._measure('policy'):
            policy=self._policy(maximum)
        report=self._report(request.messages,request.system_message,request.tools)
        if policy['input_limit'] is not None and report['estimated_tokens']>policy['input_limit']:
            raise ContextLimitExceeded('实际模型请求超过完整输入预算，尚未调用提供商。')
        cap=policy['output_reserve']
        if policy['context_window']:
            cap=min(cap,policy['context_window']-policy['safety_margin']-report['estimated_tokens'])
        cap=max(1,cap)
        request=request.override(model_settings={**request.model_settings,'max_tokens':cap})
        snapshot={**report,**policy,'source':'actual_request','profile':self.profile,
            'tools':sorted(t.get('function',t).get('name','') if isinstance(t,dict) else t.name for t in request.tools),
            'model':self.settings.model,'provider_host':self.provider,'output_reserve':cap,
            'remaining_window':policy['context_window']-report['estimated_tokens']-cap if policy['context_window'] else None,
            'compact_threshold_tokens':policy['trigger_tokens'],'compaction':self.compaction,
            'actual_main_input_tokens':None}
        if self.memory_budget is not None: snapshot['memory_budget']=self.memory_budget
        if self.task_snapshot_provider is not None: snapshot['task_projection']=task_info or {'projected':False}
        return request,snapshot

    def _settle(self,response,snapshot):
        usage=None
        for message in response.result:
            if getattr(message,'type',None)=='ai': usage=message_usage(message)
        actual=usage['input_tokens'] if usage else None
        snapshot['actual_main_input_tokens']=actual
        if actual is not None:
            self.calibration.observe(self.settings.model,self.provider,snapshot['raw_estimated_tokens'],actual)
        self._record('context_result',{'actual_main_input_tokens':actual,'profile':self.profile,'model':self.settings.model,
            'provider_host':self.provider,'raw_estimated_tokens':snapshot['raw_estimated_tokens'],
            'calibration_factor':self.calibration.factor_for(self.settings.model,self.provider)})

    def wrap_model_call(self,request,handler):
        request,snapshot=self._prepare_request(request)
        try: response=handler(request)
        except BaseException as error:
            self._settle_failed_response(error, snapshot)
            raise
        self._settle(response,snapshot); return response

    async def awrap_model_call(self,request,handler):
        request,snapshot=self._prepare_request(request)
        try: response=await handler(request)
        except BaseException as error:
            self._settle_failed_response(error, snapshot)
            raise
        self._settle(response,snapshot); return response

    def _settle_failed_response(self, error, snapshot):
        from nailong.core.budgets import TurnModelLimitExceeded
        if isinstance(error, TurnModelLimitExceeded) and error.completed_response is not None:
            # The provider completed this request; only tool execution was refused.
            self._settle(error.completed_response, snapshot)
        else:
            self._record('context_result', {'actual_main_input_tokens': None,
                'profile': self.profile, 'failed': True})
