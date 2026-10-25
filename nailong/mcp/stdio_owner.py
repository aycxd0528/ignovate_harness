"""Private local liveness channel; SDK stdio bytes never pass through it."""
import asyncio
import hmac
import secrets
import socket


class StdioOwnership:
    def __init__(self, connection):
        self.connection=connection
        self.owner=asyncio.current_task()
        self.listener=None;self.peer=None;self.watcher=None
        self.token=secrets.token_bytes(32)
        self.receipt=None

    async def __aenter__(self):
        listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.listener=listener
        try:
            listener.setblocking(False)
            listener.bind(('127.0.0.1',0));listener.listen(1)
        except BaseException:
            listener.close();raise
        return self

    @property
    def arguments(self):
        return ['--owner-channel-v1',str(self.listener.getsockname()[1]),self.token.hex()]

    async def attach(self):
        loop=asyncio.get_running_loop()
        self.peer,_=await loop.sock_accept(self.listener)
        self.listener.close();self.listener=None
        self.peer.setblocking(False)
        received=bytearray()
        while len(received)<len(self.token):
            block=await loop.sock_recv(self.peer,len(self.token)-len(received))
            if not block:raise OSError('MCP ownership channel ended before admission.')
            received.extend(block)
        if not hmac.compare_digest(received,self.token):
            raise OSError('MCP ownership channel authentication failed.')
        await loop.sock_sendall(self.peer,b'G')
        self.watcher=asyncio.create_task(self._watch(),name='mcp:stdio-owner')

    async def _watch(self):
        receipt=bytearray()
        try:
            while len(receipt)<2:
                block=await asyncio.get_running_loop().sock_recv(self.peer,2-len(receipt))
                if not block:break
                receipt.extend(block)
        except OSError:
            receipt.clear()
        self.receipt=bytes(receipt)
        self._report(self.receipt)

    def _report(self,receipt):
        connection=self.connection
        closing=connection.closing
        if closing and receipt==b'C':return
        connection.error=('MCP 服务已退出；连接已关闭。' if receipt==b'C' else
            'MCP 监护连接中断，清理无法核实；副作用需核对。')
        connection.closing=True;connection.descriptors=();connection.status='error'
        if not connection.ready.done():
            connection.ready.set_exception(ValueError(connection.error))
        # Closing seals admission, but cannot suppress a late unknown result.
        # Never recancel an owner that has already started unwinding SDK scopes.
        if not closing and not self.owner.cancelling():self.owner.cancel()

    async def __aexit__(self,*ignored):
        try:
            if self.watcher is not None:
                try:
                    # The surrounding SDK stdio context has already waited for
                    # its direct process. Consume its final receipt before
                    # closing our end; cancellation is not cleanup evidence.
                    await asyncio.gather(self.watcher,return_exceptions=True)
                finally:
                    if self.receipt is None:self._report(b'')
        finally:
            for stream in (self.listener,self.peer):
                if stream is not None:stream.close()
