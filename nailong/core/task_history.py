"""Immutable complete task snapshots and bounded record lookup, never replay."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from langchain_core.messages import HumanMessage

from nailong.core.task_context import validate_task_snapshot
from nailong.core.task_requirements import requirement_records


class TaskContextHistory:
    def __init__(self,archive,thread_id,project_root):
        self.archive=archive
        self.thread_id=thread_id
        self.project_root=Path(project_root).resolve()

    def _validate(self,snapshot):
        task=validate_task_snapshot(snapshot)
        if task['thread_id']!=self.thread_id or Path(task['project_root']).resolve()!=self.project_root:
            raise ValueError('任务归档不属于当前项目或会话。')
        task['requirements']=requirement_records(snapshot)
        return task

    def _load(self,reference):
        record=self.archive.load(self.thread_id,reference)
        if record.get('archive_truncated'):
            raise ValueError('任务归档自身不完整，不能作为被省略资料的恢复来源。')
        data=record['message']['data']
        if not (data.get('additional_kwargs') or {}).get('nailong_task_archive'):
            raise ValueError('该引用不是任务快照。')
        payload=json.loads(data['content'])
        if not isinstance(payload,dict) or payload.get('task_history_schema')!=1:
            raise ValueError('任务归档格式无效。')
        return self._validate(payload['task']),data['content']

    def save(self,snapshot):
        task=self._validate(snapshot)
        content=json.dumps({'task_history_schema':1,'task':task},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        reference=self.archive.save(self.thread_id,HumanMessage(content=content,
            name='task_context_archive',additional_kwargs={'nailong_task_archive':1}))
        self._load(reference)  # No compact view until recoverability is proven.
        return reference

    def read(self,reference,section='index',record_id='',query='',offset=0,max_chars=4000):
        if section not in {'index','requirements','steps','acceptance','evidence'}:
            raise ValueError('任务资料类型必须为 index/requirements/steps/acceptance/evidence。')
        if (type(offset) is not int or offset<0 or type(max_chars) is not int or not 1<=max_chars<=6000
                or not isinstance(record_id,str) or not isinstance(query,str) or len(query)>4096):
            raise ValueError('任务资料分页或查询参数无效。')
        task,original=self._load(reference)
        if section=='index':
            rows=[]
            for kind in ('requirements','steps','acceptance','evidence'):
                for row in task[kind]:
                    searchable=json.dumps(row,ensure_ascii=False,sort_keys=True)
                    if (record_id and row['id']!=record_id) or (query and query.casefold() not in searchable.casefold()):
                        continue
                    text=row.get('text',row.get('title',row.get('description',row.get('summary',''))))
                    rows.append({'section':kind,'id':row['id'],'description':text[:100],
                        'description_truncated':len(text)>100,'status':row.get('status',row.get('state'))})
        else:
            rows=[row for row in task[section] if (not record_id or row['id']==record_id)
                and (not query or query.casefold() in json.dumps(row,ensure_ascii=False,sort_keys=True).casefold())]
        content=json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        end=min(len(content),offset+max_chars)
        return {'reference':reference,'task_id':task['task_id'],'revision':task['revision'],
            'historical':True,'archive_complete':True,'section':section,'matched_records':len(rows),
            'version':hashlib.sha256(original.encode()).hexdigest(),'content':content[offset:end],
            'offset':offset,'next_offset':end if end<len(content) else None,'truncated':end<len(content)}
