"""Resolve a POSIX link graph from a complete pinned inventory, without I/O."""
from collections import deque
from pathlib import Path
import stat

from nailong.tools.files import FileSession

MAX_LINK_HOPS = 40


def _target(root, parent, target):
    path = Path(target)
    if path.is_absolute():
        try:
            return (), path.relative_to(root).parts
        except ValueError:
            raise ValueError('验证链接指向项目之外，无法证明完整输入覆盖。') from None
    return parent.parts, path.parts


def resolve_link(root, relative, target, inventory, phase, exclusions, deadline):
    prefix, parts = _target(root, relative.parent, target)
    prefix, pending = list(prefix), deque(parts)
    seen = set()
    hops = 0
    while pending:
        deadline.check()
        part = pending.popleft()
        if part == '.':
            continue
        if part == '..':
            if not prefix:
                raise ValueError('验证链接逃逸项目范围，无法证明完整输入覆盖。')
            prefix.pop()
            continue
        path = Path(*prefix, part)
        if (FileSession._is_protected(part) or path in exclusions
                or path.parts[:2] == ('.nailong', 'exports')):
            raise ValueError('验证链接指向保护或排除范围，无法证明完整输入覆盖。')
        version = inventory.lookup(phase, path)
        if version is None:
            # The complete namespace proves this component is absent. Check
            # the remaining lexical path still stays within the boundary.
            rest = [*prefix, part]
            for item in pending:
                if item == '..':
                    if not rest:
                        raise ValueError('验证链接逃逸项目范围。')
                    rest.pop()
                elif item != '.':
                    rest.append(item)
            return 'missing', Path(*rest)
        if stat.S_ISLNK(version[2]):
            key = (tuple(prefix), part, tuple(pending))
            if key in seen:
                return 'cycle', path
            seen.add(key)
            hops += 1
            if hops > MAX_LINK_HOPS:
                return 'link_limit', path
            base, replacement = _target(root, path.parent, version[6])
            prefix = list(base)
            pending.extendleft(reversed(replacement))
            continue
        prefix.append(part)
        if not stat.S_ISDIR(version[2]):
            return ('not_directory' if pending else 'file'), path
    return 'directory', Path(*prefix)
