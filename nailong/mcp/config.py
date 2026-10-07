"""Validated MCP declarations; credentials are explicit environment references."""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from nailong.core.preferences import atomic_json, read_config

NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,31}')
REFERENCE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')


def validate_name(name: str) -> None:
    if not isinstance(name, str) or NAME.fullmatch(name) is None:
        raise ValueError('MCP 名称需以字母开头，最多 32 个字母、数字、下划线或连字符。')


def _references(value, *, headers=False) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError('MCP env/headers 必须是对象。')
    for key, reference in value.items():
        pattern = r'[A-Za-z0-9_-]+' if headers else r'[A-Za-z_][A-Za-z0-9_]*'
        if not isinstance(key, str) or not re.fullmatch(pattern, key):
            raise ValueError('MCP 环境变量或请求头名称无效。')
        match = REFERENCE.fullmatch(reference) if isinstance(reference, str) else None
        if match is None or match[1].startswith('DEEPSEEK_') or key.startswith('DEEPSEEK_'):
            raise ValueError('MCP 凭据必须使用 ${ENV_NAME} 引用，不能引用 DeepSeek 配置。')
    return dict(value)


def validate_definition(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError('MCP 服务声明必须是对象。')
    transport = value.get('transport')
    if transport not in {'stdio', 'http', 'streamable_http'}:
        raise ValueError('MCP transport 只能为 stdio 或 http。')
    allowed = {'transport', 'timeout_seconds'} | ({'command', 'args', 'env'} if transport == 'stdio' else {'url', 'headers'})
    if set(value) - allowed:
        raise ValueError('MCP 服务声明含不支持的字段。')
    result = dict(value)
    timeout = value.get('timeout_seconds', 30)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ValueError('MCP timeout_seconds 需为 1–120 秒。')
    if transport == 'stdio':
        command = value.get('command')
        arguments = value.get('args', [])
        if not isinstance(command, str) or not command.strip() or any(c in command for c in '\x00\r\n'):
            raise ValueError('MCP command 必须为可执行文件名称或路径。')
        if not isinstance(arguments, list) or any(not isinstance(item, str) or '\x00' in item for item in arguments):
            raise ValueError('MCP args 必须是文本数组。')
        result['env'] = _references(value.get('env', {}))
    else:
        url = value.get('url')
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            valid = parsed and parsed.scheme in {'http', 'https'} and parsed.hostname and not (
                parsed.username or parsed.password or parsed.query or parsed.fragment
            ) and not any(c.isspace() or ord(c) < 32 for c in url)
            if parsed is not None:
                _ = parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('MCP URL 必须是 http(s) 地址，不含账号、查询参数或片段；认证使用 headers 环境引用。')
        result['transport'] = 'http'
        result['headers'] = _references(value.get('headers', {}), headers=True)
    return result


def resolve_references(values: dict[str, str]) -> dict[str, str]:
    _references(values, headers=True)
    result = {}
    for key, reference in values.items():
        name = REFERENCE.fullmatch(reference)[1]
        value = os.environ.get(name)
        if not value:
            raise ValueError(f'MCP 所需环境变量 {name} 未设置。')
        if '\r' in value or '\n' in value or '\x00' in value:
            raise ValueError(f'MCP 环境变量 {name} 含不支持的控制字符。')
        result[key] = value
    return result


class MCPConfigStore:
    def __init__(self, project_root):
        self.project_root = Path(project_root).resolve()
        self.path = self.project_root / '.nailong/mcp.json'

    def _read(self) -> dict:
        payload = read_config(self.path, self.project_root)
        servers = payload.get('mcpServers', {})
        if not isinstance(servers, dict) or len(servers) > 32:
            raise ValueError('mcpServers 必须是服务映射，最多 32 项。')
        for name, definition in servers.items():
            validate_name(name)
            validate_definition(definition)
        return payload

    def list_servers(self) -> dict[str, dict]:
        return {name: validate_definition(value) for name, value in self._read().get('mcpServers', {}).items()}

    def add(self, name: str, definition: dict) -> None:
        validate_name(name)
        value = validate_definition(definition)
        payload = self._read()
        servers = payload.setdefault('mcpServers', {})
        if name in servers:
            raise ValueError(f'MCP 服务 {name} 已配置；请先断开并移除。')
        if len(servers) >= 32:
            raise ValueError('最多配置 32 个 MCP 服务。')
        servers[name] = value
        atomic_json(self.path, payload, self.project_root)

    def remove(self, name: str) -> None:
        payload = self._read()
        servers = payload.get('mcpServers', {})
        if name not in servers:
            raise ValueError('MCP 服务未配置。')
        del servers[name]
        atomic_json(self.path, payload, self.project_root)
