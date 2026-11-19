"""让 tools/ 下的独立模式与测试把仓库根目录当作插件包来导入。

插件本体已从子目录上移到仓库根目录（Cloud 安装要求根目录即插件目录），
但仓库目录名 `bili-dm` 带连字符，不能直接当 Python 包名。这里注册一个
`astrbot_plugin_bili_dm` 包别名，指向仓库根目录，源码里的相对导入
（`from .core...`）与现有 import 路径都不用改。AstrBot 安装后目录名本来就是
`astrbot_plugin_bili_dm`，不经过本文件。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "astrbot_plugin_bili_dm"


def ensure_package_alias() -> None:
    if PACKAGE in sys.modules:
        return
    spec = importlib.util.spec_from_loader(PACKAGE, loader=None, is_package=True)
    spec.submodule_search_locations = [str(ROOT)]
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module


ensure_package_alias()
