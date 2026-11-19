"""把仓库根目录当作插件目录，同步到 AstrBot 的 data/plugins/。

仓库结构在 2026-11-19 改成「根目录即插件目录」（Cloud 安装要求）：main.py、
core/、views/、metadata.yaml 都在仓库根，独立运行/开发工具在 tools/。
所以这里的 SRC 是仓库根，且必须排除 tools/、.workbuddy/ 等非插件内容。

为什么是复制，而不是目录联接：
AstrBot 的插件页面发现（dashboard/services/plugin_page_service.py）会先
``Path.resolve()`` 插件目录，再 ``relative_to(plugin_store_path)``。
目录联接会被解析到工作区路径，这一步直接抛 ValueError，结果 ``views/``
扫不到——侧栏没有入口，插件详情页的视图也打不开。所以 AstrBot 那边必须是
真实目录，工作区继续作为源码，改完跑一次本脚本即可。

用法：
    python tools/sync_to_astrbot.py
    python tools/sync_to_astrbot.py --clean     # 连目标里已删除的文件一起清掉
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
SRC = TOOLS.parent

# 默认本机路径；换机器时用 ASTRBOT_DIR 指向 AstrBot 根目录即可
_ASTRBOT_DIR = os.environ.get("ASTRBOT_DIR", r"D:\astrbot")
DEFAULT_DST = Path(_ASTRBOT_DIR) / "data" / "plugins" / "astrbot_plugin_bili_dm"
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".workbuddy", "tools", "data", ".venv"}
SKIP_FILES = {".gitignore", "HANDOFF.md"}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.name in SKIP_FILES:
            continue
        yield path


def is_link(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 bili-dm 插件到 AstrBot")
    parser.add_argument("--dst", default=str(DEFAULT_DST), help="AstrBot 插件目录")
    parser.add_argument("--clean", action="store_true", help="删除目标里源码已不存在的文件")
    args = parser.parse_args()

    dst = Path(args.dst)
    if not (SRC / "main.py").is_file() or not (SRC / "metadata.yaml").is_file():
        print(f"插件根目录不完整（缺 main.py / metadata.yaml）：{SRC}", file=sys.stderr)
        return 1
    if is_link(dst):
        print(
            f"目标仍是目录联接：{dst}\n"
            "请先删除这个联接，再运行本脚本——AstrBot 的插件页面发现不接受联接。",
            file=sys.stderr,
        )
        return 1

    copied = unchanged = 0
    source_files = set()
    for src_file in iter_files(SRC):
        rel = src_file.relative_to(SRC)
        source_files.add(rel.as_posix())
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if (
            not target.exists()
            or src_file.stat().st_size != target.stat().st_size
            or not filecmp.cmp(src_file, target, shallow=False)
        ):
            shutil.copy2(src_file, target)
            copied += 1
        else:
            unchanged += 1

    removed = 0
    if args.clean and dst.exists():
        for target in sorted(iter_files(dst), reverse=True):
            rel = target.relative_to(dst)
            if rel.as_posix() not in source_files:
                target.unlink()
                removed += 1

    print(f"同步完成：{copied} 个更新，{unchanged} 个未变，删除 {removed} 个")
    print(f"  源码：{SRC}（仓库根即插件目录）")
    print(f"  目标：{dst}")
    print("改完插件后运行本脚本，再到 WebUI 点一次插件「重载」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
