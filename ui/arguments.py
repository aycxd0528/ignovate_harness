"""Slash-command words, preserving native Windows path separators."""
import os
import shlex

_WINDOWS = os.name == 'nt'


def split_arguments(text):
    if not _WINDOWS:
        return shlex.split(text)
    # Slash commands accept single-quoted words as well as Windows double
    # quotes. Keep ordinary backslashes literal; backslash runs immediately
    # before a double quote follow the native command-line quoting rules.
    escaped, quote, index = [], None, 0
    while index < len(text):
        char = text[index]
        if char == '\\' and quote != "'":
            end = index
            while end < len(text) and text[end] == '\\':
                end += 1
            count = end - index
            if quote == '"' and end < len(text) and text[end] == '"':
                escaped.append('\\' * (2 * (count // 2)))
                escaped.append('\\"' if count % 2 else '"')
                if not count % 2:
                    quote = None
                index = end + 1
                continue
            escaped.append('\\' * (count * 2))
            index = end
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        escaped.append(char)
        index += 1
    lexer = shlex.shlex(''.join(escaped), posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    return list(lexer)
