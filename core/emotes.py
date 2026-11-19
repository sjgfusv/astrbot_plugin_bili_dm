"""同步 B 站表情包。

官方接口 `x/emote/user/panel/web` 需要登录 Cookie 才能拿到会员/购买的表情包；
没登录或请求失败时用内置的一批常用表情码兜底——私信里表情本来就是 `[笑]` 这种
文本转义符，发出去客户端会自己渲染，不依赖图片。
"""

from __future__ import annotations

import httpx

from .config import UA

EMOTE_API = "https://api.bilibili.com/x/emote/user/panel/web"

# 无 Cookie / 接口不可用时的兜底表情码
BUILTIN_TEXTS = [
    "[微笑]", "[笑]", "[大笑]", "[偷笑]", "[害羞]", "[挤眼]", "[馋嘴]", "[花痴]",
    "[思考]", "[困]", "[睡]", "[猪头]", "[咳嗽]", "[酷]", "[汗]", "[泪]",
    "[大哭]", "[哈哈]", "[吐]", "[白眼]", "[黑线]", "[怒]", "[Menu]",
    "[打call]", "[赞]", "[给心心]", "[再见]", "[抠鼻]", "[ OK ]", "[加油]",
    "[加油武汉]", "[口罩]", "[视频卫星]", "[吃瓜]", "[doge]", "[滑稽]",
]

BUILTIN_PACKAGE = {
    "name": "常用表情",
    "items": [{"text": text, "url": ""} for text in BUILTIN_TEXTS],
}


def _https(url: object) -> str:
    return str(url or "").strip().replace("http://", "https://", 1)


async def fetch_emote_packages(cookie: dict) -> list[dict]:
    """拉当前账号可用的表情包；失败直接抛，让调用方用缓存兜底。"""
    headers = {
        "User-Agent": UA,
        "Referer": "https://message.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    }
    cookie_str = "; ".join(
        f"{k}={v}" for k, v in (cookie or {}).items() if v
    )
    if cookie_str:
        headers["Cookie"] = cookie_str

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=6.0)) as client:
        resp = await client.get(
            EMOTE_API, params={"business": "reply"}, headers=headers
        )
        payload = resp.json() or {}
    if payload.get("code") != 0:
        raise RuntimeError(f"表情接口返回 {payload.get('code')}")

    raw_packages = ((payload.get("data") or {}).get("packages")) or []
    packages: list[dict] = []
    for raw in raw_packages:
        if not isinstance(raw, dict):
            continue
        items = []
        for emote in raw.get("emote") or []:
            if not isinstance(emote, dict):
                continue
            if (emote.get("flags") or {}).get("no_access"):
                continue
            text = str(emote.get("text") or "").strip()
            if not text:
                continue
            item = {"text": text, "url": _https(emote.get("url"))}
            alias = (emote.get("meta") or {}).get("alias")
            if alias:
                item["alias"] = str(alias)
            items.append(item)
        if items:
            packages.append(
                {
                    "name": str(raw.get("text") or "表情包"),
                    "items": items,
                }
            )
    return packages
