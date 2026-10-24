"""Finite native lifecycle fault probe; its parent owns the temporary directory."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import traceback
from unittest.mock import patch

import aiosqlite
from agent import AsyncSqliteSaver
from nailong.core.runtime_resources import RuntimeResourceOwner


def run(root,mode):
    entered=threading.Event();finished=threading.Event()
    connections=[];workers=[];thread_errors=[];loop_errors=[];operations=[];operation_errors=[];retry_state={}
    native_connect=sqlite3.connect;native_thread_start=threading.Thread.start
    native_execute=aiosqlite.Connection._execute
    original_setup=AsyncSqliteSaver.setup
    closing=mode in ('global-discard','global-close','locked-close')
    setup_entered=None
    native_close_tasks=[]
    delayed_stop=mode in ('open-stop-global','open-stop-normal')
    stop_finished=threading.Event()
    if delayed_stop:
        def trace(frame,event,argument):
            if (event=='call' and frame.f_code.co_name=='close_and_stop'
                    and frame.f_code.co_filename==aiosqlite.core.__file__):
                time.sleep(.2);stop_finished.set()
            return None
        threading.settrace(trace)
    threading.excepthook=lambda args:thread_errors.append(str(args.exc_value))
    owner=RuntimeResourceOwner();owner._init_resource_owner()
    if mode=='factory-open':
        from agent import AgentRuntimeFactory
        from config import Settings
        from nailong.core.sessions import ProjectSessionStore
        settings=Settings('offline-fixture','https://api.invalid','deepseek-chat',root)
        store=ProjectSessionStore(root,base_dir=root/'state')
        with patch('agent.ChatDeepSeek',return_value=object()):owner=AgentRuntimeFactory(settings,session_store=store)

    def connect(*args,**kwargs):
        if not closing:entered.set();time.sleep(.15)
        try:
            if mode=='open-failure':raise OSError('native-open-failure')
            if delayed_stop:return native_connect(str(root/'absent'/'db.sqlite'),**kwargs)
            if mode=='worker-close-failure-retry':
                class CloseFailure(sqlite3.Connection):
                    def close(self):
                        self.close_attempts=getattr(self,'close_attempts',0)+1
                        if self.close_attempts<=2:raise OSError('worker-native-close-failure')
                        return super().close()
                kwargs['factory']=CloseFailure
            connection=native_connect(*args,**kwargs)
            connections.append(connection)
            return connection
        finally:
            if not closing:finished.set()

    def start_thread(thread,*args,**kwargs):
        target=getattr(thread,'_target',None)
        if target is getattr(aiosqlite.core,'_connection_worker_thread',None) or isinstance(thread,aiosqlite.Connection):
            workers.append(thread)
        return native_thread_start(thread,*args,**kwargs)

    async def execute(connection,function,*args,**kwargs):
        if (closing and getattr(function,'__name__','')=='close'
                and isinstance(getattr(function,'__self__',None),sqlite3.Connection)):
            native_close_tasks.append(asyncio.current_task().get_name())
            def delayed_close():
                entered.set();time.sleep(.15)
                try:return function(*args,**kwargs)
                finally:finished.set()
            return await native_execute(connection,delayed_close)
        return await native_execute(connection,function,*args,**kwargs)

    async def setup(saver):
        if mode=='setup-failure':raise OSError('native-setup-failure')
        return await original_setup(saver)

    async def main():
        asyncio.get_running_loop().set_exception_handler(lambda loop,context:loop_errors.append(str(context.get('exception',context.get('message')))))
        acquire=lambda:owner._get_async_saver(lambda:AsyncSqliteSaver.from_conn_string(str(root/'state.sqlite')))
        if mode in ('close-failure-retry','worker-close-failure-retry'):
            saver=await acquire();native_execute=saver.conn._execute;attempts=[]
            async def flaky_execute(function,*args,**kwargs):
                if mode=='close-failure-retry' and getattr(function,'__name__','')=='close':
                    attempts.append('close');raise OSError('queued-native-close-failure')
                return await native_execute(function,*args,**kwargs)
            with patch.object(saver.conn,'_execute',new=flaky_execute):
                try:await owner.aclose()
                except BaseException as error:retry_state['failure_type']=type(error).__name__
                retry_state['published_retained']=owner.async_checkpointer is saver
                retry_state['native_open']=all(connection.in_transaction is not None for connection in connections)
                retry_state['proxy_kept_native']=saver.conn._connection is connections[0]
                retry_state['worker_retained']=workers[0].is_alive()
            operation=asyncio.create_task(owner.aclose());operations.append(operation)
            await operation
            retry_state['attempts']=len(attempts) or getattr(connections[0],'close_attempts',0)
            return
        if mode in ('global-close','locked-close'):
            saver=await acquire()
            if mode=='locked-close':
                await saver.lock.acquire()
                async def writer():
                    try:await asyncio.sleep(10)
                    finally:saver.lock.release()
                asyncio.create_task(writer())
            operation=asyncio.create_task(owner.aclose())
            operations.append(operation)
            if mode=='locked-close':
                while owner._resource_close_task is None:await asyncio.sleep(0)
                await asyncio.sleep(.01)
                return
        elif mode=='global-discard':
            setup_entered=asyncio.Event();native_setup=AsyncSqliteSaver.setup
            async def setup(saver):
                setup_entered.set();await asyncio.sleep(.03)
                await native_setup(saver)
            with patch.object(AsyncSqliteSaver,'setup',new=setup):
                operation=asyncio.create_task(acquire());operations.append(operation)
                await setup_entered.wait();operation.cancel()
                assert await asyncio.to_thread(entered.wait,2),'native discard never entered'
                return
        else:
            async def request():
                try:
                    if mode=='caller-timeout':
                        async with asyncio.timeout(.03):await acquire()
                    else:
                        await (owner.async_runtime(thread_id='native-shutdown') if mode=='factory-open' else acquire())
                except BaseException as error:
                    chain=[]
                    for _ in range(6):
                        chain.append(type(error).__name__+':'+str(error))
                        error=error.__cause__
                        if error is None:break
                    operation_errors.append(chain)
                    raise
            operation=asyncio.create_task(request());operations.append(operation)
        assert await asyncio.to_thread(entered.wait,2),'native lifecycle operation was never entered'
        if mode in ('caller-cancel','repeated-cancel'):
            operation.cancel()
            if mode=='repeated-cancel':
                for _ in range(3):
                    await asyncio.sleep(.01);operation.cancel()
            await asyncio.gather(operation,return_exceptions=True)
        elif mode in ('caller-timeout','open-stop-normal'):await asyncio.gather(operation,return_exceptions=True)

    with patch('sqlite3.connect',side_effect=connect),patch.object(threading.Thread,'start',new=start_thread), \
            patch.object(aiosqlite.Connection,'_execute',new=execute), \
            patch.object(AsyncSqliteSaver,'setup',new=setup):
        asyncio.run(main())
    complete_at_exit=finished.is_set()
    stopped_at_exit=all(not worker.is_alive() for worker in workers)
    delayed_stop_at_exit=stop_finished.is_set() if delayed_stop else True
    for worker in workers:worker.join(timeout=2)
    def closed(connection):
        try:connection.in_transaction
        except sqlite3.ProgrammingError:return True
        return False
    facts={'native_close_tasks':native_close_tasks,'connections':len(connections),'native_closed':[closed(connection) for connection in connections],
        'operation_complete_at_loop_exit':complete_at_exit,'workers_stopped_at_loop_exit':stopped_at_exit,
        'stop_delay_finished_at_loop_exit':delayed_stop_at_exit,'worker_count':len(workers),'unpublished':len(owner._unpublished_savers),
        'published':owner.async_checkpointer is not None,'workers_alive':sum(worker.is_alive() for worker in workers),
        'thread_errors':thread_errors,'loop_errors':loop_errors,'operation_errors':operation_errors,'retry':retry_state,
        'caller_cancelled':all(operation.cancelled() for operation in operations),
        'caller_timeout':any(not operation.cancelled() and isinstance(operation.exception(),TimeoutError) for operation in operations)}
    if mode=='factory-open':owner.close()
    print(json.dumps(facts),flush=True)


if __name__=='__main__':
    try:run(Path(sys.argv[1]),sys.argv[2])
    except BaseException:
        traceback.print_exc();os._exit(70)
    # Even a failed implementation cannot leak native handles beyond the probe.
    # Tests inspect the real retained handles before this finite-process escape.
    os._exit(0)
