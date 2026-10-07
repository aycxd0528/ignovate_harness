import tempfile
import unittest
from pathlib import Path
from textual.widgets import Input
from nailong.core.bootstrap import BootstrapStore
from ui.setup import SetupApp
from ui.theme import load_theme


def contrast(a, b):
    def luminance(hex_value):
        values = [int(hex_value[i:i+2], 16)/255 for i in (1, 3, 5)]
        linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in values]
        return sum(x*y for x,y in zip(linear, (.2126,.7152,.0722)))
    x,y = sorted((luminance(a),luminance(b)))
    return (y+.05)/(x+.05)


class VisualContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import os
        previous=os.environ.pop('NO_COLOR',None)
        if previous is not None:
            self.addCleanup(os.environ.__setitem__,'NO_COLOR',previous)

    def test_semantic_text_colors_have_readable_contrast(self):
        for name in ('dark', 'light'):
            theme = load_theme(name=name)
            for token in ('foreground', 'muted', 'accent', 'success', 'danger'):
                for background in ('background', 'surface'):
                    with self.subTest(name=name, token=token, background=background):
                        self.assertGreaterEqual(contrast(getattr(theme,token), getattr(theme,background)), 4.5)

    async def test_light_setup_invalid_field_focus_and_fixed_short_window_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BootstrapStore(Path(directory)/'user')
            app = SetupApp(store=store, project_root=Path(directory), theme=load_theme(name='light'))
            async with app.run_test(size=(60,18)) as pilot:
                self.assertEqual(app.screen.styles.background.hex.lower(), '#f5f6f7')
                await pilot.press('enter')
                app.existing_key = ''
                app.query_one('#setup-base', Input).value = 'invalid'
                self.assertTrue(await pilot.click('#setup-next'))
                self.assertEqual(app.focused.id, 'setup-base')
                self.assertIn('API 地址', app.query_one('#setup-base-error').render().plain)
                self.assertNotIn('DEEPSEEK_', app.query_one('#setup-base-error').render().plain)
                self.assertLessEqual(app.query_one('#setup-next').region.bottom, 18)
                self.assertFalse(store.path.exists())
                app.query_one('#setup-base', Input).value = 'https://api.invalid'
                app.query_one('#setup-model', Input).value = 'deepseek-flash'
                await pilot.pause(.6)
                await pilot.click('#setup-next')
                self.assertEqual(app.focused.id, 'setup-key')
                self.assertTrue(app.query_one('#setup-key', Input).password)
                await pilot.press('escape')
                self.assertEqual(app.step, 0)
                await pilot.press('enter')
                self.assertEqual(app.query_one('#setup-base', Input).value, 'https://api.invalid')

    async def test_selected_help_row_uses_theme_surface_and_keeps_readable_text(self):
        from ui.help_view import HelpScreen
        from ui.commands import COMMANDS
        from textual.app import App
        from textual.widgets import OptionList
        for name in ('dark', 'light'):
            theme = load_theme(name=name)
            app = App()
            async with app.run_test(size=(80,24)) as pilot:
                panel = HelpScreen(COMMANDS, theme, initial_tab='commands')
                panel.styles.height = '100%'
                await app.screen.mount(panel)
                await pilot.pause()
                selected = panel.query_one(OptionList).get_component_rich_style('option-list--option-highlighted')
                self.assertEqual(selected.bgcolor.triplet.hex.lower(), theme.surface)
                self.assertGreaterEqual(contrast(theme.accent, selected.bgcolor.triplet.hex), 4.5)

    def test_light_no_color_muted_text_is_readable_on_both_surfaces(self):
        from unittest.mock import patch
        import os
        with patch.dict(os.environ, {'NO_COLOR':'1'}):
            theme=load_theme(name='light')
            self.assertTrue(theme.no_color)
            self.assertGreaterEqual(contrast(theme.muted,theme.background),4.5)
            self.assertGreaterEqual(contrast(theme.muted,theme.surface),4.5)

    async def test_light_field_placeholder_has_readable_semantic_color(self):
        from textual.app import App
        from ui.model_view import ModelScreen
        from textual.widgets import Input
        app=App()
        async with app.run_test(size=(60,18)) as pilot:
            app._ui_theme=load_theme(name='light')
            panel=ModelScreen({'model_name':'default','model':'deepseek-flash','models':{'default':{'model':'deepseek-flash'}}},add_only=True)
            panel.styles.height=10
            await app.screen.mount(panel);await pilot.pause()
            field=panel.query_one('#model-name',Input)
            color=field.get_component_rich_style('input--placeholder').color.triplet.hex
            self.assertGreaterEqual(contrast(color,app._ui_theme.surface),4.5)
