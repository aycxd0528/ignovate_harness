import unittest
from pathlib import Path
from textual.app import App
from textual.widgets import Static
from ui.approval_view import ApprovalPrompt, render_approval_content
from ui.theme import Theme


class ApprovalContentTests(unittest.TestCase):
    def test_expanded_mcp_approval_shows_command_and_cwd_business_arguments(self):
        action = {'name':'mcp__ops__exec', 'args':{'command':'review-danger-command', 'cwd':'/remote/project'}}
        text = render_approval_content(action, Path('/local'), Theme(no_color=True), expanded=True).plain
        self.assertIn('review-danger-command', text)
        self.assertIn('/remote/project', text)

    def test_readable_content_keeps_full_command_diff_and_redacts(self):
        action = {'name': 'run_command', 'args': {'command': 'python check.py secret', 'timeout_seconds': 15},
                  '_approval': {'reason': '运行命令', 'preview': {'cwd': '/project'}}}
        text = render_approval_content(action, Path('/fallback'), Theme(no_color=True), 'secret', True).plain
        self.assertIn('python check.py', text)
        self.assertIn('/project', text)
        self.assertIn('15', text)
        self.assertNotIn('secret', text)
        self.assertNotIn('"command":', text)


class PanelApp(App):
    def compose(self):
        yield Static('用户对话仍然可见', id='conversation')
        yield ApprovalPrompt(id='approval-panel')

    def on_mount(self):
        panel = self.query_one(ApprovalPrompt)
        panel.configure({'name': 'write_file', 'args': {'path': 'a.py', 'content': 'example'},
                         '_approval': {'suggested_rule': 'write_file(a.py)'}}, 1, 1, Path('/project'), Theme(no_color=True))
        panel.focus_choices()

    def on_approval_prompt_decided(self, event):
        self.decision = event.decision


class ApprovalPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_expanded_content_scrolls_without_covering_choices(self):
        app = PanelApp()
        async with app.run_test(size=(80, 24)) as pilot:
            panel = app.query_one(ApprovalPrompt)
            panel.configure({'name': 'write_file', 'args': {'path': 'a.py', 'content': '完整内容\n' * 100},
                             '_approval': {'suggested_rule': 'Write(./a.py)'}}, 1, 1, Path('/project'), Theme())
            await pilot.press('d')
            await pilot.pause()
            self.assertLessEqual(panel.query_one('#approval-hint').region.bottom, panel.region.bottom)
            self.assertLessEqual(panel.query_one('#approval-choices').region.bottom, panel.region.bottom)
            await pilot.press('pagedown')
            await pilot.pause()
            self.assertGreater(panel.query_one('#approval-body').scroll_y, 0)
            await pilot.press('3')
            self.assertEqual(app.decision, 'approve_session')

    async def test_default_is_reject_and_keyboard_choices_are_explicit(self):
        for key, expected in [('enter', 'reject'), ('2', 'approve'), ('3', 'approve_session'), ('escape', 'reject')]:
            with self.subTest(key=key):
                app = PanelApp()
                async with app.run_test(size=(80, 24)) as pilot:
                    await pilot.press(key)
                    await pilot.pause()
                    self.assertEqual(app.decision, expected)
                    self.assertTrue(app.query_one('#conversation').display)
                    self.assertLess(app.query_one(ApprovalPrompt).size.height, 14)

    async def test_expansion_and_placeholder_rule_do_not_approve_session(self):
        app = PanelApp()
        async with app.run_test(size=(100, 32)) as pilot:
            panel = app.query_one(ApprovalPrompt)
            panel.configure({'name': 'run_command', 'args': {'command': 'true'},
                             '_approval': {'suggested_rule': 'run_command(<command>)'}}, 1, 1, Path('/project'), Theme())
            await pilot.press('d')
            self.assertTrue(panel.expanded)
            await pilot.press('3')
            self.assertFalse(hasattr(app, 'decision'))
            await pilot.press('escape')
            self.assertEqual(app.decision, 'reject')

class SmallApprovalBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_terminal_keeps_actual_choices_and_hint_inside_panel(self):
        app=PanelApp()
        async with app.run_test(size=(60,18)) as pilot:
            panel=app.query_one(ApprovalPrompt)
            panel.configure({'name':'run_command','args':{'command':'python -m unittest','timeout_seconds':15},'_approval':{'reason':'执行项目测试','suggested_rule':'Bash(python -m unittest)','preview':{'cwd':'/project'}}},1,1,Path('/project'),Theme())
            await pilot.pause()
            for selector in ('#approval-choices','#approval-hint'):
                self.assertLessEqual(panel.query_one(selector).region.bottom,panel.content_region.bottom)
