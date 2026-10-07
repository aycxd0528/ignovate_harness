"""Offline context benchmark; optionally compare saved pre-change core files.

Run with the project's Python environment. No provider requests are made.
"""
import argparse
import copy
import importlib.util
import json
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from config import Settings
from nailong.core import context, context_runtime
from nailong.core.history_archive import HistoryArchive


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def timed(operation, samples):
    for _ in range(3):
        operation()
    elapsed = []
    for _ in range(samples):
        started = perf_counter()
        result = operation()
        elapsed.append((perf_counter() - started) * 1000)
    return round(statistics.median(elapsed), 3), result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-dir', type=Path)
    parser.add_argument('--samples', type=int, default=15)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error('--samples must be positive')
    baseline = baseline_runtime = None
    if args.baseline_dir:
        baseline = load('context_before_benchmark', args.baseline_dir / 'context.py')
        old_compact = load('compact_before_benchmark', args.baseline_dir / 'compact.py')
        baseline_runtime = load('runtime_before_benchmark', args.baseline_dir / 'context_runtime.py')
        baseline_runtime.request_report = baseline.request_report
        baseline_runtime.compact_messages = old_compact.compact_messages

    tools = [{'type': 'function', 'function': {'name': f'probe_{i}',
              'description': 'inspect local files', 'parameters': {'type': 'object',
              'properties': {'path': {'type': 'string'}}}}} for i in range(20)]
    rows = []
    for characters, fill in ((100000, 'a'), (600000, 'a'), (4000000, 'a'), (600000, '中')):
        messages = [HumanMessage(content=f'{i:04d}' + fill * (characters // 100 - 4)) for i in range(100)]
        options = {'messages': messages, 'system_message': 'local coding agent', 'tools': tools}
        cache = context.RequestEstimateCache()
        plain_ms, plain = timed(lambda: context.request_report(**options), args.samples)
        cached_ms, cached = timed(lambda: context.request_report(**options, cache=cache), args.samples)
        old_ms, old = timed(lambda: baseline.request_report(**options), args.samples) if baseline else (None, plain)
        if not old == plain == cached:
            raise AssertionError('Full accounting report changed')
        rows.append({'case': 'request_report', 'history_characters': characters,
                     'content': 'ASCII' if fill == 'a' else 'Chinese', 'baseline_ms': old_ms,
                     'optimized_ms': plain_ms, 'cached_ms': cached_ms, 'equivalent_report': True})

    def runtime_case(manager_class):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / '.nailong'
            config.mkdir()
            (config / 'settings.json').write_text(json.dumps({'context': {'soft_threshold_tokens': 2000000}}))
            manager = manager_class(Settings('placeholder', 'https://example.invalid', 'private', root),
                tools, 'BASE', 'chat', 'bench', None, HistoryArchive(root / 'archive'), {}, context.UsageCalibration())
            messages = [HumanMessage(content='调查文件')]
            for index in range(50):
                messages.extend([AIMessage(content='', tool_calls=[
                    {'name': 'read_file', 'id': str(index), 'args': {'path': f'{index}.py'}}]),
                    ToolMessage(name='read_file', tool_call_id=str(index),
                                content=json.dumps({'ok': True, 'content': f'{index:04d}' + '中' * 3000}, ensure_ascii=False))])
            model = FakeMessagesListChatModel(responses=[AIMessage(content='unused')])
            iteration = 0

            def operation():
                nonlocal iteration
                iteration += 1
                messages.append(HumanMessage(id=f'new-{iteration}', content=f'继续检查 {iteration}'))
                manager.before_model({'messages': messages}, None)
                return manager._prepare_request(ModelRequest(model=model, messages=copy.copy(messages),
                    system_message=SystemMessage(content='BASE'), tools=tools))[1]

            return timed(operation, args.samples)

    current_ms, current = runtime_case(context_runtime.ContextManagerMiddleware)
    old_ms, old = runtime_case(baseline_runtime.ContextManagerMiddleware) if baseline_runtime else (None, current)
    if current['categories'] != old['categories'] or current['estimated_tokens'] != old['estimated_tokens']:
        raise AssertionError('Runtime request accounting changed')
    rows.append({'case': 'preflight_and_final_request', 'tool_results': 50,
                 'new_user_message_per_sample': True, 'memory_and_task_projection': False,
                 'baseline_ms': old_ms, 'optimized_ms': current_ms, 'equivalent_accounting': True,
                 'last_performance': current.get('performance')})
    print(json.dumps({'samples': args.samples, 'warmups': 3, 'provider_calls': 0,
                      'results': rows}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
