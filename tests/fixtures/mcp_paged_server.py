"""Low-level fixture for paginated tools and malformed schema responses."""
import asyncio
import sys

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

server = Server('paged-test')
mode = sys.argv[1] if len(sys.argv) > 1 else 'paged'


@server.list_tools()
async def list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
    cursor = request.params.cursor if request.params else None
    schema = {'type': 'object', 'properties': {'value': {'type': 'string'}}}
    if mode == 'remote-ref':
        schema['properties']['value'] = {'$ref': 'https://example.invalid/schema'}
    if mode == 'anchor':
        schema = {'type': 'object', '$defs': {'text': {'$anchor': 'text', 'type': 'string'}},
                  'properties': {'value': {'$ref': '#text'}}}
    if mode == 'duplicate':
        return types.ListToolsResult(tools=[types.Tool(name='same', inputSchema=schema), types.Tool(name='same', inputSchema=schema)])
    if mode == 'collision':
        return types.ListToolsResult(tools=[types.Tool(name='x.y', inputSchema=schema),
                                           types.Tool(name='x_y_c4587e3826098b16', inputSchema=schema)])
    name = 'second' if cursor else 'first'
    next_cursor = 'next' if not cursor or mode == 'cycle' else None
    return types.ListToolsResult(tools=[types.Tool(name=name, inputSchema=schema)], nextCursor=next_cursor)


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


asyncio.run(main())
