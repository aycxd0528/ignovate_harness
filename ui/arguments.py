"""Slash-command words, preserving native Windows path separators."""
import os
import shlex

_WINDOWS = os.name == 'nt'


def split_arguments(text):
    if not _WINDOWS:
        return shlex.split(text)
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    lexer.escape = ''
    return list(lexer)
