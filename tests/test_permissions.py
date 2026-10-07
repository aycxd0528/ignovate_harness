import json
import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from nailong.core.permissions import Decision, PermissionEngine
from nailong.tools.registry import get_tool_specs


class PermissionEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("print('ok')", encoding="utf-8")
        (self.root / ".nailong").mkdir()
        self.engine = PermissionEngine(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def decide(self, name, args, *, profile="chat", mode="default"):
        return self.engine.decide_action(name, args, profile=profile, mode=mode)

    def test_read_only_tools_are_allowed_and_mutations_ask_by_default(self):
        self.assertEqual(self.decide("read_file", {"path": "src/main.py"}).decision, Decision.ALLOW)
        self.assertEqual(self.decide("edit_file", {"path": "src/main.py"}).decision, Decision.ASK)
        self.assertEqual(self.decide("run_command", {"command": "pytest"}).decision, Decision.ASK)

    def test_exact_session_write_grant_is_anchored_to_project_root(self):
        for granted, other in [('safe.txt', 'nested/safe.txt'), ('src/a.py', 'nested/src/a.py')]:
            with self.subTest(granted=granted):
                rule = self.engine.suggest_rule('write_file', {'path':granted})
                self.engine.grant_session(rule)
                self.assertEqual(self.decide('write_file', {'path':granted}).decision, Decision.ALLOW)
                self.assertEqual(self.decide('write_file', {'path':other}).decision, Decision.ASK)

    def test_exact_deny_does_not_block_other_directory_with_same_filename(self):
        self.engine.add_rule('deny', 'Read(./src/main.py)')
        (self.root/'nested/src').mkdir(parents=True)
        (self.root/'nested/src/main.py').write_text('safe')
        self.assertEqual(self.decide('read_file', {'path':'src/main.py'}).decision, Decision.DENY)
        self.assertEqual(self.decide('read_file', {'path':'nested/src/main.py'}).decision, Decision.ALLOW)

    def test_bypass_skips_permission_rules_without_persisting_them(self):
        self.engine.settings_path.write_text(json.dumps({'permissions': {
            'deny': ['Edit(*)', 'Bash(*)'], 'ask': ['Write(*)']}}))
        self.engine = PermissionEngine(self.root)
        before = self.engine.settings_path.read_bytes()
        for name, args in (
                ('edit_file', {'path': 'src/main.py'}),
                ('write_file', {'path': 'new.txt'}),
                ('run_command', {'command': 'echo x > new.txt && echo $(pwd)'})):
            with self.subTest(tool=name):
                result = self.decide(name, args, mode='bypassPermissions')
                self.assertEqual(result.decision, Decision.ALLOW)
                self.assertIn('跳过', result.reason)
        self.assertEqual(self.engine.settings_path.read_bytes(), before)
        self.assertEqual(PermissionEngine(self.root).decide_action('edit_file',
            {'path': 'src/main.py'}).decision, Decision.DENY)

    def test_full_access_allows_external_and_protected_paths_without_changing_default(self):
        outside = self.root.parent / (self.root.name + '-outside')
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        (self.root / 'escape').symlink_to(outside, target_is_directory=True)
        for path in ('.env', '.git/config', '.venv/a.py', '../outside.txt', str(outside / 'a.py'),
                'escape/a.py'):
            for name in ('read_file', 'write_file', 'edit_file'):
                with self.subTest(path=path, tool=name):
                    self.assertEqual(self.decide(name, {'path': path}, mode='bypassPermissions').decision,
                        Decision.ALLOW)
                    self.assertEqual(self.decide(name, {'path': path}).decision,
                        Decision.DENY)

    def test_bypass_preserves_readonly_profiles_and_init_scope(self):
        for profile in ('plan', 'review', 'subagent'):
            with self.subTest(profile=profile):
                self.assertEqual(self.decide('write_file', {'path': 'a.py'}, profile=profile,
                    mode='bypassPermissions').decision, Decision.DENY)
                self.assertEqual(self.decide('read_file', {'path': 'src/main.py'}, profile=profile,
                    mode='bypassPermissions').decision, Decision.ALLOW)
                self.assertEqual(self.decide('read_file', {'path': '../outside.txt'}, profile=profile,
                    mode='bypassPermissions').decision, Decision.ALLOW)
        self.assertEqual(self.decide('write_file', {'path': '.nailong/context.md'}, profile='init',
            mode='bypassPermissions').decision, Decision.ALLOW)
        self.assertEqual(self.decide('write_file', {'path': 'README.md'}, profile='init',
            mode='bypassPermissions').decision, Decision.DENY)

    def test_bypass_allows_registered_mcp_without_approval_but_preserves_profiles(self):
        from nailong.tools.execution import ToolExecutionContext
        from nailong.tools.files import FileSession

        calls = []
        def invoke(path):
            calls.append(path)
            return {'ok': True}
        base = next(spec for spec in get_tool_specs('chat') if spec.name == 'read_file')
        spec = replace(base, name='mcp__test__write', permission_key='mcp__test__write',
            handler=invoke, read_only=False, profiles=frozenset({'chat', 'plan', 'review', 'subagent'}))
        self.engine.add_rule('deny', 'mcp__test__write(*)')
        context = ToolExecutionContext(self.root, permission_engine=self.engine, permission_mode='bypassPermissions')
        session = FileSession(self.root)
        result = asyncio.run(context.invoke_async(spec, {'path': '/remote/object'}, profile='chat',
            file_session=session, config={}))
        self.assertTrue(result['ok'])
        self.assertEqual(calls, ['/remote/object'])
        for profile in ('plan', 'review', 'subagent'):
            with self.subTest(profile=profile):
                result = asyncio.run(context.invoke_async(spec, {'path': '/remote/object'}, profile=profile,
                    file_session=session, config={}))
                self.assertFalse(result['ok'])
        self.assertEqual(calls, ['/remote/object'])

    def test_bypass_rejects_redacted_secret_parameters_before_rules(self):
        for args in ({'path': 'a.txt', 'content': '[密钥已隐藏]'},
                {'command': 'echo [密钥已隐藏]'}, {'nested': [{'token': '[密钥已隐藏]'}]}):
            with self.subTest(args=args):
                self.assertEqual(self.decide('run_command', args, mode='bypassPermissions').decision,
                    Decision.DENY)

    def test_bypass_does_not_authorize_unknown_or_mismatched_tools(self):
        self.assertNotEqual(self.decide('unknown_tool', {}, mode='bypassPermissions').decision, Decision.ALLOW)
        spec = next(spec for spec in get_tool_specs('chat') if spec.name == 'read_file')
        self.assertEqual(self.engine.decide_action('write_file', {'path': 'a.py'},
            mode='bypassPermissions', tool_spec=spec).decision, Decision.DENY)

    def test_protected_paths_are_hard_denied_even_with_allow_rule(self):
        self.engine.add_rule("allow", "Read(*)")
        self.engine.add_rule("allow", "Edit(*)")
        for tool in ("read_file", "edit_file", "write_file"):
            with self.subTest(tool=tool):
                result = self.decide(tool, {"path": ".env"})
                self.assertEqual(result.decision, Decision.DENY)
                self.assertIn("保护", result.reason)

    def test_project_escape_is_hard_denied_even_with_allow_rule(self):
        self.engine.add_rule("allow", "Read(*)")
        result = self.decide("read_file", {"path": "../outside.txt"})
        self.assertEqual(result.decision, Decision.DENY)

    def test_absolute_project_alias_uses_same_permission_boundary(self):
        alias = self.root.parent / (self.root.name + '-alias')
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        self.engine.add_rule('allow', 'Read(./src/**)')
        result = self.decide('read_file', {'path': str(alias / 'src/main.py')})
        self.assertEqual(result.decision, Decision.ALLOW)
        self.assertEqual(result.matched_rule, 'Read(./src/**)')
        self.assertEqual(self.decide('read_file', {'path': str(alias / '../outside.txt')}).decision, Decision.DENY)

    def test_alias_cannot_hide_protected_lexical_symlink(self):
        from nailong.tools.files import FileSession
        alias = self.root.parent / (self.root.name + '-alias')
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        (self.root / '.env').symlink_to(self.root / 'src/main.py')
        path = str(alias / '.env')
        self.engine.add_rule('allow', 'Read(*)')
        self.assertEqual(self.decide('read_file', {'path': path}).decision, Decision.DENY)
        with self.assertRaises(ValueError):
            FileSession(self.root).resolve(path)
        import local_tools
        with local_tools.use_project_root(self.root):
            self.assertFalse(local_tools.read_file(path)['ok'])

    def test_explicit_deny_beats_allow(self):
        self.engine.add_rule("allow", "Bash(git status:*)")
        self.engine.add_rule("deny", "Bash(git status:*)")
        self.assertEqual(
            self.decide("run_command", {"command": "git status --short"}).decision,
            Decision.DENY,
        )

    def test_allow_command_rule_does_not_allow_unmatched_compound_command(self):
        self.engine.add_rule("allow", "Bash(git status:*)")
        self.assertEqual(
            self.decide(
                "run_command",
                {"command": "git status && rm -rf x"},
            ).decision,
            Decision.ASK,
        )

    def test_compound_command_is_allowed_only_when_every_part_matches(self):
        self.engine.add_rule("allow", "Bash(git status:*)")
        self.engine.add_rule("allow", "Bash(git diff:*)")
        self.assertEqual(
            self.decide(
                "run_command",
                {"command": "git status && git diff --stat"},
            ).decision,
            Decision.ALLOW,
        )

    def test_shell_expansion_redirection_and_here_doc_never_match_allow_rules(self):
        self.engine.add_rule("allow", "Bash(*)")
        for command in (
            "echo $(whoami)",
            "echo `whoami`",
            "echo hi > out.txt",
            "cat <<EOF\nsecret\nEOF",
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    self.decide("run_command", {"command": command}).decision,
                    Decision.ASK,
                )

    def test_all_compound_separators_require_each_command_to_match(self):
        self.engine.add_rule("allow", "Bash(git status:*)")
        for command in (
            "git status; rm -rf x",
            "git status || rm -rf x",
            "git status | rm -rf x",
            "git status\nrm -rf x",
            "cd .. && git status",
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    self.decide("run_command", {"command": command}).decision,
                    Decision.ASK,
                )

    def test_file_rules_match_paths_and_are_explained(self):
        self.engine.add_rule("allow", "Read(./src/**)")
        result = self.decide("read_file", {"path": "src/main.py"})
        self.assertEqual(result.decision, Decision.ALLOW)
        self.assertEqual(result.matched_rule, "Read(./src/**)")
        self.assertTrue(result.reason)

    def test_init_mode_only_asks_for_context_file_write(self):
        self.assertEqual(
            self.decide("write_file", {"path": ".nailong/context.md"}, profile="init").decision,
            Decision.ASK,
        )
        self.assertEqual(
            self.decide("write_file", {"path": "README.md"}, profile="init").decision,
            Decision.DENY,
        )

    def test_review_and_plan_modes_deny_mutations_even_when_allowed(self):
        self.engine.add_rule("allow", "Edit(*)")
        self.assertEqual(
            self.decide("edit_file", {"path": "src/main.py"}, profile="review").decision,
            Decision.DENY,
        )
        self.assertEqual(
            self.decide("edit_file", {"path": "src/main.py"}, mode="plan").decision,
            Decision.DENY,
        )
        self.assertEqual(
            self.decide("exit_plan_mode", {"plan_markdown": "# Plan"}, mode="plan").decision,
            Decision.ALLOW,
        )

    def test_accept_edits_mode_allows_file_mutations_but_still_asks_for_commands(self):
        self.assertEqual(
            self.decide("edit_file", {"path": "src/main.py"}, mode="acceptEdits").decision,
            Decision.ALLOW,
        )
        self.assertEqual(
            self.decide("run_command", {"command": "pytest"}, mode="acceptEdits").decision,
            Decision.ASK,
        )

    def test_session_rule_only_applies_to_its_tool_and_scope(self):
        self.engine.grant_session("Bash(pytest tests/unit:*)")
        self.assertEqual(
            self.decide("run_command", {"command": "pytest tests/unit/test_x.py"}).decision,
            Decision.ALLOW,
        )
        self.assertEqual(
            self.decide("run_command", {"command": "pytest tests/integration"}).decision,
            Decision.ASK,
        )

    def test_session_rule_is_derived_for_single_command_or_path(self):
        command_rule = self.engine.suggest_rule("run_command", {"command": "pytest tests/test_x.py"})
        edit_rule = self.engine.suggest_rule("edit_file", {"path": "src/main.py"})
        self.assertEqual(command_rule, "Bash(pytest tests/test_x.py)")
        self.assertEqual(edit_rule, "Edit(./src/main.py)")

    def test_settings_load_rules_and_invalid_settings_fail_closed(self):
        settings = self.root / ".nailong" / "settings.json"
        settings.write_text(
            json.dumps({"permissions": {"allow": ["Bash(git status:*)"]}}),
            encoding="utf-8",
        )
        engine = PermissionEngine(self.root)
        self.assertEqual(
            engine.decide_action("run_command", {"command": "git status"}).decision,
            Decision.ALLOW,
        )
        settings.write_text("{invalid", encoding="utf-8")
        malformed = PermissionEngine(self.root)
        self.assertEqual(
            malformed.decide_action("run_command", {"command": "git status"}).decision,
            Decision.ASK,
        )


if __name__ == "__main__":
    unittest.main()
