"""Bounded provider-compatible names; callers reject any resulting collision."""
import hashlib
import re


def tool_name(server: str, remote: str) -> str:
    candidate = f'mcp__{server}__{remote}'
    if '__' not in server and re.fullmatch(r'[A-Za-z0-9_-]+', remote) and len(candidate) <= 64:
        return candidate
    server_part = re.sub('_+', '_', server)[:12]
    remote_part = re.sub(r'[^A-Za-z0-9_-]', '_', remote)[:20] or 'tool'
    digest = hashlib.sha256((server + '\x00' + remote).encode('utf-8')).hexdigest()[:16]
    return f'mcp__{server_part}__{remote_part}_{digest}'
