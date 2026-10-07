"""Dynamic MCP tools keep original schemas and use the shared execution gate."""
import json

from langchain_core.tools import ToolException
from pydantic import Field

from nailong.mcp.schema import schema_validator
from nailong.mcp.naming import tool_name
from nailong.tools.registry import ToolSpec
from nailong.tools.results import failure
from tools import ExecutionTool


class MCPExecutionTool(ExecutionTool):
    validator: object = Field(exclude=True)

    def _parse_input(self, tool_input, tool_call_id):
        if not isinstance(tool_input, dict) or not self.validator.is_valid(tool_input):
            raise ToolException(json.dumps(failure('invalid_parameters',
                'MCP 工具参数不符合服务声明的 JSON Schema。', started=False), ensure_ascii=False))
        return tool_input

    def _to_args_and_kwargs(self, tool_input, tool_call_id):
        # Keep business keys such as config/run_manager distinct from LangChain kwargs.
        return (self._parse_input(tool_input, tool_call_id),), {}

    def _arguments(self, args, kwargs):
        return dict(args[0])


def _handler(manager, server, remote, owner):
    async def invoke(**arguments):
        if manager.connection_identity(server) is not owner:
            return failure('mcp_connection_changed', 'MCP 连接已更换；请开始新的回合重新发现工具。', started=False)
        return await manager.call_tool(server, remote, arguments)
    return invoke


def build_mcp_tools(manager, execution, session, api_key='') -> list:
    result = []
    for server, descriptor in manager.tools():
        owner = manager.connection_identity(server)
        remote = descriptor.name
        name = tool_name(server, remote)

        invoke = _handler(manager, server, remote, owner)

        spec = ToolSpec(name=name,
            description=f'MCP 服务 {server} 的工具 {remote}。外部工具按当前权限审批。\n{descriptor.description or ""}',
            input_schema=descriptor.inputSchema, handler=invoke, async_handler=invoke,
            read_only=False, concurrency_safe=False, permission_key=name, profiles=frozenset({'chat'}))
        result.append(MCPExecutionTool(name=name, description=spec.description,
            args_schema=descriptor.inputSchema, validator=schema_validator(descriptor.inputSchema),
            spec=spec, execution=execution, file_session=session, profile='chat',
            secret=api_key, handle_tool_error=True))
    return result
