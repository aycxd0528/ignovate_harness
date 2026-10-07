"""User requirement provenance; old history stays active until explicit replacement."""
from __future__ import annotations


def requirement_records(task):
    history=task.get('request_history')
    if history is None:
        history=list(dict.fromkeys(text for text in (task.get('objective'),task.get('latest_request')) if text))
    if not isinstance(history,list) or any(not isinstance(text,str) or not text.strip() for text in history):
        raise ValueError('任务要求历史必须为完整文本列表。')
    rows=task.get('requirements')
    if rows is None:
        # Earlier v1 records did not record introduction revisions. Do not
        # fabricate that provenance or infer supersession from recency.
        return [{'id':f'r{i+1:06d}','text':text,'source':'user','history_index':i,
            'introduced_revision':None,'status':'active','superseded_by':None} for i,text in enumerate(history)]
    if not isinstance(rows,list) or len(rows)!=len(history):
        raise ValueError('要求记录必须完整对应不可删除的用户历史。')
    result=[]
    keys={'id','text','source','history_index','introduced_revision','status','superseded_by'}
    for i,row in enumerate(rows):
        if (not isinstance(row,dict) or set(row)!=keys or row['id']!=f'r{i+1:06d}'
                or type(row['history_index']) is not int or row['history_index']!=i
                or row['text']!=history[i] or row['source']!='user'
                or row['status'] not in {'active','superseded'}):
            raise ValueError('要求 ID、来源或历史原文映射无效。')
        introduced=row['introduced_revision']
        if introduced is not None and (type(introduced) is not int or not 1<=introduced<=task['revision']):
            raise ValueError('要求来源版本无效。')
        if row['status']=='active' and row['superseded_by'] is not None:
            raise ValueError('有效要求不能同时指向替代记录。')
        result.append(dict(row))
    index={row['id']:row for row in result}
    for row in result:
        if row['status']=='superseded':
            target=index.get(row['superseded_by']) if isinstance(row['superseded_by'],str) else None
            if (target is None or target['history_index']<=row['history_index']
                    or target['introduced_revision'] is None
                    or row['introduced_revision'] is not None and target['introduced_revision']<=row['introduced_revision']):
                raise ValueError('要求替代关系必须指向更晚引入的用户记录。')
    return result


def append_requirement(task,text):
    rows=requirement_records(task)
    history=[row['text'] for row in rows]
    i=len(history)
    history.append(text)
    rows.append({'id':f'r{i+1:06d}','text':text,'source':'user','history_index':i,
        'introduced_revision':task['revision'],'status':'active','superseded_by':None})
    task['request_history']=history
    task['requirements']=rows
