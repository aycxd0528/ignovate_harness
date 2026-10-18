"""Bounded task briefs. Only a live user turn can request persistent execution."""
from __future__ import annotations
import copy
import re
from contextvars import ContextVar
from typing import Literal, Annotated
from typing_extensions import TypedDict, Required
from pydantic import ConfigDict, Field, with_config
from nailong.core.task_requests import task_request_kind

user_request = ContextVar('nailong_live_user_request', default=None)


@with_config(ConfigDict(extra='forbid',strict=True))
class BriefAcceptance(TypedDict):
    description: str
    step: str
    paths: Annotated[list[str], Field(description='验收覆盖的项目内相对文件/目录路径；只填路径，不写说明或命令。')]


@with_config(ConfigDict(extra='forbid',strict=True))
class BriefVerification(TypedDict, total=False):
    name: Required[Annotated[str, Field(description='步骤唯一名称；acceptance.step 必须逐字引用此名称。')]]
    kind: Required[Literal['test','build','run']]
    command: Required[Annotated[str, Field(description='实际可运行的验证命令；使用环境中已确认存在的解释器。')]]
    timeout_seconds: int | float
    stdout_contains: Annotated[str, Field(description='仅 kind=run 时可选；test/build 必须省略。')]
    http_url: Annotated[str, Field(description='仅 kind=run 时可选；与 stdout_contains 二选一，test/build 必须省略。')]
    expected_status: int
    generated_paths: list[str]


@with_config(ConfigDict(extra='forbid',strict=True))
class TaskBrief(TypedDict, total=False):
    objective: Required[str]
    scope: Annotated[list[str], Field(description='项目内相对文件/目录路径数组，如 ["upper.py","test_upper.py"]；只填路径，不写自然语言说明或命令。')]
    constraints: list[str]
    acceptance: list[str | BriefAcceptance]
    open_questions: list[str]
    verification: list[BriefVerification]


def bound_goal(store, thread_id, task=None):
    """Resolve a natural task's ledger even when it is paused or no longer latest."""
    goals=getattr(store,'goal_store',None)
    task=task if task is not None else store.snapshot(thread_id)
    intake=(task or {}).get('intake',{})
    if goals is None or not intake:return None
    if intake.get('goal_id'):
        goal=goals.get(intake['goal_id'])
        if goal is None or goal.thread_id!=thread_id:raise ValueError('持续任务的目标身份无法核对。')
        return goal
    # Compatibility for records written before goal_id was persisted.
    with goals._locked():
        matches=[goal for goal in goals._load().values() if goal.thread_id==thread_id
            and goal.objective in {task['objective'],intake['objective']}]
    return matches[-1] if matches else None


def validate_brief(brief):
    if not isinstance(brief,dict) or set(brief)-{'objective','scope','constraints','acceptance','open_questions','verification'}:
        raise ValueError('需求草案包含未知字段。')
    objective=brief.get('objective')
    if not isinstance(objective,str) or not objective.strip() or len(objective)>4000:
        raise ValueError('目标需为 1–4000 字符。')
    result={'objective':objective.strip()}
    for key in ('scope','constraints','open_questions'):
        rows=brief.get(key,[])
        if not isinstance(rows,list) or len(rows)>20 or any(not isinstance(row,str) or not row.strip() or len(row)>1000 for row in rows):
            raise ValueError(key+' 需为至多 20 项的有界文本列表。')
        result[key]=list(rows)
    acceptance=brief.get('acceptance',[])
    if not isinstance(acceptance,list) or len(acceptance)>20:raise ValueError('验收至多 20 项。')
    for row in acceptance:
        if isinstance(row,str):
            if not row.strip() or len(row)>1000:raise ValueError('验收文本无效。')
        elif isinstance(row,dict):
            if set(row)!={'description','step','paths'} or any(not isinstance(row[key],str) or not row[key].strip() or len(row[key])>1000 for key in ('description','step')) or not isinstance(row['paths'],list) or not row['paths'] or len(row['paths'])>20 or any(not isinstance(path,str) for path in row['paths']):
                raise ValueError('自动验收需明确 description、step 和覆盖 paths。')
        else:raise ValueError('验收无效。')
    result['acceptance']=copy.deepcopy(acceptance)
    verification=brief.get('verification',[])
    from nailong.core.verification import VerificationService
    result['verification']=VerificationService.validate_steps(verification)
    return result


class TaskIntake:
    def __init__(self,store,thread_id):
        self.store,self.thread_id=store,thread_id
        self.goals=getattr(store,'goal_store',None)

    def _request(self):
        request=user_request.get()
        if not request or request.get('thread_id')!=self.thread_id:
            raise ValueError('仅当前真实用户会话可准备或启动任务。')
        return request

    def prepare(self, brief: TaskBrief, expected_revision: int = 0) -> dict:
        """保存需求草案；列明关键疑问和可观察验收。此操作不启动实施。"""
        try:
            request=self._request(); brief=validate_brief(brief)
            brief['scope']=[self.store._relative(path) for path in brief['scope']]
            steps={row['name']:row for row in brief['verification']}
            for row in brief['acceptance']:
                if isinstance(row,dict):
                    if row['step'] not in steps:raise ValueError('验收步骤不存在。')
                    row['paths']=[self.store._relative(path) for path in row['paths']]
            with self.store._locked(self.thread_id):
                task=self.store._read(self.thread_id)
                previous=(task or {}).get('intake',{})
                if type(expected_revision) is not int or previous.get('draft_revision',0)!=expected_revision:
                    raise ValueError('需求草案版本已变化，请重新读取。')
                if task is None:
                    task=self.store.begin(self.thread_id,request['text'])
                goal=bound_goal(self.store,self.thread_id,task)
                if previous.get('stage')=='executing' and not (goal and goal.state=='paused' or task['lifecycle']=='paused'):
                    raise ValueError('实施中请先暂停，再修订草案。')
                if previous.get('stage')=='executing':self.store._invalidate(task,'已暂停并修订草案，旧验收需重新核对。')
                task['intake']={**brief,'draft_revision':expected_revision+1,'source_turn':request['turn_id'],
                    'stage':'clarifying' if brief['open_questions'] or not brief['acceptance'] else 'ready','presented_turn':None,'pending_start':None,
                    'task_revision':task['revision'],**({'goal_id':goal.id} if goal else {})}
                self.store._write(self.thread_id,task)
            return {'ok':True,'brief':task['intake']}
        except (ValueError,OSError,RuntimeError) as error:return {'ok':False,'error':str(error)}

    def start(self, draft_revision: int, max_rounds: int = 20, max_cost_usd: float = 1.0) -> dict:
        """真实用户要求实施时提交启动意图；待本轮结束后由原生目标驱动器执行。"""
        try:
            request=self._request()
            text=request['text']
            if re.search(r'不要|暂不|先不|只讨论|仅讨论|只解释|禁止|记录里|历史|归档',text) or task_request_kind(text) not in {'work','resume'}:
                raise ValueError('当前用户没有授权实施。')
            if type(max_rounds) is not int or not 1<=max_rounds<=100 or isinstance(max_cost_usd,bool) or not isinstance(max_cost_usd,(int,float)) or not 0<max_cost_usd<=100:
                raise ValueError('执行轮数 1–100，成本上限须为 0–100 美元。')
            with self.store._locked(self.thread_id):
                task=self.store._read(self.thread_id); draft=(task or {}).get('intake',{})
                if type(draft_revision) is not int or draft.get('task_revision')!=task.get('revision') or draft.get('draft_revision')!=draft_revision or draft.get('stage')!='ready' or draft.get('open_questions') or not draft.get('acceptance'):
                    raise ValueError('草案过期或仍有未解决的问题。')
                goal=bound_goal(self.store,self.thread_id,task)
                if goal and self.goals._limit_reason(goal):raise ValueError(self.goals._limit_reason(goal)+' 请明确创建新目标。')
                direct=task_request_kind(text)=='work' and not re.fullmatch(r'(?:请)?(?:开始|按这个|按计划|照这个)(?:做|实施|执行|推进)',text.strip('。！! '))
                if not direct and (not draft.get('presented_turn') or draft['presented_turn']==request['turn_id']):
                    raise ValueError('当前草案尚未向用户展示，不能沿用旧版本的启动授权。')
                draft['pending_start']={'user_text':text,'turn_id':request['turn_id'],'draft_revision':draft_revision,
                    'max_rounds':max_rounds,'max_cost_usd':float(max_cost_usd)}
                self.store._write(self.thread_id,task)
            return {'ok':True,'message':'已提交本轮结束后的持续执行；本轮不再进行文件或命令操作。'}
        except (ValueError,OSError,RuntimeError) as error:return {'ok':False,'error':str(error)}

    def present(self):
        request=self._request()
        with self.store._locked(self.thread_id):
            task=self.store._read(self.thread_id); draft=(task or {}).get('intake')
            if not draft or draft.get('stage')=='executing' or draft.get('pending_start'):return None
            draft['presented_turn']=request['turn_id']; self.store._write(self.thread_id,task)
            lines=['需求草案 v'+str(draft['draft_revision'])+'：'+draft['objective']]
            for key,label in [('scope','范围'),('constraints','约束'),('acceptance','验收'),('open_questions','待确认')]:
                if draft[key]:lines.append(label+'：'+'；'.join(row if isinstance(row,str) else row['description']+' → '+row['step']+' ['+', '.join(row['paths'])+']' for row in draft[key]))
            if draft['verification']:lines.append('验证：'+'；'.join(row['command'] for row in draft['verification']))
            return '\n'.join(lines)

    def consume(self):
        request=self._request()
        with self.store._locked(self.thread_id):
            task=self.store._read(self.thread_id); draft=(task or {}).get('intake',{})
            pending=draft.get('pending_start')
            if not pending or pending['turn_id']!=request['turn_id']:return None
            if pending['draft_revision']!=draft['draft_revision'] or draft.get('task_revision')!=task['revision']:raise ValueError('启动前草案或用户要求版本变化。')
            goal=bound_goal(self.store,self.thread_id,task)
            if goal:
                ok,reason,goal=self.goals.revise(goal.id,objective=draft['objective'],thread_id=self.thread_id)
                if not ok:raise ValueError(reason)
                draft['goal_id']=goal.id
            draft.update(pending_start=None,stage='executing',accepted_turn=request['turn_id'],implementation_started=False)
            task['revision']+=1
            task.update(objective=draft['objective'],scope=draft['scope'] or task['scope'],constraints=draft['constraints'],lifecycle='active')
            task['acceptance']=[row for row in task['acceptance'] if not row['id'].startswith('brief:')]
            self.store._invalidate(task,'用户接受需求草案，核对验收。')
            self.store._write(self.thread_id,task)
            self.store.configure_verification(self.thread_id,draft['verification'])
            for index,criterion in enumerate(draft['acceptance']):
                key='brief:'+str(draft['draft_revision'])+':'+str(index)
                if isinstance(criterion,str):self.store.add_acceptance(self.thread_id,key,criterion,kind='manual')
                else:
                    step=next(row for row in draft['verification'] if row['name']==criterion['step'])
                    self.store.add_acceptance(self.thread_id,key,criterion['description'],kind=step['kind'])
                    self.store.bind_verification(self.thread_id,key,criterion['step'],criterion['paths'])
            current=self.store._read(self.thread_id)
            return {**copy.deepcopy(draft),**pending,'task_id':current['task_id'],'task_revision':current['revision']}


def is_native_intake(spec,root):
    owner=getattr(getattr(spec,'handler',None),'__self__',None)
    return type(owner) is TaskIntake and owner.store.project_root==root and getattr(spec.handler,'__func__',None) in {TaskIntake.prepare,TaskIntake.start}
