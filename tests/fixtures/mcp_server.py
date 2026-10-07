"""Local MCP server used by integration tests; never contacts a model provider."""
import argparse
import asyncio
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel

parser = argparse.ArgumentParser()
parser.add_argument('--http', action='store_true')
parser.add_argument('--port', type=int, default=8000)
parser.add_argument('--secret-tool-name', action='store_true')
options = parser.parse_args()
server = FastMCP('nailong-test', host='127.0.0.1', port=options.port, log_level='ERROR')


class EchoResult(BaseModel):
    payload: dict
    path: str


class EnvironmentResult(BaseModel):
    token: str
    deepseek_present: bool
    pid: int


class ReservedResult(BaseModel):
    config: dict
    run_manager: str
    callbacks: list


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def echo(payload: dict, path: str = '/remote/document') -> EchoResult:
    return EchoResult(payload=payload, path=path)


@server.tool()
def touch(path: str) -> str:
    Path(path).write_text('remote side effect', encoding='utf-8')
    return 'written'


@server.tool()
def reserved(config: dict, run_manager: str, callbacks: list) -> ReservedResult:
    return ReservedResult(config=config, run_manager=run_manager, callbacks=callbacks)


@server.tool()
async def pause(seconds: float) -> str:
    await asyncio.sleep(seconds)
    return 'done'


@server.tool()
def fail() -> str:
    raise ValueError('test service failure')


@server.tool()
def environment() -> EnvironmentResult:
    return EnvironmentResult(token=os.environ.get('MCP_TEST_TOKEN', ''),
                             deepseek_present='DEEPSEEK_API_KEY' in os.environ,
                             pid=os.getpid())


if options.secret_tool_name:
    @server.tool(name=os.environ['MCP_TEST_TOKEN'])
    def credential_named() -> str:
        return 'test only'

server.run(transport='streamable-http' if options.http else 'stdio')
