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

if __name__=='__main__':unittest.main()
