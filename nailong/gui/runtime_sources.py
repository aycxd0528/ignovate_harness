"""Keep a long-lived Python adapter on one coherent application revision."""
from __future__ import annotations

import hashlib
from pathlib import Path


SOURCE_UPDATE_MESSAGE = '应用代码已更新，请重新启动 Ignovate 后继续；会话和草稿会保留。'


class RuntimeSources:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
        self._changed = False
        self._signature = self._signatures()
        self._hashes = {path: self._digest(path) for path in self._signature}

    def _signatures(self):
        files = [self.root / name for name in
                 ('agent.py', 'agent_service.py', 'config.py', 'tools.py', 'local_tools.py', 'headless.py', 'tui.py')]
        for directory in ('nailong', 'ui'):
            files.extend((self.root / directory).rglob('*.py'))
        return {path: (path.stat().st_mtime_ns, path.stat().st_size) for path in files if path.is_file()}

    @staticmethod
    def _digest(path):
        return hashlib.sha256(path.read_bytes()).digest()

    def changed(self):
        if self._changed:
            return True
        try:
            signature = self._signatures()
            if signature == self._signature:
                return False
            # A timestamp-only touch is harmless; Python interface changes are not.
            self._changed = set(signature) != set(self._signature) or any(
                self._digest(path) != self._hashes[path] for path in signature)
            if not self._changed:
                self._signature = signature
        except OSError:
            self._changed = True
        return self._changed
