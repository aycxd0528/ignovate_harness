"""Project GUI defaults and a native-only catalog of model connections."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from nailong.core.bootstrap import BootstrapStore
from nailong.core.connection import validate_connection
from nailong.core.preferences import atomic_json, read_config, safe_config_path


_DEFAULTS = {
    'default_permission': 'default', 'theme': 'system', 'font_size': 14,
    'tool_details': 'standard', 'show_changes': True, 'busy_send': 'queue',
    'shortcuts': {'new_chat': 'Mod+N', 'commands': 'Mod+K', 'find': 'Mod+F'},
}
_CHOICES = {
    'default_permission': {'default', 'acceptEdits', 'bypassPermissions'},
    'theme': {'system', 'light', 'dark'},
    'tool_details': {'compact', 'standard', 'detailed'},
    'busy_send': {'queue', 'draft'},
}
_PROVIDER_FIELDS = {'id', 'name', 'api_base', 'model', 'api_key'}
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


def _text(value, limit, label, *, empty=False):
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(label + '格式或长度无效。')
    value = value.strip()
    if not value and not empty:
        raise ValueError(label + '不能为空。')
    return value


def _preferences(values, previous=None):
    if not isinstance(values, dict) or set(values) - set(_DEFAULTS):
        raise ValueError('界面设置包含无效字段。')
    result = dict(previous or {})
    for key, value in values.items():
        if key in _CHOICES:
            if not isinstance(value, str) or value not in _CHOICES[key]:
                raise ValueError('界面设置选项无效。')
        elif key == 'font_size':
            if type(value) is not int or not 12 <= value <= 22:
                raise ValueError('聊天字号必须为 12 到 22 的整数。')
        elif key == 'show_changes':
            if type(value) is not bool:
                raise ValueError('文件变更显示设置必须为布尔值。')
        elif key == 'shortcuts':
            if not isinstance(value, dict) or set(value) - set(_DEFAULTS['shortcuts']):
                raise ValueError('快捷键设置无效。')
            shortcuts = {**_DEFAULTS['shortcuts'], **result.get('shortcuts', {})}
            for name, shortcut in value.items():
                if not isinstance(shortcut, str) or len(shortcut) > 32:
                    raise ValueError('快捷键必须为 Mod 组合。')
                parts = shortcut.split('+')
                modifiers = parts[:-1]
                if (len(parts) < 2 or parts[0] != 'Mod'
                        or any(part not in {'Mod', 'Shift', 'Alt'} for part in modifiers)
                        or len(set(modifiers)) != len(modifiers)
                        or not re.fullmatch(r'[A-Z0-9]', parts[-1])):
                    raise ValueError('快捷键必须为 Mod 与单个字母或数字的组合。')
                shortcuts[name] = shortcut
            identities = [(tuple(sorted(shortcut.split('+')[:-1])), shortcut.split('+')[-1])
                          for shortcut in shortcuts.values()]
            if len(set(identities)) != len(identities):
                raise ValueError('快捷键不能重复。')
            value = shortcuts
        result[key] = value
    return result


def _connection(api_base, model, key):
    api_base = _text(api_base, 2048, 'API 地址')
    model = _text(model, 256, '模型 ID')
    key = _text(key, 8192, 'API Key')
    return validate_connection(api_base, model, key)


def _redact(value, secrets):
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            if secret:
                value = value.replace(secret, '[密钥已隐藏]')
        return value
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()
                if key not in {'api_key', 'DEEPSEEK_API_KEY'}}
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets) for item in value]
    return value


class GUISettings:
    """Commands are called under GUIController's existing command lock."""

    def __init__(self, controller):
        self.controller = controller
        self.project = Path(controller.project).resolve()
        self.path = self.project / '.nailong/gui-settings.json'
        self.bootstrap = BootstrapStore()
        self.vault_path = self.bootstrap.path.with_name('providers.json')

    def _saved_preferences(self):
        value = read_config(self.path, self.project)
        if not value:
            return {}
        if (set(value) != {'version', 'preferences'} or type(value['version']) is not int
                or value['version'] != 1):
            raise ValueError('界面设置文件格式无效。')
        return _preferences(value['preferences'])

    def preferences(self):
        saved = self._saved_preferences()
        effective = {**_DEFAULTS, **saved}
        effective['shortcuts'] = {**_DEFAULTS['shortcuts'], **saved.get('shortcuts', {})}
        return {'preferences': effective, 'saved_keys': sorted(saved)}

    def _providers(self):
        value = read_config(self.vault_path, self.bootstrap.boundary)
        if not value:
            return []
        if (not {'version', 'providers'} <= set(value) or set(value) - {'version', 'providers', 'active_id'} or type(value['version']) is not int
                or value['version'] != 1 or not isinstance(value['providers'], list)
                or len(value['providers']) > 100):
            raise ValueError('模型连接列表格式无效。')
        providers, identifiers = [], set()
        for item in value['providers']:
            if not isinstance(item, dict) or set(item) != _PROVIDER_FIELDS:
                raise ValueError('模型连接记录格式无效。')
            identity = item['id']
            if (not isinstance(identity, str) or not _IDENTIFIER.fullmatch(identity)
                    or identity == 'current' or identity in identifiers):
                raise ValueError('模型连接标识无效或重复。')
            identifiers.add(identity)
            name = _text(item['name'], 80, '连接名称')
            base, model, key = _connection(item['api_base'], item['model'], item['api_key'])
            providers.append({'id': identity, 'name': name, 'api_base': base, 'model': model, 'api_key': key})
        if 'active_id' in value and (not isinstance(value['active_id'], str) or value['active_id'] not in identifiers):
            raise ValueError('当前模型连接标识无效。')
        return providers

    def _active_id(self, providers, current):
        selected = read_config(self.vault_path, self.bootstrap.boundary).get('active_id')
        match = next((item for item in providers if item['id'] == selected and self._same(item, current)), None)
        return match['id'] if match else next((item['id'] for item in providers if self._same(item, current)), None)

    def _current(self):
        # Validate redirected/corrupt bootstrap state even when a factory exists.
        bootstrap = self.bootstrap.read()
        configuration = dict(self.controller.configuration())
        if configuration.get('configured') is not True:
            configuration['error'] = '模型连接尚未配置，请检查本地连接设置。'
            return None, configuration
        settings = getattr(self.controller.factory, 'settings', None)
        if settings is None:
            from config import load_settings
            settings = load_settings()
        key = getattr(settings, 'api_key', '') or getattr(self.controller.sessions, 'api_key', '')
        key = key or bootstrap.get('provider', {}).get('api_key', '')
        base = getattr(settings, 'api_base', configuration['api_base'])
        model = getattr(settings, 'environment_model', None) or getattr(settings, 'model', configuration['model'])
        base, model, key = _connection(base, model, key)
        return {'id': 'current', 'name': '当前连接', 'api_base': base, 'model': model, 'api_key': key}, configuration

    @staticmethod
    def _same(left, right):
        return bool(left and right and all(left[key] == right[key] for key in ('api_base', 'model', 'api_key')))

    def catalog(self):
        preferences = self.preferences()
        providers = self._providers()
        current, configuration = self._current()
        active = self._active_id(providers, current)
        public = [{key: item[key] for key in ('id', 'name', 'api_base', 'model')}
                  | {'has_key': bool(item['api_key']), 'active': item['id'] == active}
                  for item in providers]
        if current and active is None:
            public.insert(0, {key: current[key] for key in ('id', 'name', 'api_base', 'model')}
                          | {'has_key': bool(current['api_key']), 'active': True})
        secrets = {item['api_key'] for item in providers}
        if current:
            secrets.add(current['api_key'])
        return _redact({**preferences, 'providers': public, 'configuration': configuration,
                        'paths': {'preferences': str(self.path), 'providers': str(self.vault_path),
                                  'configuration': str(self.bootstrap.path)}}, secrets)

    def update(self, changes):
        saved = self._saved_preferences()
        updated = _preferences(changes, saved)
        # Validate all catalog stores before publishing a preference change.
        self.catalog()
        if changes:
            try:
                atomic_json(self.path, {'version': 1, 'preferences': updated}, self.project)
            except OSError:
                raise ValueError('界面设置保存失败。') from None
        return self.catalog()

    def _idle(self):
        if self.controller.current_task or self.controller.queue:
            raise ValueError('请先完成或停止运行并移除队列，再修改当前连接。')

    @staticmethod
    def _snapshot(path, boundary):
        value = read_config(path, boundary)
        return path, boundary, value, path.exists()

    @staticmethod
    def _restore(snapshot):
        path, boundary, value, existed = snapshot
        if existed:
            atomic_json(path, value, boundary)
        else:
            path = safe_config_path(path, boundary)
            path.unlink(missing_ok=True)

    async def provider(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('模型连接操作必须是对象。')
        action = payload.get('action')
        if not isinstance(action, str):
            raise ValueError('模型连接操作无效。')
        allowed = {'action', 'project_id', 'id'}
        if action == 'save':
            allowed |= {'name', 'api_base', 'model', 'api_key', 'activate'}
        elif action not in {'activate', 'remove'}:
            raise ValueError('模型连接操作无效。')
        if set(payload) - allowed:
            raise ValueError('模型连接操作包含无效字段。')
        identity = payload.get('id', '')
        if not isinstance(identity, str) or (identity and not _IDENTIFIER.fullmatch(identity)):
            raise ValueError('模型连接标识无效。')
        self.preferences()
        providers = self._providers()
        current, _ = self._current()
        stored = next((item for item in providers if item['id'] == identity), None)
        existing = current if identity == 'current' else stored
        if identity and existing is None:
            raise ValueError('模型连接不存在，请重新加载设置。')
        active_id = self._active_id(providers, current)
        is_active = identity == active_id or identity == 'current' and existing is not None
        previous_vault = self._snapshot(self.vault_path, self.bootstrap.boundary)
        connection = None
        if action == 'remove':
            if not stored or is_active:
                raise ValueError('当前连接不能删除，请先切换到其他连接。')
            updated = [item for item in providers if item['id'] != identity]
        elif action == 'activate':
            if existing is None:
                raise ValueError('请选择已有模型连接。')
            self._idle()
            updated, connection = providers, existing
        else:
            activate = payload.get('activate', False)
            if type(activate) is not bool:
                raise ValueError('连接启用选项必须为布尔值。')
            name = _text(payload.get('name'), 80, '连接名称')
            key = _text(payload.get('api_key', ''), 8192, 'API Key', empty=True)
            key = key or (existing['api_key'] if existing else '')
            base, model, key = _connection(payload.get('api_base'), payload.get('model'), key)
            record = {'id': stored['id'] if stored else uuid.uuid4().hex, 'name': name,
                      'api_base': base, 'model': model, 'api_key': key}
            updated = [record if item['id'] == identity else item for item in providers]
            if stored is None:
                if len(providers) >= 100:
                    raise ValueError('模型连接列表已达到上限。')
                updated.append(record)
            if activate or is_active:
                self._idle()
                connection = record
        snapshots = [previous_vault]
        old_key = self.controller.sessions.api_key
        if connection:
            # Preflight every storage boundary before changing the vault or runtime.
            snapshots += [self._snapshot(self.bootstrap.path, self.bootstrap.boundary),
                          self._snapshot(self.project / '.nailong/settings.local.json', self.project)]
        vault_changed = action in {'save', 'remove'} or connection is not None and connection['id'] != 'current'
        next_active = connection['id'] if connection is not None and connection['id'] != 'current' else active_id
        written = False
        command_started = False
        try:
            if vault_changed:
                vault = {'version': 1, 'providers': updated}
                if next_active:
                    vault['active_id'] = next_active
                atomic_json(self.vault_path, vault, self.bootstrap.boundary)
                written = True
            if connection:
                command_started = True
                await self.controller._command_unlocked('configuration', {
                    'api_base': connection['api_base'], 'model': connection['model'],
                    'api_key': connection['api_key'], 'reasoning_effort': 'default',
                })
        except BaseException as error:
            restore_failed = False
            rollback = (snapshots[:1] if written else []) + (snapshots[1:] if command_started else [])
            for snapshot in reversed(rollback):
                try:
                    self._restore(snapshot)
                except (OSError, ValueError):
                    restore_failed = True
            self.controller.sessions.api_key = old_key
            if not isinstance(error, Exception):
                raise
            message = '模型连接保存失败，请检查本地配置。'
            if restore_failed:
                message = '模型连接保存失败，部分配置无法恢复，请检查本地配置。'
            raise ValueError(message) from None
        return self.catalog()
