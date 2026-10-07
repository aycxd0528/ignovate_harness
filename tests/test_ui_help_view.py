"""Help browsing stays local, concise, and usable in small terminals."""

import importlib
import io
import unittest

from rich.cells import cell_len
from rich.console import Console
from textual.app import App
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widgets import Button, Input, OptionList, Static, Tabs

from ui.commands import COMMANDS, CommandSpec
from ui.theme import load_theme


LONG_DETAIL = "第一句说明。\n只有选中后才展示的完整说明和验证边界。" * 30
SPECS = COMMANDS + (
    CommandSpec("/ship", "准备发布说明。\n包含完整发布步骤。", "/ship <版本>", True),
    CommandSpec("$check", LONG_DETAIL, "$check <任务>", True),
    CommandSpec("$docs", "整理项目文档。\n检索内容并检查链接。", needs_argument=True),
)


def help_module(test):
    """A missing feature is an assertion failure, not a collection error."""
    try:
        return importlib.import_module("ui.help_view")
    except ModuleNotFoundError as error:
        if error.name != "ui.help_view":
            raise
        test.fail("ui.help_view must provide the shared help renderer and screen")


def option_text(options):
    return "\n".join(
        options.get_option_at_index(index).prompt.plain
        for index in range(options.option_count)
    )


class HelpTextTests(unittest.TestCase):
    def test_default_help_teaches_input_without_dumping_skill_descriptions(self):
        help_view = help_module(self)
        text = help_view.render_help_text(SPECS).plain
        for entry in ("Enter", "Ctrl+Enter", "Ctrl+C", "Ctrl+O", "/命令", "@文件", "$Skill"):
            self.assertIn(entry, text)
        self.assertIn("/help commands", text)
        self.assertIn("/help skills", text)
        self.assertNotIn("$check", text)
        self.assertNotIn("完整说明和验证边界", text)
        self.assertLessEqual(len(text.splitlines()), 20)

    def test_plain_general_help_uses_tools_command_for_saved_tool_details(self):
        help_view = help_module(self)
        try:
            text = help_view.render_help_text(SPECS, interactive=False).plain
        except TypeError as error:
            self.fail(f"plain help must accept interactive=False: {error}")
        self.assertIn("/tools", text)
        self.assertIn("工具详情", text)
        self.assertNotIn("Ctrl+O", text)
        self.assertNotIn("完整说明和验证边界", text)
        self.assertLessEqual(len(text.splitlines()), 20)

    def test_command_help_groups_and_aligns_builtin_entries_including_task(self):
        help_view = help_module(self)
        text = help_view.render_help_text(SPECS, section="commands").plain
        self.assertIn("任务", text)
        self.assertIn("会话", text)
        self.assertIn("/task", text)
        self.assertNotIn("/ship", text)
        self.assertNotIn("$check", text)
        plan = next(line for line in text.splitlines() if line.lstrip().startswith("/plan "))
        task = next(line for line in text.splitlines() if line.lstrip().startswith("/task "))
        self.assertEqual(cell_len(plan[:plan.index("先只读")]), cell_len(task[:task.index("任务目标")]))

    def test_custom_and_skill_help_use_short_summaries(self):
        help_view = help_module(self)
        custom = help_view.render_help_text(SPECS, section="custom").plain
        skills = help_view.render_help_text(SPECS, section="skills").plain
        self.assertIn("/ship", custom)
        self.assertNotIn("/task", custom)
        self.assertNotIn("完整发布步骤", custom)
        self.assertIn("$check", skills)
        self.assertNotIn("完整说明和验证边界", skills)
        self.assertNotIn("/ship", skills)

    def test_plain_help_redacts_every_field_and_respects_ascii_no_color_width(self):
        help_view = help_module(self)
        secret = "test-secret-123"
        specs = (CommandSpec("/" + secret, "说明 " + secret, "/" + secret + " <参数>"),)
        theme = load_theme(ascii_only=True)
        from dataclasses import replace
        text = help_view.render_help_text(specs, section="custom", width=40,
                                         theme=replace(theme, no_color=True), api_key=secret)
        self.assertNotIn(secret, text.plain)
        self.assertIn("隐藏", text.plain)
        self.assertEqual(text.spans, [])
        self.assertNotIn("…", text.plain)
        output = io.StringIO()
        Console(file=output, width=40, no_color=True).print(text)
        self.assertTrue(all(cell_len(line) <= 40 for line in output.getvalue().splitlines()))

    def test_empty_sections_explain_how_to_add_commands_and_skills(self):
        help_view = help_module(self)
        self.assertIn("没有", help_view.render_help_text(COMMANDS, section="custom").plain)
        self.assertIn(".agents/skills", help_view.render_help_text(COMMANDS, section="skills").plain)


class PanelTestApp(App):
    async def show_panel(self, panel, callback=None):
        self.panel_callback = callback
        panel.styles.height = '100%'
        await self.screen.mount(panel)

    async def on_interaction_panel_resolved(self, event):
        event.stop()
        if self.panel_callback:
            self.panel_callback(event.value)
        await event.panel.remove()



class HelpScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_screen_fills_terminal_with_four_tabs_and_escape_closes(self):
        help_view = help_module(self)
        for size in ((80, 24), (100, 32)):
            with self.subTest(size=size):
                app = PanelTestApp()
                chosen = []
                async with app.run_test(size=size) as pilot:
                    screen = help_view.HelpScreen(SPECS, load_theme())
                    await app.show_panel(screen, chosen.append)
                    await pilot.pause()
                    self.assertNotIsInstance(screen, ModalScreen)
                    self.assertEqual(screen.region.size, app.size)
                    self.assertEqual(screen.query_one("#help-tabs", Tabs).tab_count, 4)
                    self.assertLessEqual(screen.query_one("#help-footer").region.bottom, size[1])
                    self.assertIn("Ctrl+Enter", screen.query_one("#help-general", Static).render().plain)
                    await pilot.press("escape")
                    self.assertEqual(chosen, [None])

    async def test_selected_skill_opens_full_description_then_returns_only_command(self):
        help_view = help_module(self)
        app = PanelTestApp()
        chosen = []
        async with app.run_test(size=(80, 24)) as pilot:
            screen = help_view.HelpScreen(SPECS, load_theme(), initial_tab="skills")
            await app.show_panel(screen, chosen.append)
            await pilot.pause()
            options = screen.query_one("#help-options", OptionList)
            self.assertIn("$check", option_text(options))
            self.assertNotIn("完整说明和验证边界", option_text(options))
            self.assertNotIn("完整说明和验证边界", screen.query_one("#help-detail", Static).render().plain)
            options.focus()
            await pilot.press("enter")
            await pilot.pause()
            detail = screen.query_one("#help-detail", Static).render().plain
            self.assertIn("$check <任务>", detail)
            self.assertIn("完整说明和验证边界", detail)
            self.assertGreater(screen.query_one("#help-detail-scroll").max_scroll_y, 0)
            self.assertGreaterEqual(screen.query_one("#help-detail-scroll").content_size.height, 3)
            self.assertFalse(screen.query_one("#help-use", Button).disabled)
            await pilot.press("down")
            await pilot.pause()
            self.assertTrue(screen.query_one("#help-use", Button).disabled)
            await pilot.press("up", "enter")
            await pilot.pause()
            await pilot.click("#help-use")
            self.assertEqual(chosen, ["$check"])

    async def test_search_matches_full_description_and_has_friendly_empty_state(self):
        help_view = help_module(self)
        app = PanelTestApp()
        async with app.run_test(size=(100, 32)) as pilot:
            screen = help_view.HelpScreen(SPECS, load_theme(), initial_tab="skills")
            await app.show_panel(screen)
            await pilot.pause()
            search = screen.query_one("#help-search", Input)
            search.value = "验证边界"
            await pilot.pause()
            options = screen.query_one("#help-options", OptionList)
            self.assertIn("$check", option_text(options))
            self.assertNotIn("$docs", option_text(options))
            search.value = "does-not-exist"
            await pilot.pause()
            self.assertEqual(options.option_count, 0)
            self.assertIn("没有匹配", screen.query_one("#help-empty", Static).render().plain)
            self.assertTrue(screen.query_one("#help-use", Button).disabled)

    async def test_long_command_catalog_scrolls_and_tab_changes_reset_details(self):
        help_view = help_module(self)
        app = PanelTestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = help_view.HelpScreen(SPECS, load_theme(), initial_tab="commands")
            await app.show_panel(screen)
            await pilot.pause()
            options = screen.query_one("#help-options", OptionList)
            self.assertGreater(options.max_scroll_y, 0)
            screen.query_one("#help-search", Input).value = "/task"
            await pilot.pause()
            options.focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("/task [status|new", screen.query_one("#help-detail", Static).render().plain)
            screen.query_one("#help-tabs", Tabs).active = "help-tab-custom"
            await pilot.pause()
            screen.query_one("#help-search", Input).value = ""
            await pilot.pause()
            self.assertIn("/ship", option_text(options))
            self.assertNotIn("/task", option_text(options))
            self.assertTrue(screen.query_one("#help-use", Button).disabled)

    async def test_screen_redacts_markup_and_key_in_list_detail_and_search(self):
        help_view = help_module(self)
        secret = "test-secret-123"
        specs = (CommandSpec("/safe", "[red]" + secret + "[/red]\n更多 " + secret,
                             "/safe " + secret),)
        app = PanelTestApp()
        async with app.run_test(size=(100, 32)) as pilot:
            screen = help_view.HelpScreen(specs, load_theme(ascii_only=True), api_key=secret, initial_tab="custom")
            await app.show_panel(screen)
            await pilot.pause()
            options = screen.query_one("#help-options", OptionList)
            self.assertNotIn(secret, option_text(options))
            self.assertIn("[red]", option_text(options))
            options.focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertNotIn(secret, screen.query_one("#help-detail", Static).render().plain)
            visible = "\n".join(Strip.join(line).text for line in app.screen._compositor.render_full_update().strips)
            self.assertFalse(any(glyph in visible for glyph in "━╸╺┌┐└┘│─▔▁"), visible)
            screen.query_one("#help-search", Input).value = secret
            await pilot.pause()
            self.assertNotIn(secret, screen.query_one("#help-search", Input).value)

    async def test_resize_preserves_selected_builtin_and_returns_same_command(self):
        help_view = help_module(self)
        app = PanelTestApp()
        chosen = []
        async with app.run_test(size=(80, 24)) as pilot:
            screen = help_view.HelpScreen(SPECS, load_theme(), initial_tab="commands")
            await app.show_panel(screen, chosen.append)
            await pilot.pause()
            options = screen.query_one("#help-options", OptionList)
            options.focus()
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            detail = screen.query_one("#help-detail", Static).render().plain
            self.assertIn("/plan <目标>", detail)
            await pilot.resize_terminal(100, 32)
            await pilot.pause()
            highlighted = options.get_option_at_index(options.highlighted).prompt.plain
            self.assertIn("/plan", highlighted)
            self.assertEqual(screen.query_one("#help-detail", Static).render().plain, detail)
            self.assertFalse(screen.query_one("#help-use", Button).disabled)
            await pilot.press("ctrl+enter")
            self.assertEqual(chosen, ["/plan"])

    async def test_resize_preserves_scrolled_skill_details_and_list_position(self):
        help_view = help_module(self)
        skills = tuple(CommandSpec(f"$skill-{index:02d}", LONG_DETAIL,
                                   f"$skill-{index:02d} <任务>", True) for index in range(40))
        app = PanelTestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = help_view.HelpScreen(skills, load_theme(), initial_tab="skills")
            await app.show_panel(screen)
            await pilot.pause()
            options = screen.query_one("#help-options", OptionList)
            options.focus()
            await pilot.press(*(["down"] * 20), "enter")
            await pilot.pause()
            details = screen.query_one("#help-detail-scroll")
            details.scroll_to(y=15, animate=False, immediate=True)
            await pilot.pause()
            old_list_y, old_details_y = options.scroll_y, details.scroll_y
            detail_text = screen.query_one("#help-detail", Static).render().plain
            self.assertGreater(old_list_y, 0)
            self.assertEqual(old_details_y, 15)
            await pilot.resize_terminal(100, 32)
            await pilot.pause()
            self.assertIn("$skill-20", options.get_option_at_index(options.highlighted).prompt.plain)
            self.assertEqual(screen.query_one("#help-detail", Static).render().plain, detail_text)
            self.assertEqual(options.scroll_y, old_list_y)
            self.assertEqual(details.scroll_y, old_details_y)


if __name__ == "__main__":
    unittest.main()
