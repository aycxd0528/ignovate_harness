"""Ensure generation-time typing and approvals never read the terminal concurrently."""
import asyncio
from contextlib import asynccontextmanager

class ApprovalInterrupted(Exception):
    pass

class InputInterrupted(Exception):
    """A terminal key event, safe to deliver across an asyncio Task boundary."""
    pass

class InteractionCancelled(asyncio.CancelledError):
    """A user's cancelled local prompt, distinct from external task cancellation."""
    pass

class InputBroker:
    def __init__(self,session):
        self.session=session; self._main=None; self.ready=asyncio.Event();self.ready.set()
        self._approval_lock=asyncio.Lock(); self._draft=''; self._interrupted_tasks=set()
    async def _read(self,message,**kwargs):
        # KeyboardInterrupt escaping a child Task aborts the entire event loop.
        try: return await self.session.prompt_async(message,**kwargs)
        except KeyboardInterrupt: raise InputInterrupted() from None
    async def prompt_async(self,message,**kwargs):
        # Approval callers use the same broker but have a different prompt label.
        if message=='> ':
            await self.ready.wait()
            task=asyncio.create_task(self._read(message,**kwargs))
            self._main=task
            try: return await task
            except asyncio.CancelledError:
                if task in self._interrupted_tasks:
                    self._interrupted_tasks.discard(task)
                    raise ApprovalInterrupted() from None
                raise
            finally:
                if self._main is task: self._main=None
        async with self.interaction() as prompt:
            return await prompt(message,**kwargs)

    @asynccontextmanager
    async def interaction(self):
        """Keep the main composer suspended for an entire multi-step input flow."""
        async with self._approval_lock:
            self.ready.clear()
            buffer=getattr(self.session,'default_buffer',None)
            self._draft=getattr(buffer,'text','')
            try:
                if self._main and not self._main.done():
                    self._interrupted_tasks.add(self._main)
                    self._main.cancel()
                    await asyncio.gather(self._main,return_exceptions=True)
                yield self._read
            except InputInterrupted:
                raise InteractionCancelled('输入操作已取消。') from None
            finally:
                if buffer is not None and self._draft: buffer.text=self._draft
                self.ready.set()
    @property
    def interrupted(self): return not self.ready.is_set()

    async def edit(self,markdown):
        from nailong.core.plan import edit_plan_with_editor_async
        async with self.interaction():
            return await edit_plan_with_editor_async(markdown)
