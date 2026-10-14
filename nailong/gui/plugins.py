"""GUI adapter for the native Skills registry and MCP runtime manager."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from nailong.core.preferences import safe_config_path
from nailong.core.safe_files import atomic_write_bytes, is_link_or_reparse, open_regular_file, pinned_directory
from nailong.core.skills import MAX_SKILL_BYTES, MAX_FRONTMATTER_BYTES, SkillRegistry, _NAME_RE, _PROTECTED_PARTS
from nailong.mcp.config import MCPConfigStore, validate_definition


class GUIPlugins:
    def __init__(self, controller):
        self.controller = controller
        self.project = controller.project

    def registry(self):
        factory = self.controller.factory
        return factory.skill_registry if factory and hasattr(factory, 'skill_registry') else SkillRegistry(self.project)

    def refresh_skills(self):
        factory = self.controller.factory
        if factory and hasattr(factory, 'reload_skills'):
            factory.reload_skills()
        else:
            self.registry().reload()

    def catalog(self):
        registry = self.registry()
        factory = self.controller.factory
        if not (self.controller.current_task or self.controller.queue):
            registry.reload()
            if factory and hasattr(factory, '_skill_catalog'):
                factory._skill_catalog = registry.catalog()
        manager = getattr(factory, 'mcp_manager', None)
        store = manager.store if manager else MCPConfigStore(self.project)
        return self.controller.safe({
            'skills': [{'name': skill.name, 'description': skill.description, 'source': skill.source,
                        'path': str(skill.root), 'enabled': skill.name not in registry.disabled}
                       for skill in registry.list_skills(include_disabled=True)],
            'diagnostics': registry.diagnostics,
            'mcp': [{'name': name, 'definition': definition,
                     **(manager.status(name) if manager else {'status': 'disconnected', 'tool_count': 0, 'error': ''})}
                    for name, definition in store.list_servers().items()],
        })

    async def mutate(self, payload):
        if self.controller.current_task or self.controller.queue:
            raise ValueError('请等待运行与队列任务结束后修改插件。')
        operation, name = payload.get('operation'), payload.get('name')
        if operation == 'skill_enable':
            if not isinstance(payload.get('enabled'), bool):
                raise ValueError('Skill 启用状态必须是布尔值。')
            registry = self.registry()
            registry.reload()
            registry.set_enabled(name, payload['enabled'])
            self.refresh_skills()
        elif operation == 'skill_install':
            self.install_skill(payload.get('path'))
            self.refresh_skills()
        elif operation in {'mcp_add', 'mcp_update', 'mcp_remove', 'mcp_connect', 'mcp_disconnect'}:
            manager = getattr(self.controller.factory, 'mcp_manager', None)
            store = manager.store if manager else MCPConfigStore(self.project)
            if operation in {'mcp_add', 'mcp_update'}:
                definition = validate_definition(payload.get('definition'))
                if operation == 'mcp_update' and manager and manager.status(name)['status'] == 'connected':
                    raise ValueError('请先断开 MCP 服务，再修改配置。')
                getattr(store, 'add' if operation == 'mcp_add' else 'update')(name, definition)
            elif operation == 'mcp_remove':
                if name not in store.list_servers():
                    raise ValueError('MCP 服务未配置。')
                if manager:
                    await manager.disconnect(name)
                store.remove(name)
            elif operation == 'mcp_connect':
                self.controller.ensure_service()
                await self.controller.factory.mcp_manager.connect(name)
            elif manager:
                await manager.disconnect(name)
        else:
            raise ValueError('不支持的插件操作。')
        self.controller.emit('plugins_updated', {'operation': operation, 'name': name})
        return self.catalog()

    def install_skill(self, value):
        """Validate a bounded local package, then publish SKILL.md last.

        No package scripts run. A package is only discoverable when all its
        resources have been installed; existing directories are never replaced.
        Native safe-file helpers reject links/reparse points in every ancestry.
        """
        if not isinstance(value, str) or not value.strip():
            raise ValueError('请选择本地 Skill 目录。')
        source = Path(value).expanduser().absolute()
        name = source.name
        if not _NAME_RE.fullmatch(name) or len(name) > 64:
            raise ValueError('Skill 目录名称无效。')
        files, total, entries = {}, 0, 0
        with pinned_directory(source):
            for directory, directories, names in os.walk(source, followlinks=False):
                for child in directories + names:
                    path = Path(directory)/child
                    entries += 1
                    if entries > 256 or len(path.relative_to(source).parts) > 16:
                        raise ValueError('Skill 目录超过安装大小或深度限制。')
                    if child in _PROTECTED_PARTS or is_link_or_reparse(path):
                        raise ValueError('Skill 不能包含保护文件或符号链接。')
                    mode = path.lstat().st_mode
                    if child in directories:
                        if not stat.S_ISDIR(mode):
                            raise ValueError('Skill 子目录无效。')
                        continue
                    if not stat.S_ISREG(mode):
                        raise ValueError('Skill 资源必须是普通文件。')
                    with open_regular_file(path) as stream:
                        data = stream.read(MAX_SKILL_BYTES+1)
                    if len(data) > MAX_SKILL_BYTES:
                        raise ValueError('Skill 文件超过 64 KiB。')
                    total += len(data)
                    if len(files) >= 128 or total > 5*1024*1024:
                        raise ValueError('Skill 目录超过安装大小限制。')
                    files[path.relative_to(source)] = data
        content = files.get(Path('SKILL.md'))
        if content is None:
            raise ValueError('Skill 目录缺少 SKILL.md。')
        # Use the same native frontmatter limits and parsing as discovery.
        prefix = content[:MAX_FRONTMATTER_BYTES+1].replace(b'\r\n', b'\n')
        marker = prefix.find(b'\n---\n', 4)
        if marker < 0:
            raise ValueError('SKILL.md 的 YAML frontmatter 超出大小限制或未结束。')
        try:
            metadata = SkillRegistry._metadata(prefix[:marker+5].decode('utf-8'))
            content.decode('utf-8')
        except Exception as error:
            raise ValueError('SKILL.md 必须是有效的 UTF-8 与 YAML frontmatter。') from error
        description = metadata.get('description')
        if metadata.get('name') != name or not isinstance(description, str) or not 1 <= len(' '.join(description.split())) <= 1024:
            raise ValueError('Skill name 必须与目录名称一致，并有有效 description。')
        target = safe_config_path(self.project/'.agents/skills'/name, self.project)
        installed = []
        created = False
        with pinned_directory(target.parent, create=True) as parent:
            try:
                if os.name == 'nt':
                    target.mkdir(mode=0o700)
                else:
                    os.mkdir(name, mode=0o700, dir_fd=parent)
                created = True
                # Publish the native entry point only after every resource exists.
                for relative in sorted(files, key=lambda path: (path == Path('SKILL.md'), str(path))):
                    destination = safe_config_path(target/relative, self.project)
                    atomic_write_bytes(destination, files[relative], replace=False)
                    installed.append(destination)
            except FileExistsError:
                if not created:
                    raise ValueError('该项目已存在同名 Skill，不能覆盖。') from None
                raise
            finally:
                if created and len(installed) != len(files):
                    # Remove only paths from this incomplete installation. Native
                    # parent checks prevent cleanup following a swapped symlink.
                    for destination in reversed(installed):
                        with pinned_directory(destination.parent) as directory:
                            if os.name == 'nt': destination.unlink(missing_ok=True)
                            else: os.unlink(destination.name, dir_fd=directory)
                    for directory, _, _ in os.walk(target, topdown=False, followlinks=False):
                        with pinned_directory(Path(directory).parent) as pinned:
                            if os.name == 'nt': Path(directory).rmdir()
                            else: os.rmdir(Path(directory).name, dir_fd=pinned)
