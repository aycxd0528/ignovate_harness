"""Stable-order project and user context files injected at runtime creation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import tempfile
import itertools
import stat

from nailong.core.preferences import safe_config_path
from nailong.core.memory_knowledge import (
    KnowledgeMetadataError, SourceValidator, declaration, invalid_knowledge,
    parse_frontmatter, project_identity, project_relative_path, EPISTEMIC_KEYS, KNOWLEDGE_DETAIL_KEYS,
)

MEMORY_SCOPES = ('user', 'project', 'local')
MAX_MEMORY_BYTES = 160_000
MAX_MEMORY_TOPICS = 200
MAX_DIRECTORY_ENTRIES = 1_000
MAX_MEMORY_FILENAME_CHARS = 128


@dataclass(frozen=True)
class MemoryEntry:
    scope: str
    path: Path
    content: str


@dataclass(frozen=True)
class MemoryEdit:
    scope: str
    path: Path
    before_digest: str | None
    before_content: str
    content: str


class MemorySnapshot(list):
    """List-compatible loaded entries with explicit per-scope read diagnostics."""

    def __init__(self, entries, *, store, reports, budget_tokens):
        super().__init__(entries)
        self.store = store
        self.reports = reports
        self._original_entries=tuple(self)
        self._original_reports={scope:dict(report) for scope,report in reports.items()}
        self.budget_tokens = budget_tokens
        self.catalog = store.catalog()
        by_id={row['document']:row for row in self.catalog['documents']}
        for report in self.reports.values():
            current=by_id.get(report['document'])
            if current is None: continue
            if report.get('version')!=current.get('version'):
                report.update(validity='stale',knowledge_issues=['memory_version_changed'],fact_verified=False)
            else:
                report.update({key:current[key] for key in (*EPISTEMIC_KEYS,*KNOWLEDGE_DETAIL_KEYS) if key in current})
                if current['status']=='error': report.update(status='error',loaded_chars=0,error=current.get('error','memory_metadata_invalid'))
        from nailong.core.memory_context import render_snapshot
        self.rendered_context = render_snapshot(self)

    def refresh_metadata(self):
        """Keep pinned memory bodies/versions; refresh only source/applicability state.

        Callers must also replace their request's fixed context with the returned
        rendered_context before using its new reading allowance.
        """
        validator=SourceValidator(self.store)
        fresh=[]
        for row in self.catalog['documents']:
            current=self.store._document_info(row['document'],validator)
            if row.get('version')!=current.get('version'):
                current={**row,'validity':'stale','applicable':None,'fact_verified':False,
                         'knowledge_issues':['memory_version_changed']}
            fresh.append(current)
        self.catalog={**self.catalog,'documents':fresh}
        by_id={row['document']:row for row in fresh}
        self.reports={scope:dict(report) for scope,report in self._original_reports.items()}
        for report in self.reports.values():
            row=by_id.get(report['document'])
            if row is None: continue
            report.update({key:row[key] for key in (*EPISTEMIC_KEYS,*KNOWLEDGE_DETAIL_KEYS) if key in row})
            if row['status']=='error': report.update(status='error',loaded_chars=0,error=row.get('error','memory_metadata_invalid'))
        self[:]=self._original_entries
        from nailong.core.memory_context import render_snapshot
        self.rendered_context=render_snapshot(self)
        return self.rendered_context

    def report(self):
        from nailong.core.memory_context import estimate_memory_tokens
        return {'max_tokens': self.budget_tokens,
                'context_tokens': estimate_memory_tokens(self.rendered_context),
                'method': 'ASCII 字符 / 4 向上取整，非 ASCII 字符按 2 tokens 估算；不是提供商计量',
                'scopes': self.reports, 'documents': len(self.catalog['documents']),
                'scan_truncated': self.catalog['truncated'], 'diagnostics': self.catalog['diagnostics']}


class MemoryStore:
    def __init__(self,project_root,*,user_file=None,api_key='',source_authorizer=None,task_scope=None):
        self.project_root=Path(project_root).resolve()
        original=Path(user_file).absolute() if user_file else Path.home()/'.nailong/context.md'
        anchor=original.parent.parent
        self.user_boundary=anchor.resolve()
        self.user_file=self.user_boundary/original.relative_to(anchor)
        self.api_key=api_key
        if source_authorizer is not None and not callable(source_authorizer):
            raise ValueError('source_authorizer 必须是只读权限回调。')
        self.source_authorizer=source_authorizer
        if task_scope is not None:
            if not isinstance(task_scope,(list,tuple)) or len(task_scope)>128:
                raise ValueError('task_scope 必须为有界的项目相对路径列表。')
            task_scope=tuple('.' if item=='.' else project_relative_path(item,api_key=api_key) for item in task_scope)
        self.task_scope=task_scope

    @property
    def project_id(self):
        return project_identity(self.project_root)

    def source_path(self,relative):
        relative=project_relative_path(relative,api_key=self.api_key)
        return safe_config_path(self.project_root/relative,self.project_root)

    def authorize_source(self,relative):
        self.source_path(relative)
        if self.source_authorizer is not None:
            return self.source_authorizer(relative) is True
        # Default checks persisted Read rules. ASK does not trigger silent reads.
        # A caller can inject its session-aware ALLOW-only decision instead.
        from nailong.core.preferences import read_config
        config=read_config(self.project_root/'.nailong/settings.json',self.project_root)
        from nailong.core.permissions import Decision, PermissionEngine
        rules=config.get('permissions',{})
        if not isinstance(rules,dict) or set(rules)-{'allow','deny','ask'}:
            raise ValueError('来源读取权限配置无效。')
        for effect in ('allow','deny','ask'):
            values=rules.get(effect,[])
            if not isinstance(values,list) or any(not isinstance(value,str) for value in values):
                raise ValueError('来源读取权限配置无效。')
            for value in values: PermissionEngine.parse_rule(effect,value,'project')
        engine=PermissionEngine(self.project_root)
        return engine.decide_action('read_file',{'path':relative},profile='chat',mode='plan').decision==Decision.ALLOW

    def knowledge(self,full,scope,*,validator=None):
        try:
            metadata=parse_frontmatter(full)
            if 'description' in metadata and not isinstance(metadata['description'],str):
                raise KnowledgeMetadataError('invalid_description')
            data=declaration(metadata,scope,api_key=self.api_key)
            return (validator or SourceValidator(self)).evaluate(data)
        except KnowledgeMetadataError as error:
            return invalid_knowledge(scope,str(error))

    def path(self,scope):
        mapping={'user':self.user_file,'project':self.project_root/'.nailong/context.md',
                 'local':self.project_root/'.nailong/context.local.md'}
        if scope not in mapping: raise ValueError('记忆层只能为 user、project 或 local。')
        return safe_config_path(mapping[scope],self.user_boundary if scope=='user' else self.project_root)

    def read(self,scope):
        return self.read_version(scope)[0]

    def read_version(self, scope):
        path=self.path(scope)
        if not path.exists(): return '', None
        return self._read_path(path, lambda: self.path(scope))

    def read_section(self, scope, section='', offset=0, max_chars=6000):
        """Read an exact heading or full body from one fixed, redacted memory layer."""
        from nailong.core.memory_selection import memory_headings

        if not isinstance(scope, str) or scope not in MEMORY_SCOPES:
            raise ValueError('记忆层只能为 user、project 或 local。')
        if not isinstance(section, str):
            raise ValueError('section 必须是记忆中的完整标题或空字符串。')
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or max_chars < 1:
            raise ValueError('offset 必须为非负整数，max_chars 必须为正整数。')
        full, version = self.read_version(scope)
        knowledge=self.knowledge(full,scope)
        if knowledge['metadata_status']=='invalid':
            raise ValueError('记忆元数据无效：'+knowledge['knowledge_issues'][0])
        section = section.replace(self.api_key, '[密钥已隐藏]') if self.api_key else section
        if section:
            matches = [heading for heading in memory_headings(full) if heading.title == section]
            if len(matches) != 1:
                raise ValueError('记忆标题不存在或重复；请按完整正文分页读取。')
            full = full[matches[0].start:matches[0].end]
        end = min(len(full), offset + min(max_chars, 6000))
        path = str(self.path(scope))
        if self.api_key:
            path = path.replace(self.api_key, '[密钥已隐藏]')
        return {**knowledge,'ok': True, 'scope': scope, 'path': path, 'section': section,
                'version': version, 'offset': offset, 'next_offset': end if end < len(full) else None,
                'truncated': end < len(full), 'total_chars': len(full), 'content': full[offset:end]}

    def _read_path(self, path, revalidate):
        raw=self._read_bytes_path(path,revalidate,MAX_MEMORY_BYTES)
        text=raw.decode('utf-8')
        return (text.replace(self.api_key,'[密钥已隐藏]') if self.api_key else text,
                hashlib.sha256(raw).hexdigest())

    def _read_bytes_path(self,path,revalidate,max_bytes):
        # Nonblocking open avoids hanging on named pipes; only regular files are memory.
        # Pin every parent descriptor instead of trusting a pathname checked
        # earlier; concurrent ancestor symlink swaps must not redirect reads.
        if os.open in os.supports_dir_fd:
            directory=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY)
            try:
                for part in path.parts[1:-1]:
                    next_directory=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                    os.close(directory); directory=next_directory
                descriptor=os.open(path.name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=directory)
            finally: os.close(directory)
        else:
            before=path.stat()
            descriptor=os.open(path,os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0))
            actual=os.fstat(descriptor)
            if (before.st_dev,before.st_ino)!=(actual.st_dev,actual.st_ino):
                os.close(descriptor)
                raise ValueError('记忆文件读取时发生路径冲突。')
        with os.fdopen(descriptor,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('记忆文档必须是普通文件。')
            revalidate()
            if before.st_size>max_bytes:
                raise ValueError('记忆或来源文件超过大小限制。')
            raw = stream.read(max_bytes + 1)
            after=os.fstat(stream.fileno())
            signature=lambda item:(item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns,item.st_ctime_ns)
            if signature(before)!=signature(after):
                raise ValueError('记忆或来源文件读取期间发生变化。')
        revalidate()
        current=path.stat(follow_symlinks=False)
        if signature(after)!=signature(current):
            raise ValueError('记忆或来源文件版本已改变。')
        if len(raw)>max_bytes: raise ValueError('记忆或来源文件超过大小限制。')
        return raw

    def topic_directory(self, scope):
        if scope not in MEMORY_SCOPES:
            raise ValueError('记忆层只能为 user、project 或 local。')
        directory = (self.user_file.parent / 'memory' if scope == 'user' else
                     self.project_root / '.nailong' / ('memory.local' if scope == 'local' else 'memory'))
        return safe_config_path(directory, self.user_boundary if scope == 'user' else self.project_root)

    def document_path(self, document):
        if not isinstance(document, str) or document.count('/') != 1:
            raise ValueError('记忆 ID 必须为 user|project|local/文件名.md。')
        scope, filename = document.split('/')
        if (scope not in MEMORY_SCOPES or not filename.endswith('.md') or filename.startswith('.')
                or '\\' in filename or len(filename) > MAX_MEMORY_FILENAME_CHARS or not all(c.isprintable() for c in filename)
                or (self.api_key and self.api_key in filename)):
            raise ValueError('记忆 ID 无效。')
        if filename == 'context.md':
            return self.path(scope)
        return safe_config_path(self.topic_directory(scope) / filename,
                                self.user_boundary if scope == 'user' else self.project_root)

    def read_document(self, document, offset=0, limit=4000):
        """Read bounded character pages; IDs never accept arbitrary filesystem paths."""
        safe_document=document.replace(self.api_key,'[密钥已隐藏]') if isinstance(document,str) and self.api_key else document
        try:
            if (type(offset) is not int or offset < 0 or type(limit) is not int
                    or not 1 <= limit <= 16000):
                raise ValueError('offset 必须为非负整数，limit 范围为 1–16000。')
            path = self.document_path(document)
            full, version = self._read_path(path, lambda: self.document_path(document))
            knowledge=self.knowledge(full,document.split('/')[0])
            if knowledge['metadata_status']=='invalid':
                return {**knowledge,'ok':False,'document':safe_document,'status':'error','version':version,
                        'error':'memory_metadata_invalid'}
            end = min(len(full), offset + limit)
            return {**knowledge,'ok': True, 'document': safe_document, 'content': full[offset:end],
                    'offset': offset, 'total_chars': len(full), 'version': version,
                    'truncated': end < len(full), 'next_offset': end if end < len(full) else None}
        except FileNotFoundError:
            return {'ok': False, 'document': safe_document, 'status': 'missing', 'error': '记忆文档不存在。'}
        except (OSError, UnicodeError, RuntimeError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) and not isinstance(error, UnicodeError) else type(error).__name__
            if self.api_key: reason = reason.replace(self.api_key, '[密钥已隐藏]')
            return {'ok': False, 'document': safe_document, 'status': 'error', 'error': reason}

    def _document_info(self, document, validator=None):
        label=document.replace(self.api_key,'[密钥已隐藏]') if isinstance(document,str) and self.api_key else document
        parts=document.split('/',1) if isinstance(document,str) else []
        scope=parts[0] if parts and parts[0] in MEMORY_SCOPES else 'project'
        info = {'document': label, 'scope': scope,
                'description': label.split('/',1)[1][:-3] if isinstance(label,str) and '/' in label else 'invalid', 'size_bytes': 0,
                'version': None, 'status': 'available'}
        try:
            path = self.document_path(document)
            full, version = self._read_path(path, lambda: self.document_path(document))
            info.update(size_bytes=path.stat().st_size, version=version,
                        status='available' if full.strip() else 'empty')
            knowledge=self.knowledge(full,info['scope'],validator=validator)
            info.update(knowledge)
            if knowledge['metadata_status']=='invalid':
                info.update(status='error',error='memory_metadata_invalid')
                return info
            if document.endswith('/context.md'):
                from nailong.core.memory_selection import memory_headings
                headings = memory_headings(full)
                info['sections'] = [heading.title[:128] for heading in headings[:10]]
                info['sections_truncated'] = len(headings) > 10 or any(len(heading.title)>128 for heading in headings[:10])
            metadata=parse_frontmatter(full)
            if 'description' in metadata:
                info['description'] = ' '.join(metadata['description'].split())[:256]
        except (OSError, UnicodeError, RuntimeError, ValueError) as error:
            info.update(invalid_knowledge(info['scope'],'document_unreadable_or_unsafe'))
            info.update(status='error', error=type(error).__name__)
        return info

    def catalog(self):
        """Discover metadata only, reporting unsafe/unreadable entries instead of hiding them."""
        documents, diagnostics = [], []
        validator=SourceValidator(self)
        truncated, topic_count = False, 0
        for scope in MEMORY_SCOPES:
            document = f'{scope}/context.md'
            try:
                path = self.path(scope)
                if path.exists(): documents.append(self._document_info(document,validator))
            except (OSError, ValueError, RuntimeError) as error:
                documents.append({**invalid_knowledge(scope,'document_unreadable_or_unsafe'),
                                  'document': document, 'scope': scope, 'status': 'error',
                                  'description': 'context', 'version': None, 'size_bytes': 0,
                                  'error': type(error).__name__})
            try:
                directory = self.topic_directory(scope)
                if not directory.exists(): continue
                children = list(itertools.islice(directory.iterdir(), MAX_DIRECTORY_ENTRIES + 1))
                if len(children) > MAX_DIRECTORY_ENTRIES:
                    truncated = True
                    diagnostics.append({'scope': scope, 'error': '目录项超过扫描上限。'})
                for child in sorted(children[:MAX_DIRECTORY_ENTRIES], key=lambda item: item.name):
                    if child.suffix != '.md' or child.name.startswith('.'):
                        continue
                    if child.name == 'context.md':
                        diagnostics.append({'scope': scope, 'error': '主题文件名 context.md 为旧记忆保留。'})
                        continue
                    if (self.api_key and self.api_key in child.name) or not all(c.isprintable() for c in child.name):
                        diagnostics.append({'scope':scope,'error':'unsafe_or_secret_document_id'})
                        continue
                    if topic_count >= MAX_MEMORY_TOPICS:
                        truncated = True
                        continue
                    documents.append(self._document_info(f'{scope}/{child.name}',validator))
                    topic_count += 1
            except (OSError, ValueError, RuntimeError) as error:
                diagnostics.append({'scope': scope, 'error': type(error).__name__})
        return {'ok': True, 'documents': documents, 'truncated': truncated, 'diagnostics': diagnostics}

    def entries(self):
        return [{'scope':scope,'path':str(self.path(scope)),'content':self.read(scope)}
                for scope in ('user','project','local')]

    def stage(self,scope,content):
        if not isinstance(content,str) or len(content.encode('utf-8'))>160_000:
            raise ValueError('记忆内容无效或超过大小限制。')
        path=self.path(scope)
        raw=path.read_bytes() if path.exists() else None
        digest=hashlib.sha256(raw).hexdigest() if raw is not None else None
        return MemoryEdit(scope,path,digest,self.read(scope),content)

    def commit(self,edit):
        path=self.path(edit.scope)
        if path!=edit.path: raise ValueError('记忆编辑路径发生冲突。')
        raw=path.read_bytes() if path.exists() else None
        digest=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if digest!=edit.before_digest: raise ValueError('记忆文件被其他进程修改，编辑发生冲突。')
        content=edit.content.replace(self.api_key,'[密钥已隐藏]') if self.api_key else edit.content
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        descriptor,temporary=tempfile.mkstemp(dir=path.parent,prefix='.memory-')
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream: stream.write(content)
            self.path(edit.scope)
            if (hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None)!=edit.before_digest:
                raise ValueError('记忆文件保存前发生冲突。')
            os.replace(temporary,path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)


def load_project_memory(
    project_root: str | Path,
    *,
    user_file: str | Path | None = None,
    api_key: str = "",
    max_chars: int = 40_000,
    source_authorizer=None,
    task_scope=None,
) -> MemorySnapshot:
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise ValueError('记忆字符上限必须为正整数。')
    store = MemoryStore(project_root, user_file=user_file, api_key=api_key,
                        source_authorizer=source_authorizer,task_scope=task_scope)
    from nailong.core.memory_context import memory_budget
    budget_tokens = memory_budget(store.project_root)
    entries=[]
    reports = {}
    validator=SourceValidator(store)
    for scope in ('user','project','local'):
        report = {'document': f'{scope}/context.md', 'version': None,
                  'total_chars': 0, 'loaded_chars': 0, 'status': 'missing'}
        try:
            full, version = store.read_version(scope)
            knowledge=store.knowledge(full,scope,validator=validator)
            report.update(knowledge)
            if knowledge['metadata_status']=='invalid':
                report.update(version=version,total_chars=len(full),status='error',error='memory_metadata_invalid')
                reports[scope]=report
                continue
            content=full[:max_chars].strip()
            report.update(version=version, total_chars=len(full), loaded_chars=len(content),
                          status='missing' if version is None else 'empty' if not full.strip()
                          else 'truncated' if len(full)>max_chars else 'loaded')
            if content: entries.append(MemoryEntry(scope,store.path(scope),content))
        except (OSError, UnicodeError, RuntimeError, ValueError) as error:
            report.update(status='error', error=type(error).__name__)
        reports[scope] = report
    return MemorySnapshot(entries, store=store, reports=reports, budget_tokens=budget_tokens)
