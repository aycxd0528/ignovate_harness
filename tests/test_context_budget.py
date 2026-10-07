import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from nailong.core import context


class ContextBudgetTests(unittest.TestCase):
    def api(self, name):
        value = getattr(context, name, None)
        self.assertTrue(callable(value), f"Missing context API: {name}")
        return value

    def test_ascii_and_non_ascii_have_distinct_conservative_estimates(self):
        estimate = self.api("estimate_text")
        cases = [("", 0), ("abcd", 1), ("abcde", 2), ("中文测试", 4), ("abcd中", 2)]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(estimate(text), expected)
        self.assertEqual(estimate("中文测试", factor=1.5), 6)

    def test_full_request_counts_system_current_input_and_framing(self):
        report = self.api("request_report")
        result = report(
            [HumanMessage(content="要求" * 100)],
            system_message=SystemMessage(content="系统" * 200),
            actual_main_input=721,
        )
        self.assertEqual(result["categories"]["base_system"]["characters"], 400)
        self.assertGreaterEqual(result["categories"]["base_system"]["tokens"], 400)
        self.assertGreaterEqual(result["categories"]["history"]["tokens"], 200)
        self.assertGreater(result["categories"]["framing"]["tokens"], 0)
        self.assertEqual(result["actual_main_input_tokens"], 721)
        self.assertEqual(result["method"], "estimated")
        self.assertEqual(result["raw_estimated_tokens"], result["estimated_tokens"])

    def test_full_dict_schema_descriptions_and_parameters_are_counted(self):
        report = self.api("request_report")
        definition = {
            "type": "function",
            "function": {
                "name": "search",
                "description": "描述" * 600,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "参数" * 350}},
                    "required": ["query"],
                },
            },
        }
        result = report([], tools=[definition])
        self.assertGreaterEqual(result["categories"]["tool_definitions"]["tokens"], 1900)
        self.assertGreater(result["categories"]["tool_definitions"]["characters"], 1900)

    def test_base_tool_schema_is_converted_and_counted(self):
        report = self.api("request_report")

        @tool(description="说明" * 250)
        def probe(query: str) -> str:
            return query

        result = report([], tools=[probe])
        self.assertGreaterEqual(result["categories"]["tool_definitions"]["tokens"], 500)
        self.assertGreater(result["categories"]["tool_definitions"]["characters"], 500)

    def test_ai_tool_call_arguments_count_even_when_content_is_empty(self):
        report = self.api("request_report")
        small = report([AIMessage(content="", tool_calls=[{"name": "search", "id": "a", "args": {"q": ""}}])])
        large = report([AIMessage(content="", tool_calls=[{"name": "search", "id": "a", "args": {"q": "中文" * 500}}])])
        self.assertGreaterEqual(large["estimated_tokens"] - small["estimated_tokens"], 1000)
        self.assertEqual(large["categories"]["history"]["characters"] - small["categories"]["history"]["characters"], 1000)

    def test_raw_openai_tool_calls_are_counted(self):
        report = self.api("request_report")
        small = {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "type": "function", "function": {"name": "search", "arguments": "{}"}}]}
        large = {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "type": "function", "function": {"name": "search", "arguments": "中文" * 100}}]}
        self.assertGreaterEqual(report([large])["estimated_tokens"] - report([small])["estimated_tokens"], 199)

    def test_invalid_tool_call_arguments_are_not_lost_beside_valid_calls(self):
        report = self.api("request_report")
        valid = [{"name": "valid", "id": "a", "args": {}}]
        small = AIMessage(content="", tool_calls=valid, invalid_tool_calls=[{"name": "broken", "id": "b", "args": "", "error": "invalid JSON"}])
        large = AIMessage(content="", tool_calls=valid, invalid_tool_calls=[{"name": "broken", "id": "b", "args": "中文" * 200, "error": "invalid JSON"}])
        self.assertGreaterEqual(report([large])["estimated_tokens"] - report([small])["estimated_tokens"], 400)

    def test_legacy_function_call_arguments_are_counted(self):
        report = self.api("request_report")
        small = AIMessage(content="", additional_kwargs={"function_call": {"name": "search", "arguments": ""}})
        large = AIMessage(content="", additional_kwargs={"function_call": {"name": "search", "arguments": "中文" * 200}})
        self.assertGreaterEqual(report([large])["estimated_tokens"] - report([small])["estimated_tokens"], 400)

    def test_exact_system_parts_are_exclusive_and_unused_parts_do_not_inflate(self):
        report = self.api("request_report")
        parts = {"base_system": "BASE", "fixed_memory": "记忆", "skill_catalog": "CAT", "tool_definitions": "unused schema" * 1000}
        messages = [
            HumanMessage(content="要求"),
            AIMessage(content="", tool_calls=[{"name": "load_skill", "id": "s", "args": {}}]),
            ToolMessage(content="正文", tool_call_id="s"),
            ToolMessage(content="结果", tool_call_id="f", name="read_file"),
            HumanMessage(content="计划", additional_kwargs={"nailong_pin": "plan"}),
        ]
        result = report(messages, system_message="BASE记忆CAT!", parts=parts)
        categories = result["categories"]
        self.assertEqual(set(categories), {"base_system", "fixed_memory", "skill_catalog", "tool_definitions", "loaded_skills", "tool_results", "history", "framing"})
        self.assertEqual(categories["base_system"]["characters"], 5)
        self.assertEqual(categories["fixed_memory"]["characters"], 4)
        self.assertEqual(categories["skill_catalog"]["characters"], 3)
        self.assertEqual(categories["loaded_skills"]["characters"], 2)
        self.assertEqual(categories["tool_results"]["characters"], 2)
        self.assertEqual(categories["tool_definitions"]["characters"], 0)
        self.assertEqual(result["estimated_tokens"], sum(row["tokens"] for row in categories.values()))
        self.assertEqual(result["pinned_messages"], 1)
        unmatched = report([], system_message="actual", parts={"base_system": "wrong", "fixed_memory": "missing"})
        self.assertEqual(unmatched["categories"]["base_system"]["characters"], 6)
        self.assertEqual(unmatched["categories"]["fixed_memory"]["characters"], 0)

    def test_mode_filtered_tools_are_the_only_tools_counted(self):
        report = self.api("request_report")
        from nailong.tools.files import FileSession
        from tools import build_tools

        with tempfile.TemporaryDirectory() as directory:
            session = FileSession(Path(directory))
            chat = report([], tools=build_tools(profile="chat", file_session=session))
            review = report([], tools=build_tools(profile="review", target_path=".", file_session=session))
        empty = report([], tools=[], parts={"tool_definitions": "old chat schema" * 1000})
        self.assertGreater(chat["categories"]["tool_definitions"]["tokens"], review["categories"]["tool_definitions"]["tokens"])
        self.assertGreater(review["categories"]["tool_definitions"]["tokens"], 0)
        self.assertEqual(empty["categories"]["tool_definitions"]["tokens"], 0)

    def test_calibrated_categories_sum_without_changing_raw_baseline(self):
        report = self.api("request_report")
        arguments = {"system_message": "中文", "messages": [HumanMessage(content="abcde")]}
        raw = report(**arguments)
        calibrated = report(**arguments, factor=1.5)
        self.assertEqual(calibrated["raw_estimated_tokens"], raw["estimated_tokens"])
        self.assertGreater(calibrated["estimated_tokens"], raw["estimated_tokens"])
        self.assertEqual(calibrated["estimated_tokens"], sum(row["tokens"] for row in calibrated["categories"].values()))

    def test_window_budget_reserves_output_and_safety_margin(self):
        budget = self.api("context_budget")
        result = budget(200_000, requested_output=4096)
        self.assertEqual(result["context_window"], 200_000)
        self.assertEqual(result["output_reserve"], 4096)
        self.assertEqual(result["safety_margin"], 1024)
        self.assertEqual(result["input_limit"], 194_880)
        self.assertEqual(result["trigger_tokens"], 150_000)
        hard = budget(4096, requested_output=4096)
        self.assertEqual(hard["output_reserve"], 1024)
        self.assertEqual(hard["safety_margin"], 81)
        self.assertEqual(hard["input_limit"], 2991)
        self.assertEqual(hard["trigger_tokens"], 2991)

    def test_tiny_window_output_reserve_leaves_input_space(self):
        budget = self.api("context_budget")
        result = budget(128, requested_output=4096)
        self.assertEqual(result["output_reserve"], 32)
        self.assertEqual(result["safety_margin"], 32)
        self.assertEqual(result["input_limit"], 64)
        self.assertEqual(result["trigger_tokens"], 64)
        self.assertEqual(budget(1)["output_reserve"], 1)
        self.assertEqual(budget(1)["input_limit"], 0)

    def test_unknown_window_retains_soft_threshold_and_explicit_unknown_limit(self):
        budget = self.api("context_budget")
        for window in (None, 0, -1, True):
            with self.subTest(window=window):
                result = budget(window, requested_output=1024, soft_threshold=500)
                self.assertIsNone(result["context_window"])
                self.assertIsNone(result["input_limit"])
                self.assertEqual(result["output_reserve"], 1024)
                self.assertEqual(result["trigger_tokens"], 500)

    def test_usage_calibration_is_monotonic_and_isolated_by_model_and_provider(self):
        calibration = self.api("UsageCalibration")()
        self.assertEqual(calibration.factor_for("model", "provider"), 1.0)
        calibration.observe("model", "provider", 100, 160)
        self.assertEqual(calibration.factor_for("model", "provider"), 1.6)
        calibration.observe("model", "provider", 100, 120)
        self.assertEqual(calibration.factor_for("model", "provider"), 1.6)
        self.assertEqual(calibration.factor_for("other-model", "provider"), 1.0)
        self.assertEqual(calibration.factor_for("model", "other-provider"), 1.0)
        calibration.observe("model", "provider", 100, 200)
        self.assertEqual(calibration.factor_for("model", "provider"), 2.0)

    def test_usage_calibration_skips_unknown_and_non_positive_usage(self):
        calibration = self.api("UsageCalibration")()
        for estimated, actual in ((100, None), (100, 0), (0, 100), (None, 100), (100, -1), (100, True)):
            with self.subTest(estimated=estimated, actual=actual):
                calibration.observe("model", "provider", estimated, actual)
                self.assertEqual(calibration.factor_for("model", "provider"), 1.0)


if __name__ == "__main__":
    unittest.main()
