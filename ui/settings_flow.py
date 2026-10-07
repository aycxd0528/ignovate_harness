"""Shared setting choices for welcome, inline and plain terminals."""


def setting_listing(title, current, options):
    lines = [title+'：']
    for number, (key, label, description) in enumerate(options, 1):
        lines.append(f'{number}. {label}'+('（当前）' if key == current else '')+' · '+description)
    return '\n'.join(lines)


def _choice(answer, options):
    for number, (key, label, _) in enumerate(options, 1):
        if answer in {key, label, str(number)}:
            return key
    return None


def prompt_setting_choice_sync(title, current, options):
    print(setting_listing(title, current, options))
    while True:
        answer = input('选择序号或名称；Enter 保留当前，q 取消：').strip()
        if not answer: return current
        if answer.lower() == 'q': return None
        choice = _choice(answer, options)
        if choice is not None: return choice
        print('选择无效，请重试。')


async def prompt_setting_choice(title, current, options, prompt, output):
    output(setting_listing(title, current, options))
    while True:
        answer = (await prompt('选择序号或名称；Enter 取消：')).strip()
        if not answer: return None
        choice = _choice(answer, options)
        if choice is not None: return choice
        output('选择无效，请重试。')
