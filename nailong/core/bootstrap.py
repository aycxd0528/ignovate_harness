"""Explicit first-run connection configuration, outside project/runtime logs."""
from __future__ import annotations

import os
from pathlib import Path

from nailong.core.preferences import atomic_json, read_config


class BootstrapStore:
    def __init__(self, directory=None):
        original = Path(directory or os.getenv('IGNOVATE_CONFIG_DIR') or
                        Path.home()/'.ignovate').expanduser().absolute()
        self.boundary = original.parent.resolve()
        self.path = self.boundary/original.name/'config.json'

    def read(self) -> dict:
        value = read_config(self.path, self.boundary)
        if not value:
            return {}
        from nailong.core.connection import validate_connection
        provider = value.get('provider')
        if not isinstance(provider, dict) or set(provider) != {'api_base', 'model', 'api_key'}:
            raise ValueError('用户连接配置无效；请检查 ~/.ignovate/config.json。')
        validate_connection(provider['api_base'], provider['model'], provider['api_key'])
        from nailong.core.reasoning import model_reasoning_kwargs
        model_reasoning_kwargs(provider['model'], value.get('reasoning_effort', 'default'))
        return value

    def completed(self) -> bool:
        return self.read().get('onboarding_complete') is True

    def save(self, api_base, model, api_key, reasoning_effort='default', *, existing_key='') -> dict:
        # Read first: corrupt or redirected files must not be silently replaced.
        previous = self.read()
        api_key = api_key.strip() or existing_key or previous.get('provider', {}).get('api_key', '')
        from nailong.core.connection import validate_connection
        api_base, model, api_key = validate_connection(api_base, model, api_key)
        from nailong.core.reasoning import model_reasoning_kwargs
        model_reasoning_kwargs(model, reasoning_effort)
        value = {'version': 1, 'onboarding_complete': True,
                 'provider': {'api_base': api_base, 'model': model, 'api_key': api_key},
                 'reasoning_effort': reasoning_effort}
        atomic_json(self.path, value, self.boundary)
        return value
