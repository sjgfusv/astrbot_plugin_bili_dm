"""版本号与元数据一致性检查（CI 与本地都能跑）。

两处版本号必须一致，否则插件市场显示的和实际加载的对不上：
- 根目录 metadata.yaml 的 version
- main.py 里 @register(...) 的第四个位置参数

顺带检查 category 是不是平台认的 key —— 写成中文会在插件详情页
显示成 [MISSING: features.extension.market.categories.xxx]。

用法：python tools/tests/check_version.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# AstrBot 插件市场的分类 key（对应前端 market.categories.* 文案）
CATEGORIES = {"ai_tools", "entertainment", "productivity", "integrations", "utilities", "other"}


def fail(message: str) -> int:
    print(f"[FAIL] {message}")
    return 1


def read_metadata() -> dict:
    text = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    info: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.+?)\s*$", line)
        if match:
            info[match.group(1)] = match.group(2).strip("'\"")
    return info


def read_register_version() -> str:
    text = (ROOT / "main.py").read_text(encoding="utf-8")
    block = re.search(r"@register\((.*?)\)\s*\nclass", text, re.S)
    if not block:
        raise SystemExit(fail("main.py 里找不到 @register(...) 调用"))
    # 只认纯版本号字面量，避免把插件名/描述当成版本
    found = re.findall(r'"(\d+\.\d+\.\d+(?:[-+][\w.]+)?)"', block.group(1))
    return found[-1] if found else ""


def norm(value: str) -> str:
    return value.strip().lstrip("vV")


def main() -> int:
    meta = read_metadata()
    meta_version = meta.get("version", "")
    reg_version = read_register_version()

    if not meta_version:
        return fail("metadata.yaml 缺少 version")
    if not reg_version:
        return fail("main.py 的 @register 里找不到版本号")
    if norm(meta_version) != norm(reg_version):
        return fail(
            f"版本号不一致：metadata.yaml={meta_version}，main.py={reg_version}"
        )

    category = meta.get("category", "")
    if not category:
        return fail("metadata.yaml 缺少 category（插件详情页会显示 MISSING）")
    if category not in CATEGORIES:
        return fail(
            f"category={category} 不是平台认的 key，可选：{', '.join(sorted(CATEGORIES))}"
        )

    if not meta.get("name"):
        return fail("metadata.yaml 缺少 name")
    if not meta.get("repo", "").startswith("http"):
        return fail("metadata.yaml 的 repo 必须是可访问的仓库地址")

    print(f"[OK] 版本 {norm(meta_version)}，分类 {category}，元数据齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
