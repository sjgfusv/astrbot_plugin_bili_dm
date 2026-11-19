"""AstrBot 插件入口：B 站私信面板。

不自己登录、不自己轮询——挂在你已经装好的 bilibili 私信适配器上：
- 历史消息：调它的 client.get_messages()
- 发消息：调它的 client.send_text_message()
- 实时收消息：监听 AstrBot 事件流（platform == bilibili）

所以不会出现两个适配器抢同一个账号的问题。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.web import error_response, json_response, request, stream_response

from .core.config import PLUGIN_NAME
from .service import PanelService


def plugin_data_dir() -> Path:
    """AstrBot 规定的插件数据目录：data/plugin_data/<插件名>/"""
    try:  # v4.30+
        from astrbot.api.util import get_astrbot_data_path
    except ImportError:  # 旧版本
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME


@register(
    PLUGIN_NAME,
    "bili-dm",
    "B 站私信面板：复用现有 bilibili 适配器，在 WebUI 里查看和回复私信",
    "0.4.2",
)
class BiliDmPanelPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self.svc = PanelService(context, plugin_data_dir())
        self._register_web_apis()

    async def terminate(self) -> None:
        with contextlib.suppress(Exception):
            await self.svc.close()

    # ---------------- 事件监听：实时收消息 ----------------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_platform_message(self, event: AstrMessageEvent):
        """只记录 B 站私信，不拦截、不改变 AstrBot 的正常处理。"""
        try:
            if event.get_platform_name() not in ("bilibili", "Bilibili_Adapter"):
                return
            self.svc.record_inbound(event)
        except Exception as exc:  # 面板出错不能影响机器人
            logger.error(f"[bili_dm] 记录消息失败: {exc}")

    # ---------------- Web API ----------------
    def _register_web_apis(self) -> None:
        prefix = f"/{PLUGIN_NAME}"
        routes = [
            (f"{prefix}/state", self.api_state, ["GET"], "面板状态"),
            (f"{prefix}/sessions", self.api_sessions, ["GET"], "会话列表"),
            (f"{prefix}/messages", self.api_messages, ["GET"], "会话消息"),
            (f"{prefix}/send", self.api_send, ["POST"], "发送私信"),
            (f"{prefix}/refresh", self.api_refresh, ["POST"], "刷新会话/消息"),
            (f"{prefix}/read", self.api_read, ["POST"], "标记已读"),
            (f"{prefix}/config", self.api_config, ["POST"], "面板设置"),
            (f"{prefix}/theme", self.api_theme, ["GET"], "调色盘主题状态"),
            (f"{prefix}/emotes", self.api_emotes, ["GET"], "表情列表"),
            (f"{prefix}/sticker/file", self.api_sticker_file, ["GET"], "读取本地表情"),
            (f"{prefix}/sticker/add", self.api_sticker_add, ["POST"], "导入本地表情"),
            (f"{prefix}/sticker/delete", self.api_sticker_delete, ["POST"], "删除本地表情"),
            (f"{prefix}/sticker/send", self.api_sticker_send, ["POST"], "发送图片表情"),
            (f"{prefix}/events", self.api_events, ["GET"], "SSE 事件流"),
        ]
        for route, handler, methods, desc in routes:
            self.context.register_web_api(route, handler, methods, desc)

    async def api_state(self):
        await self.svc.ensure_self_profile()
        return json_response(self.svc.public_state())

    async def api_theme(self):
        """给面板用的调色盘快照：配置没变时后端有缓存，轮询代价很低。"""
        return json_response(self.svc.palette_dict())

    async def api_emotes(self):
        return json_response(await self.svc.emote_data())

    async def api_sticker_file(self):
        sid = request.query.get("id", "")
        found = self.svc.sticker_file(sid)
        if not found:
            return error_response("表情不存在", status_code=404)
        raw, mime = found
        return stream_response(
            [raw],
            content_type=mime,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    async def api_sticker_add(self):
        payload = await request.json(default={})
        try:
            return json_response(
                self.svc.add_sticker(
                    str(payload.get("name") or ""),
                    str(payload.get("data") or ""),
                    str(payload.get("ext") or ""),
                )
            )
        except (RuntimeError, ValueError) as exc:
            return error_response(str(exc), status_code=400)

    async def api_sticker_delete(self):
        payload = await request.json(default={})
        return json_response(self.svc.delete_sticker(str(payload.get("id") or "")))

    async def api_sticker_send(self):
        payload = await request.json(default={})
        try:
            message = await self.svc.send_sticker(
                int(payload.get("peer") or 0), str(payload.get("id") or "")
            )
        except RuntimeError as exc:
            return error_response(str(exc), status_code=400)
        return json_response({"message": message})

    async def api_sessions(self):
        return json_response(
            {
                "sessions": self.svc.visible_sessions(),
                "scope": self.svc.cfg.scope,
                "peer_uid": self.svc.cfg.peer_uid,
            }
        )

    async def api_messages(self):
        peer = request.query.get("peer", 0, type=int)
        limit = request.query.get("limit", 50, type=int)
        if not peer:
            return error_response("缺少 peer 参数", status_code=400)
        if not self.svc._visible(peer):
            return error_response("当前设置为只显示指定的人", status_code=403)
        try:
            messages = await self.svc.load_history(peer, limit)
        except RuntimeError as exc:
            return error_response(str(exc), status_code=400)
        return json_response(
            {
                "messages": messages,
                "peer": {
                    "uid": peer,
                    "name": (self.svc.store.get_session(peer) or {}).get("name", ""),
                    "face": (self.svc.store.get_session(peer) or {}).get("face", ""),
                },
                "me": {"uid": self.svc.my_uid(), "name": self.svc.cfg.my_name},
            }
        )

    async def api_send(self):
        payload = await request.json(default={})
        peer = int(payload.get("peer") or self.svc.cfg.peer_uid or 0)
        text = str(payload.get("text") or "")
        if not self.svc._visible(peer):
            return error_response("当前设置为只显示指定的人", status_code=403)
        try:
            saved = await self.svc.send(peer, text)
        except RuntimeError as exc:
            return error_response(str(exc), status_code=400)
        return json_response({"ok": True, "message": saved})

    async def api_refresh(self):
        payload = await request.json(default={})
        peer = int(payload.get("peer") or 0)
        try:
            if peer:
                messages = await self.svc.load_history(peer, int(payload.get("limit") or 50))
                return json_response({"ok": True, "messages": messages})
            count = await self.svc.refresh_sessions()
            return json_response({"ok": True, "sessions": count})
        except RuntimeError as exc:
            return error_response(str(exc), status_code=400)

    async def api_read(self):
        payload = await request.json(default={})
        peer = int(payload.get("peer") or 0)
        if peer:
            self.svc.mark_read(peer)
        return json_response({"ok": True})

    async def api_config(self):
        payload = await request.json(default={})
        try:
            await self.svc.save_config(payload)
        except RuntimeError as exc:
            return error_response(str(exc), status_code=400)
        return json_response({"ok": True, "state": self.svc.public_state()})

    async def api_events(self):
        queue = self.svc.bus.subscribe()

        async def gen():
            try:
                yield f"data: {json.dumps({'type': 'hello'})}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=20)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                    except asyncio.CancelledError:
                        break
            finally:
                self.svc.bus.unsubscribe(queue)

        return stream_response(gen())

    # ---------------- 聊天指令 ----------------
    @filter.command("bili_panel")
    async def bili_panel(self, event: AstrMessageEvent):
        """查看面板状态"""
        st = self.svc.status_dict()
        adapter = st["adapter"]
        if not adapter.get("found"):
            yield event.plain_result(
                "没找到 bilibili 私信适配器。请在「配置 → 消息平台」里启用它，然后再试。"
            )
            return
        if not adapter.get("ready"):
            yield event.plain_result("找到适配器了，但客户端还没就绪，稍等几秒或看 AstrBot 日志。")
            return
        scope = "所有会话" if self.svc.cfg.scope == "all" else f"只看 {self.svc.cfg.peer_uid}"
        yield event.plain_result(
            f"面板就绪：{adapter.get('display')}（UID {adapter.get('uid')}）\n"
            f"显示范围：{scope}\n"
            f"已记录 {self.svc.store.count()} 条消息 / {len(self.svc.store.list_sessions())} 个会话"
        )
