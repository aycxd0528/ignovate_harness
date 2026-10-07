"""Shared built-in command descriptions for both terminal interfaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    usage: str = ""
    needs_argument: bool = False


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("/help", "显示帮助和命令列表"),
    CommandSpec("/init", "分析项目并生成 .nailong/context.md"),
    CommandSpec("/review", "只读审查改动或指定路径", "/review [路径 | --staged | --branch ref]"),
    CommandSpec("/permissions", "选择请求批准、帮我批准或完全访问权限", "/permissions [request|assist|full|rules]"),
    CommandSpec("/project", "切换工作项目并开始新会话", "/project <目录>", True),
    CommandSpec("/history", "显示当前会话历史"),
    CommandSpec("/sessions", "列出或搜索已保存会话", "/sessions [关键词]"),
    CommandSpec("/resume", "恢复指定会话", "/resume <ID|序号>", True),
    CommandSpec("/rewind", "回退到上一轮对话开始前"),
    CommandSpec("/clear", "开始一个新的会话"),
    CommandSpec("/count", "显示已完成的对话轮数"),
    CommandSpec("/plan", "先只读探索并审批执行计划", "/plan <目标>", True),
    CommandSpec("/goal", "创建、恢复或查看有界目标", "/goal [参数] <目标>"),
    CommandSpec("/cost", "查看当前会话 token 用量与费用估算"),
    CommandSpec("/context", "查看当前会话上下文占用"),
    CommandSpec("/compact", "压缩较早的工具结果"),
    CommandSpec("/about", "关于ignovate harness"),
    CommandSpec("/exit", "退出程序"),
    CommandSpec("/skills", "列出本地 Skills；用 $名称 <任务> 调用"),
    CommandSpec('/mcp', '管理外部 MCP 服务、连接与工具', '/mcp [list|add|connect|disconnect|remove|tools]'),
    CommandSpec('/diff','查看项目 Git 差异','/diff [--staged|--branch ref]'),
    CommandSpec('/verify','执行已配置的项目验证','/verify [名称|--list]'),
    CommandSpec('/doctor','本地安装、配置与终端诊断'),
    CommandSpec('/status','模型、权限、任务与用量状态'),
    CommandSpec('/reload-skills','重新发现本地 Skills'),
    CommandSpec('/stop','停止当前任务并暂停输入队列'),
    CommandSpec('/queue','管理待执行输入','/queue [clear|resume|remove 序号]'),
    CommandSpec('/rename','命名当前会话','/rename <名称>',True),
    CommandSpec('/recap','回顾会话与实际验证证据'),
    CommandSpec('/export','导出安全 Markdown 会话','/export [项目内路径]'),
    CommandSpec('/trace','查看运行轨迹；使用 --dangerously-skip-permissions 也仅导出到项目内','/trace [export [项目内路径]]'),
    CommandSpec('/model','选择、切换或新增模型配置','/model [名称 | add [名称 模型ID]] [--global]'),
    CommandSpec('/reasoning','设置模型推理强度','/reasoning [default|none|low|high|max] [--global]'),
    CommandSpec('/theme','查看或切换终端主题','/theme [dark|light|ansi] [--global]'),
    CommandSpec('/config','查看有效偏好及来源','/config [set model|theme|output_style 值] [--global]'),
    CommandSpec('/memory','记忆状态、主题目录、分页读取与编辑','/memory [show [scope]|list [scope] [offset]|read <ID> [offset]|reload|edit <scope>]'),
    CommandSpec('/tools','展开已保存工具记录','/tools [序号]'),
    CommandSpec('/task','任务目标、范围、步骤与验收证据','/task [status|new 目标|amend 目标|scope 路径...|constraint 文本|accept ID 类型 说明|bind ID 验证步骤 覆盖路径...|step ID 状态 说明|phase 阶段|confirm ID 说明|waive ID 说明|complete|resume]'),
)
COMMAND_BY_NAME = {command.name: command for command in COMMANDS}
