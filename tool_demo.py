import json
from local_tools import read_file

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent / ".env")

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.environ["DEEPSEEK_BASE_URL"],
    timeout=30.0,
    max_retries=0,
)
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取当前项目根目录中的 UTF-8 文本文件，只支持 .txt 文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名，例如 notes.txt"
                    }
                },
                "required": ["filename"],
                "additionalProperties": False
            }
        }
    }
]
messages = [
    {"role": "user", "content": "请读取 notes.txt，告诉我里面写了什么。"}
]

print("正在请求模型……")

response = client.chat.completions.create(
    model=os.environ["DEEPSEEK_MODEL"],
    messages=messages,
    reasoning_effort="none",
    max_tokens=256,
    stream=False,
    tools=tools,
    tool_choice="auto",
)

message = response.choices[0].message

if message.tool_calls:
    # 保存模型提出的工具调用请求
    messages.append(message.model_dump())

    for call in message.tool_calls:
        print("准备执行工具：", call.function.name)

        try:
            arguments = json.loads(call.function.arguments)

            if call.function.name != "read_file":
                result = {"ok": False, "error": "未知工具"}
            elif not isinstance(arguments, dict) or not isinstance(
                arguments.get("filename"), str
            ):
                result = {"ok": False, "error": "filename 必须是字符串"}
            else:
                result = read_file(arguments["filename"])

        except (ValueError, TypeError):
            result = {"ok": False, "error": "工具参数无法解析或无效"}

        print("工具结果：", result)

        # 把本地执行结果加入对话
        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(result, ensure_ascii=False),
        })

    # 再次请求模型，让它根据工具结果回答
    final_response = client.chat.completions.create(
        model=os.environ["DEEPSEEK_MODEL"],
        messages=messages,
        tools=tools,
        tool_choice="none",
        reasoning_effort="none",
        max_tokens=512,
        stream=False,
    )

    print("最终回答：", final_response.choices[0].message.content)

else:
    print("普通回复：", message.content)