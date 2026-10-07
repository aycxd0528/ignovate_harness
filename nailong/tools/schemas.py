"""One parameter model supplies both discovery schemas and runtime validation."""

from __future__ import annotations

import inspect
from typing import Any, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, create_model


def parameter_model(name: str, handler) -> type[BaseModel]:
    hints = get_type_hints(handler)
    fields = {}
    for key, parameter in inspect.signature(handler).parameters.items():
        annotation = hints.get(key, Any)
        options: dict[str, Any] = {}
        if annotation is str:
            options["max_length"] = 4096
        if key in {"path", "include", "resource", "reference", "document"}:
            options["description"] = (
                "文件路径默认限于项目内；本次启动显式启用完全文件访问时可使用项目外绝对/相对路径。"
                if key == "path" else "匹配过滤或当前上下文提供的准确资源引用。"
            )
        if key in {"pattern", "query", "name", "description", "command", "old_string"}:
            options["min_length"] = 1
        if key in {"content", "old_string", "new_string"}:
            options["max_length"] = 120_000
        if key == "plan_markdown":
            options.update(min_length=1, max_length=80_000)
        if key == "description":
            options["max_length"] = 16_000
        if key == "offset":
            options["ge"] = 1 if name == "read_file" else 0
            options["description"] = "读取文件时为从 1 开始的行号；历史/记忆/归档读取时为从 0 开始的字符偏移。"
        if key == "char_offset":
            options.update(ge=0, description="首个请求行内从 0 开始的字符偏移。")
        if key == "limit":
            maximum = {"read_file": 1000, "glob": 200, "grep": 100,
                       "search_text": 50, "list_files": 100, "memory_list": 100,
                       "memory_read": 16_000}.get(name, 1000)
            options.update(ge=1, le=maximum)
        if key == "max_chars":
            options.update(ge=1, le=12_000 if name == "read_file" else 6000)
        if key == "context":
            options.update(ge=0, le=5, description="匹配前后各包含的上下文行数。")
        if key == "timeout_seconds":
            options.update(ge=1, le=30, description="命令时限，单位秒；超时会清理进程组。")
        if key == "output_mode":
            annotation = Literal["content", "files_with_matches", "count"]
        if key == "pattern_mode":
            annotation = Literal["regex", "literal"]
        if name == "update_goal" and key == "state":
            annotation = Literal["active", "paused", "complete", "blocked"]
        if name in {"memory_list", "read_memory"} and key == "scope":
            annotation = Literal["", "user", "project", "local"] if name == "memory_list" else Literal["user", "project", "local"]
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        fields[key] = (annotation, Field(default, **options))
    return create_model(
        f"{name.title().replace('_', '')}Input",
        __config__=ConfigDict(extra="forbid", strict=True),
        **fields,
    )
