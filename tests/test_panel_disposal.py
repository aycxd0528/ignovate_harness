"""Regression for rendering a panel while its child disposal is in progress."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import Settings
from textual.widgets import TextArea
from tui import TerminalAgentApp, PlanEditScreen


class PanelDisposalRegression(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_plan_editor_is_not_rendered_after_disposal(self):
        original_exit = TextArea._message_loop_exit
        original_render = TextArea.render_lines
        stale_renders = []

        async def delay_completed_child_disposal(widget):
            screen = widget.screen
            await original_exit(widget)
            if widget.id == 'plan-edit-content':
                # Hold the parent removal open after its child has detached and
                # cleared component styles. A normal repaint then tests whether
                # the screen still tries to render the disposed editor.
                screen.refresh(repaint=True)
                await asyncio.sleep(0.15)

        def observe_render(widget, crop):
            if widget.id == 'plan-edit-content' and not widget.is_attached:
                stale_renders.append(widget.id)
            return original_render(widget, crop)

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings('fixture-key', 'https://api.invalid',
                                'deepseek-flash', Path(directory))
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None,
                                                   session_store=None), settings)
            with patch.object(TextArea, '_message_loop_exit', delay_completed_child_disposal), \
                 patch.object(TextArea, 'render_lines', observe_render):
                async with app.run_test(size=(80, 24)) as pilot:
                    task = asyncio.create_task(app._wait_panel(PlanEditScreen('draft')))
                    try:
                        async def editor_ready():
                            # Escape belongs to the focused panel. Merely
                            # yielding once can send it before mount/focus.
                            while True:
                                await pilot.pause()
                                panel = app._interaction_panel
                                editor = app.focused
                                if (isinstance(panel, PlanEditScreen)
                                        and panel.is_attached
                                        and isinstance(editor, TextArea)
                                        and editor.id == 'plan-edit-content'
                                        and editor.region.width > 0
                                        and editor.region.height > 0):
                                    return

                        await asyncio.wait_for(editor_ready(), 3)
                        await pilot.press('escape')
                        self.assertIsNone(await asyncio.wait_for(task, 3))
                        await pilot.pause()
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(stale_renders, [],
                         'The compositor rendered the editor after it was disposed.')


if __name__ == '__main__':
    unittest.main(verbosity=2)
