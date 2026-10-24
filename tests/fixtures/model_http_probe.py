"""Finite default-SDK native HTTP lifecycle probe; all traffic is loopback."""
import asyncio
import json
import os
from pathlib import Path
import socketserver
import ssl
import sys
import threading
import time
from unittest.mock import patch

from agent import AgentRuntimeFactory, ChatDeepSeek
from config import Settings
from nailong.core.sessions import ProjectSessionStore


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(4)
        try:
            while True:
                data=b''
                while b'\r\n\r\n' not in data:
                    chunk=self.request.recv(4096)
                    if not chunk:return
                    data+=chunk
                time.sleep(.2 if self.server.mode=='sdk-timeout' else .02)
                connection=b'close' if self.server.mode in ('churn','retirement-failure') else b'keep-alive'
                self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: '+connection+b'\r\n\r\nOK')
        except OSError:pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads=True


def run(root,mode):
    fd_before=len(os.listdir('/dev/fd')) if Path('/dev/fd').exists() else None
    threads_before={thread.ident for thread in threading.enumerate()}
    parts=mode.split('-');legacy='legacy' in parts;tls='tls' in parts;synchronous='sync' in parts
    mode='-'.join(part for part in parts if part not in ('legacy','tls','sync'))
    server=Server(('127.0.0.1',0),Handler)
    server.mode=mode
    certificate=Path(__file__).with_name('model_http_test_tls.pem')
    if tls and mode!='handshake-timeout':
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(certificate)
        server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'{"https" if tls else "http"}://127.0.0.1:{server.server_address[1]}'
    settings=Settings('offline-fixture',url,'deepseek-chat',root)
    store=ProjectSessionStore(root,base_dir=root/'state')
    if legacy or tls:
        import httpx
        import httpx2
        module=httpx if legacy else httpx2
        verify=ssl.create_default_context(cafile=str(certificate)) if tls and mode!='untrusted' else True
        def build(*args,**kwargs):
            return ChatDeepSeek(*args,**kwargs,http_client=module.Client(verify=verify,trust_env=False),
                http_async_client=module.AsyncClient(verify=verify,trust_env=False))
        with patch('agent.ChatDeepSeek',side_effect=build):factory=AgentRuntimeFactory(settings,session_store=store)
    else:factory=AgentRuntimeFactory(settings,session_store=store)
    sdk=factory.model.root_client if synchronous else factory.model.root_async_client
    child=None
    if mode=='child' or mode.startswith('concurrent-child'):
        child=factory._own_model(ChatDeepSeek(model=settings.model,api_key=settings.api_key,base_url=url))
        sdk=child.root_client if synchronous else child.root_async_client
    client=sdk._client
    raw=[];facts={};operations=[];attempts=[];loop_errors=[];timers=[]
    def check_sync_guard():
        try:factory.close()
        except BaseException as error:facts['sync_close_error']=type(error).__name__+':'+str(error)
        facts['factory_marked_closing']=factory._resources_closing
        try:factory.checkpointer.conn.execute('SELECT 1');facts['sqlite_still_open']=True
        except Exception as error:
            if type(error).__name__!='ProgrammingError':raise
            facts['sqlite_still_open']=False
    async def main():
        asyncio.get_running_loop().set_exception_handler(lambda loop,context:loop_errors.append(str(context.get('exception',context.get('message')))))
        if mode in ('untrusted','handshake-timeout'):
            owner=factory._model_http_owners[id(sdk)];original_add=owner.add
            def add(stream):
                raw.append(stream.get_extra_info('socket'));original_add(stream)
            with patch.object(owner,'add',new=add):
                try:
                    if mode=='untrusted':await client.get(url+'/untrusted')
                    else:await sdk.with_options(timeout=.03,max_retries=0).get(url+'/handshake',cast_to=str)
                except Exception as error:facts['request_error']=type(error).__name__
                else:raise AssertionError('TLS failure or SDK deadline was disabled')
            assert len(owner._snapshot())==0,'failed TLS stream ownership did not settle'
            await factory.aclose();return
        if synchronous and tls and mode in ('close-race','close-race-global'):
            pool=client._transport_for_url(type(client.base_url)(url))._pool
            native=pool._network_backend._native
            original_connect=native.connect_tcp;entered=threading.Event();release=threading.Event()
            def connect(*args,**kwargs):
                stream=original_connect(*args,**kwargs);original_tls=stream.start_tls
                def upgrade(*args,**kwargs):
                    upgraded=original_tls(*args,**kwargs)
                    raw.append(upgraded.get_extra_info('socket'));entered.set()
                    assert release.wait(3),'TLS gate not released'
                    return upgraded
                stream.start_tls=upgrade;return stream
            with patch.object(native,'connect_tcp',new=connect):
                request=asyncio.create_task(asyncio.to_thread(client.get,url+'/tls-race'))
                assert await asyncio.to_thread(entered.wait,3),'TLS upgrade did not finish'
                timer=threading.Timer(.15,release.set)
                timers.append(timer)
                tick=asyncio.Event();close_entered=threading.Event()
                sync_owner=factory._model_http_owners[id(sdk)];original_close=sync_owner.close
                def close_owner():
                    close_entered.set();timer.start();return original_close()
                async def ticker():
                    while not close_entered.is_set():await asyncio.sleep(0)
                    await asyncio.sleep(.02);tick.set()
                ticker_task=asyncio.create_task(ticker())
                try:
                    with patch.object(sync_owner,'close',new=close_owner):
                        if mode=='close-race-global':
                            operation=asyncio.create_task(factory.aclose());operations.append(operation)
                            assert await asyncio.to_thread(close_entered.wait,2),'native sync close never entered'
                            return
                        await factory.aclose()
                    facts['closed_at_factory_return']=[sock.fileno()==-1 for sock in raw]
                    facts['loop_progressed_during_close']=tick.is_set()
                finally:
                    if mode!='close-race-global':release.set();timer.join(timeout=1)
                await ticker_task
                await asyncio.gather(request,return_exceptions=True)
            return
        if mode=='retirement-failure':
            pool=client._transport_for_url(type(client.base_url)(url))._pool
            backend=pool._network_backend;native=backend._native;original=native.connect_tcp
            async def connect(*args,**kwargs):
                stream=await original(*args,**kwargs);raw.append(stream.get_extra_info('socket'))
                native_close=stream.aclose
                async def fail():
                    attempts.append('close')
                    if len(attempts)<=2:raise OSError('retired-native-close-failure')
                    await native_close()
                stream.aclose=fail
                return stream
            with patch.object(native,'connect_tcp',new=connect):
                try:await client.get(url+'/retire')
                except OSError:pass
                else:raise AssertionError('native close failure was hidden')
                try:await client.get(url+'/must-not-connect')
                except RuntimeError:facts['sealed']=True
                else:raise AssertionError('unverified native close allowed another request')
                try:await factory.aclose()
                except OSError:pass
                await factory.aclose()
            return
        if mode in ('connecting-close','direct-sdk-connecting-close'):
            pool=client._transport_for_url(type(client.base_url)(url))._pool
            backend=pool._network_backend;native=backend._native;original=native.connect_tcp
            entered=asyncio.Event();release=asyncio.Event()
            async def connect(*args,**kwargs):
                entered.set();await release.wait()
                stream=await original(*args,**kwargs);raw.append(stream.get_extra_info('socket'))
                return stream
            with patch.object(native,'connect_tcp',new=connect):
                request=asyncio.create_task(client.get(url+'/connecting'))
                await entered.wait()
                if mode=='direct-sdk-connecting-close':check_sync_guard()
                closing=asyncio.create_task(factory.aclose())
                await asyncio.sleep(.02);assert not closing.done(),'close lost in-flight connection'
                release.set();await closing
                await asyncio.gather(request,return_exceptions=True)
                try:await backend.connect_tcp(host='127.0.0.1',port=server.server_address[1])
                except RuntimeError:facts['sealed']=True
                else:raise AssertionError('closed backend admitted a new connection')
            return
        if mode=='churn':
            owner=factory._model_http_owners[id(sdk)]
            for _ in range(12):
                await client.get(url+'/churn')
                assert len(owner._snapshot())==0,'closed stream receipts accumulated'
            await factory.aclose();facts['churn']=12;return
        if mode=='sdk-timeout':
            from openai import APITimeoutError
            # Use the real SDK request/retry layer, with only a local endpoint.
            try:await sdk.with_options(timeout=.03,max_retries=0).get(url+'/slow',cast_to=str)
            except APITimeoutError:facts['sdk_timeout']=True
            else:raise AssertionError('SDK timeout was disabled')
            owner=factory._model_http_owners[id(sdk)]
            raw.extend(stream.get_extra_info('socket') for stream in owner._snapshot())
            await factory.aclose();return
        if synchronous:
            await asyncio.to_thread(lambda:[client.get(url+'/one'),client.get(url+'/two')])
        else:
            responses=await asyncio.gather(client.get(url+'/one'),client.get(url+'/two'))
            assert all(response.text=='OK' and response.headers['connection']=='keep-alive' for response in responses)
        # Use this client's URL type: installed OpenAI may use httpx or httpx2.
        pool=client._transport_for_url(type(client.base_url)(url))._pool
        retained=list(pool.connections)
        assert len(retained)==(1 if synchronous else 2),len(retained)
        raw.extend(c._connection._network_stream.get_extra_info('socket') for c in retained)
        if mode=='direct-sdk-close':
            check_sync_guard();await factory.aclose();return
        if mode.startswith('concurrent-child'):
            owner=factory._model_http_owners[id(sdk)]
            native_close=owner.close if synchronous else owner.aclose
            entered=threading.Event() if synchronous else asyncio.Event()
            release=threading.Event() if synchronous else asyncio.Event()
            second=asyncio.Event();calls=[]
            failure=OSError('shared-child-close-failure')
            failing=mode.endswith('failure')
            if synchronous:
                def close():
                    calls.append('close');entered.set()
                    assert release.wait(3),'concurrent sync close gate not released'
                    if failing:raise failure
                    native_close()
                helper=factory._aclose_sync_model_client;entries=[]
                async def factory_entry(client):
                    if client is sdk:
                        entries.append(client)
                        if len(entries)==2:second.set()
                    return await helper(client)
                helper_name='_aclose_sync_model_client'
            else:
                async def close():
                    calls.append('close');entered.set();await release.wait()
                    if failing:raise failure
                    await native_close()
                helper=factory._aclose_async_model_client;entries=[]
                async def factory_entry(client):
                    if client is sdk:
                        entries.append(client)
                        if len(entries)==2:second.set()
                    return await helper(client)
                helper_name='_aclose_async_model_client'
            with patch.object(owner,'close' if synchronous else 'aclose',new=close),patch.object(factory,helper_name,new=factory_entry):
                child_cleanup=asyncio.create_task(factory._release_model(child))
                if synchronous:assert await asyncio.to_thread(entered.wait,3),'child close not entered'
                else:await entered.wait()
                closing=asyncio.create_task(factory.aclose())
                await asyncio.wait_for(second.wait(),3);release.set()
                results=await asyncio.gather(child_cleanup,closing,return_exceptions=True)
            facts['concurrent_close_errors']=[type(result).__name__+':'+str(result) for result in results if isinstance(result,BaseException)]
            facts['shared_failure']=all(result is failure for result in results) if failing else False
            facts['child_close_calls']=len(calls)
            try:factory.checkpointer.conn.execute('SELECT 1');facts['sqlite_closed']=False
            except Exception as error:
                if type(error).__name__!='ProgrammingError':raise
                facts['sqlite_closed']=True
            facts['sync_clients_retained']=len(factory._owned_sync_clients)
            facts['native_open_after_failure']=[sock.fileno()>=0 for sock in raw]
            await factory.aclose()
            facts['close_operations_retained']=len(getattr(factory,'_model_client_closes',{}))
            return
        if mode=='child':
            await factory._release_model(child)
            facts['parent_open']=not factory.model.root_async_client.is_closed()
            await factory.aclose();return
        if mode=='retired':
            factory.set_model('deepseek-reasoner');await factory.aclose();return
        first=retained[0];original=first.aclose if not synchronous else None;entered=asyncio.Event()
        if synchronous:
            if mode=='native-failure':
                target=first._connection._network_stream;target=getattr(target,'_native',target)
                original_native=target.close
                def fail_native():
                    attempts.append('close')
                    if len(attempts)<=2:raise OSError('native-stream-close-failure')
                    original_native()
                with patch.object(target,'close',new=fail_native):
                    try:await factory.aclose()
                    except BaseException as error:facts['first_error']=type(error).__name__
                    facts['native_open_after_failure']=[sock.fileno()>=0 for sock in raw]
                    facts['owned_after_failure']=len(factory._owned_sync_clients)
                    await factory.aclose()
            else:await factory.aclose()
            return
        if mode in ('native-failure','native-global'):
            target=first._connection._network_stream
            target=getattr(target,'_native',target)
            original_native=target.aclose
            async def fail_native():
                attempts.append('close')
                if len(attempts)<=(1 if mode=='native-global' else 2):raise OSError('native-stream-close-failure')
                await original_native()
                if mode=='native-global':entered.set();await asyncio.sleep(.15)
            with patch.object(target,'aclose',new=fail_native):
                if mode=='native-global':
                    operation=asyncio.create_task(factory.aclose());operations.append(operation)
                    await entered.wait();return
                try:await factory.aclose()
                except BaseException as error:facts['first_error']=type(error).__name__
                facts['native_open_after_failure']=[sock.fileno()>=0 for sock in raw]
                facts['owned_after_failure']=len(factory._owned_async_clients)
                await factory.aclose()
            return
        async def close():
            if mode=='failure':raise OSError('connection-close-failure')
            await original();entered.set();await asyncio.sleep(.15)
        if mode=='normal':await factory.aclose();return
        with patch.object(first,'aclose',new=close):
            operation=asyncio.create_task(factory.aclose());operations.append(operation)
            if mode in ('global','caller-cancel','repeated-cancel'):
                await entered.wait()
                if mode=='global':return
                operation.cancel()
                if mode=='repeated-cancel':
                    for _ in range(3):await asyncio.sleep(.01);operation.cancel()
                await asyncio.gather(operation,return_exceptions=True);return
            try:await operation
            except BaseException as error:facts['first_error']=type(error).__name__
        await factory.aclose()
    try:
        asyncio.run(main())
        facts.update(mode=mode,sockets=len(raw),native_open_after_loop=[sock.fileno()>=0 for sock in raw],
            sdk_closed=sdk.is_closed(),owned_clients=len(factory._owned_async_clients),close_attempts=len(attempts),
            caller_cancelled=all(operation.cancelled() for operation in operations),synchronous=synchronous,loop_errors=loop_errors)
        if mode in ('global','native-global','caller-cancel','repeated-cancel','close-race-global'):asyncio.run(factory.aclose())
        facts['owned_after_retry']=len(factory._owned_async_clients)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=1)
        for timer in timers:timer.join(timeout=1)
    facts['fd_before']=fd_before
    facts['fd_after']=len(os.listdir('/dev/fd')) if fd_before is not None else None
    facts['new_threads']=[item.name for item in threading.enumerate() if item.ident not in threads_before]
    print(json.dumps(facts),flush=True)


if __name__=='__main__':
    import traceback
    try:run(Path(sys.argv[1]),sys.argv[2])
    except BaseException:
        traceback.print_exc();os._exit(70)
    os._exit(0)
