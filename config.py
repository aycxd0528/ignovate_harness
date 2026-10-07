"""Load local model settings without printing secret values."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from nailong.core.connection import ConfigurationError, validate_connection


@dataclass(frozen=True)
class Settings:
    api_key: str
    api_base: str
    model: str
    project_root: Path
    cli_preferences: dict = field(default_factory=dict)
    environment_model: str | None = None
    reasoning_effort: str = 'default'


def select_project_root(raw_path: str) -> Path:
    """Resolve an explicitly selected, existing work-project directory."""
    try:
        root = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigurationError(f"项目目录不存在或无法访问：{raw_path}") from error
    if not root.is_dir():
        raise ConfigurationError(f"项目目录不是文件夹：{raw_path}")
    return root


def load_settings(*, config_path=None) -> Settings:
    project_root = Path(__file__).resolve().parent
    load_dotenv(project_root / ".env")

    names = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")
    values = {name: os.getenv(name, "").strip() for name in names}
    from nailong.core.bootstrap import BootstrapStore
    store = BootstrapStore(Path(config_path).parent) if config_path is not None else BootstrapStore()
    if config_path is not None:
        # Keep BootstrapStore's canonical parent anchor (macOS /var aliases /private/var).
        # The filename still goes through the store's symlink and boundary checks.
        store.path = store.path.with_name(Path(config_path).name)
    user = store.read()
    if user:
        provider = user['provider']
        values.update(DEEPSEEK_API_KEY=provider['api_key'], DEEPSEEK_BASE_URL=provider['api_base'],
                      DEEPSEEK_MODEL=provider['model'])
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigurationError(
            "缺少配置项：" + ", ".join(missing) + "。运行 ignovate --setup 配置，或检查程序目录的 .env 文件。"
        )
    base_url, model, api_key = validate_connection(values['DEEPSEEK_BASE_URL'],
                                                 values['DEEPSEEK_MODEL'], values['DEEPSEEK_API_KEY'])
    return Settings(api_key=api_key, api_base=base_url, model=model, project_root=project_root,
                    reasoning_effort=user.get('reasoning_effort', 'default'))
