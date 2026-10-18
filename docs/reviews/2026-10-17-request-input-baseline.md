# 普通会话请求输入离线基线

日期：2026-10-17（Asia/Shanghai）。被测代码基线：a2d3a6a，加当前工作区已有改动。此报告是设计阶段诊断，未修改产品运行时代码。

## 方法与计量边界

每次测量创建新的临时项目、临时用户目录和会话数据目录，隔离真实用户配置、记忆和会话。模型替换为本地 FakeMessagesListChatModel，经真实 AgentService → AgentRuntimeFactory → create_agent → bind_tools → ContextManagerMiddleware 执行，并读取 context_request 事件。

没有联网，没有使用真实 API Key。模型替身提供的 100 输入/10 输出仅用于让原生计量路径正常结束，**不是请求的实际 token 数**，不参与下表。表中 tokens 全部是项目对实际请求内容的 raw_estimated_tokens；工具数量取模型 bind_tools 收到的集合。

场景分别安装 0 或 100 个 Skill；每个夹具 Skill 有名称、中文描述和最小正文。无 MCP、固定记忆或先前聊天，每个请求仅有一次模型调用、无工具执行。

## 结果

| Skill 数 | 用户消息 | 模型绑定工具数 | 系统估算 | Skill 目录估算 | schema 估算 | 总输入估算 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 0 | 你好 | 18 | 2,838 | 0 | 4,945 | 8,037 |
| 0 | 想和你讨论一个想法 | 18 | 2,838 | 0 | 4,945 | 8,044 |
| 0 | 介绍一下这个项目 | 11 | 1,980 | 0 | 2,972 | 5,184 |
| 100 | 你好 | 18 | 2,838 | 9,105 | 4,945 | 17,142 |
| 100 | 想和你讨论一个想法 | 18 | 2,838 | 9,105 | 4,945 | 17,149 |
| 100 | 介绍一下这个项目 | 11 | 1,980 | 9,105 | 2,972 | 14,289 |

其余估算来自当前消息、运行时补充和消息封装。真实请求中实际包含什么由捕获的绑定与 context_request 确认；不将这张表解释为提供商账单，也不由替身的 usage 推断实际节省金额。

## 对设计的影响

1. 没有任何 Skill，问候也携带 18 个工具和约 8,000 的估算输入，必须同时处理系统规则与工具定义。
2. 一般交流与问候走同样重的输入路径，只针对几个问候词做特例不能解决目标。
3. 已有简单项目介绍过滤把工具从 18 减为 11，但仍完整携带 Skill 目录；过滤部分工具不足以达到验收要求。
4. 无关 Skill 从 0 增到 100，三个场景都增加 9,105 估算 tokens。Skill 搜索必须让未选择目录不进入模型输入，不能只限制正文加载。
5. 本轮没有工具执行，因此观察到的固定输入并不依赖工具结果膨胀或多 agent 调用。

## 可复现探针

在项目根目录用 `.venv/bin/python` 执行下面代码。临时目录随机名称长度相同；估算可能随未来提示或工具契约变更，需要记录当时版本。此探针不修改项目源码。

```python
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from pydantic import PrivateAttr
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore


class ProbeModel(FakeMessagesListChatModel):
    _bindings: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self._bindings.append([getattr(tool, 'name', None) for tool in tools])
        return self.bind(**kwargs)


async def measure(skill_count, message):
    with tempfile.TemporaryDirectory(prefix='ignovate-input-baseline-') as temporary:
        home = Path(temporary) / 'home'
        home.mkdir()
        root = Path(temporary) / 'project'
        root.mkdir()
        for index in range(skill_count):
            directory = root / '.agents' / 'skills' / f'fixture-{index}'
            directory.mkdir(parents=True)
            description = '用于处理明确范围的项目分析和修改任务。' * 4
            (directory / 'SKILL.md').write_text(
                f'---\nname: fixture-{index}\ndescription: {description}\n---\n仅供离线诊断。\n',
                encoding='utf-8',
            )
        model = ProbeModel(responses=[AIMessage(
            content='收到。',
            usage_metadata={'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110},
        )])
        with patch('pathlib.Path.home', return_value=home):
            factory = AgentRuntimeFactory(
                Settings('fixture-only', 'https://fixture.invalid', 'private-fixture', root),
                session_store=ProjectSessionStore(root, base_dir=Path(temporary) / 'data'),
            )
            factory.model = model
            try:
                await AgentService(factory).run_turn(
                    message, {'configurable': {'thread_id': 'baseline'}},
                )
                requests = [row['data'] for row in factory.session_store.read_events('baseline')
                            if row.get('kind') == 'context_request']
                report = requests[-1]
                print(json.dumps({
                    'skills': skill_count, 'message': message,
                    'bound_tools': len(model._bindings[-1]), 'model_requests': len(requests),
                    'raw_estimate': report['raw_estimated_tokens'],
                    'categories': {key: value['raw_tokens']
                                   for key, value in report['categories'].items()},
                }, ensure_ascii=False))
            finally:
                await factory.aclose()
                factory.close()


async def main():
    for count in (0, 100):
        for message in ('你好', '想和你讨论一个想法', '介绍一下这个项目'):
            await measure(count, message)


asyncio.run(main())
```
