import tempfile
import unittest
from pathlib import Path
from nailong.core.permissions import PermissionEngine,Decision

class ArgvPermissionTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.engine=PermissionEngine(Path(directory.name))
    def test_deny_applies_to_argv_executable(self):
        self.engine.add_rule('deny','Bash(python:*)')
        args={'command':'','argv':['python','-m','unittest'],'expected_exit_code':0}
        self.assertEqual(self.engine.decide_action('run_command',args).decision,Decision.DENY)
        self.assertEqual(args['command'],'')
    def test_allow_applies_to_argv_and_suggested_rule_names_executable(self):
        self.engine.add_rule('allow','Bash(python:*)')
        args={'argv':['python','tests/check.py']}
        self.assertEqual(self.engine.decide_action('run_command',args).decision,Decision.ALLOW)
        self.assertIn('python',self.engine.suggest_rule('run_command',args))
    def test_literal_shell_metacharacters_do_not_gain_unmatched_allow(self):
        self.engine.add_rule('allow','Bash(python:*)')
        self.assertEqual(self.engine.decide_action('run_command',{'argv':['rm','literal;python x']}).decision,Decision.ASK)

    def test_literal_dollar_argument_cannot_escape_executable_deny(self):
        self.engine.add_rule('deny','Bash(python:*)')
        result=self.engine.decide_action('run_command',{'argv':['python','-c','print("$literal")']})
        self.assertEqual(result.decision,Decision.DENY)

    def test_suggested_argv_rule_roundtrips_windows_paths_and_schema_defaults(self):
        # Break caught: parsing literal argv as shell text rejects Windows backslashes.
        args = {'argv': [r'C:\Python\python.exe', r'C:\项目 folder\check.py']}
        expected = r"Bash('C:\Python\python.exe' 'C:\项目 folder\check.py')"
        pending = self.engine.decide_action('run_command', args)
        self.assertEqual(pending.decision, Decision.ASK)
        self.assertEqual(pending.suggested_rule, expected)
        rule = self.engine.suggest_rule('run_command', args)
        self.assertEqual(rule, expected)
        self.engine.grant_session(rule)
        result = self.engine.decide_action('run_command', {
            **args, 'command': '', 'timeout_seconds': 30, 'expected_exit_code': 0})
        self.assertEqual(result.decision, Decision.ALLOW)
        self.assertEqual(result.matched_rule, expected)
        self.assertEqual(result.suggested_rule, expected)
        self.assertEqual(args, {'argv': [r'C:\Python\python.exe', r'C:\项目 folder\check.py']})
        self.assertEqual(self.engine.decide_action('run_command', {
            'argv': [r'C:\Python\python.exe', r'C:\项目 folder\other.py']}).decision, Decision.ASK)

    def test_literal_argv_suggestion_does_not_authorize_shell_backslash_text(self):
        # Break caught: a literal argv grant bypasses the conservative shell parser.
        args = {'argv': [r'C:\Python\python.exe', r'C:\Work\check.py']}
        self.engine.grant_session(self.engine.suggest_rule('run_command', args))
        result = self.engine.decide_action('run_command', {
            'command': r"'C:\Python\python.exe' 'C:\Work\check.py'"})
        self.assertEqual(result.decision, Decision.ASK)

    def test_literal_argv_metacharacters_roundtrip_without_authorizing_raw_shell(self):
        # Break caught: shell-only safety checks reject harmless literal argv data or allow shell expansions.
        args = {'argv': ['python', '-c', 'print("$HOME")']}
        expected = "Bash(python -c 'print(\"$HOME\")')"
        self.assertEqual(self.engine.suggest_rule('run_command', args), expected)
        self.engine.grant_session(expected)
        self.assertEqual(self.engine.decide_action('run_command', args).decision, Decision.ALLOW)
        self.assertEqual(self.engine.decide_action('run_command', {
            'command': 'python -c \'print("$HOME")\''}).decision, Decision.ASK)
        self.engine.add_rule('deny', 'Bash(python:*)')
        self.assertEqual(self.engine.decide_action('run_command', args).decision, Decision.DENY)

    def test_argv_pattern_and_control_literals_cannot_create_generated_session_grants(self):
        # Break caught: generated rules broaden literal glob data or cannot represent control characters.
        for argument in ('*', '?', '[ab]', 'literal]', 'first\nsecond', 'first\rsecond', '\t', '\x1b', '\x7f'):
            with self.subTest(argument=argument):
                args = {'argv': ['python', argument]}
                self.engine.grant_session(self.engine.suggest_rule('run_command', args))
                self.assertEqual(self.engine.decide_action('run_command', args).decision, Decision.ASK)
                self.assertEqual(self.engine.decide_action('run_command', {
                    'argv': ['python', 'other']}).decision, Decision.ASK)

    def test_argv_whitespace_literals_cannot_grant_normalized_different_arguments(self):
        # Break caught: fnmatch normalization broadens a generated literal grant to different argv values.
        cases = [
            ([r'C:\Program  Files\approved.exe', 'check.py'],
             [r'C:\Program Files\approved.exe', 'check.py']),
            ([r'C:\Python\python.exe', r'C:\Work  area\check.py'],
             [r'C:\Python\python.exe', r'C:\Work area\check.py']),
            (['python', '-c', 'print("two  spaces")'],
             ['python', '-c', 'print("two spaces")']),
        ]
        for original, changed in cases:
            with self.subTest(argv=original):
                args = {'argv': original}
                self.engine.grant_session(self.engine.suggest_rule('run_command', args))
                self.assertEqual(self.engine.decide_action('run_command', args).decision, Decision.ASK)
                self.assertEqual(self.engine.decide_action('run_command', {'argv': changed}).decision,
                                 Decision.ASK)

    def test_argv_exact_grants_preserve_whitespace_in_both_directions(self):
        # Break caught: a safe single-space grant authorizes different double-space argv, or vice versa.
        cases = [
            ([r'C:\Program Files\approved.exe', 'check.py'],
             [r'C:\Program  Files\approved.exe', 'check.py'],
             r"Bash('C:\Program  Files\approved.exe' check.py)"),
            ([r'C:\Python\python.exe', r'C:\Work area\check.py'],
             [r'C:\Python\python.exe', r'C:\Work  area\check.py'],
             r"Bash('C:\Python\python.exe' 'C:\Work  area\check.py')"),
            (['python', '-c', 'print("two spaces")'],
             ['python', '-c', 'print("two  spaces")'],
             "Bash(python -c 'print(\"two  spaces\")')"),
        ]
        for single, double, double_rule in cases:
            with self.subTest(argv=single):
                engine = PermissionEngine(self.engine.project_root, rules={})
                engine.grant_session(engine.suggest_rule('run_command', {'argv': single}))
                self.assertEqual(engine.decide_action('run_command', {'argv': single}).decision, Decision.ALLOW)
                self.assertEqual(engine.decide_action('run_command', {'argv': double}).decision, Decision.ASK)
            with self.subTest(argv=double):
                engine = PermissionEngine(self.engine.project_root, rules={})
                engine.grant_session(double_rule)
                self.assertEqual(engine.decide_action('run_command', {'argv': double}).decision, Decision.ALLOW)
                self.assertEqual(engine.decide_action('run_command', {'argv': single}).decision, Decision.ASK)

    def test_manual_argv_patterns_preserve_globs_prefixes_and_exact_deny_priority(self):
        # Break caught: literal matching disables intentional wildcard grants or normalizes exact deny values.
        self.engine.add_rule('allow', r"Bash('C:\Python\python.exe' 'C:\Work *\check.py')")
        for folder in ('area', ' area'):
            self.assertEqual(self.engine.decide_action('run_command', {
                'argv': [r'C:\Python\python.exe', 'C:\\Work ' + folder + r'\check.py']}).decision, Decision.ALLOW)
        self.engine.add_rule('allow', 'Bash(python:*)')
        self.engine.add_rule('deny', "Bash(python -c 'print(\"two spaces\")')")
        single = {'argv': ['python', '-c', 'print("two spaces")']}
        double = {'argv': ['python', '-c', 'print("two  spaces")']}
        self.assertEqual(self.engine.decide_action('run_command', single).decision, Decision.DENY)
        self.assertEqual(self.engine.decide_action('run_command', double).decision, Decision.ALLOW)
        self.engine.add_rule('deny', 'Bash(python:*)')
        self.assertEqual(self.engine.decide_action('run_command', double).decision, Decision.DENY)

    def test_raw_shell_matching_keeps_legacy_whitespace_normalization(self):
        # Break caught: literal argv matching changes the legacy shell command contract.
        self.engine.add_rule('allow', "Bash(python -c 'print(\"two spaces\")')")
        result = self.engine.decide_action('run_command', {
            'command': 'python  -c    \'print("two  spaces")\''})
        self.assertEqual(result.decision, Decision.ALLOW)

if __name__=='__main__':unittest.main()
