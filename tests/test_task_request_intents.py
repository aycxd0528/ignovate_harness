"""Natural work instructions must reach the native delivery path."""
import unittest

from nailong.core.task_requests import task_request_kind


class TaskRequestIntentTests(unittest.TestCase):
    def test_natural_handoffs_and_contract_instructions_are_work(self):
        for text in (
            '请接手这个账务工具。先阅读 README.md 和原有测试，然后完成精确汇总。',
            '继续完成交付：为 ledger.py 添加原子输出。请补充回归测试并运行。',
            '请按 README 的完整契约完成最终审查并修正遗漏，重新运行原始测试与新增回归。',
            '这里有一个 CSV 工具。请实现精确汇总。',
            '请根据接口文档补齐租约确认。',
            '请先阅读现有实现。再修复发现的问题。',
        ):
            with self.subTest(text=text):
                self.assertEqual(task_request_kind(text), 'work')

    def test_questions_and_quoted_instructions_cannot_authorize_work(self):
        for text in (
            '接手这个工具是什么意思？',
            '继续完成交付了吗？',
            '请按 README 说明如何完成最终审查。',
            '他问：“请接手工具。修复代码。”这是什么意思？',
            '> 请接手工具。修复代码。\n解释这句话。',
            '```text\n请接手工具。修复代码。\n```\n解释这段示例。',
            '不要恢复任务。请先解释之前的方案。',
            '接手这个项目需要哪些准备？',
            '完成交付还需要多久？',
            '请按 README 完成验收需要哪些步骤？',
            '请按 README 完成审查有什么风险？',
            '请按 README 不要修改代码，只解释方案。',
            '请根据现有实现说明风险。',
            '请按之前修复的方案解释原因。',
            '请根据 README 中未完成的部分说明现状。',
            '请按 README 的要求只解释完成条件。',
            '请根据 README 仅说明实现思路。',
            '继续修改会导致数据丢失吗？',
            '完成交付需要额外授权吗？',
            '接手这个项目是不是需要额外权限？',
            '完成交付还要几天？',
            '请按 README 修复的方案解释原因。',
            '完成交付由谁负责？',
            '接手后去哪里找验收文档？',
            '接手这个项目划算？',
            '完成交付由谁负责',
            '完成了全部交付。',
            '补充了回归测试。',
            '接手了这个项目。',
            '接手过这个项目。',
        ):
            with self.subTest(text=text):
                self.assertEqual(task_request_kind(text), 'other')

    def test_explicit_authorization_can_request_a_coverage_explanation(self):
        self.assertEqual(task_request_kind(
            '现在授权按你的方案实现，并增加必要的回归测试。完成后运行原有测试，说明已经验证了哪些要求。'), 'work')
