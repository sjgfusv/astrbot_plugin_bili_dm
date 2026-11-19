"""最小可用的 AstrBot API 桩，仅用于本地冒烟测试。

签名与 astrbot 4.29.4 的 wheel 源码保持一致，不实现框架内部逻辑。
"""

import logging

logger = logging.getLogger("astrbot")

__all__ = ["logger"]
