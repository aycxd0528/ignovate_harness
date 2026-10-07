"""Bounded, permission-aware selection of files for one static review turn."""
from __future__ import annotations

import os

from nailong.core.permissions import Decision
from nailong.tools.files import FileSession


def select_review_files(project_root, scopes, permission_engine, *, mode='default', limit=20):
    session = FileSession(project_root)
    selected, skipped = [], []
    scanned = 0

    def skip(reason, path=None):
        if len(skipped) < 100:
            skipped.append({'reason': reason, **({'path': path} if path else {})})

    def include(candidate):
        nonlocal scanned
        scanned += 1
        if scanned > 5000:
            skip('selection_budget')
            return False
        try:
            resolved = session.resolve(str(candidate))
            if not resolved.is_file():
                skip('not_regular_file')
                return True
            relative = resolved.relative_to(session.project_root).as_posix()
            permission = permission_engine.decide_action('read_file', {'path': relative},
                profile='review', mode=mode)
            if permission.decision == Decision.DENY:
                skip('permission_denied', relative)
                return True
            # Selecting a filename gives no approval to consume its content.
            # ASK still reaches the execution gate when the model reads it.
            if relative not in selected:
                if len(selected) >= limit:
                    skip('file_limit')
                    return False
                selected.append(relative)
        except (OSError, ValueError):
            skip('path_unavailable')
        return True

    for scope in scopes:
        try:
            target = session.resolve(scope)
        except (OSError, ValueError):
            skip('scope_unavailable')
            continue
        if target.is_file():
            if not include(target):
                break
        elif target.is_dir():
            exhausted = False
            for current, directories, files in os.walk(target, followlinks=False,
                    onerror=lambda error: skip('directory_unreadable')):
                scanned += 1
                if scanned > 5000:
                    skip('selection_budget')
                    exhausted = True
                    break
                allowed = []
                for directory in sorted(directories):
                    if session._is_protected(directory):
                        continue
                    child = os.path.join(current, directory)
                    if os.path.islink(child):
                        skip('symlink_directory')
                    else:
                        allowed.append(directory)
                directories[:] = allowed
                for filename in sorted(files):
                    if session._is_protected(filename):
                        continue
                    if not include(os.path.join(current, filename)):
                        exhausted = True
                        break
                if exhausted:
                    break
            if exhausted:
                break
        else:
            skip('scope_unavailable')
    return {'paths': selected, 'selection_complete': bool(selected) and not skipped,
            'skipped_paths': skipped}
