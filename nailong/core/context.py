"""Mutually exclusive request-size estimates; these are not provider token counts."""
import json
import math
from collections import OrderedDict
from pathlib import Path
from threading import Lock


def configured_context_window(model,project_root):
    from nailong.core.preferences import read_config
    project_root=Path(project_root).resolve()
    defaults={'deepseek-flash':1_000_000,'deepseek-v4-flash':1_000_000,
              'deepseek-v4-pro':1_000_000,'deepseek-chat':65_536}
    config=read_config(Path(project_root)/'.nailong/settings.json',project_root)
    windows=config.get('context_windows',{})
    value=windows.get(model) if isinstance(windows,dict) else None
    if value is None: value=config.get('models',{}).get(model,{}).get('context_window')
    return value if isinstance(value,int) and not isinstance(value,bool) and value>0 else defaults.get(model.casefold())


def message_text(content):
    return content if isinstance(content,str) else json.dumps(content,ensure_ascii=False,default=str)


def _correction_factor(factor):
    value = float(factor)
    return max(1.0, value) if math.isfinite(value) else 1.0


def estimate_text(text, factor=1.0) -> int:
    """Estimate text conservatively; non-ASCII characters cost at least one token."""
    text = message_text(text) if text is not None else ''
    _, ascii_chars = _text_statistics(text)
    baseline = (ascii_chars + 3) // 4 + len(text) - ascii_chars
    return math.ceil(baseline * _correction_factor(factor))


def _text_statistics(text):
    return len(text), len(text) if text.isascii() else sum(character.isascii() for character in text)


class RequestEstimateCache:
    """Runtime-local bounded caches keyed by content, never message IDs.

    Store character statistics rather than rounded tokens: rounding and provider
    calibration are applied only after combining a complete category.
    """

    def __init__(self, max_entries=512, max_characters=2_000_000):
        if (type(max_entries) is not int or max_entries < 1 or
                type(max_characters) is not int or max_characters < 1):
            raise ValueError('Context cache bounds must be positive integers.')
        self.max_entries = max_entries
        self.max_characters = max_characters
        self._texts = OrderedDict()
        self._schemas = OrderedDict()
        self._sizes = {'text': 0, 'schema': 0}
        self._counts = {'text_hits': 0, 'text_misses': 0, 'schema_hits': 0, 'schema_misses': 0}

    def _remember(self, kind, entries, key, value, size):
        if size > self.max_characters:
            return
        while entries and (len(entries) >= self.max_entries or
                           self._sizes[kind] + size > self.max_characters):
            _, (_, previous_size) = entries.popitem(last=False)
            self._sizes[kind] -= previous_size
        entries[key] = (value, size)
        self._sizes[kind] += size

    def statistics(self, text):
        entry = self._texts.get(text)
        if entry is not None:
            self._texts.move_to_end(text)
            self._counts['text_hits'] += 1
            return entry[0]
        self._counts['text_misses'] += 1
        result = _text_statistics(text)
        self._remember('text', self._texts, text, result, len(text))
        return result

    @staticmethod
    def _schema_key(tool):
        from langchain_core.tools import BaseTool, Tool
        if isinstance(tool, dict):
            return 'dict:' + message_text(tool)
        if isinstance(tool, BaseTool):
            # Read the live provider-facing schema; nested dictionary edits and
            # schema/name/description reassignment must invalidate conversion.
            custom = isinstance(tool, Tool) and (tool.metadata or {}).get('type') == 'custom_tool'
            if not custom and getattr(type(tool), 'tool_call_schema', None) is not BaseTool.tool_call_schema:
                return None
            schema = None if custom else tool.tool_call_schema
            if isinstance(schema, type):
                schema = schema.model_json_schema() if hasattr(schema, 'model_json_schema') else schema.schema()
            return message_text([type(tool).__module__, type(tool).__qualname__,
                                 tool.name, tool.description, schema, tool.metadata,
                                 bool(tool.args_schema)])
        # Arbitrary callables/custom schema providers have no stable version
        # contract. Convert them on every request instead of guessing.
        return None

    def definition_text(self, tool):
        from langchain_core.utils.function_calling import convert_to_openai_tool
        key = self._schema_key(tool)
        entry = self._schemas.get(key) if key is not None else None
        if entry is not None:
            self._schemas.move_to_end(key)
            self._counts['schema_hits'] += 1
            return entry[0]
        self._counts['schema_misses'] += 1
        result = message_text(convert_to_openai_tool(tool))
        if key is not None:
            self._remember('schema', self._schemas, key, result, len(key) + len(result))
        return result

    def snapshot(self):
        return {**self._counts, 'text_entries': len(self._texts), 'schema_entries': len(self._schemas),
                'text_characters': self._sizes['text'], 'schema_characters': self._sizes['schema']}


def _field(message, key, default=None):
    return message.get(key, default) if isinstance(message, dict) else getattr(message, key, default)


def _role(message):
    role = _field(message, 'type') or _field(message, 'role', '')
    return {'assistant': 'ai', 'user': 'human'}.get(role, role)


def _tool_calls(message):
    calls = (_field(message, 'tool_calls', None) or []) + (_field(message, 'invalid_tool_calls', None) or [])
    metadata = _field(message, 'additional_kwargs', {}) or {}
    return calls or metadata.get('tool_calls', []) or []


def request_report(messages, system_message=None, tools=(), parts=None,
                   actual_main_input=None, factor=1.0, cache=None) -> dict:
    """Account for one actual request, including schemas, arguments and framing.

    ``parts`` labels exact portions of the supplied system content. Missing or
    stale parts never add input that is absent from the request. Use the raw
    estimate, rather than the corrected estimate, when observing provider usage.
    """
    from langchain_core.utils.function_calling import convert_to_openai_tool

    messages = list(messages)
    definitions = [cache.definition_text(tool) if cache is not None else
                   message_text(convert_to_openai_tool(tool)) for tool in tools]
    names = ('base_system', 'fixed_memory', 'skill_catalog', 'tool_definitions',
             'loaded_skills', 'tool_results', 'history', 'framing')
    if any(_role(message)=='human' and (_field(message,'additional_kwargs',{}) or {}).get('nailong_task_context')==1 for message in messages):
        names=names[:-1]+('task_context','framing')
    fragments = {name: [] for name in names}
    framing_allowance = 3 + 4 * (len(messages) + len(definitions))

    if system_message is not None:
        content = system_message if isinstance(system_message, str) else _field(system_message, 'content', '')
        remaining = [message_text(content) if content is not None else '']
        for name in names[:4]:
            part = (parts or {}).get(name, '')
            if not isinstance(part, str) or not part:
                continue
            for index, text in enumerate(remaining):
                start = text.find(part)
                if start >= 0:
                    fragments[name].append(part)
                    remaining[index:index + 1] = [text[:start], text[start + len(part):]]
                    break
        fragments['base_system'].extend(remaining)
        fragments['framing'].append('system')
        framing_allowance += 4

    if definitions:
        # Match json.dumps(list_of_definitions) exactly without decoding cached
        # schema text back into dictionaries.
        fragments['tool_definitions'].append('[')
        for index, text in enumerate(definitions):
            if index:
                fragments['tool_definitions'].append(', ')
            fragments['tool_definitions'].append(text)
        fragments['tool_definitions'].append(']')

    call_names = {}
    for message in messages:
        for call in _tool_calls(message):
            call_names[call.get('id')] = call.get('name') or (call.get('function') or {}).get('name')

    pinned_messages = 0
    for message in messages:
        role = _role(message)
        metadata = _field(message, 'additional_kwargs', {}) or {}
        pinned = role == 'system' or bool(metadata.get('nailong_pin'))
        pinned_messages += pinned
        if role == 'tool':
            name = _field(message, 'name') or call_names.get(_field(message, 'tool_call_id'))
            category = 'loaded_skills' if name in {'load_skill', 'read_skill_resource'} else 'tool_results'
        elif role=='human' and metadata.get('nailong_task_context')==1:
            category='task_context'
        elif pinned:
            category = 'fixed_memory'
        else:
            category = 'history'
        content = _field(message, 'content', '')
        fragments[category].append(message_text(content) if content is not None else '')
        if role == 'ai':
            calls = _tool_calls(message)
            if calls:
                fragments['history'].append(message_text(calls))
            function_call = _field(message, 'function_call') or metadata.get('function_call')
            if function_call:
                fragments['history'].append(message_text(function_call))
        fragments['framing'].extend(str(value) for value in
                                    (role, _field(message, 'name'), _field(message, 'tool_call_id')) if value)

    correction = _correction_factor(factor)
    categories = {}
    for name, values in fragments.items():
        statistics = [cache.statistics(text) if cache is not None else _text_statistics(text) for text in values]
        characters = sum(row[0] for row in statistics)
        ascii_chars = sum(row[1] for row in statistics)
        raw_tokens = (ascii_chars + 3) // 4 + characters - ascii_chars
        if name == 'framing':
            raw_tokens += framing_allowance
        categories[name] = {'characters': characters, 'tokens': math.ceil(raw_tokens * correction),
                            'raw_tokens': raw_tokens}
    return {'categories': categories,
            'estimated_tokens': sum(row['tokens'] for row in categories.values()),
            'raw_estimated_tokens': sum(row['raw_tokens'] for row in categories.values()),
            'method': 'estimated', 'actual_main_input_tokens': actual_main_input,
            'message_count': len(messages), 'pinned_messages': pinned_messages}


def context_budget(window, requested_output=4096, soft_threshold=150000) -> dict:
    """Reserve output and safety space before applying the soft trigger."""
    known = isinstance(window, int) and not isinstance(window, bool) and window > 0
    output_reserve = max(1, int(requested_output))
    safety_margin = 0
    input_limit = None
    if known:
        output_reserve = max(1, min(output_reserve, window // 4))
        safety_margin = min(1024, max(32, window // 50))
        input_limit = max(0, window - output_reserve - safety_margin)
    return {'context_window': window if known else None, 'output_reserve': output_reserve,
            'safety_margin': safety_margin, 'input_limit': input_limit,
            'trigger_tokens': min(soft_threshold, input_limit) if known else soft_threshold}


class UsageCalibration:
    """Monotonic correction factors, isolated by model and provider."""

    def __init__(self):
        self._factors = {}
        self._lock = Lock()

    def factor_for(self, model, provider):
        with self._lock:
            return self._factors.get((model, provider), 1.0)

    def observe(self, model, provider, estimated, actual):
        """Observe actual input usage against an uncalibrated baseline estimate."""
        valid = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and value > 0 for value in (estimated, actual))
        with self._lock:
            key = (model, provider)
            factor = self._factors.get(key, 1.0)
            if valid:
                factor = max(factor, actual / estimated, 1.0)
                self._factors[key] = factor
            return factor


def context_report(parts,messages,actual_main_input=None):
    characters={key:len(parts.get(key,'')) for key in ('base_system','fixed_memory','skill_catalog','tool_definitions')}
    characters.update(loaded_skills=0,tool_results=0,history=0)
    calls={}
    for message in messages:
        for call in getattr(message,'tool_calls',[]) or []:
            calls[call.get('id')]=call.get('name')
    for message in messages:
        role=getattr(message,'type','')
        metadata=getattr(message,'additional_kwargs',{}) or {}
        text=message_text(getattr(message,'content',''))
        if role=='tool':
            name=getattr(message,'name',None) or calls.get(getattr(message,'tool_call_id',None))
            key='loaded_skills' if name in {'load_skill','read_skill_resource'} else 'tool_results'
        elif role=='human' and metadata.get('nailong_task_context')==1:
            key='task_context'
            characters.setdefault(key,0)
        elif role=='system' or metadata.get('nailong_pin'):
            key='fixed_memory'
        else: key='history'
        characters[key]+=len(text)
        if role=='ai' and getattr(message,'tool_calls',None):
            characters['history']+=len(json.dumps(message.tool_calls,ensure_ascii=False,default=str))
    categories={key:{'characters':count,'tokens':(count+3)//4} for key,count in characters.items()}
    return {'categories':categories,'estimated_tokens':sum(row['tokens'] for row in categories.values()),
            'method':'字符数 / 4 的分类估算，未计供应商封装；实际用量以 API 返回为准',
            'actual_main_input_tokens':actual_main_input,'message_count':len(messages),
            'pinned_messages':sum(getattr(message,'type',None)=='system' or bool((getattr(message,'additional_kwargs',{}) or {}).get('nailong_pin')) for message in messages)}
