"""Explicit output allowance and bounded recovery for truncated model responses."""
from dataclasses import dataclass
import uuid
from contextvars import ContextVar

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from nailong.core.usage import message_usage


active_model_responses = ContextVar('nailong_model_responses', default=None)

from nailong.core.preferences import read_config


@dataclass(frozen=True)
class GenerationPolicy:
    max_output_tokens: int
    max_truncation_retries: int = 1


def generation_policy(settings) -> GenerationPolicy:
    # Match the provider's documented default generation allowances. The
    # completion cap includes reasoning; max thinking may exhaust 32K before
    # starting the answer. These are defaults, not the server's upper limit.
    supported = settings.model in {'deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-pro'}
    maximum = ({'max': 131072, 'none': 8192}.get(settings.reasoning_effort, 65536)
               if supported else 4096)
    options = {'max_output_tokens': maximum, 'max_truncation_retries': 1}
    root = settings.project_root.resolve()
    for name in ('settings.json', 'settings.local.json'):
        config = read_config(root / '.nailong' / name, root)
        values = config.get('generation', {})
        if not isinstance(values, dict):
            raise ValueError('generation 必须是 JSON 对象。')
        options.update({key: values[key] for key in options if key in values})
    maximum = options['max_output_tokens']
    retries = options['max_truncation_retries']
    if type(maximum) is not int or maximum < 1:
        raise ValueError('generation.max_output_tokens 必须是正整数。')
    if type(retries) is not int or retries not in (0, 1):
        raise ValueError('generation.max_truncation_retries 只能为 0 或 1。')
    return GenerationPolicy(maximum, retries)


def response_diagnostics(message) -> dict:
    metadata = message.response_metadata or {}
    usage = message_usage(message)
    content = message.content
    text = content if isinstance(content, str) else ''.join(
        block.get('text', '') for block in content if isinstance(block, dict) and block.get('type') == 'text')
    reasoning = ((message.usage_metadata or {}).get('output_token_details') or {}).get('reasoning')
    if reasoning is None:
        reasoning = ((metadata.get('token_usage') or {}).get('completion_tokens_details') or {}).get('reasoning_tokens')
    return {'model_call_id': metadata.get('nailong_model_call_id') or message.id or str(uuid.uuid4()),
        'finish_reason': metadata.get('finish_reason'), 'body_chars': len(text),
        'body_nonempty': bool(text.strip()),
        'requested_output_tokens': metadata.get('nailong_request_max_tokens'),
        'output_reasoning_tokens': reasoning, 'usage': usage,
        'tool_calls': len(message.tool_calls), 'invalid_tool_calls': len(message.invalid_tool_calls)}


class ModelOutputTruncated(RuntimeError):
    def __init__(self, response, diagnostics):
        super().__init__('模型输出达到生成上限，回答未完成；已停止本轮并保留已有产物和会话。')
        self.completed_response = response
        self.diagnostics = diagnostics


class GenerationRecoveryMiddleware(AgentMiddleware):
    """Never execute truncated calls; fund at most one changed continuation request."""
    def __init__(self, policy, record=None, *, scope='main'):
        super().__init__()
        self.policy = policy
        self.record = record
        self.scope = scope

    def _observe(self, response):
        latest = None
        for message in response.result:
            if getattr(message, 'type', None) != 'ai':
                continue
            info = response_diagnostics(message)
            observed = message.response_metadata.get('nailong_response_observed')
            if self.record is not None and not observed:
                self.record('model_response', info)
            pending = active_model_responses.get()
            if pending is not None and self.scope == 'main' and not observed:
                pending.append(info)
            latest = (message, info)
        return latest

    @staticmethod
    def _continuation(request, message):
        return request.override(messages=[*request.messages, message, HumanMessage(content=(
            '上一条模型响应因输出上限被截断。请沿用原任务与所有约束继续，避免重复探索或重述。'
            '已有正文视为未完成片段；重新给出完整答案或给出完成原任务所需的下一步。'
            '没有执行的操作和验证不能宣称完成。'))])

    def wrap_model_call(self, request, handler):
        previous = None
        for attempt in range(self.policy.max_truncation_retries + 1):
            try:
                response = handler(request)
            except Exception as error:
                self._recovery_failed(error, previous)
                raise
            message = self._inspect(response, attempt)
            if message is None:
                return response
            previous = (response, response_diagnostics(message))
            request = self._continuation(request, message)

    async def awrap_model_call(self, request, handler):
        previous = None
        for attempt in range(self.policy.max_truncation_retries + 1):
            try:
                response = await handler(request)
            except Exception as error:
                self._recovery_failed(error, previous)
                raise
            message = self._inspect(response, attempt)
            if message is None:
                return response
            previous = (response, response_diagnostics(message))
            request = self._continuation(request, message)

    def _inspect(self, response, attempt):
        latest = self._observe(response)
        if latest is None:
            if attempt:
                raise ModelOutputTruncated(response, {'recovery_failure': 'missing_response'})
            return None
        message, info = latest
        if info['finish_reason'] != 'length':
            if attempt:
                if (info['finish_reason'] not in {'stop', 'tool_calls'} or message.invalid_tool_calls or
                        (not info['body_nonempty'] and not message.tool_calls)):
                    raise ModelOutputTruncated(response, {**info, 'recovery_failure': 'empty_continuation'})
                message.response_metadata['nailong_generation_recovery'] = {'attempts': attempt, 'recovered': True}
            return None
        from nailong.core.budgets import active_turn_budget
        budget = active_turn_budget.get() if self.scope == 'main' else None
        if message.tool_calls or message.invalid_tool_calls or attempt == self.policy.max_truncation_retries:
            raise ModelOutputTruncated(response, info)
        if budget is not None and budget.summary_only:
            raise ModelOutputTruncated(response, {**info, 'recovery_failure': 'turn_call_limit'})
        return message

    @staticmethod
    def _recovery_failed(error, previous):
        if previous is None:
            return
        from nailong.core.cumulative_budget import SessionTokenBudgetExceeded
        response, info = previous
        if isinstance(error, SessionTokenBudgetExceeded):
            error.diagnostics['truncated_response'] = info
            return
        raise ModelOutputTruncated(response, {**info, 'recovery_failure': {
            'type': type(error).__name__, 'message': str(error)}}) from error
