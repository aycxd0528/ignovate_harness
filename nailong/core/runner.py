"""One serialized writer per session, with bounded input and explicit resume."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Callable

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
        self._pump_task=None; self._current_task=None; self._closed=False
    @property
    def busy(self): return self.current_item is not None or bool(self.queue) or self.state=='cancelling'
    def submit(self,message,execute,*,project_root=None,thread_id=None):
        if self._closed: raise ValueError('会话队列已关闭。')
        project=str(project_root or self.project_root); thread=str(thread_id or self.thread_id)
        if (project,thread)!=(self.project_root,self.thread_id): raise ValueError('待执行输入属于不同的项目或会话。')
        if len(self.queue)>=10: raise ValueError('输入队列已满（10 项）。请先删除或完成待执行项。')
        future=asyncio.get_running_loop().create_future()
        # Avoid unhandled-future warnings while preserving awaitable errors for callers.
        future.add_done_callback(lambda item: item.exception() if not item.cancelled() else None)
        self.queue.append(WorkItem(message,execute,project,thread,future))
        self._start()
        return future
    def _start(self):
        if self.state!='paused' and (self._pump_task is None or self._pump_task.done()):
            self._pump_task=asyncio.create_task(self._pump())
    async def _pump(self):
        while self.queue and self.state not in {'paused', 'cancelling'} and not self._closed:
            self.state='running'; self.current_item=self.queue.pop(0)
            item=self.current_item
            try:
                self._current_task=asyncio.create_task(item.execute())
                result=await self._current_task
                if not item.future.done(): item.future.set_result(result)
            except asyncio.CancelledError:
                self.state='paused'
                if not item.future.done(): item.future.cancel()
            except Exception as error:
                if not item.future.done(): item.future.set_exception(error.with_traceback(None))
            finally:
                self.current_item=None; self._current_task=None
        if self.state=='cancelling': self.state='paused'
        elif self.state!='paused': self.state='idle'
    async def stop(self):
        already_cancelling=self.state=='cancelling'
        self.state='cancelling' if self._current_task else 'paused'
        if self._current_task and not already_cancelling:
            self._current_task.cancel()
        if self._pump_task:
            await asyncio.shield(asyncio.gather(self._pump_task,return_exceptions=True))
        self.state='paused'
    def resume(self):
        if self._closed: raise ValueError('会话队列已关闭。')
        if self.state=='cancelling': raise ValueError('当前操作尚未停止。')
        if self.current_item: raise ValueError('当前任务仍在运行。')
        self.state='idle'; self._start()
    def remove(self,index):
        if not 1<=index<=len(self.queue): raise ValueError('待执行项序号无效。')
        item=self.queue.pop(index-1); item.future.cancel(); return item.message
    def clear(self):
        for item in self.queue: item.future.cancel()
        self.queue.clear()
    def rebind(self,project_root,thread_id):
        if self.busy: raise ValueError('请先 /stop 并清空待执行输入，再切换项目或会话。')
        self.project_root=str(project_root); self.thread_id=str(thread_id); self.state='idle'
    async def wait_idle(self):
        if self._pump_task: await asyncio.shield(self._pump_task)
    async def close(self):
        self._closed=True
        await self.stop(); self.clear()
