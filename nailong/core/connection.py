"""Model connection validation using only the Python standard library."""

from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when required local Agent configuration is missing or invalid."""


def validate_connection(base_url: str, model: str, api_key: str) -> tuple[str, str, str]:
    if not all(isinstance(value, str) for value in (base_url, model, api_key)):
        raise ConfigurationError('模型连接配置必须为文本。')
    base_url, model, api_key = base_url.strip(), model.strip(), api_key.strip()
    if any(char.isspace() or ord(char)<32 or ord(char)==127 for char in base_url):
        raise ConfigurationError('DEEPSEEK_BASE_URL 不能包含空白或控制字符。')
    if not api_key or any(ord(char)<32 or ord(char)==127 for char in api_key):
        raise ConfigurationError('DEEPSEEK_API_KEY 不能为空或包含控制字符。')
    if not model or any(char.isspace() or ord(char)<32 or ord(char)==127 for char in model):
        raise ConfigurationError('DEEPSEEK_MODEL 不能为空或包含空白、控制字符。')
    try:
        parsed_url = urlsplit(base_url)
        port = parsed_url.port
    except ValueError as error:
        raise ConfigurationError(
            "DEEPSEEK_BASE_URL 必须是包含有效主机名和端口的 HTTP(S) 地址。"
        ) from error
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or (port is not None and not 1 <= port <= 65535)
        or parsed_url.username is not None or parsed_url.password is not None
        or parsed_url.query or parsed_url.fragment
        or parsed_url.netloc.endswith(":")
    ):
        raise ConfigurationError(
            "DEEPSEEK_BASE_URL 必须是包含有效主机名和端口的 HTTP(S) 地址。"
        )

    return base_url.rstrip('/'), model, api_key
