import tempfile
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import local_tools
from nailong.tools.files import FileSession
from nailong.tools.registry import TOOL_SPECS, build_tool_specs
from tools import ToolExecutionContext, build_tools


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.session = FileSession(self.root)

    def test_registry_order_is_stable_and_includes_p0_tools(self):
        names = [spec.name for spec in build_tool_specs(profile='chat', session=self.session)]
        self.assertEqual(
            names,
            ["list_files", "read_file", "glob", "grep", "search_text", "edit_file", "write_file", "run_command", "task", "update_goal", "load_skill", "read_skill_resource", "memory_list", "memory_read", "read_tool_result"],
        )

    def test_profile_filters_preserve_registry_order(self):
        self.assertEqual(
            [spec.name for spec in build_tool_specs(profile='init', session=self.session)],
            ["list_files", "read_file", "write_file", "read_tool_result"],
        )
        self.assertEqual(
            [spec.name for spec in build_tool_specs(profile='review', target_path='.', session=self.session)],
            ["list_files", "read_file", "read_tool_result"],
        )

    def test_every_spec_has_a_valid_contract(self):
        self.assertTrue(TOOL_SPECS)
        for spec in TOOL_SPECS:
            with self.subTest(tool=spec.name):
                self.assertTrue(spec.name)
                self.assertTrue(spec.description)
                self.assertIn("type", spec.input_schema)
                self.assertEqual(spec.input_schema["type"], "object")
                self.assertTrue(callable(spec.handler))
                self.assertIsInstance(spec.read_only, bool)
                self.assertIsInstance(spec.concurrency_safe, bool)
                self.assertTrue(spec.permission_key)
                self.assertTrue(spec.profiles)

    def test_mutating_tools_are_not_marked_read_only_or_concurrency_safe(self):
        by_name = {spec.name: spec for spec in TOOL_SPECS}
        for name in ("edit_file", "write_file", "run_command"):
            self.assertFalse(by_name[name].read_only)
            self.assertFalse(by_name[name].concurrency_safe)

    def test_langchain_tool_schemas_match_registered_contracts(self):
        registered = {spec.name: spec for spec in TOOL_SPECS}
        built = {tool.name: tool for tool in build_tools(file_session=self.session)}
        self.assertEqual(set(built), set(registered))
        for name, spec in registered.items():
            schema = built[name].args_schema.model_json_schema()
            with self.subTest(tool=name):
                self.assertEqual(set(schema["properties"]), set(spec.input_schema["properties"]))
                self.assertEqual(
                    set(schema.get("required", [])),
                    set(spec.input_schema.get("required", [])),
                )

    def test_mutating_tool_handlers_are_serialized_for_a_shared_session(self):
        (self.root / 'edit.txt').write_text('old\n')
        (self.root / 'write.txt').write_text('original\n')
        execution = ToolExecutionContext(self.root, approval_handler=lambda *args: {'type': 'approve'})
        tools = {tool.name: tool for tool in build_tools(file_session=self.session, execution_context=execution)}
        for path in ('edit.txt', 'write.txt'):
            self.assertTrue(json.loads(tools['read_file'].invoke({'path': path}))['read_complete'])
        state_lock = threading.Lock()
        active = 0
        maximum_active = 0
        completed_operations = []
        original_write = self.session._atomic_write
        original_capture = local_tools._capture_command_output

        def tracked(label, operation, *args, **kwargs):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(.02)
                result = operation(*args, **kwargs)
                with state_lock:
                    completed_operations.append(label)
                return result
            finally:
                with state_lock:
                    active -= 1

        ready = threading.Barrier(3)
        def invoke(name, arguments):
            ready.wait(timeout=2)
            return json.loads(tools[name].invoke(arguments))
        with (
            patch.object(self.session, '_atomic_write', side_effect=lambda *args, **kwargs: tracked('file', original_write, *args, **kwargs)),
            patch.object(local_tools, '_capture_command_output', side_effect=lambda *args, **kwargs: tracked('command', original_capture, *args, **kwargs)),
            ThreadPoolExecutor(max_workers=3) as executor,
        ):
            futures = [executor.submit(invoke, 'edit_file', {'path': 'edit.txt', 'old_string': 'old', 'new_string': 'new'}),
                       executor.submit(invoke, 'write_file', {'path': 'write.txt', 'content': 'replacement'}),
                       executor.submit(invoke, 'run_command', {'command': "printf 'command done'"})]
            results = [future.result(timeout=3) for future in futures]
        self.assertTrue(all(result['ok'] for result in results), results)
        self.assertEqual((self.root / 'edit.txt').read_text(), 'new\n')
        self.assertEqual((self.root / 'write.txt').read_text(), 'replacement')
        self.assertEqual(results[2]['output'], 'command done')
        self.assertEqual(sorted(completed_operations), ['command', 'file', 'file'])
        self.assertEqual(maximum_active, 1)


if __name__ == "__main__":
    unittest.main()
