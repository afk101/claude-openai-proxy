"""OpenAI-to-Claude API Proxy

一个代理服务，使 OpenAI Chat Completions 客户端能够调用 Claude Messages 兼容服务。
"""

from dotenv import load_dotenv

# 加载项目根目录 .env 文件中的环境变量（如果存在）
# 注意：已有的环境变量不会被覆盖
load_dotenv()
