"""B 站私信接口封装。

官方没有开放私信 API，这里用的是 Web 端在用的这批接口（api.vc.bilibili.com），
认证靠浏览器 Cookie：SESSDATA（身份）+ bili_jct（CSRF）。接口随时可能变，出错时
本模块会把 B 站的原始 code/msg 透出来，方便判断是登录失效还是风控。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .config import ORIGIN, REFERER, UA, Config

VC = "https://api.vc.bilibili.com"
API = "https://api.bilibili.com"
PASSPORT = "https://passport.bilibili.com"

# B 站返回码 -> 人话
CODE_HINTS = {
    -101: "账号未登录，Cookie 里的 SESSDATA 失效了，需要重新登录",
    -352: "被风控拦截（-352），通常要等十几分钟或换个网络/降低频率再试",
    -412: "请求被拦截（-412），检查请求头/Cookie 是否完整",
    -400: "请求参数不对",
    -500: "服务端错误",
    21011: "对方设置了不接收陌生人私信",
    25005: "私信被限制（可能因为非互关）",
    25006: "发送太频繁，被限流了",
    25007: "对方已关闭私信",
}


class BiliError(Exception):
    def __init__(self, code: int | str, msg: str = "", action: str = ""):
        self.code = code
        self.raw_msg = msg
        self.action = action
        hint = CODE_HINTS.get(int(code) if str(code).lstrip("-").isdigit() else 0, "")
        text = f"[{action}] code={code} {msg}"
        if hint:
            text += f" —— {hint}"
        super().__init__(text)


def _msg_text(msg_type: int, content: Any) -> tuple[str, str]:
    """把 B 站消息体压成 (kind, text)，非文本类给占位符。"""
    data: dict = {}
    if isinstance(content, str):
        try:
            data = json.loads(content) if content.strip().startswith("{") else {}
        except json.JSONDecodeError:
            data = {}
    elif isinstance(content, dict):
        data = content

    if msg_type == 1:
        return "text", str(data.get("content", "") or "")
    if msg_type == 2:
        return "image", str(data.get("url", "") or "[图片]")
    placeholders = {
        5: "[撤回了一条消息]",
        6: "[表情]",
        7: "[分享]",
        8: "[音频]",
        10: "[视频]",
        11: "[动态]",
        12: "[系统消息]",
        13: "[系统消息]",
        14: "[系统消息]",
        15: "[系统消息]",
    }
    return "other", placeholders.get(msg_type, f"[消息类型 {msg_type}]")


class BiliClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._http: httpx.AsyncClient | None = None

    # ---------- 基础设施 ----------
    async def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=10.0),
                follow_redirects=True,
                headers={
                    "User-Agent": UA,
                    "Referer": REFERER,
                    "Origin": ORIGIN,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                cookies=self.cfg.cookie,
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def sync_cookies(self) -> None:
        """配置里的 cookie 变更后同步给连接池。"""
        if self._http is not None:
            self._http.cookies.clear()
            for k, v in self.cfg.cookie.items():
                self._http.cookies.set(k, v)

    async def ensure_buvid(self) -> None:
        """补 buvid3/buvid4，少这两个字段更容易触发风控。"""
        if self.cfg.cookie.get("buvid3"):
            return
        try:
            c = await self.http()
            r = await c.get(f"{API}/x/frontend/finger/spi")
            data = (r.json() or {}).get("data") or {}
            b3, b4 = data.get("b_3"), data.get("b_4")
            if b3:
                self.cfg.cookie["buvid3"] = b3
                c.cookies.set("buvid3", b3)
                c.cookies.set("buvid4", b4 or "")
                self.cfg.save()
        except (httpx.HTTPError, ValueError):
            pass

    async def _request(self, method: str, url: str, *, action: str, **kwargs) -> dict:
        c = await self.http()
        try:
            r = await c.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise BiliError("network", f"请求失败：{exc}", action) from exc
        try:
            payload = r.json()
        except ValueError as exc:
            raise BiliError(r.status_code, f"返回不是 JSON：{r.text[:120]}", action) from exc
        if not isinstance(payload, dict):
            raise BiliError("bad-json", "返回结构异常", action)
        code = payload.get("code", 0)
        if code != 0:
            raise BiliError(code, payload.get("message") or payload.get("msg") or "", action)
        return payload.get("data") or {}

    # ---------- 账号 ----------
    async def nav(self) -> dict:
        c = await self.http()
        try:
            r = await c.get(f"{API}/x/web-interface/nav")
            payload = r.json() or {}
        except (httpx.HTTPError, ValueError) as exc:
            raise BiliError("network", str(exc), "获取账号信息") from exc
        data = payload.get("data") or {}
        if not data.get("isLogin"):
            raise BiliError(-101, "未登录", "获取账号信息")
        return {
            "mid": int(data.get("mid") or 0),
            "uname": data.get("uname") or "",
            "face": data.get("face") or "",
        }

    async def user_card(self, mid: int) -> dict:
        """取对端昵称/头像。取不到不算错误，面板里降级显示 mid。"""
        try:
            c = await self.http()
            r = await c.get(f"{API}/x/web-interface/card", params={"mid": mid})
            data = ((r.json() or {}).get("data") or {}).get("card") or {}
            return {
                "mid": int(data.get("mid") or mid),
                "name": data.get("name") or "",
                "face": data.get("face") or "",
                "sign": data.get("sign") or "",
            }
        except (httpx.HTTPError, ValueError):
            return {"mid": mid, "name": "", "face": "", "sign": ""}

    # ---------- 私信 ----------
    async def single_unread(self) -> dict:
        c = await self.http()
        return await self._request(
            "GET",
            f"{VC}/session_svr/v1/session_svr/single_unread",
            action="查未读",
            params={"unread_type": 0, "build": 0, "mobi_app": "web"},
        )

    async def get_sessions(self) -> list[dict]:
        c = await self.http()
        data = await self._request(
            "GET",
            f"{VC}/session_svr/v1/session_svr/get_sessions",
            action="拉会话列表",
            params={"session_type": 1, "group_id": 0, "sort_rule": 2, "build": 0, "mobi_app": "web"},
        )
        out = []
        for s in data.get("session_list") or []:
            out.append(
                {
                    "talker_id": int(s.get("talker_id") or 0),
                    "unread": int(s.get("unread_count") or 0),
                    "last_ts": int(s.get("session_ts") or 0),
                    "last_msg": _msg_text(int((s.get("last_msg") or {}).get("msg_type", 0)), (s.get("last_msg") or {}).get("content")),
                }
            )
        return out

    async def fetch_msgs(
        self,
        talker_id: int | None = None,
        size: int | None = None,
        begin_seqno: int = 0,
    ) -> list[dict]:
        """拉取与对端的消息，按时间正序返回标准化后的列表。

        begin_seqno 为 0 时拉最新一批；传某条消息的 msg_seqno 则往前翻更早的。
        """
        cfg = self.cfg
        talker = int(talker_id or cfg.peer_uid)
        if not talker:
            raise BiliError(-400, "还没设置对端 UID", "拉取消息")
        params = {
            "talker_id": talker,
            "session_type": 1,
            "size": size or cfg.history_size,
            "sender_device_id": 1,
            "build": 0,
            "mobi_app": "web",
        }
        if begin_seqno:
            params["begin_seqno"] = int(begin_seqno)
        data = await self._request(
            "GET",
            f"{VC}/svr_sync/v1/svr_sync/fetch_session_msgs",
            action="拉取消息",
            params=params,
        )
        out: list[dict] = []
        for m in data.get("messages") or []:
            msg_type = int(m.get("msg_type") or 0)
            kind, text = _msg_text(msg_type, m.get("content"))
            sender = int(m.get("sender_uid") or 0)
            out.append(
                {
                    "msg_key": str(m.get("msg_key") or ""),
                    "sender_uid": sender,
                    "peer_uid": talker,
                    "direction": "out" if sender == cfg.my_uid else "in",
                    "ts": int(m.get("timestamp") or 0),
                    "kind": kind,
                    "text": text,
                    "msg_type": msg_type,
                    "raw": m,
                }
            )
        out.sort(key=lambda x: (x["ts"], x["msg_key"]))
        return out

    async def send_text(self, receiver_uid: int | None = None, text: str = "", dev_id: str | None = None) -> dict:
        cfg = self.cfg
        receiver = int(receiver_uid or cfg.peer_uid)
        if not receiver:
            raise BiliError(-400, "还没设置对端 UID", "发送消息")
        if not text.strip():
            raise BiliError(-400, "内容为空", "发送消息")
        if not cfg.csrf:
            raise BiliError(-101, "Cookie 里缺少 bili_jct（CSRF）", "发送消息")

        form = {
            "msg[sender_uid]": str(cfg.my_uid),
            "msg[receiver_id]": str(receiver),
            "msg[receiver_type]": "1",
            "msg[msg_type]": "1",
            "msg[msg_status]": "0",
            "msg[dev_id]": dev_id or cfg.dev_id,
            "msg[timestamp]": str(int(time.time())),
            "msg[content]": json.dumps({"content": text}, ensure_ascii=False),
            "csrf": cfg.csrf,
            "csrf_token": cfg.csrf,
            "build": "0",
            "mobi_app": "web",
        }
        data = await self._request(
            "POST",
            f"{VC}/web_im/v1/web_im/send_msg",
            action="发送消息",
            data=form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://message.bilibili.com/",
            },
        )
        return {
            "msg_key": str(data.get("msg_key") or ""),
            "ts": int(time.time()),
        }

    async def mark_read(self, talker_id: int | None = None, ack_seqno: int = 0) -> None:
        cfg = self.cfg
        talker = int(talker_id or cfg.peer_uid)
        if not talker or not cfg.csrf:
            return
        await self._request(
            "POST",
            f"{VC}/session_svr/v1/session_svr/update_ack",
            action="标记已读",
            data={
                "talker_id": str(talker),
                "session_type": "1",
                "ack_seqno": str(ack_seqno),
                "csrf": cfg.csrf,
                "csrf_token": cfg.csrf,
                "build": "0",
                "mobi_app": "web",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
