"""Bounded committed patches with complete operation counts."""
import difflib

DIFF_LIMIT = 12000


def diff_statistics(diff):
    additions = deletions = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith('@@ '):
            in_hunk = True
        elif in_hunk:
            additions += line.startswith('+')
            deletions += line.startswith('-')
    return {'additions': additions, 'deletions': deletions}


def committed_patch(before, after, path):
    lines = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile='a/'+path if before else '/dev/null', tofile='b/'+path)
    preview = []; size = additions = deletions = 0; in_hunk = False
    for line in lines:
        if line.startswith('@@ '):
            in_hunk = True
        elif in_hunk:
            additions += line.startswith('+'); deletions += line.startswith('-')
        value = line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
        if size < DIFF_LIMIT:
            preview.append(value[:DIFF_LIMIT-size])
        size += len(value)
    return {'diff': ''.join(preview), 'diff_truncated': size > DIFF_LIMIT,
            'additions': additions, 'deletions': deletions}
