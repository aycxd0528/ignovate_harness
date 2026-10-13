"""Native preference and read-only workspace adapters for GUI controls."""
from dataclasses import replace
from pathlib import Path
import sqlite3

from nailong.core.preferences import PreferenceStore, read_config, atomic_json, validate_new_model
from nailong.core.sessions import ProjectSessionStore


def model_preferences(controller):
    if getattr(controller.factory, 'preferences', None) is not None:
        return controller.factory.preferences
    from config import load_settings
    settings = getattr(controller.factory, 'settings', None) or load_settings()
    preferences = PreferenceStore(controller.project)
    preferences.default_model = getattr(settings, 'environment_model', None) or settings.model
    preferences.default_reasoning = getattr(settings, 'reasoning_effort', 'default')
    preferences.effective(cli=getattr(settings, 'cli_preferences', None))
    return preferences


def change_model(controller, payload):
    if controller.current_task or controller.queue:
        raise ValueError('请先完成或停止运行并移除队列，再修改模型。')
    controller.ensure_service()
    factory = controller.factory
    previous = factory.preferences
    current = previous.effective()
    preferences = PreferenceStore(controller.project, user_path=previous.user_path)
    preferences.default_model = previous.default_model
    preferences.default_reasoning = previous.default_reasoning
    local = read_config(preferences.local_path, controller.project)
    action = payload.get('action')
    if action == 'add':
        name, model = validate_new_model(payload.get('name'), payload.get('model'), current['models'])
        local['models'] = {**local.get('models', {}), name: {'model': model}}
    elif action == 'select':
        name = payload.get('name')
        if not isinstance(name, str) or name not in current['models']:
            raise ValueError('模型未配置，请重新打开模型列表。')
    else:
        raise ValueError('模型操作无效。')
    local.update(model=name, reasoning_effort='default')
    # Validate and build locally before publishing the atomic preference write.
    updated = preferences.effective(_replacement=(preferences.local_path, local))
    old_settings, old_model = factory.settings, factory.model
    try:
        factory.settings = replace(old_settings, reasoning_effort='default')
        factory.set_model(updated['model'])
        atomic_json(preferences.local_path, local, controller.project)
    except BaseException:
        factory.settings, factory.model = old_settings, old_model
        raise
    factory.preferences = preferences
    result = controller.configuration()
    controller.emit('configuration_updated', result)
    return result


def workspace_catalog(controller, paths):
    if not isinstance(paths, list) or len(paths) > 8 or any(not isinstance(p, str) for p in paths):
        raise ValueError('工作区列表无效。')
    result = []
    for value in dict.fromkeys(paths):
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            continue
        sessions = ProjectSessionStore(path, base_dir=controller.sessions.root.parent.parent)
        branches = []
        database = sessions.root / 'branches.sqlite'
        if database.is_file():
            try:
                with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
                    db.row_factory = sqlite3.Row
                    branches = [dict(row) for row in db.execute('''SELECT b.*,
                        COALESCE((SELECT MAX(created) FROM nodes WHERE branch_id=b.id),b.created) AS updated_at
                        FROM branches b ORDER BY b.created''')]
            except sqlite3.Error:
                continue
        result.append({'id': sessions.root.name, 'path': str(path), 'name': path.name, 'branches': branches})
    return result


def validate_goal(factory, limits):
    from nailong.core.costs import CostEstimator
    import math
    if not isinstance(limits, dict):
        raise ValueError('目标限制无效。')
    rounds, cost = limits.get('max_rounds', 20), limits.get('max_cost_usd', 1.0)
    if type(rounds) is not int or not 1 <= rounds <= 100 or type(cost) not in (int, float) or not math.isfinite(cost) or not 0 < cost <= 100:
        raise ValueError('目标限制须为 1–100 轮、0–100 美元之间的正数。')
    if not CostEstimator(factory.settings.model, factory.settings.project_root).available:
        raise ValueError('当前模型没有价格配置，请先配置单价再使用目标模式。')
    return {'max_rounds': rounds, 'max_cost_usd': cost}
