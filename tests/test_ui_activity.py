import unittest
from io import StringIO
from rich.console import Console
from ui.activity import ToolGroup
from ui.theme import Theme


def display(group, width=80):
    stream = StringIO()
    Console(file=stream, width=width, no_color=True).print(group)
    return stream.getvalue()


class ToolGroupTests(unittest.TestCase):
    def test_each_failure_is_visible_even_when_more_than_two(self):
        group = ToolGroup(1, Theme(no_color=True))
        for index in range(4):
            group.add(index + 1, dict(name='run_command', ok=False, exit_code=index + 1,
                preview={'command': f'python fail{index}.py'}, output_snippet='trace'))
        folded = display(group)
        for index in range(4):
            self.assertIn(f'fail{index}.py', folded)
            self.assertIn(f'退出码 {index + 1}', folded)

    def test_many_successes_take_one_row_and_keep_expandable_output(self):
        group = ToolGroup(1, Theme(no_color=True), 'secret')
        for index in range(12):
            group.add(index + 1, dict(name='read_file', path=f'file{index}.py', ok=True, elapsed_ms=10))
        group.add(13, dict(name='run_command', ok=True, exit_code=0,
                          preview={'command': 'python check.py secret'}, output_snippet='Ran 12 tests\nOK', elapsed_ms=800))
        folded = display(group)
        self.assertEqual(len(folded.strip().splitlines()), 1)
        self.assertIn('读取 12', folded)
        self.assertIn('命令 1', folded)
        self.assertNotIn('Ran 12', folded)
        group.expanded = True
        expanded = display(group)
        self.assertIn('file11.py', expanded)
        self.assertIn('Ran 12 tests', expanded)
        self.assertIn('退出码 0', expanded)
        self.assertNotIn('secret', expanded)

    def test_failure_and_timeout_remain_visible_when_folded_and_narrow(self):
        group = ToolGroup(2, Theme(no_color=True))
        group.add(1, dict(name='read_file', ok=True, path='README.md'))
        group.add(2, dict(name='run_command', ok=False, exit_code=-1, timed_out=True,
                          preview={'command': 'python very_long_file_name.py'}, output_snippet='trace'))
        folded = display(group, 40)
        self.assertIn('超时', folded)
        self.assertIn('退出码 -1', folded)
        self.assertIn('trace', folded)

    def test_single_command_keeps_command_and_exit_status(self):
        group = ToolGroup(1, Theme(no_color=True))
        group.add(1, dict(name='run_command', ok=True, exit_code=0,
                          preview={'command': 'python check.py'}))
        self.assertIn('python check.py', display(group))
        self.assertIn('退出码 0', display(group))
