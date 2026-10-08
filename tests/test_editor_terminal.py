"""A POSIX editor must keep access to its controlling terminal."""
import os
import shlex
import signal
import sys
import time
import unittest


@unittest.skipUnless(os.name == 'posix', 'POSIX controlling-terminal regression')
class EditorTerminalTests(unittest.TestCase):
    def test_synchronous_editor_retains_controlling_terminal(self):
        import pty
        from nailong.core.plan import edit_plan_with_editor
        pid, descriptor = pty.fork()
        if pid == 0:
            try:
                code = 'import os; fd=os.open("/dev/tty",os.O_RDWR); os.close(fd)'
                command = shlex.join([sys.executable, '-c', code])
                result = edit_plan_with_editor('original', editor=command)
                os._exit(0 if result == 'original' else 3)
            except BaseException:
                os._exit(2)
        completed = False
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited:
                    completed = True
                    self.assertTrue(os.WIFEXITED(status), status)
                    self.assertEqual(os.WEXITSTATUS(status), 0)
                    break
                time.sleep(.02)
            self.assertTrue(completed, 'editor did not exit')
        finally:
            if not completed:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(descriptor)


if __name__ == '__main__':
    unittest.main()
