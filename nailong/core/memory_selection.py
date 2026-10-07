"""Bounded core memory and heading indexes for the three fixed memory files."""

from __future__ import annotations

from dataclasses import dataclass
import re

from nailong.core.memory import MemoryEntry
from nailong.core.memory_knowledge import SourceValidator


SCOPES = ('user', 'project', 'local')


class CoreMemorySnapshot(list):
    """List-compatible fixed cores with diagnostics, independent of topic budgets."""

    def __init__(self, entries, *, store, reports):
        super().__init__(entries)
        self.store = store
        self.reports = reports


@dataclass(frozen=True)
class Heading:
    title: str
    level: int
    start: int
    end: int


def memory_headings(text: str) -> list[Heading]:
    """Find Markdown ATX sections, including children and excluding fenced code."""
    found = []
    fence = None
    position = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
        else:
            heading = re.match(r'^ {0,3}(#{1,6})[ \t]+(.+?)\s*$', line.rstrip('\r\n'))
            if heading:
                title = re.sub(r'[ \t]+#+[ \t]*$', '', heading[2]).strip()
                if title:
                    found.append((title, len(heading[1]), position))
        position += len(line)
    sections = []
    for index, (title, level, start) in enumerate(found):
        end = next((found[next_index][2] for next_index in range(index + 1, len(found))
                    if found[next_index][1] <= level), len(text))
        sections.append(Heading(title, level, start, end))
    return sections


def _positive_limit(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError('记忆字符上限必须为正整数。')


def _core(text: str, scope: str, max_chars: int) -> tuple[str, str, bool]:
    if not text.strip() or len(text) <= max_chars:
        return text.strip(), '', False
    headings = memory_headings(text)
    core = next((item for item in headings if item.title.casefold() in {'核心约定', 'core'}), None)
    selected = text[core.start:core.end] if core else text
    notice = f'\n\n[记忆核心已截断；正文标题及版本见索引，使用 read_memory(scope="{scope}") 分页读取。]'
    if max_chars <= len(notice):
        return selected[:max_chars].strip(), core.title if core else '', True
    remaining = max(0, max_chars - len(notice))
    content = selected[:remaining].strip() + notice
    return content[:max_chars], core.title if core else '', True


def select_core_memory(store, max_chars: int = 2048) -> CoreMemorySnapshot:
    """Select independent bounded cores without changing legacy loading APIs."""
    _positive_limit(max_chars)
    entries, reports = [], {}
    validator=SourceValidator(store)
    for scope in SCOPES:
        report = {'document': f'{scope}/context.md', 'scope': scope, 'version': None,
                  'total_chars': 0, 'loaded_chars': 0, 'status': 'missing', 'section': ''}
        try:
            full, version = store.read_version(scope)
            knowledge=store.knowledge(full,scope,validator=validator)
            report.update(knowledge)
            if knowledge['metadata_status']=='invalid':
                report.update(version=version,total_chars=len(full),status='error',error='memory_metadata_invalid')
                reports[scope]=report
                continue
            content, section, truncated = _core(full, scope, max_chars)
            report.update(version=version, total_chars=len(full), loaded_chars=len(content),
                          section=section, truncated=truncated,
                          status='missing' if version is None else 'empty' if not full.strip()
                          else 'truncated' if truncated else 'loaded')
            if content:
                entries.append(MemoryEntry(scope, store.path(scope), content))
        except (OSError, UnicodeError, RuntimeError, ValueError) as error:
            report.update(status='error', error=type(error).__name__)
        reports[scope] = report
    return CoreMemorySnapshot(entries, store=store, reports=reports)


def memory_index(store, max_core_chars: int = 2048) -> str:
    """List fixed scopes, headings and current versions, never their bodies."""
    _positive_limit(max_core_chars)
    lines = ['记忆标题与版本索引（使用 read_memory 按 scope/section 分页读取正文）：']
    for scope in SCOPES:
        try:
            path = str(store.path(scope))
            full, version = store.read_version(scope)
            knowledge=store.knowledge(full,scope)
            status = 'missing' if version is None else 'empty' if not full.strip() else 'available'
            lines.append(f'[{scope}] path={path} version={version or "missing"} status={status} '
                         f'total_chars={len(full)} core_limit={max_core_chars}')
            lines.append(f"  knowledge={knowledge['knowledge_state']} validity={knowledge['validity']} applicable={knowledge['applicable']}; 来源摘要匹配不等于事实已验证。")
            if knowledge['metadata_status']=='invalid':
                lines.append('  [元数据无效：'+','.join(knowledge['knowledge_issues'])+'；不加载正文。]')
                continue
            used, titles = 0, set()
            for heading in memory_headings(full):
                if heading.title in titles:
                    continue
                titles.add(heading.title)
                line = f'  section={heading.title}'
                if used + len(line) + 1 > max_core_chars:
                    lines.append('  [标题索引已截断；可用 section="" 分页读取全文。]')
                    break
                lines.append(line)
                used += len(line) + 1
        except (OSError, UnicodeError, RuntimeError, ValueError) as error:
            lines.append(f'[{scope}] status=error version=unknown error={type(error).__name__}')
    result = '\n'.join(lines)
    return result.replace(store.api_key, '[密钥已隐藏]') if store.api_key else result
