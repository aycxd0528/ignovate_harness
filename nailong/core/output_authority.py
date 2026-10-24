"""Active output ownership; complete generation is distinct from verification.

Immutable bounded membership snapshots and a small atomic head/intent keep
publication recoverable. Admissions grant no rights. Only an owned, completed
process followed by a complete artifact observation may seal a generation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re

from nailong.core.file_locks import file_lock
from nailong.core.input_snapshot import _Deadline
from nailong.core.safe_files import atomic_write_bytes, open_regular_file
from nailong.core.verification_artifacts import _path, _json, artifact_index, load_manifest, validate_reference

CHUNK_BYTES = 1024 * 1024
MAX_BYTES = 64 * CHUNK_BYTES
METADATA_BYTES = 256 * 1024
_HEX = re.compile(r'[a-f0-9]{64}\Z')


def covered(path, scopes):
    return any(item in scopes for item in (path, *path.parents) if item.parts)


def remove_scope(index, scope):
    key = os.fsencode(scope)
    prefix = key + os.fsencode(os.sep)
    end = prefix[:-1] + bytes((prefix[-1] + 1,))
    clause='path=? OR (path>=? AND path<?)'
    parameters=(key,prefix,end)
    released=index.connection.execute('SELECT COALESCE(SUM(length(path)+32),0) FROM paths WHERE '+clause,parameters).fetchone()[0]
    index.connection.execute('DELETE FROM paths WHERE '+clause,parameters)
    index.bytes-=released


def remove_path(index, path):
    key=os.fsencode(path)
    cursor=index.connection.execute('DELETE FROM paths WHERE path=?',(key,))
    index.bytes-=(len(key)+32)*cursor.rowcount


class OutputAuthority:
    def __init__(self, boundary, project):
        self.boundary = Path(boundary)
        self.project = str(Path(project).resolve())
        self.directory = self.boundary/'output-authority-v1'
        self.required = self.boundary/'output-authority-required.json'

    @contextmanager
    def _locked(self):
        try:
            with file_lock(self.boundary/'output-authority.lock',blocking=False):
                yield
        except (TypeError,KeyError,IndexError,AttributeError,UnicodeError,RecursionError,json.JSONDecodeError) as error:
            raise ValueError('输出归属账本结构损坏，未重置或扩大权限。') from error

    @staticmethod
    def _bytes(path, limit):
        with open_regular_file(path) as source:
            data = source.read(limit+1)
        if len(data)>limit:
            raise ValueError('完整输出归属记录超出容量。')
        return data

    def _put(self, data, suffix='.json'):
        identity = hashlib.sha256(data).hexdigest()
        self._named(identity+suffix,data)
        return identity

    def _named(self, name, data):
        limit=CHUNK_BYTES if name.endswith('.part') else METADATA_BYTES
        if len(data)>limit:
            raise ValueError('完整输出归属记录超出容量。')
        path = self.directory/name
        try:
            atomic_write_bytes(path,data,replace=False)
        except FileExistsError:
            if self._bytes(path,len(data))!=data:
                raise ValueError('输出归属不可变记录冲突或损坏。')

    def _object(self, identity, kind):
        if not isinstance(identity,str) or not _HEX.fullmatch(identity):
            raise ValueError('输出归属引用无效。')
        data = self._bytes(self.directory/(identity+'.json'),METADATA_BYTES)
        if hashlib.sha256(data).hexdigest()!=identity:
            raise ValueError('输出归属记录校验失败。')
        value = json.loads(data)
        if (type(value.get('version')) is not int or value['version']!=1
                or value.get('kind')!=kind or value.get('project')!=self.project):
            raise ValueError('输出归属记录身份无效。')
        return value

    def _paths(self, index):
        pending = bytearray();parts=[];total=count=0
        digest = hashlib.sha256()
        def flush():
            if pending:
                data = bytes(pending)
                parts.append([self._put(data,'.part'),len(data)])
                pending.clear()
        for path in index:
            row = _json(str(_path(str(path))))+b'\n'
            if len(row)>CHUNK_BYTES or total+len(row)>MAX_BYTES:
                raise ValueError('完整活动输出归属超过64MiB。')
            if len(pending)+len(row)>CHUNK_BYTES:
                flush()
            pending.extend(row);digest.update(row);total+=len(row);count+=1
        flush()
        return self._put(_json({'version':1,'kind':'paths','project':self.project,
            'parts':parts,'count':count,'bytes':total,'digest':digest.hexdigest()}))

    def _load_paths(self, identity, index):
        header = self._object(identity,'paths')
        if (not isinstance(header.get('parts'),list) or len(header['parts'])>128
                or type(header.get('count')) is not int or header['count']<0
                or type(header.get('bytes')) is not int or not 0<=header['bytes']<=MAX_BYTES
                or not isinstance(header.get('digest'),str) or not _HEX.fullmatch(header['digest'])):
            raise ValueError('完整输出归属清单头无效。')
        total=count=0;digest=hashlib.sha256();previous=None
        for identity,size in header['parts']:
            index.deadline.check()
            if not isinstance(identity,str) or not _HEX.fullmatch(identity) or type(size) is not int or not 0<size<=CHUNK_BYTES:
                raise ValueError('输出归属片段引用无效。')
            data = self._bytes(self.directory/(identity+'.part'),size)
            total+=len(data)
            if len(data)!=size or total>MAX_BYTES or hashlib.sha256(data).hexdigest()!=identity or not data.endswith(b'\n'):
                raise ValueError('输出归属清单缺失或损坏。')
            for line in data.splitlines():
                path = _path(json.loads(line));key=os.fsencode(path)
                if previous is not None and previous>=key:
                    raise ValueError('输出归属条目重复或顺序无效。')
                previous=key;index.add(path);count+=1
            digest.update(data)
        if total!=header['bytes'] or count!=header['count'] or digest.hexdigest()!=header['digest']:
            raise ValueError('输出归属清单不完整。')

    def _head(self):
        marker=json.loads(self._bytes(self.required,METADATA_BYTES))
        if marker!={'version':1,'project':self.project}:
            raise ValueError('输出归属账本项目身份损坏。')
        data=self._bytes(self.directory/'head.json',METADATA_BYTES)
        head=json.loads(data)
        if (set(head)!={'version','project','generation','membership','operation'}
                or type(head['version']) is not int or head['version']!=1 or head['project']!=self.project
                or type(head['generation']) is not int or not 0<=head['generation']<=10**12
                or not isinstance(head['membership'],str) or not _HEX.fullmatch(head['membership'])):
            raise ValueError('输出归属账本头无效。')
        if head['operation'] is None:
            if head['generation']!=0:
                raise ValueError('输出归属账本缺少生成回执。')
        else:
            operation=self._completion(head['operation'])
            if operation['generation']!=head['generation'] or operation['membership']!=head['membership']:
                raise ValueError('输出归属账本与生成回执不匹配。')
        return head,hashlib.sha256(data).hexdigest()

    @staticmethod
    def _metadata(value):
        data=_json(value)
        if len(data)>METADATA_BYTES:
            raise ValueError('完整输出归属记录超出容量。')
        return data

    def _publish_head(self, head):
        _Deadline().check()
        data=self._metadata(head)
        atomic_write_bytes(self.directory/'head.json',data)
        return hashlib.sha256(data).hexdigest()

    def _recover(self):
        head,token=self._head()
        try:
            intent=json.loads(self._bytes(self.directory/'intent.json',1024))
        except FileNotFoundError:
            return head,token
        if set(intent)!={'operation'}:
            raise ValueError('输出归属提交意图无效。')
        operation=self._completion(intent['operation'])
        if head['operation']==intent['operation']:
            return head,token
        if operation['expected']!=token or operation['generation']!=head['generation']+1:
            raise ValueError('输出归属提交版本冲突，未回放旧结果。')
        with artifact_index() as index:
            self._load_paths(operation['membership'],index)
        head={**head,'generation':operation['generation'],'membership':operation['membership'],'operation':intent['operation']}
        return head,self._publish_head(head)

    def load(self, index, migrate):
        """Initialize once; a missing ledger after initialization is never empty."""
        with self._locked():
            try:
                self._bytes(self.required,METADATA_BYTES)
            except FileNotFoundError:
                if self.directory.exists():
                    raise ValueError('输出归属账本标记丢失，未重置权限。')
                warnings=migrate(index)
                atomic_write_bytes(self.required,self._metadata({'version':1,'project':self.project}),replace=False)
                membership=self._paths(index)
                head={'version':1,'project':self.project,'generation':0,'membership':membership,'operation':None}
                return self._publish_head(head),warnings
            head,token=self._recover()
            self._load_paths(head['membership'],index)
            return token,[]

    def admit(self, expected, binding, scopes, parents, baseline, source):
        """An immutable admission is diagnostic, never an output claim."""
        with self._locked(),artifact_index() as active,artifact_index() as declared,artifact_index() as ancestors:
            head,token=self._recover();self._load_paths(head['membership'],active)
            if token!=expected:
                raise ValueError('输出归属版本已变化，未启动旧验证。')
            for raw in scopes:declared.add(_path(raw))
            for raw in parents:
                parent=_path(str(raw))
                if not any(parent in path.parents for path in declared):
                    raise ValueError('输出父目录不属于本次实际声明。')
                ancestors.add(parent)
            validate_reference(baseline)
            value={'version':1,'kind':'admission','project':self.project,'expected':expected,
                'binding':binding,'scopes':self._paths(declared),'parents':self._paths(ancestors),
                'baseline':baseline,'source':source}
            reference=self._put(_json(value));self._admission(reference,require_key=False)
            key=hashlib.sha256(self._operation_key(binding).encode()).hexdigest()+'.admission-key'
            self._named(key,_json({'admission':reference}))
            return reference

    @staticmethod
    def _operation_key(binding):
        if (set(binding)!={'run_id','step_index','configuration','task_id','task_revision','thread_id'}
                or not isinstance(binding['run_id'],str) or not re.fullmatch(r'[a-f0-9]{32}',binding['run_id'])
                or type(binding['step_index']) is not int or not 1<=binding['step_index']<=30
                or not isinstance(binding['configuration'],str) or not _HEX.fullmatch(binding['configuration'])
                or not isinstance(binding['thread_id'],str) or not 1<=len(binding['thread_id'])<=128
                or not (binding['task_id'] is None and binding['task_revision'] is None
                    or isinstance(binding['task_id'],str) and bool(binding['task_id'])
                    and type(binding['task_revision']) is int and binding['task_revision']>=1)):
            raise ValueError('原生输出执行回执身份无效。')
        return binding['run_id']+':'+str(binding['step_index'])

    def _admission(self, reference, *, require_key=True):
        value=self._object(reference,'admission')
        if set(value)!={'version','kind','project','expected','binding','scopes','parents','baseline','source'}:
            raise ValueError('输出归属准入字段不完整。')
        self._operation_key(value['binding']);validate_reference(value['baseline'])
        if not isinstance(value.get('expected'),str) or not _HEX.fullmatch(value['expected']):
            raise ValueError('输出归属准入版本无效。')
        if (not isinstance(value.get('source'),list) or len(value['source'])!=2
                or any(not isinstance(p,str) or not _HEX.fullmatch(p) for p in value['source'])):
            raise ValueError('输出归属缺少完整输入版本。')
        if any(not isinstance(value.get(key),str) or not _HEX.fullmatch(value[key]) for key in ('scopes','parents')):
            raise ValueError('输出归属缺少完整声明引用。')
        if require_key:
            key=hashlib.sha256(self._operation_key(value['binding']).encode()).hexdigest()+'.admission-key'
            if json.loads(self._bytes(self.directory/key,1024))!={'admission':reference}:
                raise ValueError('输出原生准入身份冲突。')
        return value

    def _completion(self, reference):
        value=self._object(reference,'completion')
        if (set(value)!={'version','kind','project','admission','expected','generation','membership','request'}
                or type(value['generation']) is not int or value['generation']<1
                or not isinstance(value['membership'],str) or not _HEX.fullmatch(value['membership'])):
            raise ValueError('输出生成完成回执不完整。')
        admission=self._admission(value['admission'])
        if value['expected']!=admission['expected'] or set(value['request'])!={'admission','manifest'} or value['request']['admission']!=value['admission']:
            raise ValueError('输出生成回执与准入不匹配。')
        validate_reference(value['request']['manifest'])
        key=hashlib.sha256(self._operation_key(admission['binding']).encode()).hexdigest()+'.completion-key'
        if json.loads(self._bytes(self.directory/key,1024))!={'operation':reference}:
            raise ValueError('输出生成完成身份冲突。')
        return value

    def complete(self, admission, manifest):
        """Seal observed outputs before fallible task/goal/log notifications."""
        with self._locked(),artifact_index() as active,artifact_index() as produced,artifact_index() as baseline,artifact_index() as scopes,artifact_index() as parents:
            value=self._admission(admission);binding=value['binding']
            header=load_manifest(self.boundary,manifest,self.project,produced)
            if any(header.get(key)!=binding[key] for key in ('run_id','configuration','task_id','task_revision','step_index','thread_id')):
                raise ValueError('输出观察不属于本次原生执行。')
            request={'admission':admission,'manifest':manifest}
            key=hashlib.sha256(self._operation_key(binding).encode()).hexdigest()+'.completion-key'
            head,token=self._recover();self._load_paths(head['membership'],active)
            try:previous=json.loads(self._bytes(self.directory/key,1024))
            except FileNotFoundError:previous=None
            if previous is not None:
                operation=self._completion(previous['operation'])
                if operation['request']!=request:
                    raise ValueError('同一执行不能登记不同输出证明。')
                if head['operation']==previous['operation']:
                    return token
            if token!=value['expected']:
                raise ValueError('输出归属已被新执行更新，未覆盖新版本。')
            self._load_paths(value['scopes'],scopes);self._load_paths(value['parents'],parents)
            load_manifest(self.boundary,value['baseline'],self.project,baseline)
            for scope in scopes:remove_scope(active,scope)
            for parent in parents:remove_path(active,parent)
            for path in baseline:
                if path not in produced:
                    remove_path(active,path)
            for path in produced:
                if path in parents or covered(path,scopes):active.add(path)
            membership=self._paths(active)
            operation={'version':1,'kind':'completion','project':self.project,'admission':admission,
                'expected':token,'generation':head['generation']+1,'membership':membership,'request':request}
            identity=self._put(_json(operation));self._named(key,_json({'operation':identity}))
            _Deadline().check()
            atomic_write_bytes(self.directory/'intent.json',_json({'operation':identity}))
            return self._publish_head({**head,'generation':operation['generation'],'membership':membership,'operation':identity})
