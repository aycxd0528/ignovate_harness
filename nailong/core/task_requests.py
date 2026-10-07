"""Conservative, local recognition of task status and explicit work requests."""
import re


_RESUME = {
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
    r'(?:修复|修改|实现|审查|重构|优化|排查|测试|验证|创建|新增|增加|编辑|删除|重命名|构建|运行|执行)'
    r'|^(?:please\s+)?(?:review|debug|implement|refactor|fix|create|edit|rename|build|test|run)\b',
    re.IGNORECASE,
)
_QUESTION = re.compile(r'是什么|为什么|怎么样|怎样|如何|是否|能否|可否|了吗|了么|先解释|解释一下|说明一下|\b(?:what|why|how|whether)\b', re.IGNORECASE)
_NO_EXECUTION = re.compile(
    r'(?:不要|不需要|禁止|暂不|先不|别)(?:再|先|立即|马上)?(?:恢复|继续)'
    r'|(?:不要|禁止|暂不|先不|别)(?:再)?执行(?:任务|任何操作|任何任务)'
    r'|^(?:请)?(?:仅|只(?:需|要)?)(?:解释|说明|查询)'
)


def task_request_kind(message: str) -> str:
    """Match whole status questions; a mixed question/change request stays work."""
    text = message.strip().rstrip('。.!！?？·').strip()
    if _STATUS.fullmatch(text):
        return 'status'
    if _NO_EXECUTION.search(text):
        return 'other'
    if text.casefold() in _RESUME:
        return 'resume'
    # A question about fixing/testing is not permission to resume that work.
    for clause in re.split(r'[，,；;\n]', text):
        if _WORK.search(clause.strip()) and not _QUESTION.search(clause):
            # An imperative may be politely phrased as a question ("请修改…可以吗？").
            if not clause.strip().endswith(('了吗', '了吗？', '了吗?', '了么', '了么？', '了么?')):
                return 'work'
    return 'other'


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
