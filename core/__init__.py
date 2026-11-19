"""bili-dm 核心：B 站私信客户端、轮询、存储、扫码登录。

这一层不依赖 AstrBot，也不依赖 FastAPI：
- AstrBot 插件（astrbot_plugin_bili_dm）复用它
- 独立运行模式（tools/local/server.py）也复用它
"""

from .config import Config, PLUGIN_NAME
from .runtime import BiliRuntime, get_runtime, peek_runtime, reset_runtime

__all__ = [
    "Config",
    "PLUGIN_NAME",
    "BiliRuntime",
    "get_runtime",
    "peek_runtime",
    "reset_runtime",
]
