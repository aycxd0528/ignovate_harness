"""Per-call dollars in the same atomic authority as goal round settlement."""
from contextlib import ExitStack
import hashlib
import math

from nailong.core.file_locks import file_lock


def _identity(value):
    if not isinstance(value,str) or not value.strip() or len(value)>200 or '\0' in value:
        raise ValueError('目标费用身份无效。')
    return value


def _usd(value):
    if type(value) not in (int,float):
        raise ValueError('目标费用必须为有限非负数。')
    try:result=float(value)
    except (OverflowError,ValueError) as error:
        raise ValueError('目标费用必须为有限非负数。') from error
    if not math.isfinite(result) or result<0:
        raise ValueError('目标费用必须为有限非负数。')
    return result


def validate_cost_rounds(goal):
    rows=goal.pending_cost_rounds
    if not isinstance(rows,dict) or len(rows)>128:
        raise ValueError('目标费用预留记录无效或过多。')
    charged=0.0;reserved=0.0
    for identity,row in rows.items():
        _identity(identity)
        if (identity in goal.settled_round_ids or not isinstance(row,dict)
                or set(row)!={'charged_usd','reservations','usage_complete'}
                or type(row['usage_complete']) is not bool
                or not isinstance(row['reservations'],dict) or len(row['reservations'])>1024):
            raise ValueError('目标费用预留与结算身份冲突。')
        charged+=_usd(row['charged_usd'])
        for reservation,amount in row['reservations'].items():
            _identity(reservation);reserved+=_usd(amount)
    if not math.isfinite(charged) or not math.isfinite(reserved) or charged>goal.spent_usd+1e-9:
        raise ValueError('目标已入账费用与预留记录不一致。')


def finish_cost_round(goal,round_id,cost):
    """Called inside GoalStore's final round transaction; no second journal."""
    cost=_usd(cost)
    row=goal.pending_cost_rounds.pop(round_id,None) if round_id else None
    if row is None:return cost,False
    unknown=bool(row['reservations']) or not row['usage_complete']
    total=max(cost,row['charged_usd']+sum(row['reservations'].values()))
    return max(0.0,total-row['charged_usd']),unknown


class GoalCostLedger:
    def __init__(self,store):
        self.store=store

    def recovery_goals(self,thread_id):
        with self.store._locked():
            return [goal for goal in self.store._load().values()
                if goal.thread_id==thread_id and goal.cost_recovery_required]

    def lease(self,goal_id,round_id):
        identity=hashlib.sha256((_identity(goal_id)+'\0'+_identity(round_id)).encode()).hexdigest()
        directory=self.store.path.with_name(self.store.path.name+'.cost-rounds')
        return file_lock(directory/(identity+'.lock'),blocking=False)

    def recover(self,goal_id=None,*,ignore_round=None):
        """Only an unowned native round lease establishes an orphan."""
        changed=[]
        with self.store._locked(),ExitStack() as leases:
            goals=self.store._load()
            for goal in goals.values():
                if goal_id is not None and goal.id!=goal_id:continue
                recovered=False;unknown=False
                for identity,row in list(goal.pending_cost_rounds.items()):
                    if identity==ignore_round:continue
                    try:leases.enter_context(self.lease(goal.id,identity))
                    except BlockingIOError:continue
                    unknown=unknown or bool(row['reservations']) or not row['usage_complete']
                    # Known costs already reached spent_usd in settle(). Only
                    # unfinished provider reservations need a conservative charge.
                    goal.spent_usd+=sum(row['reservations'].values())
                    goal.round+=1;goal.settled_round_ids.append(identity)
                    del goal.pending_cost_rounds[identity];recovered=True
                if recovered:
                    goal.state='paused'
                    goal.cost_recovery_required=True
                    goal.pause_reason=('上一轮模型用量未知，已按预留额保守计费；请核对任务后显式恢复。'
                        if unknown else '上一轮收尾中断，已保留实际费用；请核对任务后显式恢复。')
                    changed.append(goal.id)
            if changed:self.store._save(goals)
        return changed

    def reserve(self,goal_id,round_id,reservation,amount,*,allow_paused=False):
        _identity(round_id);_identity(reservation);amount=_usd(amount)
        if self.recover(goal_id,ignore_round=round_id):
            raise ValueError('已恢复中断轮次的费用，目标已暂停，请核对后显式恢复。')
        with self.store._locked():
            goals=self.store._load();goal=goals.get(goal_id)
            # A live native round can finish the model's final response after
            # update_goal pauses/completes it. New paused rounds are limited to
            # the ordinary adapter's explicitly authorized brief revision path.
            admitted=goal is not None and round_id in goal.pending_cost_rounds
            if (goal is None or goal.cost_recovery_required
                    or goal.state!='active' and not admitted and not (allow_paused and goal.state=='paused')
                    or round_id in goal.settled_round_ids or goal.round>=goal.max_rounds):
                raise ValueError('目标已暂停、结算或不存在，不能预留模型费用。')
            if any(identity!=round_id for identity in goal.pending_cost_rounds):
                raise ValueError('目标仍有其他运行的费用预留，不能另开预算。')
            pending=sum(sum(row['reservations'].values()) for row in goal.pending_cost_rounds.values())
            if amount>goal.max_cost_usd-goal.spent_usd-pending+1e-12:
                raise ValueError('当前目标持久额度不足，不能预留模型调用。')
            row=goal.pending_cost_rounds.setdefault(round_id,{
                'charged_usd':0.0,'reservations':{},'usage_complete':True})
            if not row['usage_complete'] or reservation in row['reservations'] or len(row['reservations'])>=1024:
                raise ValueError('目标预留重复、用量未知或并发调用过多。')
            row['reservations'][reservation]=amount
            self.store._save(goals)

    def _change(self,goal_id,round_id,reservation,*,cost=None,complete=True):
        if cost is not None:cost=_usd(cost)
        with self.store._locked():
            goals=self.store._load();goal=goals.get(goal_id)
            row=goal.pending_cost_rounds.get(round_id) if goal else None
            if row is None or reservation not in row['reservations']:
                raise ValueError('原生目标费用预留不存在或已经结算。')
            del row['reservations'][reservation]
            if cost is not None:
                row['charged_usd']+=cost;goal.spent_usd+=cost
                row['usage_complete']=row['usage_complete'] and complete
            elif not row['reservations'] and not row['charged_usd'] and row['usage_complete']:
                del goal.pending_cost_rounds[round_id]
            self.store._save(goals)

    def release(self,goal_id,round_id,reservation):
        self._change(goal_id,round_id,reservation)

    def settle(self,goal_id,round_id,reservation,cost,*,complete):
        self._change(goal_id,round_id,reservation,cost=cost,complete=complete)
