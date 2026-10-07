"""Bounded memory declarations and current, read-authorized source fingerprints.

``observed`` describes matching file hashes, never semantic or test verification.
No sources are fetched from the network or written by this module.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

MAX_SOURCES = 8
MAX_APPLICABILITY_PATHS = 16
MAX_REFERENCE_CHARS = 180
MAX_SOURCE_PATH_CHARS = 512
MAX_SOURCE_BYTES = 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = MAX_SOURCES * (MAX_SOURCE_BYTES + 1)
MAX_SOURCE_CHECKS = 64
KNOWLEDGE_TYPES = {'preference', 'project_fact', 'experience'}
EPISTEMIC_KEYS = ('metadata_status','knowledge_type','knowledge_state','validity','applicable','fact_verified')
KNOWLEDGE_DETAIL_KEYS = ('origin','confirmed_by','confirmation_is_declaration','applicability',
                         'sources','knowledge_issues','evidence_basis')
_PROTECTED = {'.git', '.venv', '__pycache__', '.ssh'}
_REFERENCE = re.compile(r'(?:session:[A-Za-z0-9_-]{1,80}#event:[A-Za-z0-9_-]{1,80}|user:[A-Za-z0-9_-]{1,80})\Z')
_DIGEST = re.compile(r'[a-fA-F0-9]{64}\Z')


class KnowledgeMetadataError(ValueError):
    """Only a safe error code, never raw metadata or a credential, is exposed."""


def project_identity(project_root) -> str:
    return hashlib.sha256(str(project_root.resolve()).encode('utf-8')).hexdigest()


def project_relative_path(value: str, *, api_key: str = '') -> str:
    if (not isinstance(value, str) or not value or len(value) > MAX_SOURCE_PATH_CHARS
            or value != value.strip() or '\\' in value or not all(c.isprintable() for c in value)
            or '[密钥已隐藏]' in value or (api_key and api_key in value)):
        raise KnowledgeMetadataError('invalid_source_path')
    path = PurePosixPath(value)
    if path.is_absolute() or ':' in value or any(part in {'', '.', '..'} for part in value.split('/')):
        raise KnowledgeMetadataError('invalid_source_path')
    for component in path.parts:
        name = component.casefold()
        if (name in _PROTECTED or name == '.env' or name.startswith('.env.')
                or name in {'id_rsa', 'id_ed25519', 'credentials.json', 'secrets.json'}
                or name.endswith(('.pem', '.key'))):
            raise KnowledgeMetadataError('protected_source_path')
    return path.as_posix()


def parse_frontmatter(full: str) -> dict:
    normalized = full.replace('\r\n', '\n')
    if not normalized.startswith('---\n'):
        return {}
    end = normalized.find('\n---\n', 4)
    if end < 0 and normalized.endswith('\n---'):
        end = len(normalized) - 4
    if end < 0 or len(normalized[:end].encode('utf-8')) > 8192:
        raise KnowledgeMetadataError('invalid_frontmatter_boundary')
    try:
        import yaml
        class UniqueKeyLoader(yaml.SafeLoader):
            pass

        def unique_mapping(loader,node,deep=False):
            loader.flatten_mapping(node)
            mapping={}
            for key_node,value_node in node.value:
                key=loader.construct_object(key_node,deep=deep)
                try:
                    if key in mapping: raise KnowledgeMetadataError('duplicate_frontmatter_key')
                    mapping[key]=loader.construct_object(value_node,deep=deep)
                except TypeError as error:
                    raise KnowledgeMetadataError('invalid_frontmatter_key') from error
            return mapping

        UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,unique_mapping)
        metadata = yaml.load(normalized[4:end],Loader=UniqueKeyLoader)
    except ImportError as error:
        raise KnowledgeMetadataError('yaml_dependency_unavailable') from error
    except yaml.YAMLError as error:
        raise KnowledgeMetadataError('invalid_frontmatter_yaml') from error
    if not isinstance(metadata, dict):
        raise KnowledgeMetadataError('frontmatter_not_mapping')
    return metadata


def _mapping(value, allowed: set[str], code: str) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) or key not in allowed for key in value):
        raise KnowledgeMetadataError(code)
    return value


def _digest(value, code='invalid_source_digest') -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise KnowledgeMetadataError(code)
    return value.lower()


def declaration(metadata: dict, scope: str, *, api_key='') -> dict:
    """Validate the complete declaration before any dependency read is allowed."""
    legacy = {'metadata_status': 'absent', 'knowledge_type': None,
              'knowledge_state': 'legacy', 'validity': 'unverified', 'applicable': True,
              'applicability': {'scope': scope, 'paths': [], 'project_id': None},
              'sources': [], 'knowledge_issues': ['source_not_declared'],
              'fact_verified': False, 'evidence_basis': 'none'}
    if 'knowledge' not in metadata:
        return legacy
    item = _mapping(metadata['knowledge'], {'type', 'state', 'origin', 'confirmed_by', 'applicability', 'sources'},
                    'invalid_knowledge_mapping')
    kind = item.get('type')
    state = item.get('state', 'candidate')
    origin = item.get('origin', 'unknown')
    # Avoid hashing or set-membership of malformed YAML list/dict values.
    if not isinstance(kind, str) or kind not in KNOWLEDGE_TYPES:
        raise KnowledgeMetadataError('invalid_knowledge_type')
    if not isinstance(state, str) or state not in {'candidate', 'confirmed'}:
        raise KnowledgeMetadataError('invalid_knowledge_state')
    if not isinstance(origin, str) or origin not in {'user', 'runtime', 'model', 'unknown'}:
        raise KnowledgeMetadataError('invalid_knowledge_origin')
    confirmed_by = item.get('confirmed_by')
    if (state == 'confirmed' and confirmed_by != 'user') or (state != 'confirmed' and confirmed_by is not None):
        raise KnowledgeMetadataError('invalid_confirmation_declaration')
    applicability = _mapping(item.get('applicability', {}), {'scope', 'paths', 'project_id'},
                             'invalid_applicability')
    declared_scope = applicability.get('scope', scope)
    if not isinstance(declared_scope, str) or declared_scope != scope:
        raise KnowledgeMetadataError('applicability_scope_mismatch')
    paths = applicability.get('paths', [])
    if not isinstance(paths, list) or len(paths) > MAX_APPLICABILITY_PATHS:
        raise KnowledgeMetadataError('invalid_applicability_paths')
    paths = [project_relative_path(path, api_key=api_key) for path in paths]
    if len(set(paths)) != len(paths):
        raise KnowledgeMetadataError('duplicate_applicability_path')
    identity = applicability.get('project_id')
    if identity is not None:
        identity = _digest(identity, 'invalid_applicability_project_id')
    sources = item.get('sources', [])
    if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
        raise KnowledgeMetadataError('invalid_sources')
    clean_sources, seen = [], set()
    for source in sources:
        source = _mapping(source, {'kind', 'path', 'sha256', 'ref'}, 'invalid_source_mapping')
        source_kind = source.get('kind')
        if source_kind == 'file' and set(source) == {'kind', 'path', 'sha256'}:
            clean = {'kind': 'file', 'path': project_relative_path(source['path'], api_key=api_key),
                     'sha256': _digest(source['sha256'])}
            unique = ('file', clean['path'])
        elif source_kind == 'reference' and set(source) == {'kind', 'ref'}:
            ref = source['ref']
            if (not isinstance(ref, str) or len(ref) > MAX_REFERENCE_CHARS or not _REFERENCE.fullmatch(ref)
                    or (api_key and api_key in ref)):
                raise KnowledgeMetadataError('invalid_source_reference')
            clean = {'kind': 'reference', 'ref': ref}
            unique = ('reference', ref)
        else:
            raise KnowledgeMetadataError('invalid_source_kind')
        if unique in seen:
            raise KnowledgeMetadataError('duplicate_source')
        seen.add(unique); clean_sources.append(clean)
    if scope=='user' and identity is None and any(source['kind']=='file' for source in clean_sources):
        raise KnowledgeMetadataError('user_file_sources_require_project_id')
    return {**legacy, 'metadata_status': 'valid', 'knowledge_type': kind, 'knowledge_state': state,
            'origin': origin, 'confirmed_by': confirmed_by, 'confirmation_is_declaration': state == 'confirmed',
            'applicability': {'scope': scope, 'paths': paths, 'project_id': identity},
            'sources': clean_sources, 'knowledge_issues': [], 'evidence_basis': 'file_sha256'}


def invalid_knowledge(scope: str, error: str) -> dict:
    return {'metadata_status': 'invalid', 'knowledge_type': None, 'knowledge_state': 'candidate',
            'validity': 'unverified', 'applicable': None,
            'applicability': {'scope': scope, 'paths': [], 'project_id': None},
            'sources': [], 'knowledge_issues': [error], 'fact_verified': False, 'evidence_basis': 'none'}


class SourceValidator:
    """Per-operation hash cache and total I/O bound; never reuse last-round validity."""
    def __init__(self, store):
        self.store = store
        self.cache = {}
        self.remaining_bytes = MAX_TOTAL_SOURCE_BYTES

    def _source(self, source: dict) -> dict:
        if source['kind'] == 'reference':
            return {**source, 'status': 'unverified', 'reason': 'reference_not_resolved'}
        path = source['path']
        if path not in self.cache:
            try:
                target = self.store.source_path(path)
                if len(self.cache)>=MAX_SOURCE_CHECKS:
                    result = {'status': 'unverified', 'reason': 'source_validation_limit'}
                elif self.remaining_bytes <= 0:
                    result = {'status': 'unverified', 'reason': 'source_budget_exhausted'}
                elif self.store.authorize_source(path) is not True:
                    result = {'status': 'unverified', 'reason': 'source_permission_not_allowed'}
                else:
                    # The reader returns only bytes to this trusted hashing layer.
                    # They never enter source records, prompts or tool output.
                    # Reserve the maximum read including its over-limit probe.
                    # Refund unused bytes only after a successful read: failed
                    # reads may already have consumed I/O before a race check.
                    limit = min(MAX_SOURCE_BYTES, self.remaining_bytes - 1)
                    reservation = limit + 1
                    self.remaining_bytes -= reservation
                    raw = self.store._read_bytes_path(target, lambda: self.store.source_path(path), limit)
                    self.remaining_bytes += reservation - (len(raw) + 1)
                    result = {'status': 'observed', 'current_sha256': hashlib.sha256(raw).hexdigest()}
            except FileNotFoundError:
                result = {'status': 'stale', 'reason': 'source_missing'}
            except Exception:
                # A broken injected policy or unreadable dependency must fail
                # closed. Never return exception text containing paths/secrets.
                result = {'status': 'unverified', 'reason': 'source_unreadable_or_unsafe'}
            self.cache[path] = result
        result = dict(self.cache[path])
        if result['status'] == 'observed' and result['current_sha256'] != source['sha256']:
            result.update(status='stale', reason='source_digest_changed')
        return {**source, **result}

    def evaluate(self, data: dict) -> dict:
        if data['metadata_status'] != 'valid':
            return data
        result = dict(data, sources=[])
        applicability = data['applicability']
        identity = applicability['project_id']
        if identity is not None and identity != project_identity(self.store.project_root):
            return {**result, 'applicable': False, 'knowledge_issues': ['different_project'],
                    'sources': [{**source, 'status': 'unverified', 'reason': 'not_applicable'} for source in data['sources']]}
        paths = applicability['paths']
        if paths and self.store.task_scope is None:
            result.update(applicable=None, knowledge_issues=['task_scope_unknown'])
        elif paths:
            allowed = [PurePosixPath(path) for path in paths]
            actual = [PurePosixPath(path) for path in self.store.task_scope]
            result['applicable'] = any(a == b or a in b.parents or b in a.parents for a in allowed for b in actual)
            if not result['applicable']:
                return {**result, 'knowledge_issues': ['outside_task_scope'],
                        'sources': [{**source, 'status': 'unverified', 'reason': 'not_applicable'} for source in data['sources']]}
        result['sources'] = [self._source(source) for source in data['sources']]
        files = [source for source in result['sources'] if source['kind'] == 'file']
        if any(source['status'] == 'stale' for source in files):
            result['validity'] = 'stale'
        elif files and all(source['status'] == 'observed' for source in files) and result['applicable'] is True:
            result['validity'] = 'observed'
        else:
            result['validity'] = 'unverified'
        if not files:
            result['knowledge_issues'] = [*result['knowledge_issues'], 'file_source_not_declared']
        if any(source['status'] == 'unverified' for source in files):
            result['knowledge_issues'] = [*result['knowledge_issues'], 'source_validation_incomplete']
        return result
