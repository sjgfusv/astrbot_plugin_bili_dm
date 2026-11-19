"""桥接层：找到用户已经装好的 bilibili 私信适配器，复用它的客户端。

这样面板不额外建连接、不重复轮询，也不会和现有适配器抢同一个账号。
"""

from __future__ import annotations

import json
from typing import Any

ADAPTER_NAMES = ("bilibili", "Bilibili_Adapter", "bilibili_adapter")


def normalize_ts(value: object) -> int:
    """把 B 站各种精度的时间戳统一成秒。

    `get_sessions` 的 `session_ts` 实测是微秒（16 位），而 `fetch_session_msgs`
    里的 `timestamp` 是秒。前端统一按秒显示，混着存就会出现 Invalid Date。
    """
    try:
        number = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0
    if number >= 10**14:      # 微秒
        return number // 1_000_000
    if number >= 10**11:      # 毫秒
        return number // 1_000
    return number


def find_adapter(context) -> Any | None:
    """在已加载的平台实例里找 B 站私信适配器。"""
    try:
        insts = context.platform_manager.get_insts()
    except Exception:
        return None
    for inst in insts or []:
        try:
            name = inst.meta().name
        except Exception:
            continue
        if name in ADAPTER_NAMES:
            return inst
    return None


def get_client(adapter) -> Any | None:
    return getattr(adapter, "client", None)


def get_self_uid(adapter) -> int:
    for attr in ("_self_uid", "self_uid"):
        value = getattr(adapter, attr, None)
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return 0


def get_cookie(adapter) -> dict:
    """从适配器配置里拼出 cookie，用于取对端名片。"""
    cfg = getattr(adapter, "config", None) or {}
    cookie: dict[str, str] = {}
    for key in ("SESSDATA", "bili_jct", "buvid3", "buvid4", "DedeUserID"):
        value = cfg.get(key)
        if value:
            cookie[key] = str(value)
    return cookie


def describe(adapter) -> dict:
    """面板右上角展示用的适配器状态。"""
    if adapter is None:
        return {"found": False, "name": "", "ready": False, "uid": 0, "error": ""}
    client = get_client(adapter)
    status = getattr(adapter, "status", None)
    return {
        "found": True,
        "name": getattr(adapter.meta(), "name", "bilibili"),
        "display": getattr(adapter.meta(), "adapter_display_name", "") or "Bilibili 私信",
        "ready": client is not None,
        "uid": get_self_uid(adapter),
        "running": bool(getattr(adapter, "_running", False)),
        "poll_interval": getattr(adapter, "poll_interval", None),
        "status": getattr(status, "value", "") if status is not None else "",
        "error": "",
    }


# ---------------- 原始消息解析 ----------------
PLACEHOLDERS = {
    5: "[撤回了一条消息]",
    6: "[表情]",
    7: "[分享]",
    8: "[音频]",
    10: "[视频]",
    11: "[动态]",
    14: "[分享]",
}


def parse_raw(raw: dict, my_uid: int) -> dict | None:
    """把 B 站原始私信压成面板能渲染的记录；无法识别返回 None。"""
    msg_type = int(raw.get("msg_type") or 0)
    sender = int(raw.get("sender_uid") or 0)
    content = raw.get("content")
    data: dict = {}
    if isinstance(content, str):
        try:
            data = json.loads(content) if content.strip().startswith("{") else {}
        except json.JSONDecodeError:
            data = {}
    elif isinstance(content, dict):
        data = content

    kind = "text"
    text = ""
    if msg_type == 1:
        text = str(data.get("content") or "")
        if not text:
            return None
    elif msg_type == 2:
        kind = "image"
        text = str(data.get("url") or data.get("image_url") or "")
        if not text:
            return None
    elif msg_type in (7, 14):
        kind = "other"
        title = data.get("title") or data.get("desc") or ""
        text = f"[分享] {title}".strip()
    else:
        kind = "other"
        text = PLACEHOLDERS.get(msg_type, f"[消息类型 {msg_type}]")

    return {
        "msg_key": str(raw.get("msg_key") or ""),
        "sender_uid": sender,
        "direction": "out" if my_uid and sender == my_uid else "in",
        "ts": normalize_ts(raw.get("timestamp")),
        "kind": kind,
        "text": text,
        "raw": raw,
    }
