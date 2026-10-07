"""Persistent weather on the left and truthful model settings on the right."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from rich.cells import cell_len
from config import Settings
from agent_service import TurnEvent
from tui import TerminalAgentApp

class ComposerInfoTests(unittest.IsolatedAsyncioTestCase):
    def app(self, root, mode='default', effort=None):
        factory = SimpleNamespace(model=SimpleNamespace(model_kwargs={'reasoning_effort':effort})) if effort else None
        service = SimpleNamespace(runtime_factory=factory, session_store=None, permission_mode=mode)
        return TerminalAgentApp(service, Settings('secret-key', 'https://api.invalid', 'deepseek-flash', Path(root)),
                                permission_mode=mode)

    async def test_empty_weather_stays_left_and_settings_align_right_above_composer(self):
        with tempfile.TemporaryDirectory() as root:
            for size in [(213,56),(100,32),(80,24)]:
                app=self.app(root)
                async with app.run_test(size=size) as pilot:
                    await pilot.pause()
                    self.assertFalse(app.query('#selection-hint'))
                    weather=app.query_one('#token-weather')
                    self.assertTrue(weather.display)
                    self.assertIn('Clear',weather.content.plain)
                    self.assertIn('0%',weather.content.plain)
                    self.assertIn('0 / 1m',weather.content.plain)
                    stats=app.query_one('#composer-stats')
                    composer=app.query_one('#composer-frame')
                    self.assertEqual(stats.size.height,1)
                    self.assertLessEqual(stats.region.bottom,composer.region.y)
                    self.assertNotIn('\n',stats.content.plain)
                    self.assertIn('默认',stats.content.plain)
                    self.assertIn('请求批准',stats.content.plain)
                    self.assertLess(weather.region.x,stats.region.x)
                    self.assertEqual(weather.region.y,stats.region.y)
                    self.assertEqual(stats.region.right,app.query_one('#composer-info').content_region.right)
                    self.assertNotIn('会话',stats.content.plain)
                    self.assertNotIn('Token',stats.content.plain)
                    self.assertLessEqual(cell_len(stats.content.plain),stats.size.width)

    async def test_live_weather_and_max_full_access_stay_on_opposite_sides_after_clear(self):
        with tempfile.TemporaryDirectory() as root:
            for size in [(213,56),(100,32),(80,24)]:
                app=self.app(root, mode='bypassPermissions', effort='max')
                async with app.run_test(size=size) as pilot:
                    app._append_user('开始对话')
                    app._observe_usage(TurnEvent('usage',{'input_tokens':19800,'output_tokens':200}))
                    await pilot.pause()
                    stats=app.query_one('#composer-stats')
                    weather=app.query_one('#token-weather')
                    composer=app.query_one('#composer-frame')
                    self.assertTrue(weather.display)
                    self.assertEqual(stats.region.y,weather.region.y)
                    self.assertEqual(stats.size.height,1)
                    self.assertEqual(weather.size.height,1)
                    self.assertLessEqual(weather.region.bottom,composer.region.y)
                    self.assertLessEqual(weather.region.right,size[0])
                    self.assertIn('Clear',weather.content.plain)
                    self.assertIn('最高',stats.content.plain)
                    self.assertIn('完全访问',stats.content.plain)
                    self.assertLessEqual(weather.region.right,stats.region.x)
                    self.assertEqual(stats.region.right,app.query_one('#composer-info').content_region.right)
                    app._dispatch('/clear')
                    await app.session_runner.wait_idle()
                    await pilot.pause()
                    self.assertTrue(weather.display)
                    self.assertIn('Clear',weather.content.plain)
                    self.assertIn('0%',weather.content.plain)
                    self.assertIn('最高',stats.content.plain)

    async def test_long_model_and_unknown_usage_fit_without_fake_clear(self):
        with tempfile.TemporaryDirectory() as root:
            app=self.app(root, mode='plan', effort='high')
            from dataclasses import replace
            app.settings=replace(app.settings,model='custom-model-with-a-very-long-name')
            async with app.run_test(size=(80,24)) as pilot:
                app._append_user('开始')
                app._observe_usage(TurnEvent('usage_missing',{'scope':'main'}))
                for size in [(80,24),(50,18),(100,32)]:
                    await pilot.resize_terminal(*size)
                    await pilot.pause()
                    weather=app.query_one('#token-weather')
                    stats=app.query_one('#composer-stats')
                    self.assertIn('未知',weather.content.plain)
                    self.assertNotIn('Clear',weather.content.plain)
                    self.assertIn('高 · 计划只读',stats.content.plain)
                    self.assertIn('计划只读',stats.content.plain)
                    self.assertLessEqual(weather.region.right,stats.region.x)
                    self.assertLessEqual(cell_len(weather.content.plain),weather.size.width)
                    self.assertEqual(stats.region.right,app.query_one('#composer-info').content_region.right)
                    self.assertEqual(stats.size.height,1)
                    self.assertIn('custom-model-with-a-very-long-name',app._status_details())

if __name__=='__main__':
    unittest.main()
