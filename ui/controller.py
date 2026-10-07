"""Shared command resolution and local actions for all interactive interfaces."""

from dataclasses import dataclass
import shlex

from nailong.core.commands import CommandRegistry
from nailong.core.costs import CostEstimator
from nailong.core.permissions import PermissionEngine
from nailong.tools.registry import get_tool_specs
from ui.commands import COMMANDS, COMMAND_BY_NAME, CommandSpec
from ui.flows import parse_goal_request


@dataclass(frozen=True)
class CommandRequest:
    command: str
    argument: str
    message: str
    kind: str = "local"
    prompt: str = ""
    profile: str = "chat"
    target_path: str | None = None
    allowed_tools: frozenset[str] | None = None
    review_paths: frozenset[str] | None = None


class CommandController:
    def __init__(self, settings):
        self.settings = settings
        self.registry = CommandRegistry(settings.project_root, api_key=settings.api_key)
        self.cost_estimator = CostEstimator(settings.model, settings.project_root)

    def specs(self) -> tuple[CommandSpec, ...]:
        custom = tuple(
            CommandSpec("/" + name, description)
            for name, (description, _) in self.registry.list_commands().items()
            if "/" + name not in COMMAND_BY_NAME
        )
        skills=getattr(self,'skill_registry',None)
        skill_commands=tuple(CommandSpec('$'+skill.name,skill.description,needs_argument=True) for skill in skills.list_skills()) if skills else ()
        return COMMANDS + custom + skill_commands

    def resolve(self, message: str) -> CommandRequest:
        pieces = message.strip().split(maxsplit=1)
        command = pieces[0] if pieces else ""
        argument = pieces[1].strip() if len(pieces) > 1 else ""
        if not command.startswith("/"):
            return CommandRequest(command, argument, message, "prompt", message)
        spec = COMMAND_BY_NAME.get(command)
        if spec is not None:
            if spec.needs_argument and not argument and command != "/resume":
                raise ValueError("用法：" + spec.usage)
            if command in {"/init", "/rewind", "/compact", "/cost", "/context"} and argument:
                raise ValueError("用法：" + command)
            if command == "/review" and (not argument or argument.startswith("--")):
                return CommandRequest(command, argument, message)
            if command in {"/init", "/review", "/plan"}:
                profile = command[1:]
                prompt = f"为以下目标制定执行计划：\n{argument}" if profile == "plan" else message
                return CommandRequest(command, argument, message, "prompt", prompt, profile,
                                      argument if profile == "review" else None)
            return CommandRequest(command, argument, message)
        available = {tool.name for profile in ("chat", "init", "review", "plan") for tool in get_tool_specs(profile)}
        manager = getattr(self, 'mcp_manager', None)
        if manager is not None:
            from nailong.mcp.tools import tool_name
            available.update(tool_name(server, descriptor.name) for server, descriptor in manager.tools())
        custom = self.registry.resolve(
            command[1:], shlex.split(argument),
            available_tools=available,
        )
        if custom is None:
            raise ValueError("未知命令。输入 /help 查看可用命令。")
        target = None
        if custom.model_profile == "review":
            arguments = shlex.split(argument)
            if not arguments:
                raise ValueError("此自定义命令需要提供审查目标路径。")
            target = arguments[0]
        return CommandRequest(command, argument, message, "prompt", custom.prompt,
                              custom.model_profile, target, custom.allowed_tools)

    def information(self, request, service, thread_id, *, completed_turns=0) -> str | None:
        command = request.command
        if command == "/help":
            from ui.help_view import render_help_text
            section = request.argument or 'general'
            if section not in {'general', 'commands', 'custom', 'skills'}:
                return '用法：/help [general|commands|custom|skills]'
            return render_help_text(self.specs(), section=section, api_key=self.settings.api_key).plain
        if command == "/about":
            return "这是一个使用 LangChain、DeepSeek 和本地工具的学习型 CLI Agent。"
        if command == "/count":
            return f"本次运行已完成 {completed_turns} 轮对话。"
        if command == "/permissions":
            summary = getattr(service, "get_permission_summary", None)
            return summary() if callable(summary) else PermissionEngine(self.settings.project_root).format_rules()
        if command == "/skills":
            registry = getattr(getattr(service, "runtime_factory", None), "skill_registry", None)
            skills = registry.list_skills() if registry is not None else ()
            if not skills:
                return "当前项目没有发现本地 Skill。可在 .agents/skills/<名称>/SKILL.md 中添加。"
            return "\n".join(f"${skill.name}  [{skill.source}] {skill.description}" for skill in skills) + "\n输入 $名称 <任务> 使用 Skill；新增 Skill 后重新启动程序。"
        if command == "/context":
            context = service.get_context_summary(thread_id)
            return (
                f"当前上下文约 {context['estimated_tokens']} tokens，"
                f"{context['message_count']} 条消息、{context['user_turns']} 个用户回合，"
                f"{context['pinned_messages']} 条固定消息。自动压缩阈值为 "
                f"{service.compact_threshold_tokens} tokens。"
            )
        if command == "/cost":
            usage = {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0}
            estimated = False
            store = getattr(service, "session_store", None)
            for record in store.read_events(thread_id) if store is not None else []:
                if record.get("kind") == "usage_missing":
                    estimated = True
                if record.get("kind") != "usage":
                    continue
                data = record.get("data") or {}
                estimated |= bool(data.get("estimated"))
                for key in usage:
                    usage[key] += max(0, int(data.get(key, 0) or 0))
            total = usage["input_tokens"] + usage["output_tokens"]
            text = (f"会话用量（含子代理）：输入 {usage['input_tokens']}（缓存命中 {usage['cache_hit_tokens']}）/ "
                    f"输出 {usage['output_tokens']} / 合计 {total} tokens")
            if self.cost_estimator.available:
                text += f"；按配置单价估算约 ${self.cost_estimator.estimate(usage):.6f}。"
            else:
                text += f"；模型 {self.settings.model} 未配置价格，费用未估算。"
            if estimated:
                text += " 部分调用缺少完整用量，统计可能不完整。"
            return text
        return None

    def goal(self, argument, factory, thread_id) -> tuple[object | None, list[str]]:
        store = getattr(factory, "goal_store", None)
        if store is None:
            raise ValueError("当前运行时没有目标存储。")
        if argument in {"status", "状态"}:
            goal = store.active() or store.latest()
            if goal is None:
                return None, ["当前项目还没有保存的目标。"]
            lines = [f"目标 {goal.id} · {goal.state} · 第 {goal.round}/{goal.max_rounds} 轮 · "
                     f"约 ${goal.spent_usd:.4f}/${goal.max_cost_usd:.4f}\n{goal.objective}"]
            if goal.pause_reason:
                lines.append(goal.pause_reason)
            return None, lines
        if argument in {"pause", "暂停"}:
            goal = store.active()
            if goal is None:
                return None, ["当前没有进行中的目标。"]
            changed, reason, _ = store.update(goal.id, state="paused", summary="用户暂停了目标。", thread_id=thread_id)
            return None, ["目标已暂停。" if changed else reason]
        if not self.cost_estimator.available:
            raise ValueError(f"模型 {self.settings.model} 未配置价格；请先在 .nailong/settings.json 的 pricing 中设置输入、缓存命中及输出单价。")
        if argument:
            objective, rounds, cost = parse_goal_request(argument)
            goal = store.create(objective, max_rounds=rounds, max_cost_usd=cost, thread_id=thread_id)
            return goal, [f"已创建目标 {goal.id}，最多 {rounds} 轮、约 ${cost:.2f}。"]
        goal = store.active()
        if goal is not None:
            # Bind only after the driver owns the lease; never steal a running session.
            return goal, []
        latest = store.latest()
        if latest is not None and latest.state == "paused":
            changed, reason, goal = store.resume(latest.id, thread_id=thread_id)
            if not changed:
                raise ValueError(reason)
            return goal, ["已恢复暂停目标。"]
        return None, ["当前没有进行中的目标。用法：/goal <目标>；输入 /goal status 查看最近目标。"]
