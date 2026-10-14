"""Turn-local identities for tool observations, separate from graph projections.

A graph update can project old messages again. That is not a new handler
execution. Call IDs alone are not identities: a new AI message may reuse one.
"""
from __future__ import annotations
import json

from langchain_core.messages import AIMessage, ToolMessage


def _identity(message):
    identity = getattr(message, 'id', None)
    return ('message', str(identity)) if identity else ('object', id(message))


def result_identity(message):
    metadata=getattr(message,'additional_kwargs',{}) or {}
    identity=metadata.get('nailong_tool_result_id')
    if not identity:
        try:
            payload=json.loads(message.content)
            identity=payload.get('tool_result_id') if isinstance(payload,dict) else None
        except (ValueError,TypeError):
            return None
    return identity if isinstance(identity,str) and 0<len(identity)<=128 else None


def result_origin(message):
    origin=(getattr(message,'additional_kwargs',{}) or {}).get('nailong_tool_result_turn_id')
    if origin is None:
        try:
            payload=json.loads(message.content)
            origin=payload.get('tool_result_turn_id') if isinstance(payload,dict) else None
        except (ValueError,TypeError):
            return None
    return origin if isinstance(origin,str) and 0<len(origin)<=128 else None


class TurnToolLedger:
    def __init__(self, boundary_messages, turn_id):
        self.turn_id = turn_id
        self.boundary = {_identity(m) for m in boundary_messages}
        self.completed_history = {str(m.tool_call_id) for m in boundary_messages
                                  if isinstance(m, ToolMessage)}
        self.historical_results={result_identity(m) for m in boundary_messages
                                 if isinstance(m,ToolMessage) and result_identity(m)}
        self.seen_result_identities=set()
        self.owners = {}
        self.requests = set()
        self.resumed_bindings=set()
        self.arguments={}
        self.results = set()
        self.seen_result_messages = set()
        self.diagnostics = set()
        self.pending = {}
        for message in boundary_messages:
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    call_id = str(call.get('id', ''))
                    self.pending[call_id] = (_identity(message), call)
            elif isinstance(message,ToolMessage):
                self.pending.pop(str(message.tool_call_id),None)

    @property
    def request_count(self):
        return len(self.requests)

    def historical_ai(self, message):
        return (_identity(message) in self.boundary
                or bool((getattr(message, 'additional_kwargs', {}) or {}).get('nailong_compact_summary')))

    def register(self, message):
        if self.historical_ai(message):
            return []
        fresh = []
        for call in message.tool_calls:
            call_id = str(call.get('id', ''))
            binding = (_identity(message), call_id)
            if binding in self.requests:
                continue
            self.requests.add(binding)
            self.owners[call_id] = binding
            self.arguments[call_id]=call.get('args',{}) or {}
            fresh.append(call)
        return fresh

    def consume(self, message):
        identity = _identity(message)
        call_id = str(message.tool_call_id)
        result_id=result_identity(message)
        historical = (identity in self.boundary
                      or (result_id is not None and result_id in self.historical_results)
                      or (call_id in self.completed_history and call_id not in self.owners
                          and call_id not in self.pending and result_id is None))
        if historical:
            return 'history'
        if identity in self.seen_result_messages or (result_id and result_id in self.seen_result_identities):
            return 'duplicate'
        binding = self.owners.get(call_id)
        if binding is None and call_id in self.pending:
            owner, call = self.pending[call_id]
            binding = (owner, call_id)
            self.requests.add(binding)
            self.resumed_bindings.add(binding)
            self.owners[call_id]=binding
            self.arguments[call_id]=call.get('args',{}) or {}
        # Legacy adapters may provide a result without its requesting AI. Keep
        # one observation, with unknown execution state rather than inventing it.
        if binding is None:
            binding = ('unattributed', call_id)
        if binding in self.results:
            return 'duplicate'
        self.results.add(binding)
        self.seen_result_messages.add(identity)
        if result_id:
            self.seen_result_identities.add(result_id)
        return 'current'

    def metadata(self, message, call_id, state):
        owner = self.owners.get(call_id)
        return {'turn_id': self.turn_id,
                'event_id': f'{self.turn_id}:{owner or _identity(message)}:{call_id}:{state}',
                'source_message_id': getattr(message, 'id', None),
                'tool_result_id': result_identity(message),
                'identity_coverage': 'runtime_result_id' if result_identity(message) else 'message_identity_only',
                'execution_state': state,'resumed':owner in self.resumed_bindings}


    def replay_diagnostic(self, message, reason):
        key = (_identity(message), reason)
        if key in self.diagnostics or len(self.diagnostics) >= 64:
            return None
        self.diagnostics.add(key)
        return {'turn_id': self.turn_id, 'source_message_id': getattr(message, 'id', None),
                'call_id': getattr(message, 'tool_call_id', None),
                'tool_result_id': result_identity(message),
                'replayed': True, 'reason': reason, 'original_turn_id': result_origin(message)}
