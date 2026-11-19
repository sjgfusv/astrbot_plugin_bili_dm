#!/usr/bin/env python
"""入口：python tools/run.py serve|login|status|peer|send|tail"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _bootstrap  # noqa: E402  把仓库根目录注册成插件包别名
from cli import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
