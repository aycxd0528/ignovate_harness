"""Conservative, local recognition of task status and explicit work requests."""
import re


_RESUME = {
    '按这个做', '按这个实施', '按这个推进', '按计划执行', '开始实施', '开始执行',
    '继续', '请继续', '开始', '开始修改', '开始修复', '继续修改', '继续执行', '请继续执行',
    '恢复任务', '恢复执行', '继续任务', 'continue', 'resume', 'resume task', 'continue task',
}
_STATUS = re.compile(
    r'(?:请问|请告诉我|告诉我|查看|看看|看一下|查询)?(?:现在|当前|目前)?'
    r'(?:(?:任务|项目|工作|修复|测试|验证|实现)?(?:的)?(?:进展|进度|状态)'
    r'(?:如何|怎么样|怎样|是什么|到哪了|到哪里了|情况)?'
    r'|(?:任务|工作|修复|测试|验证|实现)?(?:完成了吗|做完了吗|做到哪了|做到哪里了)'
    r'|还有哪些未完成)'
    r'|(?:what is |what\x27s |show |check )?(?:the )?(?:current )?(?:task )?(?:status|progress)',
    re.IGNORECASE,
)
_WORK = re.compile(
    r'^(?:(?:请|帮我|请帮我|现在|开始|麻烦|麻烦你|然后|接下来)\s*)*'
    r'(?:(?:反复|依次|逐个|分别)\s*)*'
    r'(?:修复|修改|实现|审查|重构|优化|排查|测试|验证|创建|新增|增加|编辑|删除|重命名|构建|运行|执行|写入|写文件|做)'
    r'|^(?:please\s+)?(?:review|debug|implement|refactor|fix|create|edit|rename|build|test|run)\b',
    re.IGNORECASE,
)
_QUESTION = re.compile(r'是什么|为什么|怎么样|怎样|如何|是否|能否|可否|了吗|了么|先解释|解释一下|说明一下|\b(?:what|why|how|whether)\b', re.IGNORECASE)
_NO_EXECUTION = re.compile(
    r'(?:不要|不需要|禁止|暂不|先不|别)(?:再|先|立即|马上)?(?:恢复|继续)'
    r'|(?:不要|禁止|暂不|先不|别)(?:再)?执行(?:任务|任何操作|任何任务)'
    r'|^(?:请)?(?:仅|只(?:需|要)?)(?:解释|说明|查询)'
)
_AUTHORIZED_WORK = re.compile(
    r'^(?:我\s*)?(?:(?:请|现在|现已|正式|明确)\s*)*'
    r'(?:授权|批准|同意)\s*(?:你|您)?\s*'
    r'(?:(?:按|按照|依照|照)\s*(?:你|您)?的?\s*(?:方案|计划)\s*)?'
    r'(?:(?:开始|继续)\s*)?'
    r'(?:实现|实施|执行|修改|修复|重构|优化|测试|验证|创建|新增|编辑|构建|运行)'
)


def _live_request_text(message: str) -> str:
    """Quoted prose, blockquotes and fenced examples cannot authorize work."""
    lines, fence = [], None
    for line in message.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            delimiter = marker.group(1)
            if fence is None:
                fence = (delimiter[0], len(delimiter))
            elif delimiter[0] == fence[0] and len(delimiter) >= fence[1]:
                fence = None
            continue
        if fence is None and not line.lstrip().startswith('>'):
            lines.append(line)
    return re.sub(r'''“[^”]*”|‘[^’]*’|"[^"]*"|'[^']*'|`[^`\n]*`''', '', '\n'.join(lines))


def task_request_kind(message: str) -> str:
    """Match whole status questions; a mixed question/change request stays work."""
    live = _live_request_text(message).strip()
    text = live.rstrip('。.!！?？·').strip()
    if _STATUS.fullmatch(text):
        return 'status'
    if _NO_EXECUTION.search(text):
        return 'other'
    if explicit_new_task(text):return 'work'
    if text.casefold() in _RESUME:
        return 'resume'
    if _AUTHORIZED_WORK.search(text) and (_QUESTION.search(live) or re.search(
        r'吗|么|[?？]|不同意|不授权|未授权|(?:撤回|撤销|取消)授权|(?:不要|暂不|先不)(?:执行|实施)', live)):
        return 'other'
    # A question about fixing/testing is not permission to resume that work.
    for index, clause in enumerate(re.split(r'[，,；;\n]', text)):
        authorization = (index == 0 and _AUTHORIZED_WORK.search(clause.strip())
            and not re.search(r'吗|么|[?？]', re.split(r'[，,；;\n]', live)[0]))
        if (_WORK.search(clause.strip()) or authorization) and not _QUESTION.search(clause):
            # An imperative may be politely phrased as a question ("请修改…可以吗？").
            if not clause.strip().endswith(('了吗', '了吗？', '了吗?', '了么', '了么？', '了么?')):
                return 'work'
    return 'other'


def explicit_new_task(message):
    """A live top-level label explicitly separates new work from an old ledger."""
    match=re.match(r'^\s*(?:(?:请)?(?:开始|创建|建立)(?:一个)?\s*)?(?:新目标|新任务|new\s+(?:goal|task))\s*[:：]\s*(\S[\s\S]*)$',message,re.I)
    return match.group(1).strip() if match else None


def is_simple_project_question(message: str) -> bool:
    """Keep brief project/module introductions local unless delegation is requested."""
    text = message.strip()
    if len(text) > 240 or '项目' not in text:
        return False
    if re.search(r'子代理|subagent|delegate|委派|并行|分别|多个|对比|审查|修复|修改|实现|优化', text, re.IGNORECASE):
        return False
    return bool(re.match(r'^(?:请|帮我|请帮我)?(?:介绍|解释|概述|说明|讲解)', text)
        or re.match(r'^(?:这个|当前|本)?项目', text) and re.search(r'是什么|怎么样|如何|做什么|用途|结构|上下文', text))


def render_task_status(task: dict | None) -> str:
    """Show recorded facts without reconciling, validating, or modifying them."""
    if task is None:
        return '当前会话没有工程任务。'
    lines = [
        f"目标：{task['objective']}",
        f"阶段：{task['phase']} · 状态：{task['lifecycle']} · 要求版本：{task['revision']}",
        '范围：' + ', '.join(task['scope']),
    ]
    if task['progress']:
        lines.append('已记录进展：' + task['progress'])
    steps = task['steps']
    lines.append(f"任务步骤：{sum(row['state'] == 'done' for row in steps)}/{len(steps)} 已完成")
    for row in steps[:20]:
        lines.append(f"步骤 {row['id']} [{row['state']}] {row['title']}")
    if len(steps) > 20:
        lines.append(f'另有 {len(steps) - 20} 个步骤；输入 /task status 查看。')
    for row in task['acceptance'][:20]:
        lines.append(f"已记录验收 {row['id']} [{row['status']}] {row['description']}")
    if len(task['acceptance']) > 20:
        lines.append(f"另有 {len(task['acceptance']) - 20} 项验收；输入 /task status 查看。")
    lines.extend('待验证：' + value for value in task['pending_verification'][:10])
    lines.extend('阻塞：' + value for value in task['blockers'][:10])
    lines.append('以上为保存的任务记录，未重新核对当前文件或执行验证。')
    return '\n'.join(lines)
