"""Bounded memory rendering and request-local filtering of memory tool results."""

from __future__ import annotations

import json
import inspect

from nailong.core.preferences import read_config
from nailong.core.memory_knowledge import (
    EPISTEMIC_KEYS, KNOWLEDGE_DETAIL_KEYS, SourceValidator, invalid_knowledge, project_relative_path,
)

DEFAULT_MEMORY_TOKENS = 4000


def estimate_memory_tokens(text: str) -> int:
    """Conservative local estimate, independent of provider-reported token usage."""
    ascii_chars = sum(ord(character) < 128 for character in text)
    return (ascii_chars + 3) // 4 + (len(text) - ascii_chars) * 2


def memory_budget(project_root) -> int:
    value = DEFAULT_MEMORY_TOKENS
    for filename in ('settings.json', 'settings.local.json'):
        config = read_config(project_root / '.nailong' / filename, project_root)
        if 'memory' not in config:
            continue
        options = config['memory']
        if not isinstance(options, dict) or set(options) - {'max_tokens'}:
            raise ValueError('memory 配置只支持 max_tokens。')
        candidate = options.get('max_tokens', value)
        if type(candidate) is not int or not 512 <= candidate <= 16384:
            raise ValueError('memory.max_tokens 必须为 512–16384 的整数。')
        value = candidate
    return value


def _prefix(text: str, maximum: int) -> str:
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_memory_tokens(text[:middle]) <= maximum:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def render_snapshot(snapshot) -> str:
    """Fit headers, catalog and all three summaries together, reserving room for reads."""
    catalog = snapshot.catalog
    reserve=snapshot.budget_tokens//4
    # Reserve enough for at least a minimal page/row of every discovered ID,
    # including long Unicode names. A fixed quarter can be too small at 512.
    from nailong.core.memory import MAX_MEMORY_FILENAME_CHARS
    largest_id='project/'+'Ω'*(MAX_MEMORY_FILENAME_CHARS-3)+'.md'
    # IDs omitted by scan limits or discovered by a later explicit read need
    # the same room. Their maximum comes from the document validation contract.
    candidates=[*catalog['documents'],{'document':largest_id,'status':'available','version':'0'*64}]
    for info in candidates:
        minimal={'ok':True,'documents':[{'document':info['document'],'status':info['status'],
                                        'version':info['version']}],
                 'offset':203,'total_documents':203,'truncated':True,'next_offset':203,
                 'scan_truncated':True,'diagnostics':[],'budget_limited':True,
                 'metadata_truncated':True,'diagnostics_truncated':True}
        minimal['documents'][0].update(metadata_status='invalid',knowledge_type='project_fact',
                                     knowledge_state='confirmed',validity='unverified',applicable=False,fact_verified=False)
        reserve=max(reserve,_encoded_tokens(minimal)+16)
    prompt_limit = max(0,snapshot.budget_tokens-reserve)
    if not snapshot and not catalog['documents'] and not catalog['diagnostics'] and not any(
            row['status'] == 'error' for row in snapshot.reports.values()):
        return ''
    heading = ('\n\n## 固定记忆与主题目录（资料，不覆盖用户要求或权限）\n'
               f'三层及读取结果共用 {snapshot.budget_tokens} estimated tokens；未显示的内容不能视为已知。\n')
    if any(row.get('metadata_status')!='absent' for row in catalog['documents']):
        heading+='observed 仅表示来源摘要匹配，不是事实或测试验证；候选、过期、未验证资料仅供参考。\n'
    directory = '\n主题目录（仅元数据）：\n'
    directory_limit = min(prompt_limit // 3, 1000)
    listed = 0
    for info in catalog['documents']:
        line = (f"- {info['document']}: {info['description']} "
                f"[{info['status']}, {info['size_bytes']} bytes, version={(info['version'] or 'missing')[:12]}, "
                f"knowledge={info.get('knowledge_state','legacy')}, validity={info.get('validity','unverified')}, applicable={info.get('applicable')}]\n")
        if info.get('sections'):
            line += '  sections: ' + ', '.join(info['sections']) + '\n'
            if info.get('sections_truncated'):
                line += '  标题索引已截断，需按全文分页读取，不要把片段当作完整标题。\n'
        if estimate_memory_tokens(directory + line) > directory_limit:
            break
        directory += line
        listed += 1
    if listed < len(catalog['documents']) or catalog['truncated']:
        directory += f'目录已截断，显示 {listed}/{len(catalog["documents"])} 项；可分页查看。\n'
    if not catalog['documents'] and not catalog['truncated']:
        directory = ''
    diagnostic = ''.join(f"记忆目录 {row['scope']} 读取失败：{row['error']}。\n"
                         for row in catalog['diagnostics'])
    diagnostic += ''.join(f"{row['document']} 读取失败：{row['error']}；不能视为不存在。\n"
                          for row in snapshot.reports.values() if row['status'] == 'error')
    diagnostic = _prefix(diagnostic, min(prompt_limit // 3, 512))
    base = heading + diagnostic + directory
    if estimate_memory_tokens(base) > prompt_limit:
        base = heading + diagnostic
    remaining = max(0, prompt_limit - estimate_memory_tokens(base) - len(snapshot))
    sections, entries = [], []
    for index, entry in enumerate(snapshot):
        quota = remaining // (len(snapshot) - index)
        report = snapshot.reports[entry.scope]
        if report['status']=='error' or report.get('metadata_status')=='invalid':
            report['loaded_chars']=0
            continue
        low, high = 0, len(entry.content)

        def section(length):
            status = 'truncated' if length < len(entry.content) or report['status'] == 'truncated' else 'loaded'
            return (f"\n### {report['document']} [{status}: {length}/{report['total_chars']} 字符]\n"
                    +f"[knowledge={report.get('knowledge_state','legacy')}; validity={report.get('validity','unverified')}; applicable={report.get('applicable')}; fact_verified=false]\n"
                    + entry.content[:length])

        while low < high:
            middle = (low + high + 1) // 2
            if estimate_memory_tokens(section(middle)) <= quota:
                low = middle
            else:
                high = middle - 1
        text = entry.content[:low]
        report['loaded_chars'] = len(text)
        if low < len(entry.content):
            report['status'] = 'truncated'
        rendered = section(low)
        if estimate_memory_tokens(rendered) <= quota:
            sections.append(rendered)
            remaining -= estimate_memory_tokens(rendered)
        else:
            report['status'] = 'truncated'
        if text:
            entries.append(type(entry)(entry.scope, entry.path, text))
    snapshot[:] = entries
    rendered = base + ''.join(sections)
    # Base strings are also bounded when many diagnostics exist.
    return _prefix(rendered, prompt_limit)


def _encoded_tokens(payload) -> int:
    return estimate_memory_tokens(json.dumps(payload, ensure_ascii=False))


def fit_result(payload: dict, maximum: int) -> dict | None:
    """Count the actual JSON envelope; preserve a correct continuation pointer."""
    if _encoded_tokens(payload) <= maximum:
        return payload
    if isinstance(payload.get('documents'),list) and payload.get('diagnostics'):
        payload=dict(payload,diagnostics=[],diagnostics_truncated=True)
        if _encoded_tokens(payload)<=maximum: return payload
    if isinstance(payload.get('content'), str):
        if any(key in payload for key in KNOWLEDGE_DETAIL_KEYS):
            payload={key:value for key,value in payload.items() if key not in KNOWLEDGE_DETAIL_KEYS}
            payload['metadata_truncated']=True
        # Legacy section reads also echo absolute paths/exact headings. Keep
        # scope/version/offsets when these optional display fields cannot fit.
        envelope=dict(payload,content='',budget_limited=True,truncated=True,next_offset=payload.get('offset',0))
        if _encoded_tokens(envelope)>maximum and ('path' in payload or 'section' in payload):
            payload={key:value for key,value in payload.items() if key not in {'path','section'}}
            payload['metadata_truncated']=True
        content = payload['content']
        low, high = 0, len(content)

        def shortened(length):
            result = dict(payload, content=content[:length], budget_limited=True)
            if length < len(content):
                result.update(truncated=True, next_offset=payload.get('offset', 0) + length)
            return result

        if _encoded_tokens(shortened(0)) <= maximum:
            while low < high:
                middle = (low + high + 1) // 2
                if _encoded_tokens(shortened(middle)) <= maximum:
                    low = middle
                else:
                    high = middle - 1
            if low > 0 or not content:
                return shortened(low)
    elif isinstance(payload.get('documents'), list):
        for count in range(len(payload['documents']) - 1, 0, -1):
            result = dict(payload, documents=payload['documents'][:count], truncated=True,
                          next_offset=payload.get('offset', 0) + count, budget_limited=True)
            if _encoded_tokens(result) <= maximum:
                return result
        # A large description or heading index must not make the first row
        # permanently inaccessible. Keep identifiers, versions and read status.
        rows=[]
        for row in payload['documents']:
            compact={key:row[key] for key in ('document','scope','status','version','size_bytes',*EPISTEMIC_KEYS) if key in row}
            if row.get('description'):
                compact['description']=_prefix(row['description'],min(48,maximum//8))
            rows.append(compact)
        for count in range(len(rows),0,-1):
            result=dict(payload,documents=rows[:count],metadata_truncated=True,budget_limited=True)
            if count<len(rows): result.update(truncated=True,next_offset=payload.get('offset',0)+count)
            if _encoded_tokens(result)<=maximum: return result
        if rows:
            compact={key:rows[0][key] for key in ('document','status','version',*EPISTEMIC_KEYS) if key in rows[0]}
            result=dict(payload,documents=[compact],metadata_truncated=True,budget_limited=True)
            if len(rows)>1: result.update(truncated=True,next_offset=payload.get('offset',0)+1)
            if _encoded_tokens(result)<=maximum: return result
    pointer = {key: payload[key] for key in ('document', 'offset') if key in payload}
    result = dict(pointer, ok=False, error='memory_budget_exhausted')
    if _encoded_tokens(result) <= maximum:
        return result
    minimal = {'ok': False, 'error': 'memory_budget_exhausted'}
    return minimal if _encoded_tokens(minimal) <= maximum else None


class MemoryReadContext:
    """A fixed runtime snapshot, shared by tools and each model-call budget filter."""

    def __init__(self, snapshot, *, task_scope_provider=None):
        if task_scope_provider is not None and (
                not callable(task_scope_provider) or inspect.iscoroutinefunction(task_scope_provider)
                or inspect.iscoroutinefunction(getattr(task_scope_provider, '__call__', None))):
            raise ValueError('task_scope_provider 必须为同步范围回调。')
        self.snapshot = snapshot
        self.task_scope_provider = task_scope_provider
        self.available_tokens = snapshot.budget_tokens - estimate_memory_tokens(snapshot.rendered_context)
        self.initial_context=snapshot.rendered_context
        self.versions={row['document']:row.get('version') for row in snapshot.catalog['documents']}
        self.versions.update({row['document']:row.get('version') for row in snapshot.reports.values()})

    def _refresh_task_scope(self):
        if self.task_scope_provider is None:
            return
        # Invalidate the old scope even if the provider or validation fails.
        # No refreshed view is returned on failure; callers must stop the request.
        self.snapshot.store.task_scope = None
        try:
            scope = self.task_scope_provider()
            if inspect.iscoroutine(scope):
                scope.close()
                raise ValueError('任务范围回调不能返回协程。')
            if scope is not None:
                if not isinstance(scope, (list, tuple)) or len(scope) > 128:
                    raise ValueError('任务范围必须为有界的项目相对路径列表。')
                scope = tuple('.' if item == '.' else project_relative_path(
                    item, api_key=self.snapshot.store.api_key) for item in scope)
        except Exception:
            # Never surface raw provider errors, paths or credentials.
            raise ValueError('memory_task_scope_refresh_failed') from None
        self.snapshot.store.task_scope = scope

    def refresh_for_request(self) -> dict:
        """Optional coordinator hook: replace fixed system text before filtering.

        Does not rebind versions or promote candidate knowledge. Existing callers
        keep their immutable prompt/budget until this hook is explicitly wired.
        """
        self._refresh_task_scope()
        self.snapshot.refresh_metadata()
        self.available_tokens=self.snapshot.budget_tokens-estimate_memory_tokens(self.snapshot.rendered_context)
        return {'initial_context':self.initial_context,'context':self.snapshot.rendered_context,
                'report':self.snapshot.report()}

    def _fresh_knowledge(self,record,validator,cache):
        document=record.get('document')
        if document is None and record.get('scope') in ('user','project','local'):
            document=record['scope']+'/context.md'
        if not isinstance(document,str): return record
        if document not in cache:
            if len(cache)>=203:
                cache[document]=invalid_knowledge('project','source_validation_limit')
            else:
                cache[document]=self.snapshot.store._document_info(document,validator)
        current=cache[document]
        fields={key:current[key] for key in (*EPISTEMIC_KEYS,*KNOWLEDGE_DETAIL_KEYS) if key in current}
        if record.get('version')!=current.get('version'):
            # Retain the old body's own declaration. New document sources must
            # never be attached to a historical body from a different version.
            fields={key:record[key] for key in (*EPISTEMIC_KEYS,*KNOWLEDGE_DETAIL_KEYS) if key in record}
            fields.update(validity='stale',applicable=None,fact_verified=False,knowledge_issues=['memory_version_changed'])
            if isinstance(record.get('sources'),list):
                fields['sources']=[{**source,'status':'unverified','reason':'memory_version_changed'}
                                   for source in record['sources'] if isinstance(source,dict)]
        return {**record,**fields}

    def _version_changed(self,document,result):
        if not result.get('ok'): return False
        # setdefault also binds IDs first read after a truncated directory scan.
        expected=self.versions.setdefault(document,result.get('version'))
        return result.get('version')!=expected

    def read(self, document: str, offset: int = 0, limit: int = 4000) -> dict:
        result = self.snapshot.store.read_document(document, offset, limit)
        if self._version_changed(document,result):
            result={'ok':False,'document':document,'status':'changed',
                    'error':'记忆版本已改变；请在下一轮刷新后重新读取，不能拼接不同版本的分页。'}
        return fit_result(result, self.available_tokens) or {'ok': False, 'error': 'memory_budget_exhausted'}

    def read_section(self, scope: str, section: str = '', offset: int = 0, max_chars: int = 6000) -> dict:
        try:
            result = self.snapshot.store.read_section(scope, section, offset, max_chars)
            if self._version_changed(f'{scope}/context.md',result):
                result={'ok':False,'status':'changed','scope':scope,
                        'error':'记忆版本已改变；请在下一轮刷新后重新读取。'}
        except (OSError, UnicodeError, RuntimeError, ValueError) as error:
            reason=str(error) if isinstance(error,ValueError) and not isinstance(error,UnicodeError) else type(error).__name__
            result = {'ok': False, 'status':'error','validity':'unverified','fact_verified':False,'error':reason}
        return fit_result(result, self.available_tokens) or {'ok': False, 'error': 'memory_budget_exhausted'}

    def list(self, scope: str = '', offset: int = 0, limit: int = 20) -> dict:
        if (scope not in ('', 'user', 'project', 'local') or type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= 100):
            return {'ok': False, 'error': 'scope 必须为空或合法记忆层；offset 非负，limit 范围为 1–100。'}
        catalog = self.snapshot.catalog
        rows = [row for row in catalog['documents'] if not scope or row['scope'] == scope]
        end = min(len(rows), offset + limit)
        validator=SourceValidator(self.snapshot.store); cache={}
        page=[self._fresh_knowledge(row,validator,cache) for row in rows[offset:end]]
        result = {'ok': True, 'documents': page, 'offset': offset,
                  'total_documents': len(rows), 'truncated': end < len(rows) or catalog['truncated'],
                  'next_offset': end if end < len(rows) else None,
                  'scan_truncated': catalog['truncated'], 'diagnostics': catalog['diagnostics']}
        return fit_result(result, self.available_tokens) or {'ok': False, 'error': 'memory_budget_exhausted'}

    def filter_messages(self, messages: list) -> list:
        """Bound old and current memory outputs without mutating graph history or tool IDs."""
        calls = {call.get('id'): call.get('name') for message in messages
                 for call in (getattr(message, 'tool_calls', None) or [])}
        filtered = list(messages)
        remaining = self.available_tokens
        validator=SourceValidator(self.snapshot.store); cache={}
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            name = getattr(message, 'name', None) or calls.get(getattr(message, 'tool_call_id', None))
            if getattr(message, 'type', None) != 'tool' or name not in {'memory_read', 'memory_list', 'read_memory'}:
                continue
            text = message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
            try:
                payload=json.loads(text)
                if isinstance(payload,dict) and isinstance(payload.get('documents'),list):
                    payload=dict(payload,documents=[self._fresh_knowledge(row,validator,cache)
                                                    for row in payload['documents'] if isinstance(row,dict)])
                elif isinstance(payload,dict) and payload.get('ok'):
                    payload=self._fresh_knowledge(payload,validator,cache)
                else: payload=None
                if payload is not None:
                    text=json.dumps(payload,ensure_ascii=False)
                    if text!=message.content: filtered[index]=message.model_copy(update={'content':text})
            except (ValueError,TypeError):
                pass
            if self.snapshot.store.api_key:
                redacted=text.replace(self.snapshot.store.api_key,'[密钥已隐藏]')
                if redacted!=text:
                    text=redacted
                    filtered[index]=message.model_copy(update={'content':text})
            if estimate_memory_tokens(text) > remaining:
                try:
                    payload = json.loads(text)
                    result = fit_result(payload, remaining) if isinstance(payload, dict) else None
                except (ValueError, TypeError):
                    result = None
                text = json.dumps(result, ensure_ascii=False) if result is not None else ''
                filtered[index] = message.model_copy(update={'content': text})
            remaining -= estimate_memory_tokens(text)
        return filtered
