import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from config import Settings
from tui import TerminalAgentApp, ChatInput
from textual.errors import NoWidget
from textual.widgets import Input, OptionList


class EmbeddedInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def wait_for_condition(self, pilot, condition, description):
        async def ready():
            while True:
                await pilot.pause()
                if condition():
                    return
        try:
            await asyncio.wait_for(ready(), 3)
        except TimeoutError:
            self.fail(f'Timed out waiting for {description}; focus={getattr(pilot.app.focused, "id", None)!r}')

    async def wait_for_panel(self, pilot, app, panel, focus_id):
        await self.wait_for_condition(
            pilot,
            lambda: (app._interaction_panel is panel and panel.is_attached
                     and app.focused is not None and app.focused.is_attached
                     and app.focused.id == focus_id
                     and panel.content_region.height > 0
                     and app.focused.content_region.height > 0),
            f'{type(panel).__name__} to mount and focus {focus_id}',
        )

    async def wait_for_target(self, pilot, selector, *, focus_id=None):
        def ready():
            matches = pilot.app.query(selector)
            if not matches:
                return False
            target = matches[0]
            if (not target.is_attached or target.content_region.height <= 0
                    or target.content_region.width <= 0
                    or target.disabled or target.has_class('-active')):
                return False
            if focus_id is not None and getattr(pilot.app.focused, 'id', None) != focus_id:
                return False
            try:
                hit, _ = pilot.app.get_widget_at(*target.region.offset)
            except NoWidget:
                return False
            return hit is target
        await self.wait_for_condition(pilot, ready, f'visible {selector} with focus {focus_id}')
        return pilot.app.query_one(selector)

    async def wait_for_composer(self, pilot):
        composer = pilot.app.query_one('#composer', ChatInput)
        await self.wait_for_condition(pilot,
            lambda: (pilot.app._interaction_panel is None and not composer.disabled
                     and pilot.app.focused is composer),
            'the panel to close and restore composer focus')

    def make_app(self, root):
        settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', root)
        app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), settings)
        for i in range(13):
            app.actions.preferences.add_model(f'm{i}', f'deepseek-model-{i}')
        return app

    async def test_model_uses_main_layout_and_back_preserves_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            async with app.run_test(size=(80, 24)) as pilot:
                composer = app.query_one('#composer', ChatInput)
                composer.load_text('未发送的草稿')
                app._dispatch('/model')
                await self.wait_for_target(pilot, '#model-add', focus_id='model-options')
                self.assertEqual(len(app.screen_stack), 1)
                menu = app.query_one('#model-options', OptionList)
                menu.highlighted = 2
                self.assertTrue(await pilot.click('#model-add'))
                await self.wait_for_target(pilot, '#model-name', focus_id='model-name')
                app.query_one('#model-name', Input).value = 'draft-model'
                await pilot.press('escape')
                await self.wait_for_target(pilot, '#model-add', focus_id='model-options')
                self.assertTrue(menu.display)
                self.assertEqual(menu.highlighted, 2)
                self.assertTrue(await pilot.click('#model-add'))
                await self.wait_for_target(pilot, '#model-name', focus_id='model-name')
                self.assertEqual(app.query_one('#model-name', Input).value, 'draft-model')
                await pilot.press('escape')
                await self.wait_for_target(pilot, '#model-add', focus_id='model-options')
                await pilot.press('escape')
                await asyncio.wait_for(app.session_runner.wait_idle(), 3)
                await self.wait_for_composer(pilot)
                self.assertEqual(composer.text, '未发送的草稿')
                self.assertIs(app.focused, composer)

    async def test_many_models_and_settings_keep_footer_visible_in_small_terminal(self):
        from ui.settings_view import SettingScreen
        from nailong.core.reasoning import reasoning_options
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            async with app.run_test(size=(60, 18)) as pilot:
                app._dispatch('/model')
                await self.wait_for_target(pilot, '#model-add', focus_id='model-options')
                self.assertEqual(len(app.screen_stack), 1)
                host = app.query_one('#interaction-host')
                for selector in ('#model-add', '#model-use', '#model-keys'):
                    await self.wait_for_target(pilot, selector)
                    self.assertLessEqual(app.query_one(selector).region.bottom, host.region.bottom)
                self.assertTrue(await pilot.click('#model-add'))
                await self.wait_for_target(pilot, '#model-save', focus_id='model-name')
                app.query_one('#model-name', Input).value = 'default'
                app.query_one('#model-id', Input).value = 'new-model'
                self.assertTrue(await pilot.click('#model-save'))
                await self.wait_for_condition(pilot,
                    lambda: (app.query_one('#model-error').display
                             and 'default' in app.query_one('#model-error').render().plain
                             and getattr(app.focused, 'id', None) == 'model-name'),
                    'duplicate model validation to preserve the form and focus')
                self.assertEqual(app.focused.id, 'model-name')
                await pilot.press('escape')
                await self.wait_for_target(pilot, '#model-add', focus_id='model-options')
                await pilot.press('escape')
                await asyncio.wait_for(app.session_runner.wait_idle(), 3)
                await self.wait_for_composer(pilot)
                panel = SettingScreen('推理强度', 'high', reasoning_options('deepseek-flash'))
                task = asyncio.create_task(app._wait_panel(panel))
                await self.wait_for_panel(pilot, app, panel, 'setting-options')
                await self.wait_for_target(pilot, '#setting-keys')
                self.assertLessEqual(app.query_one('#setting-keys').region.bottom, host.region.bottom)
                self.assertEqual(app.query_one('#setting-options', OptionList).highlighted, 3)
                await pilot.press('escape')
                self.assertIsNone(await asyncio.wait_for(task, 3))
                await self.wait_for_composer(pilot)
                self.assertEqual(len(app.screen_stack), 1)

    async def test_help_plan_editor_and_rewind_are_inline_and_safe_to_cancel(self):
        from tui import PlanReviewScreen, PlanEditScreen, RewindConfirmationScreen, CopyReplyScreen
        from ui.help_view import HelpScreen
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', Path(directory))
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), settings)
            async with app.run_test(size=(80, 24)) as pilot:
                composer = app.query_one('#composer', ChatInput)
                composer.load_text('保留草稿')
                panels = [(PlanReviewScreen('# 计划\n完整计划\n'*40), 'reject', 'plan-reject'),
                          (PlanEditScreen('正在编辑的内容'), None, 'plan-edit-content'),
                          (RewindConfirmationScreen(), False, 'cancel'),
                          (HelpScreen(app.controller.specs(), app._ui_theme), None, 'help-tabs'),
                          (CopyReplyScreen(lambda: ['可选取任意文字'], app._ui_theme), None, 'copy-body')]
                for panel, expected, focus_id in panels:
                    with self.subTest(panel=type(panel).__name__):
                        task = asyncio.create_task(app._wait_panel(panel))
                        try:
                            await self.wait_for_panel(pilot, app, panel, focus_id)
                            self.assertEqual(len(app.screen_stack), 1)
                            self.assertTrue(app.query_one('#composer-info').display)
                            self.assertGreater(app.query_one('#transcript').size.height, 0)
                            self.assertLessEqual(panel.region.bottom, app.query_one('#composer-info').region.y)
                            if isinstance(panel, PlanReviewScreen):
                                self.assertEqual(app.focused.id, 'plan-reject')
                                await pilot.press('enter')
                            elif isinstance(panel, RewindConfirmationScreen):
                                self.assertEqual(app.focused.id, 'cancel')
                                await pilot.press('enter')
                            else:
                                await pilot.press('escape')
                            self.assertEqual(await asyncio.wait_for(task, 3), expected)
                            await self.wait_for_condition(
                                pilot, lambda: app._interaction_panel is None and app.focused is composer,
                                'the cancelled panel to restore composer focus',
                            )
                            self.assertFalse(panel.is_attached)
                            self.assertFalse(composer.disabled)
                            self.assertEqual(composer.text, '保留草稿')
                        finally:
                            if not task.done():
                                task.cancel()
                            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)

    async def test_failed_model_save_keeps_form_and_retries_without_partial_write(self):
        from agent import AgentRuntimeFactory
        from agent_service import AgentService
        from nailong.core.sessions import ProjectSessionStore
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as data:
            root = Path(directory)
            settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', root)
            factory = AgentRuntimeFactory(settings, session_store=ProjectSessionStore(root, base_dir=data))
            app = TerminalAgentApp(AgentService(factory, session_store=factory.session_store), settings)
            async with app.run_test(size=(80,24)) as pilot:
                app._dispatch('/model add')
                await self.wait_for_target(pilot, '#model-save', focus_id='model-name')
                app.query_one('#model-name', Input).value = 'pro'
                app.query_one('#model-id', Input).value = 'deepseek-v4-pro'
                with patch('nailong.core.preferences.atomic_json', side_effect=OSError('disk unavailable')):
                    self.assertTrue(await pilot.click('#model-save'))
                    await self.wait_for_condition(pilot,
                        lambda: (app.query_one('#model-error').display
                                 and '保存失败' in app.query_one('#model-error').render().plain
                                 and getattr(app.focused, 'id', None) == 'model-save'
                                 and app._interaction_future is not None
                                 and not app._interaction_future.done()),
                        'failed save to show its error and accept another explicit choice')
                    self.assertIsNotNone(app._interaction_panel)
                    self.assertEqual(app.query_one('#model-name', Input).value, 'pro')
                    self.assertIn('保存失败', app.query_one('#model-error').render().plain)
                    self.assertFalse(factory.preferences.local_path.exists())
                await self.wait_for_target(pilot, '#model-save', focus_id='model-save')
                self.assertTrue(await pilot.click('#model-save'))
                await asyncio.wait_for(app.session_runner.wait_idle(), 3)
                await self.wait_for_composer(pilot)
                self.assertEqual(app.settings.model, 'deepseek-v4-pro')
                self.assertIn('pro', factory.preferences.effective()['models'])

    async def test_keyboard_status_exposes_full_metrics_without_tooltips_or_popups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', root, reasoning_effort='max')
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None, permission_mode='bypassPermissions'), settings)
            async with app.run_test(size=(60,18)) as pilot:
                await pilot.press('f8')
                await pilot.pause()
                text = '\n'.join(line.text for line in app.query_one('#transcript').lines)
                self.assertIn('deepseek-flash', text)
                self.assertIn('完全访问', text)
                self.assertIn('最高', text)
                self.assertIn('0%', text)
                self.assertIsNone(app.query_one('#composer-stats').tooltip)
                self.assertFalse(app.ENABLE_COMMAND_PALETTE)
                self.assertEqual(len(app.screen_stack), 1)

    async def test_required_interaction_waits_for_reading_and_approval_owns_composer(self):
        from ui.content_panels import CopyReplyScreen, PlanReviewScreen
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings('fixture','https://api.invalid','deepseek-flash',Path(directory))
            app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),settings)
            async with app.run_test(size=(60,18)) as pilot:
                app.query_one('#transcript').register_reply('已有回复')
                approval=asyncio.create_task(app._request_approval({'name':'run_command','args':{'command':'echo hello'}},1,1))
                await pilot.pause()
                await pilot.press('f7')
                await pilot.pause()
                self.assertIsNone(app._interaction_panel)
                self.assertTrue(app.query_one('#composer').disabled)
                self.assertLessEqual(app.query_one('#composer-frame').region.bottom,18)
                await pilot.press('escape')
                self.assertEqual(await approval,'reject')
                read=asyncio.create_task(app._wait_panel(CopyReplyScreen(lambda:['已有回复'],app._ui_theme)))
                await pilot.pause()
                required=asyncio.create_task(app._wait_panel(PlanReviewScreen('计划')))
                await pilot.pause()
                self.assertFalse(required.done())
                await pilot.press('escape')
                await read
                await pilot.pause()
                self.assertIsInstance(app._interaction_panel,PlanReviewScreen)
                await pilot.press('escape')
                self.assertEqual(await required,'reject')
                self.assertFalse(app.query_one('#composer').disabled)

    async def test_multiline_draft_help_actions_and_model_buttons_render_inside_host(self):
        from ui.help_view import HelpScreen
        with tempfile.TemporaryDirectory() as directory:
            app=self.make_app(Path(directory))
            async with app.run_test(size=(60,18)) as pilot:
                composer=app.query_one('#composer',ChatInput)
                composer.load_text('draft\n'*7)
                await pilot.pause()
                panel=HelpScreen(app.controller.specs(),app._ui_theme,initial_tab='commands')
                task=asyncio.create_task(app._wait_panel(panel))
                await self.wait_for_panel(pilot, app, panel, 'help-options')
                await pilot.press('enter')
                await self.wait_for_target(pilot, '#help-use', focus_id='help-options')
                host=app.query_one('#interaction-host')
                for selector in ('#help-options','#help-use','#help-footer'):
                    await self.wait_for_target(pilot, selector)
                    control=app.query_one(selector)
                    self.assertGreater(control.content_region.height,0)
                    self.assertLessEqual(control.region.bottom,host.content_region.bottom)
                await pilot.press('escape');await asyncio.wait_for(task, 3)
                await self.wait_for_composer(pilot)
                self.assertEqual(composer.text,'draft\n'*7)
                app._dispatch('/model')
                await self.wait_for_target(pilot, '#model-add', focus_id='model-options')
                for selector in ('#model-cancel','#model-add','#model-use'):
                    await self.wait_for_target(pilot, selector)
                    self.assertGreater(app.query_one(selector).content_region.height,0)
                await pilot.press('escape');await asyncio.wait_for(app.session_runner.wait_idle(), 3)
                await self.wait_for_composer(pilot)

    async def test_incoming_approval_waits_for_read_view_then_restores_disabled_state(self):
        from ui.content_panels import CopyReplyScreen
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings('fixture','https://api.invalid','deepseek-flash',Path(directory))
            app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),settings)
            async with app.run_test(size=(60,18)) as pilot:
                read=asyncio.create_task(app._wait_panel(CopyReplyScreen(lambda:['正文'],app._ui_theme)))
                await pilot.pause()
                approval=asyncio.create_task(app._request_approval({'name':'run_command','args':{'command':'true'}},1,1))
                await pilot.pause()
                self.assertFalse(app.query_one('#approval-panel').display)
                self.assertIsNotNone(app._interaction_panel)
                await pilot.press('escape');await read;await pilot.pause()
                self.assertTrue(app.query_one('#approval-panel').display)
                self.assertTrue(app.query_one('#composer').disabled)
                self.assertLessEqual(app.query_one('#composer-frame').region.bottom,18)
                await pilot.press('escape');self.assertEqual(await approval,'reject')
                self.assertFalse(app.query_one('#composer').disabled)

    async def test_zero_models_has_visible_add_action_and_cancel_without_selection(self):
        from ui.model_view import ModelScreen
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings('fixture','https://api.invalid','deepseek-flash',Path(directory))
            app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),settings)
            async with app.run_test(size=(80,24)) as pilot:
                panel=ModelScreen({'model_name':'default','model':'deepseek-flash','models':{}})
                task=asyncio.create_task(app._wait_panel(panel))
                await self.wait_for_panel(pilot, app, panel, 'model-add')
                await self.wait_for_target(pilot, '#model-add', focus_id='model-add')
                self.assertEqual(app.focused.id,'model-add')
                self.assertTrue(app.query_one('#model-use').disabled)
                self.assertLessEqual(app.query_one('#model-keys').region.bottom,panel.content_region.bottom)
                await pilot.press('escape');self.assertIsNone(await asyncio.wait_for(task, 3))
                await self.wait_for_composer(pilot)
