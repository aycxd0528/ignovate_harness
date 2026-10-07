import unittest
from textual.app import App
from rich.text import Text
from ui.transcript import TranscriptLog


class Counted:
    def __init__(self, text):
        self.text, self.renders = text, 0
    def __rich_console__(self, console, options):
        self.renders += 1
        yield Text(self.text)


class CacheApp(App):
    def compose(self):
        yield TranscriptLog(id='log', min_width=1, wrap=True)


class TranscriptCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_updates_reuse_completed_entries_and_resize_reflows(self):
        app = CacheApp()
        async with app.run_test(size=(80,24)) as pilot:
            log = app.query_one(TranscriptLog)
            old = [Counted('完成的历史 '+str(i)) for i in range(100)]
            for item in old:
                log.write(item)
            live = Counted('推理中')
            log.write(live)
            await pilot.pause()
            counts = [item.renders for item in old]
            for i in range(3):
                live.text = '当前进度 '+str(i)
                log.refresh_renderable(live)
                await pilot.pause()
            self.assertEqual([item.renders for item in old], counts)
            self.assertIn('当前进度 2', '\n'.join(line.text for line in log.lines))
            self.assertEqual(len(log.lines), 101)
            await pilot.resize_terminal(60,18)
            await pilot.pause()
            self.assertTrue(all(item.renders > before for item,before in zip(old,counts)))
