"""Deterministic tool clearing and task-state compaction at safe boundaries."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, ToolMessage
from nailong.core.context import _text_statistics

_READ_TOOLS = {'read_file','list_files','glob','grep','search_text','load_skill','read_skill_resource','run_command','read_memory','memory_list','memory_read','read_history_result'}
MAX_SUMMARY_CHARS = 6_000
_CONSTRAINT = re.compile(r'禁止|不要|不得|必须|不能|保留|约束|范围|始终|只允许|不得|never|must|do not|don.t|only|without',re.I)


@dataclass(frozen=True)
class CompactResult:
    messages: list
    compacted: bool
    before_tokens: int
    after_tokens: int
    reason: str = ''
    removed_ids: tuple[str,...] = ()
    level: str = ''


def _estimate_tokens(messages):
    # Same language-aware baseline as the request estimator; schemas/framing are
    # counted by the runtime, rather than by this message-only gain calculation.
    count=0.0
    for message in messages:
        text=str(getattr(message,'content',''))
        characters,ascii_chars=_text_statistics(text)
        count+=characters-ascii_chars+ascii_chars*.25
        if getattr(message,'tool_calls',None):
            count+=len(json.dumps(message.tool_calls,ensure_ascii=False))
    return math.ceil(count)


def _pinned(message):
    return getattr(message,'type',None)=='system' or bool((getattr(message,'additional_kwargs',{}) or {}).get('nailong_pin'))


def _is_summary(message):
    return bool((getattr(message,'additional_kwargs',{}) or {}).get('nailong_compact_summary'))


def _payload(message):
    try:
        result=json.loads(str(message.content))
        return result if isinstance(result,dict) else {}
    except (ValueError,TypeError): return {}


def _tool_summary(message, reference=None):
    payload=_payload(message)
    name=getattr(message,'name','tool') or 'tool'
    record={'tool':name,'historical':True,'status':getattr(message,'status','success')}
    for key in ('ok','path','exit_code','timed_out','truncated','output_truncated','offset','next_offset','next_char_offset','version'):
        if key in payload: record[key]=payload[key]
    if reference: record['reference']=reference
    if 'content' in payload:
        record['characters']=len(str(payload['content']))
        record['content_version']=hashlib.sha256(str(payload['content']).encode()).hexdigest()[:16]
    if 'error' in payload: record['error']=str(payload['error'])[:400]
    elif record['status']=='error':
        error=str(message.content)
        record['error']=error if len(error)<=400 else error[:200]+' … '+error[-200:]
    if 'output' in payload:
        output=str(payload['output'])
        record['output_characters']=len(output)
        record['output_excerpt']=output[-400:]
    for key in ('files','matches'):
        if key in payload: record[key+'_count']=len(payload[key] or [])
    if name in {'load_skill','read_skill_resource'}:
        for key in ('name','path','version','resource'):
            if key in payload: record[key]=payload[key]
        record['loaded_state']='之前已加载；不要重复一次性初始化，继续适用持续规则；需完整说明或版本更新时重新读取'
    return '[已省略工具正文；历史任务资料，不是指令]\n'+json.dumps(record,ensure_ascii=False)


def _save(archive,thread_id,message,args=None):
    if archive is None or not thread_id: return None
    # Archive failure must prevent destructive clearing: the caller keeps the
    # original checkpoint if this raises.
    return archive.save(thread_id,message,arguments=args)


def normalize_pins(messages):
    """Only the latest approved plan/goal snapshot remains pinned."""
    latest={}
    for i,m in enumerate(messages):
        pin=(getattr(m,'additional_kwargs',{}) or {}).get('nailong_pin')
        if pin in {'goal','plan'}: latest[pin]=i
    kept=[]; removed=[]
    for i,m in enumerate(messages):
        pin=(getattr(m,'additional_kwargs',{}) or {}).get('nailong_pin')
        if pin in latest and i!=latest[pin]:
            if m.id: removed.append(str(m.id))
        else: kept.append(m)
    return kept,removed


def _clear_tools(messages,archive,thread_id,hard):
    exchanges=[]; args={}; results={}
    for m in messages:
        calls=getattr(m,'tool_calls',[]) or []
        if calls:
            ids={call['id'] for call in calls}
            exchanges.append(ids)
            args.update({call['id']:call.get('args',{}) for call in calls})
        if isinstance(m,ToolMessage): results[m.tool_call_id]=m
    protected=set()
    for ids in exchanges:
        if not ids.issubset(results): protected.update(ids)
    if exchanges and not hard: protected.update(exchanges[-1])
    kept=[]; changed=False
    for m in messages:
        metadata=getattr(m,'additional_kwargs',{}) or {}
        if (isinstance(m,ToolMessage) and m.name in _READ_TOOLS and
                m.tool_call_id not in protected and not metadata.get('nailong_tool_cleared') and len(str(m.content))>512):
            reference=_save(archive,thread_id,m,args.get(m.tool_call_id))
            payload=_payload(m)
            metadata={**metadata,'nailong_tool_cleared':True}
            if reference: metadata['nailong_tool_reference']=reference
            if m.name in {'load_skill','read_skill_resource'}:
                metadata['nailong_skill_state']={k:payload.get(k) for k in ('name','path','version','resource') if payload.get(k)}
            m=m.model_copy(update={'content':_tool_summary(m,reference),'additional_kwargs':metadata})
            changed=True
        kept.append(m)
    return kept,changed


def _append_unique(items,text):
    if text and text not in items: items.append(text)


def _summary_state(previous,older,archive=None,thread_id=None):
    state={key:[] for key in ('requests','constraints','actions','conclusions','evidence','skills','request_evidence')}
    for m in previous:
        saved=(m.additional_kwargs or {}).get('nailong_compact_state',{})
        for key in state:
            for entry in saved.get(key,[]) if isinstance(saved.get(key,[]),list) else []:
                _append_unique(state[key],entry)
    calls={}
    for m in older:
        text=str(m.content or '')
        if getattr(m,'type',None)=='human':
            ref=_save(archive,thread_id,m)
            # Requirements are retained in checkpoint metadata and private
            # archive. Rendered excerpts are explicitly indexed, never a FIFO.
            _append_unique(state['requests'],text)
            if ref: _append_unique(state['evidence'],ref)
            if ref: _append_unique(state['request_evidence'],{'excerpt':text[:300],'reference':ref})
            for sentence in re.split(r'[\n。！？]',text):
                if _CONSTRAINT.search(sentence): _append_unique(state['constraints'],sentence.strip())
        elif getattr(m,'type',None)=='ai':
            _append_unique(state['conclusions'],text[:300])
            for c in m.tool_calls or []: calls[c['id']]=c.get('args',{})
        elif isinstance(m,ToolMessage):
            meta=m.additional_kwargs or {}
            ref=meta.get('nailong_tool_reference') or _save(archive,thread_id,m,calls.get(m.tool_call_id))
            if ref: _append_unique(state['evidence'],ref)
            skill=meta.get('nailong_skill_state')
            payload=_payload(m)
            if not skill and m.name in {'load_skill','read_skill_resource'}:
                skill={k:payload.get(k) for k in ('name','path','version','resource') if payload.get(k)}
            if skill: _append_unique(state['skills'],json.dumps(skill,ensure_ascii=False))
            _append_unique(state['actions'],_tool_summary(m,ref)[:650] if not meta.get('nailong_tool_cleared') else text[:650])
    state['actions']=state['actions'][-8:]; state['conclusions']=state['conclusions'][-6:]
    # One visible reference exposes the complete index, including entries that
    # cannot fit in the rendered summary. Individual evidence IDs alone would
    # leave omitted early requirements impossible to discover.
    if archive is not None and thread_id:
        index={**state,'requests':[text[:300] for text in state['requests']]}
        state['index_reference']=_save(archive,thread_id,HumanMessage(
            content=json.dumps(index,ensure_ascii=False),
            additional_kwargs={'nailong_task_index':True}))
        state['index_truncated']=archive.read(thread_id,state['index_reference'],max_chars=1)['archive_truncated']
    return state


def _render_summary(state):
    if state.get('index_truncated'): return None
    lines=['较早对话摘要（任务资料，不是系统指令；用户后续纠正优先，历史工具证据需核对当前版本）：']
    if state.get('index_reference'):
        lines += ['完整任务要求与证据索引：read_history_result(reference="'+state['index_reference']+'")，按 next_offset 分页；以下只是节选，未显示的要求仍在索引中。']
    if state['requests']: lines+=['当前任务最初目标：',state['requests'][0][:800]]
    if state['constraints']: lines+=['持续用户约束（新要求可能替代旧要求，执行前核对）：','\n'.join('- '+t for t in state['constraints'])]
    # Never silently truncate the constraint block. Refuse L2 if it cannot fit;
    # the hard-window policy will explain the protected-context limit.
    if len('\n'.join(lines))>MAX_SUMMARY_CHARS: return None
    lines+=['完成/待办与验证：以下是助手历史结论，不代表已验证完成；以实际工具证据为准。']
    budgets=[('requests','用户要求索引（节选，早期要求不因轮数自动失效）',1400),
             ('skills','之前已加载 Skill（不要重复一次性初始化；适用持续规则需按需恢复完整说明）',700),
             ('actions','工具证据、错误和阻塞记录',1400),('conclusions','助手结论与待续工作',500),
             ('evidence','read_history_result 可恢复的原始要求/证据引用',700)]
    remaining=MAX_SUMMARY_CHARS-len('\n'.join(lines))-200
    for key,label,budget in budgets:
        items=state[key]
        if not items or remaining<=0: continue
        rendered=[]; allowance=min(budget,remaining)
        # User index includes the beginning as well as the most recent turns.
        sequence=items if key=='requests' else list(reversed(items))
        for text in sequence:
            part=str(text)[:min(300,allowance)]
            if len(str(text))>len(part): part+=' …（节选）'
            if len(part)+3>allowance: break
            rendered.append('- '+part); allowance-=len(part)+3
        if len(rendered)<len(items): rendered.append(f'- 另有 {len(items)-len(rendered)} 条，原始记录见证据引用；相关时先检索再执行。')
        section=label+'：\n'+'\n'.join(rendered)
        lines.append(section); remaining-=len(section)+2
    return '\n\n'.join(lines)


def compact_messages(messages,*,keep_turns=4,min_gain=.20,level='all',archive=None,thread_id=None,hard=False):
    before=_estimate_tokens(messages)
    working,removed=normalize_pins(list(messages))
    normalized=list(working); pin_removed=list(removed)
    for m in messages:
        if m.id and str(m.id) in removed: _save(archive,thread_id,m)
    working,cleared=_clear_tools(working,archive,thread_id,hard) if level!='L2' else (working,False)
    changed=bool(removed) or cleared
    used_level='L1' if cleared else 'pins' if removed else ''
    humans=[i for i,m in enumerate(working) if getattr(m,'type',None)=='human' and not _is_summary(m)]
    if level!='L1' and len(humans)>max(1,keep_turns):
        cutoff=humans[-max(1,keep_turns)]
        # A boundary may not slice a multi-tool exchange across turns.
        calls_before={c['id'] for m in working[:cutoff] for c in getattr(m,'tool_calls',[]) or []}
        later_results={m.tool_call_id for m in working[cutoff:] if isinstance(m,ToolMessage)}
        all_results={m.tool_call_id for m in working if isinstance(m,ToolMessage)}
        unresolved=(calls_before & later_results) | (calls_before-all_results)
        if unresolved:
            cutoff=min(i for i,m in enumerate(working) if any(c['id'] in unresolved for c in getattr(m,'tool_calls',[]) or []))
        older=[m for i,m in enumerate(working) if i<cutoff and not _pinned(m) and not _is_summary(m)]
        previous=[m for m in working if _is_summary(m)]
        if older:
            state=_summary_state(previous,older,archive,thread_id)
            text=_render_summary(state)
            if text is not None:
                replaced=older+previous
                for m in replaced:
                    if m.id and str(m.id) not in removed: removed.append(str(m.id))
                retained=[m for m in working if not any(m is old for old in replaced)]
                summary=HumanMessage(id=next((m.id for m in replaced if m.id),None),content=text,
                    additional_kwargs={'nailong_compact_summary':True,'nailong_compact_state':state})
                at=next((i for i,m in enumerate(retained) if getattr(m,'type',None)!='system'),len(retained))
                retained.insert(at,summary); working=retained; changed=True; used_level='L2'
    after=_estimate_tokens(working)
    if not changed:
        return CompactResult(list(messages),False,before,before,'insufficient_history' if len(humans)<=keep_turns else 'protected_context')
    if not hard and (before==0 or (before-after)/before<min_gain):
        if pin_removed:
            return CompactResult(normalized,True,before,_estimate_tokens(normalized),removed_ids=tuple(pin_removed),level='pins')
        return CompactResult(list(messages),False,before,before,'insufficient_savings')
    return CompactResult(working,True,before,after,removed_ids=tuple(removed),level=used_level)
