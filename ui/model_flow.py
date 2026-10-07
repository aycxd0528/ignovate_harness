"""Model selection and configuration shared by terminal adapters."""
from dataclasses import dataclass

from nailong.core.preferences import validate_new_model


@dataclass(frozen=True)
class ModelChoice:
    name: str
    model_id: str | None = None


def model_listing(preferences: dict) -> str:
    rows = [f"当前模型：{preferences['model_name']} · {preferences['model']}"]
    for index, (name, definition) in enumerate(preferences['models'].items(), 1):
        marker = '（当前）' if definition['model'] == preferences['model'] else ''
        rows.append(f"{index}. {name} · {definition['model']}{marker}")
    rows.append('切换：/model <名称>；新增并切换：/model add <名称> <模型ID> [--global]')
    return '\n'.join(rows)


async def prompt_model_choice(preferences, prompt, output, *, add_only=False):
    """Return a choice without writing; empty input cancels any form step."""
    models = preferences['models']
    if not add_only:
        output(model_listing(preferences))
        names = list(models)
        while True:
            answer = (await prompt('选择模型（序号或名称；a 新增；Enter 取消）：')).strip()
            if not answer:
                return None
            if answer in models:
                return ModelChoice(answer)
            if answer.isdecimal() and 1 <= int(answer) <= len(names):
                return ModelChoice(names[int(answer)-1])
            if answer.lower() in {'a', 'add'} or answer == '新增':
                break
            output('选择无效，请输入模型序号、名称或 a。')
    output('新增模型沿用当前 API 地址和密钥；名称和模型 ID 留空可取消。')
    while True:
        name = (await prompt('模型名称：')).strip()
        if not name:
            return None
        try:
            validate_new_model(name, 'placeholder', models)
            break
        except ValueError as error:
            output(str(error))
    while True:
        model_id = (await prompt('模型 ID：')).strip()
        if not model_id:
            return None
        try:
            name, model_id = validate_new_model(name, model_id, models)
            return ModelChoice(name, model_id)
        except ValueError as error:
            output(str(error))
