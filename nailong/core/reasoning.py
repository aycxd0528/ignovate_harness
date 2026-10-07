"""Provider-backed reasoning settings, distinct from tool-call budgets."""

REASONING_LABELS = {
    'default': '模型默认', 'none': '关闭推理', 'low': '低', 'high': '高', 'max': '最高',
}
_V4_MODELS = {'deepseek-flash', 'deepseek-v4-pro', 'deepseek-v4-flash'}

PERMISSION_LABELS = {
    'default': '请求批准', 'acceptEdits': '帮我批准',
    'bypassPermissions': '完全访问权限', 'plan': '计划只读',
}
PERMISSION_OPTIONS = (
    ('default', '请求批准', '文件修改与命令执行均请求批准'),
    ('acceptEdits', '帮我批准', '项目内文件修改自动批准，命令执行仍需批准'),
    ('bypassPermissions', '完全访问权限', '任意文件路径与网络目标，跳过逐项审批；受系统权限约束'),
)


def reasoning_options(model: str) -> tuple[tuple[str, str, str], ...]:
    descriptions = {'default': '沿用模型服务端默认行为', 'none': '关闭思考模式',
                    'low': '较少推理，适合简单任务', 'high': '日常编码与调研',
                    'max': '更充分的推理，可能增加等待和用量'}
    keys = REASONING_LABELS if model in _V4_MODELS else ('default',)
    return tuple((key, REASONING_LABELS[key], descriptions[key]) for key in keys)


def model_reasoning_kwargs(model: str, effort: str = 'default') -> dict:
    if not isinstance(effort, str) or effort not in REASONING_LABELS:
        raise ValueError('推理强度只能为 default、none、low、high 或 max。')
    if effort == 'default':
        return {}
    if model not in _V4_MODELS:
        raise ValueError('当前模型的推理参数尚未确认；请先设置 /reasoning default。')
    return {'reasoning_effort': effort,
            'extra_body': {'thinking': {'type': 'disabled' if effort == 'none' else 'enabled'}}}
