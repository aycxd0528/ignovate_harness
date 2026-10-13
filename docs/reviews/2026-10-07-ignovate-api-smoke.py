"""Real API check with fixture files; save metadata only, never provider reasoning."""
import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import load_settings
from agent import AgentRuntimeFactory
from agent_service import AgentService
from nailong.core.sessions import ProjectSessionStore
from nailong.core.model import HarnessChatDeepSeek
from nailong.core.reasoning import model_reasoning_kwargs


async def run():
    settings = load_settings()
    results = []
    for effort in ('low', 'none'):
        with tempfile.TemporaryDirectory(prefix='ignovate-api-project-') as project, tempfile.TemporaryDirectory(prefix='ignovate-api-data-') as data:
            root = Path(project)
            (root/'intro.txt').write_text('fixture identifier: HARNESS_SETUP_OK\n')
            configured = replace(settings, project_root=root, reasoning_effort=effort,
                                 cli_preferences={'model':'default', 'reasoning_effort':effort})
            factory = AgentRuntimeFactory(configured, session_store=ProjectSessionStore(root, base_dir=data))
            factory.model = HarnessChatDeepSeek(model=factory.settings.model,
                api_key=configured.api_key, base_url=configured.api_base, timeout=30, max_retries=0,
                max_tokens=1000, **model_reasoning_kwargs(factory.settings.model, effort))
            service = AgentService(factory)
            events = []
            prompt = ('直接调用 read_file 读取 intro.txt；不要列目录或搜索。然后仅返回文件里的 identifier。'
                      if effort=='low' else '不要调用工具，只回答 OK。')
            try:
                async def collect():
                    return [event async for event in service.stream_turn(prompt,
                        {'configurable':{'thread_id':'reasoning-'+effort}},
                        max_model_calls=3 if effort=='low' else 1)]
                events = await asyncio.wait_for(collect(), 85)
                answer = next((event.data.get('text','') for event in reversed(events)
                               if event.kind=='final'), '')
                expected = 'HARNESS_SETUP_OK' if effort=='low' else 'OK'
                tools = [event.data.get('name') for event in events if event.kind=='tool_end']
                usage = [event.data for event in events if event.kind=='usage']
                results.append({'reasoning_effort':effort,
                    'ok': expected in answer and ('read_file' in tools if effort=='low' else not tools),
                    'answer_matches':expected in answer, 'tools':tools, 'model_calls':len(usage),
                    'tokens':sum(row.get('total_tokens', row.get('input_tokens',0)+row.get('output_tokens',0)) for row in usage)})
            except Exception as error:
                results.append({'reasoning_effort':effort, 'ok':False, 'error_type':type(error).__name__})
            finally:
                await factory.aclose()
                factory.close()
    report = {'results':results, 'ok':all(row['ok'] for row in results)}
    target = Path(__file__).with_suffix('.json')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__=='__main__':
    asyncio.run(run())
