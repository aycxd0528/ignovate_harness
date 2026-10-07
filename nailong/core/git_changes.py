"""Bounded Git differences without external diff, textconv or shell parsing."""

from __future__ import annotations

import difflib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nailong.tools.files import FileSession

MAX_PATCH_BYTES = 256 * 1024
MAX_CHANGE_FILES = 200


@dataclass(frozen=True)
class Change:
    path: str
    status: str
    patch: str
    old_path: str | None = None


@dataclass(frozen=True)
class ChangeSet:
    kind: str
    base: str
    changes: tuple[Change, ...]
    skipped: tuple[dict, ...] = ()
    truncated: bool = False

    @property
    def patch(self):
        return ''.join(change.patch for change in self.changes)

    @property
    def paths(self):
        return frozenset(path for change in self.changes for path in (change.path, change.old_path) if path)

    def batches(self):
        return tuple(self.changes[index:index+20] for index in range(0, len(self.changes), 20))

    def summary(self):
        lines = [f'改动范围：{self.kind}；基准：{self.base}；{len(self.changes)} 个文件。']
        lines.extend(f'{change.status}  {change.old_path+" → " if change.old_path else ""}{change.path}' for change in self.changes)
        lines.extend(f'未覆盖：{item["path"]}（{item["reason"]}）' for item in self.skipped)
        if self.truncated:
            lines.append('差异或文件列表已截断；未覆盖部分不能视为已审查。')
        return '\n'.join(lines)


class GitChanges:
    def __init__(self, project_root, api_key=''):
        self.root = Path(project_root).resolve()
        self.files = FileSession(self.root)
        self.api_key = api_key

    def _git(self, *args, limit=MAX_PATCH_BYTES):
        command = ['git', '--no-pager', '--literal-pathspecs', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                   '-c', 'core.pager=cat', '-c', 'diff.external=', *args]
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                result = subprocess.run(command, cwd=self.root, stdout=out, stderr=err, timeout=5)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ValueError('Git 不可用或读取差异超时。') from error
            if result.returncode:
                raise ValueError('Git 无法读取此项目或指定分支；请检查仓库和分支名称。')
            out.seek(0)
            raw = out.read(limit+1)
        return raw[:limit], len(raw) > limit

    def _safe(self, path):
        if not path or Path(path).is_absolute() or '..' in Path(path).parts:
            raise ValueError('路径超出项目范围。')
        target = self.files.resolve(path, allow_missing=True)
        if (self.root/path).is_symlink():
            raise ValueError('符号链接正文不进入改动审查。')
        return target

    def select(self, kind='working', ref=None):
        if kind not in {'working', 'staged', 'branch'}:
            raise ValueError('差异范围无效。')
        self._git('rev-parse', '--is-inside-work-tree')
        try:
            head = self._git('rev-parse', '--verify', 'HEAD')[0].decode().strip()
        except ValueError:
            head = None
        if kind == 'branch':
            if not ref or ref.startswith('-') or len(ref) > 1024 or not head:
                raise ValueError('分支名称无效，或仓库尚无提交。')
            commit = self._git('rev-parse', '--verify', '--end-of-options', ref+'^{commit}')[0].decode().strip()
            base = self._git('merge-base', head, commit)[0].decode().strip()
            revisions = [base, head]
        elif kind == 'staged':
            base = head or '空仓库'
            revisions = ['--cached'] + ([head] if head else [])
        else:
            base = head or '空仓库'
            revisions = [head] if head else ['--cached']
        flags = ['--relative', '--no-ext-diff', '--no-textconv', '--no-color', '--find-renames']
        raw, names_cut = self._git('diff', *flags, '--name-status', '-z', *revisions, '--', '.', limit=1024*1024)
        parts = raw.split(b'\0')
        candidates = []
        index = 0
        while index+1 < len(parts) and parts[index]:
            status = parts[index].decode('ascii', errors='replace')
            count = 2 if status.startswith(('R', 'C')) else 1
            if index+count >= len(parts):
                names_cut = True
                break
            names = [item.decode('utf-8', errors='surrogateescape') for item in parts[index+1:index+count+1]]
            candidates.append((status, names[-1], names[0] if count == 2 else None))
            index += count+1
        if kind == 'working':
            raw, more_cut = self._git('ls-files', '--others', '--exclude-standard', '-z', '--', '.', limit=1024*1024)
            names_cut |= more_cut
            candidates.extend(('?', name.decode('utf-8', errors='surrogateescape'), None)
                              for name in raw.split(b'\0')[:-1] if name)
        changes, skipped = [], []
        truncated, used = names_cut, 0
        for status, path, old in candidates:
            try:
                target = self._safe(path)
                if old:
                    self._safe(old)
                path.encode('utf-8')
                if len(changes) >= MAX_CHANGE_FILES:
                    skipped.append({'path': path, 'reason': '超过 200 个文件上限。'})
                    truncated = True
                    continue
                if status == '?' or (kind=='working' and head is None):
                    if not target.exists(): continue
                    with target.open('rb') as source:
                        data = source.read(12_001)
                    if b'\0' in data:
                        raise ValueError('二进制文件。')
                    cut = len(data) > 12_000
                    content = data[:12_000].decode('utf-8')
                    patch = ''.join(difflib.unified_diff([], content.splitlines(keepends=True), fromfile='/dev/null', tofile='b/'+path))
                    if cut:
                        skipped.append({'path': path, 'reason': '新增文件正文超过单文件读取上限。'})
                else:
                    raw, cut = self._git('diff', *flags, *revisions, '--', *tuple(p for p in (old, path) if p))
                    patch = raw.decode('utf-8')
                    if any(line.startswith(('Binary files ', 'GIT binary patch')) for line in patch.splitlines()):
                        raise ValueError('二进制差异。')
                if self.api_key:
                    patch = patch.replace(self.api_key, '[密钥已隐藏]')
                if used+len(patch.encode('utf-8')) > MAX_PATCH_BYTES:
                    skipped.append({'path': path, 'reason': '超过 256 KiB 差异上限。'})
                    truncated = True
                    continue
                if cut:
                    truncated = True
                    if status != '?':
                        skipped.append({'path': path, 'reason': '单文件差异已截断。'})
                changes.append(Change(path, status, patch, old))
                used += len(patch.encode('utf-8'))
            except (OSError, ValueError, UnicodeError) as error:
                skipped.append({'path': path, 'reason': str(error)[:300]})
        return ChangeSet(kind, base, tuple(changes), tuple(skipped), truncated)
