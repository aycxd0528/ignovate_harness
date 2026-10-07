"""Shared, local slash-command flow for project MCP services."""
import shlex

from ui.actions import CommandResult

USAGE = ('用法：/mcp [list | add <名称> --transport http <URL> | '
         'add <名称> --transport stdio -- <命令> [参数...] | '
         'connect <名称> | disconnect <名称> | remove <名称> | tools <名称>]')


async def execute_mcp(argument, manager, *, permission_mode='default'):
    args = shlex.split(argument)
    operation = args[0] if args else 'list'
    if operation == 'list' and len(args) <= 1:
        servers = manager.store.list_servers()
        rows = {name: {**manager.status(name), 'transport': definition['transport']}
                for name, definition in servers.items()}
        text = '\n'.join(f"{name} · {row['transport']} · {row['status']} · {row['tool_count']} 个工具"
                         + (f" · {row['error']}" if row['error'] else '') for name, row in rows.items())
        return CommandResult(text or '尚未配置 MCP 服务。\n' + USAGE, data={'servers': rows})
    if operation == 'add' and len(args) >= 5 and args[2] == '--transport':
        if permission_mode == 'plan':
            raise ValueError('计划模式不能修改 MCP 配置。')
        name, transport = args[1], args[3]
        if transport == 'http' and len(args) == 5:
            definition = {'transport': 'http', 'url': args[4]}
        elif transport == 'stdio' and len(args) >= 6 and args[4] == '--':
            definition = {'transport': 'stdio', 'command': args[5], 'args': args[6:]}
        else:
            raise ValueError(USAGE)
        manager.store.add(name, definition)
        return CommandResult(f'MCP 服务 {name} 已配置；使用 /mcp connect {name} 连接。', data={'server': name})
    if operation not in {'connect', 'disconnect', 'remove', 'tools'} or len(args) != 2:
        raise ValueError(USAGE)
    name = args[1]
    if name not in manager.store.list_servers():
        raise ValueError('MCP 服务未配置。')
    if operation == 'tools':
        from nailong.mcp.tools import tool_name
        descriptors = manager.tools(name)
        text = '\n'.join(f'{tool_name(name, item.name)} · {item.description or "暂无说明"}' for item in descriptors)
        return CommandResult(manager.sanitize(text) or '服务未连接或没有工具；使用 /mcp connect <名称>。')
    if permission_mode == 'plan' and operation in {'connect', 'remove'}:
        raise ValueError('计划模式不能连接 MCP 服务或修改配置。')
    if operation == 'connect':
        status = await manager.connect(name)
        return CommandResult(f"MCP 服务 {name} · {status['status']} · {status['tool_count']} 个工具。",
                             data={'server': name, **status})
    await manager.disconnect(name)
    if operation == 'remove':
        manager.store.remove(name)
    return CommandResult(f'MCP 服务 {name} 已' + ('移除。' if operation == 'remove' else '断开。'))
