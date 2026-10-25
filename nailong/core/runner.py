"""One serialized writer per session, with bounded input and explicit resume."""
from __future__ import annotations
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable

_active_runner_inputs=ContextVar('active_session_runner_inputs',default=())

@dataclass
class WorkItem:
    message: str
    execute: Callable
    project_root: str
    thread_id: str
    future: asyncio.Future

class SessionRunner:
    def __init__(self,project_root,thread_id):
        self.project_root=str(project_root); self.thread_id=str(thread_id)
        self.state='idle'; self.queue=[]; self.current_item=None
        self._pump_task=None; self._current_task=None; self._current_input=None; self._stop_task=None; self._closed=False
    @property
    def _stopping(self):
        return self._stop_task is not None and not self._stop_task.done()
    @property
    def busy(self): return self.current_item is not None or bool(self.queue) or self.state=='cancelling' or self._stopping
    def submit(self,message,execute,*,project_root=None,thread_id=None):
        if self._closed: raise ValueError('会话队列已关闭。')
        project=str(project_root or self.project_root); thread=str(thread_id or self.thread_id)
        if (project,thread)!=(self.project_root,self.thread_id): raise ValueError('待执行输入属于不同的项目或会话。')
        self.queue[:]=[item for item in self.queue if not item.future.cancelled()]
        if len(self.queue)>=10: raise ValueError('输入队列已满（10 项）。请先删除或完成待执行项。')
        future=asyncio.get_running_loop().create_future()
        # Avoid unhandled-future warnings while preserving awaitable errors for callers.
        future.add_done_callback(self._future_done)
        self.queue.append(WorkItem(message,execute,project,thread,future))
        self._start()
        return future
    def _start(self):
        if not self._closed and not self._stopping and self.state!='paused' and (self._pump_task is None or self._pump_task.done()):
            self._stop_task=None
            self._pump_task=asyncio.create_task(self._pump())
    def _future_done(self,future):
        if not future.cancelled():
            future.exception()
        else:
            self.queue[:]=[item for item in self.queue if not item.future.cancelled()]
            if self.current_item is not None and self.current_item.future is future:
                # Withdrawing a running input uses the same owned stop as /stop;
                # the caller can join it with stop(), wait_idle(), or close().
                self._request_stop()
    async def _execute(self,item,identity):
        token=_active_runner_inputs.set((*_active_runner_inputs.get(),(self,identity)))
        try:return await item.execute()
        finally:_active_runner_inputs.reset(token)
    def _self_control(self):
        return asyncio.current_task() is self._current_task or any(
            runner is self and identity is self._current_input
            for runner,identity in _active_runner_inputs.get())
    async def _pump(self):
        cleanup_error=None
        while self.queue and self.state not in {'paused', 'cancelling'} and not self._closed:
            self.state='running'; self.current_item=self.queue.pop(0)
            item=self.current_item
            try:
                if item.future.cancelled():continue
                self._current_input=object()
                self._current_task=asyncio.create_task(self._execute(item,self._current_input))
                result=await self._current_task
                if not item.future.done(): item.future.set_result(result)
            except asyncio.CancelledError:
                self.state='paused'
                if not item.future.done(): item.future.cancel()
            except Exception as error:
                if not item.future.done(): item.future.set_exception(error.with_traceback(None))
                else:
                    # A cancelled Future cannot carry a late cleanup failure.
                    # Keep it on this pump's result for every owned joiner.
                    cleanup_error=error.with_traceback(None)
                    self._request_stop()
            finally:
                # Future callbacks may run after this pump wakes up from the
                # child. Honor cancellation here before admitting another item.
                if self._current_task is not None and item.future.cancelled():
                    self._request_stop()
                self.current_item=None; self._current_task=None; self._current_input=None
        if self.state=='cancelling': self.state='paused'
        elif self.state!='paused': self.state='idle'
        return cleanup_error
    def _request_stop(self):
        if self._stopping:return self._stop_task
        pump=self._pump_task
        self.state='cancelling' if pump is not None and not pump.done() else 'paused'
        if self._current_task is not None:
            self._current_task.cancel()
        self._stop_task=asyncio.create_task(self._finish_stop(pump))
        # Retrieval prevents an unhandled-task warning when Future.cancel()
        # initiated the stop; awaiting the task still reports the saved error.
        self._stop_task.add_done_callback(lambda task:task.exception() if not task.cancelled() else None)
        return self._stop_task
    async def _finish_stop(self,pump):
        result=None
        if pump is not None:
            result=(await asyncio.gather(pump,return_exceptions=True))[0]
        self.state='paused'
        if isinstance(result,BaseException):raise result
    async def stop(self):
        await self.begin_stop()
    def begin_stop(self):
        """Establish the stop barrier now; return an owned, cancellable waiter.

        Synchronous controllers such as signal callbacks can schedule the waiter
        later without leaving admission open or accidentally joining a new stop.
        """
        if self._self_control():
            self.state='paused'
            raise RuntimeError('运行中的输入不能等待自身停止；请从队列外调用 stop。')
        stopping=self._request_stop()
        return self._wait_stop(stopping)
    async def _wait_stop(self,stopping):
        # The coroutine may have been created in a controller and then handed
        # to the very input whose cleanup this owner is joining.
        if not stopping.done() and self._self_control():
            raise RuntimeError('运行中的输入不能等待自身停止；请从队列外等待 stop。')
        try:
            await asyncio.shield(stopping)
        except asyncio.CancelledError as cancelled:
            # A controller cancellation must not let an outer finally close
            # model/checkpoint resources while the current input is cleaning up.
            from nailong.core.process_io import join_cleanup
            try:await join_cleanup(stopping)
            except Exception as error:raise cancelled from error
            raise
        return self._stop_task is stopping and self.state=='paused'
    def resume(self):
        if self._closed: raise ValueError('会话队列已关闭。')
        if self.state=='cancelling' or self._stopping: raise ValueError('当前操作尚未停止。')
        if self.current_item: raise ValueError('当前任务仍在运行。')
        self.state='idle'; self._start()
    def remove(self,index):
        if not 1<=index<=len(self.queue): raise ValueError('待执行项序号无效。')
        item=self.queue.pop(index-1); item.future.cancel(); return item.message
    def clear(self):
        for item in self.queue: item.future.cancel()
        self.queue.clear()
    def rebind(self,project_root,thread_id):
        if self._closed: raise ValueError('会话队列已关闭。')
        if self.busy: raise ValueError('请先 /stop 并清空待执行输入，再切换项目或会话。')
        self.project_root=str(project_root); self.thread_id=str(thread_id); self.state='idle'
    async def wait_idle(self):
        if self._self_control():
            raise RuntimeError('运行中的输入不能等待自身完成；请从队列外调用 wait_idle。')
        while True:
            pump=self._pump_task
            result=await asyncio.shield(pump) if pump is not None else None
            if self._pump_task is pump and self._stop_task is not None:await asyncio.shield(self._stop_task)
            if isinstance(result,BaseException):raise result
            if self._pump_task is pump:return
    async def close(self):
        self._closed=True
        self.clear()
        try:await self.stop()
        finally:self.clear()
