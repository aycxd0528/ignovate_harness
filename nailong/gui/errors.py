"""Useful failed-run descriptions without inspecting HTTP payloads."""
from __future__ import annotations

import re


_REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', re.ASCII)


def describe_run_error(error: BaseException) -> str:
    """Describe at most three exceptions; callers must redact secrets afterward.

    Keep complete messages until the caller's redaction step: truncating here
    could cut a secret in half and defeat exact secret replacement. Only stable
    scalar HTTP metadata is included, never request/response objects or headers.
    """
    descriptions = []
    seen = set()
    while error is not None and id(error) not in seen and len(descriptions) < 3:
        seen.add(id(error))
        name = type(error).__name__
        message = str(error).strip()
        if not message:
            message = '操作等待超时，请核对连接与运行状态。' if isinstance(error, TimeoutError) else '运行失败。'
        detail = f'{name}: {message}'
        metadata = []
        status = getattr(error, 'status_code', None)
        if type(status) is int and 100 <= status <= 599:
            metadata.append(f'HTTP {status}')
        request_id = getattr(error, 'request_id', None)
        if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id):
            metadata.append(f'request_id={request_id}')
        if metadata:
            detail += ' [' + '; '.join(metadata) + ']'
        descriptions.append(detail)
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return '；原因：'.join(descriptions)
