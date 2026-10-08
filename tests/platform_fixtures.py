"""Real process fixtures expressed in each platform's native command syntax."""
import os
import shlex
import subprocess
import sys


def shell_join(arguments):
    values = list(arguments)
    if os.name != 'nt':
        return shlex.join(values)
    if values[0] == sys.executable and '-c' in values:
        index = values.index('-c') + 1
        source = values[index]
        values[index] = "exec(bytes.fromhex('" + source.encode('utf-8').hex() + "').decode('utf-8'))"
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    return '& ' + ' '.join(map(quote, values))


def python_command(source):
    return shell_join([sys.executable, '-B', '-u', '-c', source])


def editor_command(arguments):
    return subprocess.list2cmdline(arguments) if os.name == 'nt' else shlex.join(arguments)
