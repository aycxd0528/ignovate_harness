"""Persistent guarded goals for bounded multi-turn work."""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import nullcontext, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from nailong.core.file_locks import file_lock
from nailong.core.safe_files import atomic_write_bytes, is_link_or_reparse, open_regular_file, pinned_directory


GoalState = Literal["active", "paused", "complete", "blocked"]


@dataclass
class Goal:
    id: str
    objective: str
    state: GoalState
    round: int
    max_rounds: int
    max_cost_usd: float
    spent_usd: float
    blocked_rounds: int
    last_blocker: str | None
    created_at: str
    idle_rounds: int = 0
    last_summary: str = ""
    blocker_observed_round: int = -1
    pause_reason: str = ""
    verification_command: str = ""
    verification_output: str = ""
    verification_exit_code: int | None = None
    verification_tool: str = ""
    verification_recorded_at: str = ""
    verification_succeeded: bool = False
    verification_thread_id: str | None = None
    verification_run_id: str = ""
    verification_fingerprint: str = ""
    verification_project_root: str = ""
    verification_generated_paths: list[str] = field(default_factory=list)
    verification_manifest: dict | None = None
    verification_task_id: str = ''
    verification_task_revision: int = 0
    thread_id: str | None = None
    settled_round_ids: list[str] = field(default_factory=list)
    last_progress: dict = field(default_factory=dict)
    recent_progress: list[dict] = field(default_factory=list)
    no_change_rounds: int = 0
    pending_cost_rounds: dict = field(default_factory=dict)
    cost_recovery_required: bool = False


@dataclass
class _CompletionObservation:
    goal: Goal | None
    task_store: object
    project_root: Path | None
    task: dict | None = None
    current: str | None = None
    verification_current: str | None = None


class GoalStore:
    def __init__(self, path: str | Path, *, api_key: str = "", project_root=None):
        original = Path(path).expanduser().absolute()
        if is_link_or_reparse(original):
            raise ValueError('目标状态路径不能是符号链接或重解析点。')
        self.path = original if os.name == 'nt' else original.resolve()
        self.api_key = api_key
        self.project_root = Path(project_root).resolve() if project_root else None
        self.task_store = None  # Optional factory-owned continuity/coverage guard.
        self._lock = threading.RLock()
        self._lock_depth = 0
        self._driver_owner = None
        from nailong.core.goal_costs import GoalCostLedger
        self.costs = GoalCostLedger(self)
        with pinned_directory(self.path.parent, create=True):
            pass

    @contextmanager
    def _locked(self):
        """Protect complete read/modify/write transactions across CLI processes."""
        with self._lock:
            if self._lock_depth:
                yield
                return
            lock_path = self.path.with_name(self.path.name + '.lock')
            with file_lock(lock_path):
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1

    @contextmanager
    def driver_lease(self):
        """One goal driver per project, with a nonblocking cross-process lease."""
        import asyncio
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        owner = (threading.get_ident(), task)
        with self._lock:
            if self._driver_owner == owner:
                yield
                return
            lease = file_lock(self.path.with_name(self.path.name + '.driver.lock'), blocking=False)
            try:
                lease.__enter__()
            except BlockingIOError:
                raise ValueError('此项目的目标已在另一运行中执行，请等待或停止该运行。') from None
            self._driver_owner = owner
        try:
            yield
        finally:
            with self._lock:
                self._driver_owner = None
                lease.__exit__(None, None, None)

    def _load(self) -> dict[str, Goal]:
        try:
            with open_regular_file(self.path, binary=False) as source:
                payload = json.load(source)
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError('目标状态无法读取或已损坏，未重置进度和费用。') from error
        if not isinstance(payload, list):
            raise ValueError('目标状态必须为列表，未重置进度和费用。')
        goals = {}
        try:
            for value in payload:
                goal = Goal(**value)
                self._validate_goal(goal)
                if goal.id in goals:
                    raise ValueError('duplicate goal')
                goals[goal.id] = goal
            if sum(goal.state == 'active' for goal in goals.values()) > 1:
                raise ValueError('multiple active goals')
        except (TypeError, ValueError) as error:
            raise ValueError('目标状态身份、预算或记录无效，未重置进度和费用。') from error
        return goals

    @staticmethod
    def _validate_goal(goal: Goal) -> None:
        """Reject damaged ledgers instead of dropping their spending history."""
        for name in ('id', 'objective', 'created_at'):
            if not isinstance(getattr(goal, name), str) or not getattr(goal, name).strip():
                raise ValueError('invalid goal identity')
        if goal.state not in {'active', 'paused', 'complete', 'blocked'}:
            raise ValueError('invalid goal state')
        for name in ('round', 'max_rounds', 'blocked_rounds', 'idle_rounds',
                     'verification_task_revision', 'no_change_rounds', 'blocker_observed_round'):
            value = getattr(goal, name)
            if type(value) is not int or value < (-1 if name == 'blocker_observed_round' else 0):
                raise ValueError('invalid goal counter')
        if not 1 <= goal.max_rounds <= 100:
            raise ValueError('invalid goal round limit')
        for name in ('spent_usd', 'max_cost_usd'):
            from nailong.core.goal_costs import _usd
            _usd(getattr(goal,name))
        if not 0 < goal.max_cost_usd <= 100:
            raise ValueError('invalid goal cost limit')
        for name in ('last_summary', 'pause_reason', 'verification_command', 'verification_output',
                     'verification_tool', 'verification_recorded_at', 'verification_run_id',
                     'verification_fingerprint', 'verification_project_root', 'verification_task_id'):
            if not isinstance(getattr(goal, name), str):
                raise ValueError('invalid goal text')
        for value in (goal.thread_id, goal.verification_thread_id, goal.last_blocker):
            if value is not None and not isinstance(value, str):
                raise ValueError('invalid goal binding')
        if type(goal.verification_succeeded) is not bool or (goal.verification_exit_code is not None
                and type(goal.verification_exit_code) is not int):
            raise ValueError('invalid goal verification')
        if type(goal.cost_recovery_required) is not bool or goal.cost_recovery_required and goal.state!='paused':
            raise ValueError('invalid goal cost recovery state')
        for values in (goal.settled_round_ids, goal.verification_generated_paths):
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError('invalid goal references')
        if goal.verification_manifest is not None:
            from nailong.core.verification_artifacts import validate_reference
            validate_reference(goal.verification_manifest)
        if (not isinstance(goal.last_progress, dict) or not isinstance(goal.recent_progress, list)
                or any(not isinstance(row, dict) for row in goal.recent_progress)):
            raise ValueError('invalid goal progress')
        from nailong.core.goal_costs import validate_cost_rounds
        validate_cost_rounds(goal)

    def _save(self, goals: dict[str, Goal]) -> None:
        content = []
        for goal in goals.values():
            self._validate_goal(goal)
            data = asdict(goal)
            if self.api_key:
                for key, value in data.items():
                    if isinstance(value, str):
                        data[key] = value.replace(self.api_key, "[密钥已隐藏]")
            content.append(data)
        encoded = json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        atomic_write_bytes(self.path, encoded)

    def create(
        self,
        objective: str,
        *,
        max_rounds: int = 20,
        max_cost_usd: float = 1.0,
        thread_id: str | None = None,
    ) -> Goal:
        objective = str(objective).strip()
        if not objective:
            raise ValueError("目标内容不能为空。")
        if not 1 <= int(max_rounds) <= 100:
            raise ValueError("目标轮数必须在 1 到 100 之间。")
        if not 0 < float(max_cost_usd) <= 100:
            raise ValueError("目标成本上限必须大于 0 且不超过 100 美元。")
        with self.driver_lease(), self._locked():
            self.costs.recover()
            goals = self._load()
            if any(goal.state == "active" for goal in goals.values()):
                raise ValueError("已有进行中的目标；请先用 /goal status 查看或暂停它。")
            safe_objective = objective.replace(self.api_key, "[密钥已隐藏]") if self.api_key else objective
            goal = Goal(
                id=uuid.uuid4().hex,
                objective=safe_objective,
                state="active",
                round=0,
                max_rounds=int(max_rounds),
                max_cost_usd=float(max_cost_usd),
                spent_usd=0.0,
                blocked_rounds=0,
                last_blocker=None,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                thread_id=thread_id,
            )
            goals[goal.id] = goal
            self._save(goals)
            return goal

    def get(self, goal_id: str) -> Goal | None:
        with self._locked():
            return self._load().get(goal_id)

    def active(self) -> Goal | None:
        with self._locked():
            goals = self._load().values()
            return next((goal for goal in reversed(list(goals)) if goal.state == "active"), None)

    def latest(self) -> Goal | None:
        with self._locked():
            goals = self._load().values()
            return next(reversed(list(goals)), None)

    def attach_thread(self, goal_id: str, thread_id: str) -> Goal | None:
        """Bind an active persisted goal to the thread currently driving it."""
        with self.driver_lease(), self._locked():
            goals = self._load()
            goal = goals.get(goal_id)
            if goal is None or goal.state != "active":
                return goal
            if goal.thread_id != str(thread_id):
                self._clear_verification(goal)
            goal.thread_id = str(thread_id)
            goals[goal.id] = goal
            self._save(goals)
            return goal

    def revise(self, goal_id, *, objective, thread_id):
        """Accept a revised paused brief without creating another spending ledger."""
        with self.driver_lease(), self._locked():
            goals=self._load();goal=goals.get(goal_id)
            if goal is None or goal.thread_id!=thread_id or goal.state!='paused':
                return False,'只能修订当前会话已暂停的目标。',goal
            if self._limit_reason(goal):return False,self._limit_reason(goal)+' 请明确创建新目标。',goal
            if not isinstance(objective,str) or not objective.strip():return False,'目标不能为空。',goal
            goal.objective=objective.replace(self.api_key,'[密钥已隐藏]') if self.api_key else objective
            self._clear_verification(goal)
            goal.last_progress={}
            self._save(goals)
            return True,'已修订原目标，费用和轮数保留。',goal

    def resume(self, goal_id: str, *, thread_id: str | None = None) -> tuple[bool, str, Goal | None]:
        """Resume a paused goal only after an explicit caller action."""
        with self.driver_lease(), self._locked():
            recovered=self.costs.recover(goal_id)
            goals = self._load()
            goal = goals.get(goal_id)
            if goal is None:
                return False, "找不到该目标。", None
            if recovered:return False,goal.pause_reason,goal
            if goal.state != "paused":
                return False, f"目标当前状态为 {goal.state}，不能恢复。", goal
            limit_reason = self._limit_reason(goal)
            if limit_reason:
                return False, limit_reason + " 请创建新目标。", goal
            if any(item.state == "active" for item in goals.values()):
                return False, "已有进行中的目标；请先完成或暂停它。", goal
            goal.state = "active"
            goal.cost_recovery_required = False
            goal.pause_reason = ""
            goal.blocked_rounds = 0
            goal.last_blocker = None
            goal.blocker_observed_round = -1
            goal.no_change_rounds = 0
            self._clear_verification(goal)
            if thread_id:
                goal.thread_id = str(thread_id)
            goals[goal.id] = goal
            self._save(goals)
            return True, "目标已恢复。", goal

    @staticmethod
    def _limit_reason(goal: Goal) -> str:
        if goal.spent_usd >= goal.max_cost_usd:
            return "已达到目标成本上限。"
        if goal.round >= goal.max_rounds:
            return "已达到目标轮数上限。"
        return ""

    def prepare_round(self, goal_id: str) -> Goal | None:
        """Read current state and enforce limits before any new model call."""
        self.costs.recover(goal_id)
        with self._locked():
            goals = self._load()
            goal = goals.get(goal_id)
            if goal is not None and goal.state == "active":
                reason = self._limit_reason(goal)
                if reason:
                    goal.state = "paused"
                    goal.pause_reason = reason
                    self._save(goals)
            return goal

    @staticmethod
    def _clear_verification(goal: Goal) -> None:
        goal.verification_command = ""
        goal.verification_output = ""
        goal.verification_exit_code = None
        goal.verification_tool = ""
        goal.verification_recorded_at = ""
        goal.verification_succeeded = False
        goal.verification_thread_id = None
        goal.verification_run_id = ""
        goal.verification_fingerprint = ""
        goal.verification_project_root = ""
        goal.verification_generated_paths = []
        goal.verification_manifest = None
        goal.verification_task_id = ''
        goal.verification_task_revision = 0

    def record_verified_run(self, record: dict) -> bool:
        if record.get('status') != 'passed' or not record.get('complete'):
            return False
        tasks = self.task_store
        if tasks is not None and (not isinstance(record.get('thread_id'), str) or not record['thread_id']):
            return False
        # Intake owns the task before updating its goal. Keep the same order
        # here so a verification result cannot deadlock a user amendment.
        binding = tasks._locked(record.get('thread_id')) if tasks is not None else nullcontext()
        with binding, self._locked():
            goals = self._load()
            goal = goals.get(record.get('goal_id'))
            if goal is None or goal.state != 'active' or goal.thread_id != record.get('thread_id'):
                return False
            if tasks is not None:
                task=tasks.snapshot(goal.thread_id)
                if task is None or (task['task_id'],task['revision'])!=(record.get('task_id'),record.get('task_revision')):
                    # A stale receipt has no authority to erase newer proof.
                    # Completion independently binds proof to the current task.
                    return False
            rows = record.get('steps', [])
            if not rows or not all(row.get('ok') and row.get('started') for row in rows):
                return False
            goal.verification_tool = 'verify'
            goal.verification_command = '\n'.join(row['command'] for row in rows)[:2000]
            goal.verification_output = '\n'.join(row.get('output','') for row in rows)[:4000]
            goal.verification_exit_code = 0  # Aggregate acceptance, individual exits remain in evidence.
            goal.verification_thread_id = goal.thread_id
            goal.verification_recorded_at = datetime.now(timezone.utc).isoformat()
            goal.verification_succeeded = True
            goal.verification_run_id = record['run_id']
            goal.verification_fingerprint = record['input_after']
            goal.verification_project_root = record['cwd']
            goal.verification_generated_paths = record.get('generated_paths', [])
            goal.verification_manifest = record.get('artifact_manifest')
            goal.verification_task_id = record.get('task_id') or ''
            goal.verification_task_revision = record.get('task_revision') or 0
            self._save(goals)
            return True

    @staticmethod
    def _has_verification(goal: Goal) -> bool:
        return bool(
            goal.verification_tool in {'run_command', 'verify'}
            and goal.verification_exit_code == 0
            and goal.verification_recorded_at
            and goal.verification_succeeded
            and goal.verification_command.strip()
            and goal.verification_thread_id == goal.thread_id
        )

    def _completion_observation(self, goal_id, thread_id):
        """Collect slow input observations without a goal or task transaction."""
        observed = _CompletionObservation(self.get(goal_id), self.task_store, self.project_root)
        goal = observed.goal
        if (goal is None or goal.state != 'active'
                or goal.thread_id is not None and goal.thread_id != thread_id
                or any(row['reservations'] or not row['usage_complete']
                    for row in goal.pending_cost_rounds.values())):
            return observed
        from nailong.core.verification import current_evidence
        if observed.task_store is not None:
            task = observed.task_store.snapshot(thread_id) if thread_id else None
            observed.task = task
            if task is None or task['objective'] != goal.objective or task['lifecycle'] in {'paused', 'blocked'}:
                return observed
            if goal.verification_tool == 'verify' and (task['task_id'], task['revision']) != (
                    goal.verification_task_id, goal.verification_task_revision):
                return observed
            try:
                observed.current = current_evidence(observed.project_root, task,
                    observed.task_store.sessions.root)
            except (OSError, ValueError):
                pass
        if self._has_verification(goal) and goal.verification_tool == 'verify':
            try:
                observed.verification_current = current_evidence(goal.verification_project_root,
                    {'verification_generated_paths': goal.verification_generated_paths,
                     'verification_manifest': goal.verification_manifest}, self.path.parent)
            except (OSError, ValueError):
                pass
        return observed

    def record_verification(
        self,
        command: str,
        result: dict,
        *,
        thread_id: str | None = None,
    ) -> bool:
        """Record the real result returned by the run_command tool."""
        if self.project_root is not None:
            from nailong.core.preferences import read_config
            try:
                configured = read_config(self.project_root / '.nailong/settings.json', self.project_root).get('verification', {}).get('steps')
            except (OSError, ValueError, AttributeError):
                configured = True  # Invalid configuration cannot bypass complete verification.
            if configured:
                self.invalidate_verification(thread_id=thread_id)
                return False
        with self._locked():
            goals = self._load()
            goal = next(
                (item for item in reversed(list(goals.values())) if item.state == "active"),
                None,
            )
            if goal is None or (goal.thread_id is not None and goal.thread_id != thread_id):
                return False
            command = str(command)
            output = str(result.get("output") or result.get("error") or "")
            exit_code = result.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                exit_code = None
            if self.api_key:
                command = command.replace(self.api_key, "[密钥已隐藏]")
                output = output.replace(self.api_key, "[密钥已隐藏]")
            timed_out = bool(result.get("timed_out"))
            goal.verification_command = command[:2_000]
            goal.verification_output = (
                f"退出码: {exit_code if exit_code is not None else '未知'}；"
                f"超时: {'是' if timed_out else '否'}\n{output[:4_000]}"
            )
            goal.verification_exit_code = exit_code
            goal.verification_tool = "run_command"
            goal.verification_thread_id = thread_id
            goal.verification_recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            goal.verification_succeeded = bool(
                result.get("ok") is True and exit_code == 0 and not timed_out
            )
            goals[goal.id] = goal
            self._save(goals)
            return goal.verification_succeeded

    def invalidate_verification(self, *, thread_id: str | None = None) -> bool:
        """Clear prior proof after a successful file mutation in the goal thread."""
        with self._locked():
            goals = self._load()
            goal = next(
                (item for item in reversed(list(goals.values())) if item.state == "active"),
                None,
            )
            if goal is None or (goal.thread_id is not None and goal.thread_id != thread_id):
                return False
            if not goal.verification_tool and not goal.verification_command:
                return False
            self._clear_verification(goal)
            goals[goal.id] = goal
            self._save(goals)
            return True

    def update(
        self,
        goal_id: str,
        *,
        state: GoalState,
        summary: str = "",
        blocker: str | None = None,
        thread_id: str | None = None,
    ) -> tuple[bool, str, Goal | None]:
        if state not in {"active", "paused", "complete", "blocked"}:
            return False, "目标状态无效。", None
        observed = self._completion_observation(goal_id, thread_id) if state == 'complete' else None
        task_identity = ((observed.task['task_id'], observed.task['revision'])
            if observed is not None and observed.task is not None else None)
        binding = (observed.task_store.bound_task(thread_id, *task_identity)
            if task_identity is not None else nullcontext())
        # All combined task/goal mutations acquire the task first. No input
        # scan runs inside either transaction; compare the full saved states.
        with binding as bound_task, self._locked():
            goals = self._load()
            goal = goals.get(goal_id)
            if goal is None:
                return False, "找不到该目标。", None
            if goal.state != "active" and not (goal.state == "paused" and state == "paused"):
                return False, f"目标当前状态为 {goal.state}，不能继续更新。", goal
            if goal.thread_id is not None and goal.thread_id != thread_id:
                return False, "当前会话不属于此目标；请先恢复目标会话。", goal
            if observed is not None and (goal != observed.goal
                    or self.task_store is not observed.task_store
                    or self.project_root != observed.project_root):
                return False, '完成检查期间目标状态或验证记录已变化，请重新核对。', goal
            if observed is not None:
                from nailong.core.input_snapshot import active_snapshot_cancellation
                cancellation = active_snapshot_cancellation.get()
                if cancellation is not None and cancellation.is_set():
                    return False, '完成检查已取消，未提交目标完成状态。', goal
            if state=='complete' and any(row['reservations'] or not row['usage_complete']
                    for row in goal.pending_cost_rounds.values()):
                return False,'仍有在途或用量未知的模型费用，不能完成整体目标。',goal
            if self.api_key:
                summary = summary.replace(self.api_key, "[密钥已隐藏]")
                blocker = blocker.replace(self.api_key, "[密钥已隐藏]") if blocker else blocker
            if state=='complete' and getattr(self,'coordination_path',None) is not None:
                from nailong.core.preferences import read_config
                try:
                    coordination=read_config(self.coordination_path,self.coordination_path.parent)
                    if self.coordination_path.exists() and (coordination.get('version')!=1 or coordination.get('project')!=str(self.project_root) or not isinstance(coordination.get('leases'),dict)):
                        raise ValueError('协调状态身份无效。')
                    rows=coordination.get('dispatches',{})
                    if not isinstance(rows,dict) or any(row.get('goal_id')==goal.id and row.get('state') in {'queued','admitted','running','needs_reconciliation'} for row in rows.values()):
                        return False,'仍有未完成或待核对的派发，不能完成整体目标。',goal
                except (ValueError,OSError,TypeError,AttributeError):
                    return False,'无法核对派发控制状态，未完成整体目标。',goal
            current = observed.current if observed is not None else None
            if state == 'complete' and self.task_store is not None:
                from nailong.core.delivery import build_delivery_report
                task = observed.task
                if task is None or task['objective'] != goal.objective:
                    return False, '当前目标没有对应任务验收，请先核对 /task。', goal
                if task['lifecycle'] in {'paused', 'blocked'}:
                    return False, '当前任务已暂停或阻塞，请先明确恢复后再完成。', goal
                if goal.verification_tool == 'verify' and (task['task_id'], task['revision']) != (
                        goal.verification_task_id, goal.verification_task_revision):
                    self._clear_verification(goal)
                    self._save(goals)
                    return False, '目标验证不属于当前任务版本，请重新 /verify。', goal
            if state == "complete" and not self._has_verification(goal):
                return False, "完成目标必须先在当前目标会话中成功实际执行验证命令。", goal
            if state == "complete" and goal.verification_tool == "verify":
                if observed.verification_current != goal.verification_fingerprint:
                    self._clear_verification(goal)
                    self._save(goals)
                    return False, "项目输入已变化或摘要无法完整取得，请重新 /verify。", goal
            if state == "blocked":
                blocker = (blocker or "").strip()
                if not blocker:
                    return False, "标记 blocked 必须说明阻塞原因。", goal
                if goal.blocker_observed_round != goal.round:
                    if blocker == goal.last_blocker and goal.blocker_observed_round == goal.round - 1:
                        goal.blocked_rounds += 1
                    else:
                        goal.last_blocker = blocker
                        goal.blocked_rounds = 1
                    goal.blocker_observed_round = goal.round
                if goal.blocked_rounds < 3:
                    self._save(goals)
                    return False, f"相同阻塞原因已连续记录 {goal.blocked_rounds}/3 轮；目标仍保持 active。", goal
            if task_identity is not None:
                if bound_task != observed.task:
                    self._clear_verification(goal)
                    self._save(goals)
                    return False, '完成前任务身份、要求或状态已变化，请重新核对 /task 并验证。', goal
                task = self.task_store.reconcile(thread_id, current_input_fingerprint=current)
                report = build_delivery_report(task, current_input_fingerprint=current)
                if report['status'] not in {'verified', 'reviewed'}:
                    return False, '目标验收尚未满足；配置命令成功不能替代功能目标覆盖，请查看 /task。', goal
                self.task_store.set_state(thread_id, phase='deliver', lifecycle='completed',
                    current_input_fingerprint=current)
            goal.state = state
            goal.last_summary = summary.replace(self.api_key, "[密钥已隐藏]") if self.api_key else summary
            goal.pause_reason = summary if state == "paused" else ""
            goals[goal.id] = goal
            self._save(goals)
            return True, f"目标状态已更新为 {state}。", goal

    def record_round(
        self,
        goal_id: str,
        *,
        cost_usd: float,
        files_changed: bool,
        tool_calls: int,
        summary: str = "",
        unattended_approval: bool = False,
        round_id: str | None = None,
        stop_reason: str = "",
        progress: dict | None = None,
    ) -> Goal | None:
        with self._locked():
            goals = self._load()
            goal = goals.get(goal_id)
            if goal is None:
                return goal
            if round_id and round_id in goal.settled_round_ids:
                return goal
            completed = goal.state == "complete"
            blocked = goal.state == "blocked"
            paused = goal.state == "paused"
            from nailong.core.goal_costs import finish_cost_round
            additional_cost,unknown_cost=finish_cost_round(goal,round_id,cost_usd)
            if unknown_cost:goal.cost_recovery_required=True
            if unknown_cost and not stop_reason:
                stop_reason='模型用量未知或仍有未结算预留，已保守计费并暂停目标。'
            goal.round += 1
            goal.spent_usd += additional_cost
            if round_id:
                goal.settled_round_ids.append(round_id)
            if progress is not None:
                from nailong.core.goal_control import PROGRESS_WINDOW
                from nailong.core.delivery import _known
                # Retain the compact assessment, never raw task/tool output.
                progress = progress if isinstance(progress, dict) else {}
                keys = ('status', 'reason', 'task_id', 'task_revision', 'project_root',
                    'current_input_fingerprint', 'evidence_ids', 'acceptance_ids')
                record = {key: progress.get(key) for key in keys}
                for key in ('evidence_ids', 'acceptance_ids'):
                    values = record[key]
                    record[key] = [value[:200] for value in values[:64] if isinstance(value, str)] if isinstance(values, list) else []
                for key in ('status', 'reason', 'task_id', 'project_root', 'current_input_fingerprint'):
                    value = record[key]
                    record[key] = (value.replace(self.api_key, '[密钥已隐藏]') if self.api_key else value)[:2048] if isinstance(value, str) else None
                if type(record['task_revision']) is not int or record['task_revision'] < 1:
                    record['task_revision'] = None
                valid_binding = (record['task_revision'] is not None and all(_known(record[key])
                    for key in ('task_id', 'project_root', 'current_input_fingerprint')))
                if (record['status'] not in {'progress', 'no_change', 'unknown'}
                        or record['status'] in {'progress', 'no_change'} and not valid_binding
                        or record['status'] == 'progress' and not (record['evidence_ids'] or record['acceptance_ids'])):
                    record['status'] = 'unknown'
                    record['reason'] = 'invalid_progress_assessment'
                binding = ('task_id', 'task_revision', 'project_root', 'current_input_fingerprint')
                same = all(record[key] == goal.last_progress.get(key) for key in binding)
                goal.no_change_rounds = (min(PROGRESS_WINDOW, goal.no_change_rounds + 1) if same else 1) if record['status'] == 'no_change' else 0
                goal.last_progress = record
                goal.recent_progress = (goal.recent_progress + [{**record, 'round_id': round_id}])[-PROGRESS_WINDOW:]
            goal.last_summary = summary.replace(self.api_key, "[密钥已隐藏]") if self.api_key else summary
            if goal.spent_usd >= goal.max_cost_usd:
                goal.state = "paused"
                goal.pause_reason = "已达到目标成本上限。"
            elif unattended_approval:
                goal.state = "paused"
                goal.pause_reason = "遇到需要用户审批的操作；无人值守模式暂停目标。"
            elif stop_reason:
                goal.state = "paused"
                goal.pause_reason = stop_reason.replace(self.api_key, "[密钥已隐藏]") if self.api_key else stop_reason
            elif paused:
                pass
            elif completed or blocked:
                pass
            elif goal.round >= goal.max_rounds:
                goal.state = "paused"
                goal.pause_reason = "已达到目标轮数上限。"
            elif not files_changed and tool_calls <= 0:
                goal.idle_rounds += 1
                if goal.idle_rounds >= 2:
                    goal.state = "paused"
                    goal.pause_reason = "连续两轮没有文件变更或工具调用，已停止空转。"
            else:
                goal.idle_rounds = 0
            goals[goal.id] = goal
            self._save(goals)
            return goal
