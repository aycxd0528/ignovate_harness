import os
from pathlib import Path
from dotenv import load_dotenv

# 找到与当前脚本放在同一目录的 .env
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path)

api_key = os.getenv("DEEPSEEK_API_KEY")
base_url = os.getenv("DEEPSEEK_BASE_URL")
model = os.getenv("DEEPSEEK_MODEL")

if api_key:
    print("密钥已读取")
else:
    print("没有读取到密钥，请检查配置")

print(f"接口地址：{base_url}")
print(f"模型名称：{model}")