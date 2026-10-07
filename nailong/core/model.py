"""DeepSeek chat serialization preserving its required thinking tool field."""
from langchain_deepseek import ChatDeepSeek
from langchain_core.messages import AIMessage


class HarnessChatDeepSeek(ChatDeepSeek):
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = self._convert_input(input_).to_messages()
        rows = payload.get('messages', [])
        if len(messages) != len(rows):
            raise ValueError('模型消息序列化长度不一致。')
        for message, row in zip(messages, rows):
            if isinstance(message, AIMessage) and isinstance(message.additional_kwargs.get('reasoning_content'), str):
                row['reasoning_content'] = message.additional_kwargs['reasoning_content']
        return payload
