"""Conservative one-time legacy output migration, without wall-clock ordering."""
import os
from pathlib import Path

from nailong.core.output_authority import covered
from nailong.core.verification_artifacts import _path, artifact_index, load_manifest


def migrate_outputs(store, project, declared, configuration, destination):
    connection=destination.connection
    connection.execute('CREATE TABLE scratch.legacy_claims(path BLOB,thread TEXT,PRIMARY KEY(path,thread))')
    connection.execute('CREATE TABLE scratch.legacy_scopes(path BLOB,thread TEXT,PRIMARY KEY(path,thread))')
    warnings=[]
    def warn(message):
        if len(warnings)<8:warnings.append(message)
    if store is None or not hasattr(store,'list_sessions'):
        return warnings
    for session in store.list_sessions():
        thread=session['thread_id']
        # Sequence within one durable event stream is meaningful. Across
        # streams, intersect overlapping claims instead of guessing by dates.
        for event in store.read_events(thread):
            destination.deadline.check()
            data=event.get('data',{})
            if event.get('kind')!='verification' or not isinstance(data,dict):continue
            if data.get('output_authority_version'):
                raise ValueError('已启用的活动输出归属账本缺失，未从旧事件重置。')
            if data.get('cwd')!=str(project) or not any(row.get('started') is True for row in data.get('steps',[]) if isinstance(row,dict)):
                continue
            raw_scopes=data.get('declared_generated_paths')
            if data.get('configuration_fingerprint')==configuration:
                raw_scopes=declared
            if not isinstance(raw_scopes,list) or len(raw_scopes)>=100 and data.get('configuration_fingerprint')!=configuration:
                raise ValueError('旧输出声明范围不完整，不能迁移或恢复历史路径权限。')
            with artifact_index() as scopes,artifact_index() as observed:
                for raw in raw_scopes:scopes.add(_path(raw))
                for scope in scopes:
                    key=os.fsencode(scope);prefix=key+os.fsencode(os.sep)
                    end=prefix[:-1]+bytes((prefix[-1]+1,))
                    connection.execute('DELETE FROM legacy_claims WHERE thread=? AND (path=? OR (path>=? AND path<?))',
                        (thread,key,prefix,end))
                    connection.execute('INSERT OR IGNORE INTO legacy_scopes VALUES (?,?)',(key,thread))
                try:
                    reference=data.get('artifact_manifest')
                    if reference is not None:
                        header=load_manifest(store.root,reference,project,observed)
                        if (header.get('run_id')!=data.get('run_id')
                                or header.get('configuration')!=data.get('configuration_fingerprint')):
                            raise ValueError('旧输出证明与执行身份不符。')
                    else:
                        paths=data.get('generated_paths',[])
                        if not isinstance(paths,list):raise ValueError('旧输出预览无效。')
                        for raw in paths:
                            path=_path(raw)
                            if not covered(path,scopes):continue
                            observed.add(path)
                            for parent in path.parents:
                                if parent.parts and covered(parent,scopes):observed.add(parent)
                except (ValueError,OSError):
                    warn('旧产物证明无法完整读取，其声明范围的历史权限未恢复。')
                    continue
                # A failed old run may revoke earlier claims, but lacks the
                # new native admission receipt needed to establish new ones.
                if data.get('status')!='passed' or data.get('complete') is not True:continue
                for path in observed:
                    if covered(path,scopes):
                        connection.execute('INSERT OR IGNORE INTO legacy_claims VALUES (?,?)',(os.fsencode(path),thread))
    for (key,) in connection.execute('SELECT DISTINCT path FROM legacy_claims ORDER BY path'):
        destination.deadline.check();path=Path(os.fsdecode(key));conflict=False
        for parent in (path,*path.parents):
            if not parent.parts:continue
            missing=connection.execute('SELECT 1 FROM legacy_scopes s WHERE s.path=? AND NOT EXISTS('
                'SELECT 1 FROM legacy_claims c WHERE c.path=? AND c.thread=s.thread) LIMIT 1',
                (os.fsencode(parent),key)).fetchone()
            if missing is not None:conflict=True;break
        if conflict:
            warn('旧会话之间的输出归属顺序不明确，冲突路径未迁移。')
        else:destination.add(path)
    return warnings
