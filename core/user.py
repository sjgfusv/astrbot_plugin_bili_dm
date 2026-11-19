"""对端名片：拿昵称和头像，结果缓存到 sessions 表，不重复请求。"""

from __future__ import annotations

import httpx

from .config import UA

CARD_API = "https://api.bilibili.com/x/web-interface/card"


async def fetch_card(uid: int, cookie: dict) -> dict:
    """取用户名片。失败返回空 dict，调用方自行降级成 UID 显示。"""
    if not uid:
        return {}
    cookie_str = "; ".join(f"{k}={v}" for k, v in (cookie or {}).items() if v)
    headers = {
        "User-Agent": UA,
        "Referer": "https://message.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=6.0)) as client:
            resp = await client.get(CARD_API, params={"mid": uid}, headers=headers)
            payload = resp.json() or {}
    except (httpx.HTTPError, ValueError):
        return {}
    card = ((payload.get("data") or {}).get("card")) or {}
    return {
        "uid": int(card.get("mid") or uid),
        "name": card.get("name") or "",
        "face": card.get("face") or "",
    }
