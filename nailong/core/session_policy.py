"""Session permission selection shared by GUI, CLI and serial dispatch."""
from nailong.core.preferences import atomic_json,read_config
from nailong.core.file_locks import file_lock
MODES={'default','acceptEdits','bypassPermissions','plan'}


def session_mode(sessions,thread_id,mode=None):
    sessions.session_path(thread_id)
    path=sessions.root/'session-policies'/f'{thread_id}.json'
    with file_lock(path.with_suffix('.lock')):
        if mode is not None:
            if mode not in MODES:raise ValueError('会话权限模式无效。')
            atomic_json(path,{'thread_id':thread_id,'mode':mode},sessions.root)
            return mode
        record=read_config(path,sessions.root)
        if not record and not path.exists():return 'default'
        if record.get('thread_id')!=thread_id or record.get('mode') not in MODES:raise ValueError('会话权限状态损坏。')
        return record['mode']


def restricted_mode(source,target):
    ranks={'plan':0,'default':1,'acceptEdits':2,'bypassPermissions':3}
    return min((source,target),key=ranks.__getitem__)
