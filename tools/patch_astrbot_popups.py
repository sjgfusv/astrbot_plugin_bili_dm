r"""给 AstrBot 的插件视图 iframe 加 allow-popups（视频卡片新标签跳转需要）。

AstrBot 在 dashboard/dist/assets/PluginViewPage-*.js 里把 sandbox 写死了：

    sandbox="allow-scripts allow-forms allow-downloads"

没有配置项，只能改构建产物。脚本会先备份原文件，再做精确字符串替换；重复执行安全。

注意：
- AstrBot 升级会覆盖 dist，升级后需要重跑本脚本。
- 改完要在浏览器里 Ctrl+F5 强刷 Dashboard（JS 文件名带 hash 但内容变了，普通刷新可能吃到缓存）。
- 不重启 AstrBot 一般也生效（静态文件按磁盘读取），不生效就重启一次。

用法：
    python tools/patch_astrbot_popups.py
    # 自定义 AstrBot 安装位置 / 包路径：
    ASTRBOT_DIR=/path/to/astrbot python tools/patch_astrbot_popups.py
    ASTRBOT_PACKAGE=/path/to/site-packages/astrbot python tools/patch_astrbot_popups.py
"""

from __future__ import annotations

import os
from pathlib import Path

_ASTRBOT_DIR = os.environ.get("ASTRBOT_DIR", r"D:\astrbot")
DEFAULT_PACKAGE = Path(_ASTRBOT_DIR) / ".venv" / "Lib" / "site-packages" / "astrbot"

OLD = 'sandbox:"allow-scripts allow-forms allow-downloads"'
NEW = (
    'sandbox:"allow-scripts allow-forms allow-downloads '
    'allow-popups allow-popups-to-escape-sandbox"'
)


def main() -> int:
    root = Path(os.environ.get("ASTRBOT_PACKAGE") or str(DEFAULT_PACKAGE))
    assets = root / "dashboard" / "dist" / "assets"
    if not assets.is_dir():
        print(f"找不到 AstrBot dashboard 资源目录：{assets}", file=__import__("sys").stderr)
        return 1

    files = sorted(assets.glob("PluginViewPage-*.js"))
    if not files:
        print(f"在 {assets} 里没找到 PluginViewPage-*.js", file=__import__("sys").stderr)
        return 1

    changed = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        if NEW in text:
            print(f"已是补丁状态：{path.name}")
            continue
        if OLD not in text:
            print(f"未找到原 sandbox 字符串（版本可能变了）：{path.name}")
            continue

        backup = Path(str(path) + ".bak")
        if not backup.exists():
            backup.write_text(text, encoding="utf-8")
            print(f"  已备份：{backup.name}")
        path.write_text(text.replace(OLD, NEW), encoding="utf-8")
        changed += 1
        print(f"  已打补丁：{path.name}")

    print(f"\n完成：{changed} 个文件被修改。请 Ctrl+F5 强刷 Dashboard。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
