"""Local JSON Schema validation; remote references never trigger network fetches."""
import json

from jsonschema import validators


def schema_validator(schema: dict):
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise ValueError('MCP 工具参数 schema 必须声明 object。')
    if len(json.dumps(schema, ensure_ascii=False)) > 64_000:
        raise ValueError('MCP 工具参数 schema 超过 64,000 字符。')
    def check(value, depth=0):
        if depth > 64:
            raise ValueError('MCP 工具参数 schema 嵌套过深。')
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'$ref', '$dynamicRef', '$recursiveRef'} and (not isinstance(item, str) or not item.startswith('#')):
                    raise ValueError('MCP 工具 schema 仅允许文档内引用。')
                if key in {'properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas', 'dependencies'} and isinstance(item, dict):
                    for subschema in item.values():
                        check(subschema, depth + 1)
                elif key in {'allOf', 'anyOf', 'oneOf', 'prefixItems', 'items', 'additionalItems',
                             'additionalProperties', 'unevaluatedProperties', 'unevaluatedItems',
                             'contains', 'propertyNames', 'not', 'if', 'then', 'else', 'contentSchema'}:
                    check(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                check(item, depth + 1)
    check(schema)
    cls = validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def check_provider_schema(name, schema, description=''):
    """Exercise the same LangChain conversion used when binding the actual tool."""
    from langchain_core.tools import StructuredTool
    from langchain_core.utils.function_calling import convert_to_openai_tool
    metadata = StructuredTool(name=name, description=description, args_schema=schema)
    convert_to_openai_tool(metadata)
