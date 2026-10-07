"""Observe static review input coverage after successful model requests.

This module never reads files or model conclusions. Its caller owns runtime
provenance, the current-turn boundary, independent input fingerprints, and task
identity. Only character ranges actually consumed by successful requests count.
"""
from __future__ import annotations

import json
from pathlib import PurePosixPath, PureWindowsPath
from threading import RLock


_UNKNOWN = {'', 'unknown', 'missing', 'unavailable', 'none', 'null', 'n/a', '?', '未知', '未获取'}
_MAX_FILES = 20


def _known(value):
    return (isinstance(value, str) and value == value.strip()
            and value.casefold() not in _UNKNOWN)


def _file_path(value):
    if not isinstance(value, str) or not value or '\0' in value or value.endswith('/'):
        return None
    path, windows = PurePosixPath(value), PureWindowsPath(value)
    if (path.is_absolute() or windows.drive or windows.root
            or '..' in path.parts or '..' in windows.parts or str(path) == '.'):
        return None
    return str(path)


def _field(message, key, default=None):
    return message.get(key, default) if isinstance(message, dict) else getattr(message, key, default)


def _result(message):
    content = _field(message, 'content')
    if isinstance(content, list):
        if any(not isinstance(row, dict) or row.get('type') != 'text'
               or not isinstance(row.get('text'), str) for row in content):
            return None
        content = ''.join(row['text'] for row in content)
    if not isinstance(content, str):
        return None
    try:
        result = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(result, dict) or result.get('ok') is not True:
        return None
    body = result.get('data', result)
    if not isinstance(body, dict) or body.get('ok', True) is not True:
        return None
    # Transport truncation is different from ordinary, recoverable file paging.
    if any(result.get(key) or body.get(key) for key in
           ('result_truncated', 'output_truncated', 'cancelled')):
        return None
    if _field(message, 'status') == 'error':
        return None
    return body


def _merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def _missing(intervals, total):
    result, cursor = [], 0
    for start, end in intervals:
        if cursor < start:
            result.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < total:
        result.append([cursor, total])
    return result


class ReviewCoverageCollector:
    """One collector per review workflow; no persistence, I/O or TaskStore import.

    ``selected_paths`` must be files enumerated by the runtime, never a model's
    asserted scope. ``excluded_call_ids`` must include pre-existing read_file
    results: merely consuming a historical body does not establish freshness.
    ``consume_request`` is called only after the actual model handler succeeds,
    with the post-context-filter messages it was given.
    """

    def __init__(self, selected_paths, *, fingerprint_before=None,
                 selection_complete=True, skipped_paths=(), excluded_call_ids=()):
        if isinstance(selected_paths, (str, bytes)):
            raise ValueError('审查范围必须为明确枚举的文件集合。')
        self._files = {}
        self._selection_reasons = []
        for raw in selected_paths:
            path = _file_path(raw)
            if path is None:
                raise ValueError('审查范围必须使用项目内相对文件路径。')
            if path in self._files:
                continue
            if len(self._files) >= _MAX_FILES:
                self._selection_reasons.append('选定文件超过 20 个，审查范围不完整。')
                break
            self._files[path] = {'version': None, 'total_chars': None, 'ranges': [],
                                 'observed': False, 'reasons': []}
        if not self._files:
            self._selection_reasons.append('没有明确选定的审查文件。')
        if selection_complete is not True or skipped_paths:
            self._selection_reasons.append('范围枚举有遗漏、跳过或截断，不能声明完整审查。')
        if isinstance(excluded_call_ids, (str, bytes)):
            raise ValueError('历史调用 ID 必须为集合。')
        self._seen = set(excluded_call_ids)
        if any(not isinstance(identifier, str) or not identifier for identifier in self._seen):
            raise ValueError('历史调用 ID 无效。')
        self._before = fingerprint_before
        self._notes = []
        self._lock = RLock()

    def consume_request(self, messages):
        """Add valid read_file pages from an actual successful request only.

        Repeated call IDs, failed/cropped results, unsupported bodies and model
        prose contribute zero coverage. Previously valid pages are retained when
        later context compaction clears their bodies. Fresh calls can retry a
        failed page, but different file versions never combine into a pass.
        """
        with self._lock:
            for message in messages:
                if (_field(message, 'type', _field(message, 'role')) != 'tool'
                        or _field(message, 'name') != 'read_file'):
                    continue
                identifier = _field(message, 'tool_call_id')
                if not isinstance(identifier, str) or not identifier or identifier in self._seen:
                    continue
                self._seen.add(identifier)
                body = _result(message)
                if body is None:
                    self._note('失败、裁剪或无效的读取结果未计入覆盖。')
                    continue
                path = _file_path(body.get('path'))
                if path not in self._files:
                    continue
                file = self._files[path]
                version = body.get('version')
                total, offset, content = body.get('total_chars'), body.get('content_offset'), body.get('content')
                if (isinstance(content, str) and '[密钥已隐藏]' in content
                        or body.get('content_redacted') or body.get('content_transformed')):
                    # Offsets refer to the original decoded file. A replacement
                    # can grow and falsely bridge an unread source interval.
                    self._note('脱敏或变换后的正文没有可靠的源字符映射，未计入覆盖。')
                    continue
                if (not _known(version) or type(total) is not int or total < 0
                        or type(offset) is not int or offset < 0 or not isinstance(content, str)
                        or offset > total or offset + len(content) > total):
                    self._note('缺少可靠版本或字符区间的读取结果未计入覆盖。')
                    continue
                if file['observed'] and (version != file['version'] or total != file['total_chars']):
                    reason = '读取期间文件版本或同版本长度发生变化，不能合并覆盖。'
                    if reason not in file['reasons']:
                        file['reasons'].append(reason)
                    continue
                file.update(observed=True, version=version, total_chars=total)
                file['ranges'] = _merge(file['ranges'] + [[offset, offset + len(content)]])

    def _note(self, text):
        if text not in self._notes:
            self._notes.append(text)

    def finish(self, *, fingerprint_after=None, completed=False, task_revision=None,
               evidence_id='', artifact_ref=''):
        """Return reviewed process evidence, or explicit gaps without evidence.

        ``completed`` means the runtime observed normal end of the review model
        workflow. It must be false on cancellation, failure or partial dispatch.
        The caller must still reject a changed task identity/revision on insertion.
        """
        with self._lock:
            reasons = list(self._selection_reasons)
            files = []
            any_observed = False
            for path, file in self._files.items():
                total = file['total_chars']
                ranges = file['ranges']
                missing = _missing(ranges, total) if file['observed'] else None
                complete = file['observed'] and not missing and not file['reasons']
                file_reasons = list(file['reasons'])
                if not file['observed']:
                    file_reasons.append('没有本轮实际消费的有效文件分页。')
                elif missing:
                    file_reasons.append('模型实际消费的字符区间仍有缺页。')
                files.append({'path': path, 'version': file['version'], 'total_chars': total,
                    'covered_chars': sum(end - start for start, end in ranges),
                    'missing_ranges': missing, 'status': 'complete' if complete else
                    'partial' if file['observed'] else 'unknown', 'reasons': file_reasons})
                any_observed = any_observed or file['observed']
                if not complete:
                    reasons.append(f'{path} 的当前静态审查输入覆盖不完整。')
                    reasons.extend(f'{path}: {reason}' for reason in file_reasons)
            if not _known(self._before) or not _known(fingerprint_after):
                reasons.append('前后独立输入指纹未知，无法确认审查时效。')
            elif self._before != fingerprint_after:
                reasons.append('审查前后项目输入已变化，不能声明当前审查完成。')
            if completed is not True:
                reasons.append('审查模型流程没有正常结束。')
            if type(task_revision) is not int or task_revision < 1:
                reasons.append('没有有效的绑定任务版本。')
            if not _known(evidence_id) or not isinstance(artifact_ref, str):
                reasons.append('没有有效的运行时证据身份或引用。')
            complete = not reasons
            paths = list(self._files)
            evidence = None
            if complete:
                evidence = {'id': evidence_id, 'kind': 'review', 'source': 'runtime',
                    'status': 'passed', 'task_revision': task_revision, 'paths': paths,
                    'input_fingerprint': fingerprint_after, 'coverage': 'complete',
                    'summary': '选定文件的静态审查输入及模型流程覆盖已完成；不证明代码无缺陷，未运行测试。',
                    'artifact_ref': artifact_ref}
            return {'status': 'reviewed' if complete else 'unverified',
                'coverage': 'complete' if complete else 'partial' if any_observed else 'unknown',
                'paths': paths, 'files': files, 'reasons': reasons, 'notes': list(self._notes),
                'evidence': evidence}
