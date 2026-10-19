"""Installation-wide desktop guide state, separate from connection setup."""
from __future__ import annotations

import os
from pathlib import Path

from nailong.core.bootstrap import BootstrapStore
from nailong.core.preferences import atomic_json, read_config, safe_config_path


_READ_ERROR = '首次使用引导状态无法读取，请检查本机配置文件后重试。'
_WRITE_ERROR = '首次使用引导状态无法保存，请检查本机配置目录后重试。'


class OnboardingStore:
    def __init__(self):
        bootstrap = BootstrapStore()
        self.path = bootstrap.path.with_name('onboarding.json')
        self.boundary = bootstrap.boundary

    def read(self) -> str:
        try:
            path = safe_config_path(self.path, self.boundary)
            if not path.exists():
                return 'pending'
            value = read_config(path, self.boundary)
            if (set(value) != {'version', 'status'} or type(value['version']) is not int
                    or value['version'] != 1 or not isinstance(value['status'], str)
                    or value['status'] not in {'completed', 'skipped'}):
                raise ValueError(_READ_ERROR)
            return value['status']
        except (ValueError, OSError, RuntimeError) as error:
            raise ValueError(_READ_ERROR) from error

    @staticmethod
    def workspace_placeholder(project) -> bool:
        welcome = os.getenv('IGNOVATE_DESKTOP_WELCOME_PROJECT')
        if not welcome:
            return False
        try:
            return Path(welcome).resolve() == Path(project).resolve()
        except (OSError, RuntimeError, ValueError):
            # A broken trusted desktop path cannot establish a real workspace.
            return True

    def snapshot(self, project) -> dict:
        error = None
        try:
            status = self.read()
        except ValueError:
            status, error = 'pending', _READ_ERROR
        placeholder = self.workspace_placeholder(project)
        state = {'version': 1, 'status': status,
                 'required': status == 'pending' or status == 'completed' and placeholder,
                 'workspace_placeholder': placeholder}
        if error:
            state['error'] = error
        return state

    def save(self, status: str) -> None:
        if not isinstance(status, str) or status not in {'completed', 'skipped'}:
            raise ValueError('首次使用引导操作无效。')
        self.read()  # Corrupt or redirected state must never be replaced silently.
        try:
            atomic_json(self.path, {'version': 1, 'status': status}, self.boundary)
        except (ValueError, OSError, RuntimeError) as error:
            raise ValueError(_WRITE_ERROR) from error
