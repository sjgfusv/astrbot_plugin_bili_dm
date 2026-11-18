"""AstrBot 调色盘（astrbot_plugin_palette）适配。

调色盘的工作方式是在 Dashboard 的 ``index.html`` 里注入 style/script，把整个
WebUI 的容器、卡片和 ``.plugin-view-frame`` 都改成透明 + 毛玻璃。但插件视图是
独立 iframe 文档，那份注入进不来——面板会变成壁纸上一块死白的板子。

iframe 又带了 ``sandbox="allow-scripts ..."``（没有 ``allow-same-origin``），
所以面板也读不到父页面的 CSS 变量，只能由后端把调色盘的配置读出来下发给前端，
让面板自己复刻同一套观感：透明底 + 玻璃表面 + 主色联动 + 文字可读性增强。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PALETTE_PLUGIN = "astrbot_plugin_palette"

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")

TEXT_MODES = {"off", "soft_shadow", "stroke"}


def _hex(value: Any) -> str:
    """只接受 6 位十六进制，其它一律当没配。"""
    match = _HEX.match(str(value or "").strip())
    return f"#{match.group(1).lower()}" if match else ""


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def _int(value: Any, low: int, high: int, default: int) -> int:
    return int(round(_clamp(value, low, high, default)))


def astrbot_data_dir(data_dir: Path | str | None) -> Path | None:
    """从插件数据目录反推 AstrBot 的 data 目录。

    AstrBot 插件数据固定在 ``<root>/data/plugin_data/<插件名>``；独立模式下这个
    结构不成立，于是允许用 ``ASTRBOT_DATA`` 环境变量显式指定（方便本地预览）。
    """
    if data_dir is not None:
        path = Path(data_dir)
        if path.parent.name == "plugin_data":
            return path.parent.parent
    env = os.environ.get("ASTRBOT_DATA", "").strip()
    return Path(env) if env else None


def config_path(data_dir: Path | str | None) -> Path | None:
    root = astrbot_data_dir(data_dir)
    if root is None:
        return None
    return root / "config" / f"{PALETTE_PLUGIN}_config.json"


def _unavailable(reason: str, path: Path | None = None) -> dict[str, Any]:
    return {
        "available": False,
        "enabled": False,
        "reason": reason,
        "config_path": str(path) if path else "",
    }


def read_palette(data_dir: Path | str | None = None) -> dict[str, Any]:
    """读取调色盘配置，缺点是不抛异常——面板不该因为它没装就挂掉。"""
    path = config_path(data_dir)
    if path is None:
        return _unavailable("找不到 AstrBot 数据目录")
    if not path.exists():
        return _unavailable("未检测到调色盘配置（没装，或它还没保存过设置）", path)

    try:
        # 调色盘写出来的是带 BOM 的 UTF-8
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return _unavailable("调色盘配置读取失败", path)
    if not isinstance(raw, dict):
        return _unavailable("调色盘配置格式异常", path)

    primary = _hex(raw.get("theme_primary"))
    secondary = _hex(raw.get("theme_secondary"))
    auto_theme = bool(raw.get("auto_theme_enabled", True))

    # 只有开了自动取色并成功算过色，主色才真的会被调色盘注入 Dashboard
    theme_active = bool(auto_theme and primary and secondary)

    mode = str(raw.get("text_enhancement_mode") or "soft_shadow").strip()
    if mode not in TEXT_MODES:
        mode = "soft_shadow"

    wallpaper = (
        str(raw.get("landscape_background_image") or "").strip()
        or str(raw.get("portrait_background_image") or "").strip()
        or str(raw.get("background_image") or "").strip()
    )

    return {
        "available": True,
        "enabled": bool(raw.get("enabled")),
        "reason": "",
        "config_path": str(path),
        "config_mtime": int(path.stat().st_mtime),
        "theme_active": theme_active,
        "primary": primary,
        "secondary": secondary,
        "surface_opacity": round(_clamp(raw.get("surface_opacity"), 0.0, 1.0, 0.0), 3),
        "glass_blur": _int(raw.get("stats_card_blur"), 0, 40, 14),
        "mobile_glass": _int(raw.get("mobile_sidebar_glass"), 0, 40, 18),
        "text_enhancement": {
            "mode": mode,
            "strength": round(
                _clamp(raw.get("text_enhancement_strength"), 0.0, 2.0, 1.0), 2
            ),
        },
        "wallpaper": {
            "present": bool(wallpaper),
            "dynamic": bool(raw.get("dynamic_background_enabled", True)),
            "dim": round(_clamp(raw.get("background_dim"), 0.0, 1.0, 0.5), 3),
            "blur": _int(raw.get("background_blur"), 0, 40, 0),
        },
    }
