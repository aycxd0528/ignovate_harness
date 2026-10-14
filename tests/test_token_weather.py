"""Weather reflects measured main-context samples, never cumulative spend."""
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from rich.cells import cell_len
from ui.presentation import SessionMetrics
from ui.theme import Theme, load_theme
from ui.token_weather import render_token_weather


def events(*turns):
    rows = []
    for samples in turns:
        rows.append({'kind': 'turn_start'})
        rows.extend({'kind': 'usage', 'data': {'input_tokens': sample, 'output_tokens': 10}}
                    for sample in samples)
    return rows


class TokenWeatherTests(unittest.TestCase):
    def test_persistent_empty_weather_does_not_create_a_usage_sample_or_trend(self):
        metrics=SessionMetrics()
        row=render_token_weather(metrics,context_window=1_000_000,show_empty=True).plain
        self.assertIn('Clear',row)
        self.assertIn('0%',row)
        self.assertIn('0 / 1m',row)
        self.assertNotIn('last turns',row)
        self.assertNotIn('last turn',row)
        self.assertIsNone(metrics.last_input_tokens)
        self.assertEqual(metrics.context_history,[])
        metrics.observe_missing({'scope':'main'})
        unknown=render_token_weather(metrics,context_window=1_000_000,show_empty=True).plain
        self.assertIn('未知',unknown)
        self.assertNotIn('Clear',unknown)

    def render(self, metrics, window=200_000, width=100, theme=None):
        try:
            module = importlib.import_module('ui.token_weather')
        except ModuleNotFoundError:
            self.fail('Token Weather renderer is missing')
        return module.render_token_weather(metrics, context_window=window, width=width, theme=theme or Theme(no_color=True))

    def test_reference_states_and_round_growth_match_screenshots(self):
        for turns, label, percent, delta in [(((19_200,),), 'Clear', '10%', '+19.2k'),
                (((19_200,), (107_300,)), 'Showers', '54%', '+88.1k'),
                (((19_200,), (134_400,), (161_100,)), 'Storm', '81%', '+26.7k')]:
            with self.subTest(label=label):
                row = self.render(SessionMetrics.from_events(events(*turns))).plain
                self.assertIn(label, row)
                self.assertIn(percent, row)
                self.assertIn(delta, row)
                self.assertIn('200k', row)

    def test_small_growth_in_large_window_has_a_visible_rising_trend(self):
        metrics = SessionMetrics.from_events(events((11_200,), (20_300,)))
        row = self.render(metrics, window=1_000_000, width=200).plain
        self.assertIn('last turns ▁█', row)
        self.assertIn('Clear 2%', row)
        self.assertIn('20.3k / 1m', row)
        self.assertIn('▲ +9.1k', row)
        self.assertEqual(metrics.context_history, [11_200, 20_300])

    def test_relative_trend_preserves_decline_plateau_and_missing_samples(self):
        cases = [
            (events((20_300,), (11_200,)), '█▁', '▼ -9.1k'),
            (events((20_300,), (20_300,)), '▁▁', '= +0'),
            (events((0,), (0,)), '▁▁', '= +0'),
            (events((20_300,)), '▁', '▲ +20.3k'),
            (events((11_200,)) + [{'kind': 'turn_start'}, {'kind': 'usage_missing'}]
             + events((20_300,)), '▁·█', '增量待统计'),
        ]
        for records, trend, growth in cases:
            with self.subTest(trend=trend, growth=growth):
                row = self.render(SessionMetrics.from_events(records), window=1_000_000, width=200).plain
                self.assertIn('last turns ' + trend + ' · ', row)
                self.assertIn(growth, row)

    def test_trend_rescales_after_old_extreme_leaves_recent_eight_turns(self):
        samples = (1_000_000, 11_200, 12_500, 13_800, 15_100, 16_400, 17_700, 19_000, 20_300)
        metrics = SessionMetrics.from_events(events(*((sample,) for sample in samples)))
        row = self.render(metrics, window=1_000_000, width=200).plain
        self.assertIn('last turns ▁▂▃▄▅▆▇█', row)
        self.assertIn('▲ +1.3k', row)

    def test_ascii_trend_shows_small_changes_without_color(self):
        theme = Theme(glyph_running='>', no_color=True)
        metrics = SessionMetrics.from_events(events((11_200,), (20_300,)))
        row = self.render(metrics, window=1_000_000, width=200, theme=theme)
        self.assertIn('last turns .@', row.plain)
        self.assertTrue(row.plain.isascii())
        self.assertFalse(row.spans)

    def test_weather_thresholds_and_over_capacity_are_explicit(self):
        for current, label, percent in [
            (0, 'Clear', 0), (58_000, 'Clear', 29),
            (60_000, 'Cloudy', 30), (98_000, 'Cloudy', 49),
            (100_000, 'Showers', 50), (158_000, 'Showers', 79),
            (160_000, 'Storm', 80), (200_000, 'Storm', 100),
            (220_000, 'Storm', 110),
        ]:
            with self.subTest(current=current):
                row = self.render(SessionMetrics.from_events(events((current,)))).plain
                self.assertIn(f'{label} {percent}%', row)

    def test_multiple_calls_in_one_turn_replace_one_sample_and_child_is_excluded(self):
        records = events((19_200,), (21_000, 35_000, 107_300))
        records.append({'kind': 'usage', 'data': {'scope': 'subagent', 'input_tokens': 180_000, 'output_tokens': 10}})
        metrics = SessionMetrics.from_events(records)
        self.assertEqual(getattr(metrics, 'context_history', None), [19_200, 107_300])
        row = self.render(metrics).plain
        self.assertIn('Showers', row)
        self.assertIn('+88.1k', row)
        self.assertNotIn('180k', row)

    def test_compaction_shows_negative_growth_and_lower_weather(self):
        row = self.render(SessionMetrics.from_events(events((161_100,), (19_200,)))).plain
        self.assertIn('Clear', row)
        self.assertIn('-141.9k', row)
        self.assertIn('▼', row)

    def test_missing_main_usage_creates_gap_without_fake_clear_or_growth(self):
        for data in [{}, {'estimated': True, 'input_tokens': 10}, {'input_tokens': True}, {'input_tokens': -3}]:
            with self.subTest(data=data):
                records = events((107_300,)) + [{'kind': 'turn_start'}, {'kind': 'usage', 'data': data}]
                row = self.render(SessionMetrics.from_events(records)).plain
                self.assertIn('未知', row)
                self.assertNotIn('Clear', row)
                self.assertNotIn('0%', row)
        records = events((107_300,)) + [{'kind': 'turn_start'}, {'kind': 'usage_missing', 'data': {'scope': 'main'}}]
        records += events((161_100,))
        metrics = SessionMetrics.from_events(records)
        self.assertEqual(getattr(metrics, 'context_history', None), [107_300, None, 161_100])
        self.assertNotIn('+53.8k', self.render(metrics).plain)

    def test_child_missing_or_rewound_usage_does_not_change_weather(self):
        records = events((19_200,), (107_300,))
        records += [{'kind': 'usage_missing', 'data': {'scope': 'subagent'}},
                    {'kind': 'usage_missing', 'data': {'scope': 'main', 'rewound': True}},
                    {'kind': 'usage', 'data': {'input_tokens': 190_000, 'rewound': True}}]
        row = self.render(SessionMetrics.from_events(records)).plain
        self.assertIn('Showers', row)
        self.assertIn('+88.1k', row)

    def test_hidden_goal_rounds_do_not_add_user_turns_and_history_is_bounded(self):
        records = events((19_200,)) + [{'kind': 'turn_start', 'data': {'visible': False}},
                    {'kind': 'usage', 'data': {'input_tokens': 107_300}}]
        metrics = SessionMetrics.from_events(records)
        self.assertEqual(metrics.turns, 1)
        self.assertEqual(getattr(metrics, 'context_history', None), [107_300])
        metrics = SessionMetrics.from_events(events(*((1000 * i,) for i in range(1, 41))))
        history = getattr(metrics, 'context_history', [])
        self.assertEqual(len(history), 8)
        self.assertEqual(history[-1], 40_000)
        self.assertIn('+1k', self.render(metrics).plain)

    def test_unknown_window_and_initial_state_do_not_guess_capacity(self):
        row = self.render(SessionMetrics(), window=200_000).plain
        self.assertEqual('', row)
        self.assertNotIn('Clear', row)
        for window in [None, 0, -1, True]:
            row = self.render(SessionMetrics.from_events(events((19_200,))), window=window).plain
            self.assertIn('未知窗口', row)
            self.assertNotIn('%', row)

    def test_small_width_ascii_and_no_color_remain_readable(self):
        metrics = SessionMetrics.from_events(events((19_200,), (161_100,)))
        for width in [20, 32, 47, 72, 100]:
            for theme in [Theme(), Theme(no_color=True), load_theme(ascii_only=True), load_theme(name='light')]:
                with self.subTest(width=width, theme=theme):
                    row = self.render(metrics, width=width, theme=theme)
                    self.assertLessEqual(cell_len(row.plain), width)
                    self.assertIn('Storm', row.plain)
                    self.assertIn('81%', row.plain)
                    if theme.no_color:
                        self.assertFalse(row.spans)
                    if theme.glyph_running == '>':
                        self.assertTrue(row.plain.isascii())


class TokenWeatherTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_two_turn_small_growth_updates_the_visible_trend(self):
        from agent_service import TurnEvent
        from config import Settings
        from tui import TerminalAgentApp
        from textual.widgets import Static
        with tempfile.TemporaryDirectory() as directory:
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None),
                Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', Path(directory)))
            async with app.run_test(size=(213, 56)) as pilot:
                app._append_user('第一轮')
                app._observe_usage(TurnEvent('usage', {'input_tokens': 11_200, 'output_tokens': 10}))
                await pilot.pause()
                self.assertIn('last turns ▁', app.query_one('#token-weather', Static).content.plain)
                app._append_user('第二轮')
                app._observe_usage(TurnEvent('usage', {'input_tokens': 20_300, 'output_tokens': 10}))
                await pilot.pause()
                row = app.query_one('#token-weather', Static).content.plain
                self.assertIn('last turns ▁█', row)
                self.assertIn('2%', row)
                self.assertIn('+9.1k', row)

    async def test_live_usage_updates_weather_without_filling_transcript(self):
        from agent_service import TurnEvent
        from config import Settings
        from tui import TerminalAgentApp
        from textual.widgets import RichLog, Static
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.nailong').mkdir()
            (root / '.nailong/settings.json').write_text(json.dumps({'context_windows': {'weather-model': 200000}}))
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None),
                Settings('secret', 'https://api.invalid', 'weather-model', root))
            async with app.run_test(size=(100, 32)) as pilot:
                app._append_user('第一轮')
                app._observe_usage(TurnEvent('usage', {'input_tokens': 19200, 'output_tokens': 10}))
                app._append_user('第二轮')
                app._observe_usage(TurnEvent('usage', {'input_tokens': 24000, 'output_tokens': 10}))
                app._observe_usage(TurnEvent('usage', {'input_tokens': 107300, 'output_tokens': 10}))
                app._observe_usage(TurnEvent('usage', {'scope': 'subagent', 'input_tokens': 180000}))
                await pilot.pause()
                row = app.query_one('#token-weather', Static).content.plain
                self.assertIn('Showers', row)
                self.assertIn('54%', row)
                self.assertIn('+88.1k',
                              render_token_weather(app._session_metrics, context_window=200000, width=200).plain)
                self.assertNotIn('Showers', '\n'.join(line.text for line in app.query_one('#transcript', RichLog).lines))
                for width, height in [(80,24), (50,18), (100,32)]:
                    await pilot.resize_terminal(width, height)
                    await pilot.pause()
                    weather = app.query_one('#token-weather', Static)
                    self.assertLessEqual(weather.region.bottom, height)
                    self.assertEqual(weather.size.height, 1)
                    self.assertGreater(app.query_one('#transcript').size.height, 5)
                    self.assertIn('Showers', weather.content.plain)
                    self.assertLessEqual(cell_len(weather.content.plain), weather.content_size.width)

    async def test_restore_resume_and_clear_use_only_current_session_history(self):
        from config import Settings
        from tui import TerminalAgentApp
        from textual.widgets import Static
        histories = {'first': events((19200,), (107300,)), 'second': events((161100,))}
        store = SimpleNamespace(read_events=lambda thread: histories.get(thread, []),
                                resolve_session=lambda name: name)
        class Service:
            runtime_factory = None
            session_store = store
            def get_history(self, thread):
                return []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.nailong').mkdir()
            (root / '.nailong/settings.json').write_text(json.dumps({'context_windows': {'weather-model': 200000}}))
            app = TerminalAgentApp(Service(), Settings('secret', 'https://api.invalid', 'weather-model', root), thread_id='first')
            async with app.run_test(size=(80,24)) as pilot:
                self.assertIn('Showers', app.query_one('#token-weather', Static).content.plain)
                app._dispatch('/resume second')
                await pilot.pause()
                self.assertIn('Storm', app.query_one('#token-weather', Static).content.plain)
                self.assertEqual(app._session_metrics.context_history, [161100])
                app._dispatch('/clear')
                await pilot.pause()
                self.assertIn('Clear', app.query_one('#token-weather', Static).content.plain)
                self.assertIn('0%', app.query_one('#token-weather', Static).content.plain)
                self.assertTrue(app.query_one('#token-weather', Static).display)
                self.assertEqual(app._session_metrics.context_history, [])
