"""面板业务层：复用现有 bilibili 适配器的客户端，不额外轮询。"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
from urllib.parse import urlsplit

from astrbot.api import logger

from . import bridge
from .core.config import Config
from .core.emotes import BUILTIN_PACKAGE, fetch_emote_packages
from .core.palette import read_palette
from .core.stickers import StickerStore
from .core.poller import EventBus
from .core.store import Store
from .core.user import fetch_card

# 与 bilibili 适配器一致：只允许把 B 站自有图片域名交给浏览器加载。
BILI_IMAGE_HOSTS = (".biliimg.com", ".hdslb.com")
IMAGE_PLACEHOLDER = "[图片]"


def trusted_image_url(value: object) -> str:
    """只接受 https 的 B 站图片地址，避免 raw_message 被塞进外部 URL。"""
    url = str(value or "").strip()
    if not url.lower().startswith("https://"):
        return ""
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    if not any(host.endswith(suffix) for suffix in BILI_IMAGE_HOSTS):
        return ""
    return url


def image_url_from_raw(raw: object) -> str:
    """适配器把原始消息保留在 raw_message，图片 URL 只在这里面。"""
    if not isinstance(raw, dict):
        return ""
    content = raw.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return ""
    if not isinstance(content, dict):
        return ""
    return trusted_image_url(content.get("url") or content.get("image_url"))


class PanelService:
    def __init__(self, context, data_dir) -> None:
        self.context = context
        self.cfg = Config.load(data_dir)
        self.store = Store(self.cfg.data_dir / "messages.db")
        self.stickers = StickerStore(self.cfg.data_dir)
        self._emote_cache: dict | None = None
        self._emote_at = 0.0
        # 老数据兼容：微秒时间戳改回秒，清掉历史遗留的重复占位消息
        self.store.normalize_timestamps()
        self.store.cleanup_local_duplicates()
        self.bus = EventBus()
        self._tasks: set[asyncio.Task] = set()
        self.last_error = ""
        self.last_sync = 0
        self._self_profile_at = 0.0  # 本人名片拉取节流

    # ---------------- 适配器 ----------------
    @property
    def adapter(self):
        return bridge.find_adapter(self.context)

    @property
    def client(self):
        adapter = self.adapter
        return bridge.get_client(adapter) if adapter else None

    def my_uid(self) -> int:
        adapter = self.adapter
        if adapter is None:
            return self.cfg.my_uid
        return bridge.get_self_uid(adapter) or self.cfg.my_uid

    def _cookie(self) -> dict:
        adapter = self.adapter
        return bridge.get_cookie(adapter) if adapter else {}

    # ---------------- 状态 ----------------
    def status_dict(self) -> dict:
        adapter = self.adapter
        client = bridge.get_client(adapter) if adapter else None
        if adapter is None:
            state = "error"
        elif client is None:
            state = "waiting_login"
        else:
            state = "online"
        info = bridge.describe(adapter) if adapter else {"found": False}
        return {
            "state": state,
            "last_sync": int(self.last_sync),
            "last_error": self.last_error,
            "interval": self.cfg.panel_refresh,
            "adapter": info,
            "peer_uid": self.cfg.peer_uid,
            "logged_in": client is not None,
        }

    def public_state(self) -> dict:
        adapter = self.adapter
        sessions = self.store.list_sessions()
        return {
            "config": self.cfg.public(),
            "status": self.status_dict(),
            "messages": {"count": self.store.count(), "unread": self.store.total_unread()},
            "sessions": {"count": len(sessions)},
            "runtime": {
                "mode": "astrbot-panel",
                "demo": False,
                "data_dir": str(self.cfg.data_dir),
            },
            "adapter": bridge.describe(adapter) if adapter else {"found": False},
            "palette": self.palette_dict(),
            "astrbot": {
                "platform_name": "bilibili",
                "adapter_display": (bridge.describe(adapter).get("display") if adapter else ""),
            },
        }

    def palette_dict(self) -> dict:
        """调色盘状态。读盘失败只降级，绝不影响面板主流程。"""
        if not self.cfg.palette_sync:
            return {"available": False, "enabled": False, "reason": "面板已关闭跟随"}
        cached = getattr(self, "_palette_cache", None)
        try:
            data = read_palette(self.cfg.data_dir)
        except Exception as exc:  # 适配层不该拖垮面板
            logger.debug(f"[bili_dm] 读取调色盘配置失败: {exc}")
            return {"available": False, "enabled": False, "reason": "读取异常"}
        signature = (data.get("available"), data.get("config_mtime"), data.get("enabled"))
        if cached and cached[0] == signature:
            return cached[1]
        self._palette_cache = (signature, data)
        return data

    # ---------------- 表情 ----------------
    async def emote_data(self) -> dict:
        """B 站表情包 + 本地表情，缓存 6 小时。失败就用内置表情码兜底。"""
        now = time.time()
        if self._emote_cache is not None and now - self._emote_at < 6 * 3600:
            return self._emote_cache

        packages: list[dict] = []
        error = ""
        try:
            packages = await fetch_emote_packages(self._cookie())
        except Exception as exc:  # 表情不可用不能影响面板
            error = str(exc)
        if not packages:
            packages = [BUILTIN_PACKAGE]
            error = error or "接口没返回表情包，已用内置表情码"

        data = {
            "packages": packages,
            "stickers": self.stickers.list_with_data(),
            "error": error,
            "synced_at": int(now),
        }
        self._emote_cache = data
        self._emote_at = now
        return data

    def add_sticker(self, name: str, encoded: str, ext: str) -> dict:
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise RuntimeError("图片数据不是合法的 base64") from None
        item = self.stickers.add(name, raw, ext)
        self._emote_cache = None
        return {"sticker": item, "stickers": self.stickers.list()}

    def delete_sticker(self, sid: str) -> dict:
        removed = self.stickers.delete(sid)
        self._emote_cache = None
        return {"ok": removed, "stickers": self.stickers.list()}

    def sticker_file(self, sid: str) -> tuple[bytes, str] | None:
        item = self.stickers.get(sid)
        if not item:
            return None
        try:
            return (self.stickers.root / item["file"]).read_bytes(), item["mime"]
        except OSError:
            return None

    async def send_sticker(self, peer: int, sid: str) -> dict:
        """本地表情按图片私信发送：复用适配器的 upload_image + send_image_message。"""
        if not peer:
            raise RuntimeError("缺少 peer 参数")
        if peer == self.my_uid():
            raise RuntimeError("不能给自己发私信")
        client = self.client
        if client is None:
            raise RuntimeError("B 站适配器还没就绪")
        upload = getattr(client, "upload_image", None)
        send_image = getattr(client, "send_image_message", None)
        if not callable(upload) or not callable(send_image):
            raise RuntimeError("当前适配器不支持发送图片表情")

        item = self.stickers.get(sid)
        if not item:
            raise RuntimeError("表情不存在")
        try:
            raw = (self.stickers.root / item["file"]).read_bytes()
        except OSError:
            raise RuntimeError("表情文件读取失败") from None

        info = await upload(raw, item["file"], f"sticker:{sid}")
        if not info:
            raise RuntimeError("图片上传失败")
        ok = await send_image(int(peer), info)
        if not ok:
            raise RuntimeError("表情发送失败，看 AstrBot 日志里的 B 站返回码")

        ts = int(time.time())
        saved = self.store.add_message(
            peer_uid=peer,
            sender_uid=self.my_uid(),
            direction="out",
            ts=ts,
            kind="image",
            text=str(info.get("image_url") or ""),
            msg_key=f"local-out-{time.time_ns()}-{peer}",
        )
        self.store.upsert_session(peer, last_text="[图片表情]", last_ts=ts, unread=None)
        if saved:
            self.bus.publish({"type": "message", "data": saved})
        return saved or {}

    # ---------------- 本人名片 ----------------
    async def ensure_self_profile(self) -> None:
        """登录后把自己的昵称/头像补进配置，面板左栏不再显示占位名。

        只拉一次，失败 5 分钟内不重试；适配器那边只给 UID，没有昵称和头像。
        """
        uid = self.my_uid()
        if not uid:
            return
        if self.cfg.my_uid == uid and self.cfg.my_name:
            return
        if time.time() - self._self_profile_at < 300:
            return
        self._self_profile_at = time.time()
        card = await fetch_card(uid, self._cookie())
        if not card.get("name"):
            return
        self.cfg.my_uid = uid
        self.cfg.my_name = card["name"]
        self.cfg.my_face = card.get("face") or ""
        self.cfg.save()

    # ---------------- 会话 ----------------
    def visible_sessions(self) -> list[dict]:
        my_uid = self.my_uid()
        items = [s for s in self.store.list_sessions() if s["peer_uid"] != my_uid]
        if self.cfg.scope == "single":
            uid = self.cfg.peer_uid
            items = [s for s in items if s["peer_uid"] == uid]
            if not any(s["peer_uid"] == uid for s in items) and uid and uid != my_uid:
                items = [
                    {
                        "peer_uid": uid,
                        "name": self.cfg.peer_name or "",
                        "face": "",
                        "last_text": "",
                        "last_ts": 0,
                        "unread": 0,
                    }
                ]
        for item in items:
            if not item.get("name"):
                item["name"] = f"UID {item['peer_uid']}"
        return items

    def _visible(self, peer: int) -> bool:
        if self.cfg.scope != "single":
            return True
        return int(peer) == int(self.cfg.peer_uid or 0)

    # ---------------- 历史消息 ----------------
    async def load_history(self, peer: int, limit: int = 50) -> list[dict]:
        client = self.client
        if client is None:
            raise RuntimeError("B 站适配器还没就绪，先在「消息平台」里确认它已启用")
        if not peer:
            raise RuntimeError("缺少 peer 参数")

        size = max(5, min(int(limit), 100))
        data = await client.get_messages(peer, 1, 0, size=size)
        self.last_sync = time.time()
        if not data:
            self.last_error = "拉取历史失败（接口无返回）"
            return self.store.messages_of(peer, size)

        raw_list = data.get("messages") or []
        if isinstance(raw_list, dict):
            raw_list = raw_list.get("messages") or []
        my_uid = self.my_uid()
        parsed = []
        for raw in raw_list:
            item = bridge.parse_raw(raw, my_uid)
            if item:
                parsed.append(item)
        parsed.sort(key=lambda x: (x["ts"], x["msg_key"]))

        self.last_error = ""
        for item in parsed:
            # 本地发送时先插了一条占位消息，这里认领成服务器那条，不再重复插入
            if (
                item["direction"] == "out"
                and item["msg_key"]
                and self.store.reconcile_outgoing(
                    peer, item["text"], item["ts"], item["msg_key"]
                )
            ):
                continue
            saved = self.store.add_message(
                peer_uid=peer,
                sender_uid=item["sender_uid"],
                direction=item["direction"],
                ts=item["ts"],
                kind=item["kind"],
                text=item["text"],
                msg_key=item["msg_key"] or None,
                raw=item["raw"],
            )
            if saved and item["direction"] == "in":
                self.store.bump_session(peer, item["text"], item["ts"], unread=False)

        if parsed:
            last = parsed[-1]
            self.store.upsert_session(
                peer, last_text=last["text"][:120], last_ts=last["ts"], unread=None
            )
        self._spawn_profile(peer)
        return self.store.messages_of(peer, size)

    async def _load_share_card(self, peer: int) -> None:
        """分享消息补拉历史：封面/标题/链接只在原始消息里有。"""
        try:
            await asyncio.sleep(0.4)  # 等适配器把这条同步到会话历史
            await self.load_history(peer, 30)
        except Exception as exc:  # 补拉失败不影响机器人
            logger.debug(f"[bili_dm] 分享卡片补拉失败: {exc}")
            return
        self.bus.publish({"type": "messages", "data": {"peer": peer}})

    # ---------------- 发送 ----------------
    async def send(self, peer: int, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise RuntimeError("内容为空")
        if not peer:
            raise RuntimeError("缺少 peer 参数")
        if peer == self.my_uid():
            raise RuntimeError("不能给自己发私信")
        client = self.client
        if client is None:
            raise RuntimeError("B 站适配器还没就绪")

        ok = await client.send_text_message(int(peer), text)
        if not ok:
            raise RuntimeError("发送失败，看 AstrBot 日志里的 B 站返回码")
        ts = int(time.time())
        saved = self.store.add_message(
            peer_uid=peer,
            sender_uid=self.my_uid(),
            direction="out",
            ts=ts,
            kind="text",
            text=text,
            # 秒级时间戳在同一秒连发两条会撞 dedupe_key，纳秒才够唯一
            msg_key=f"local-out-{time.time_ns()}-{peer}",
        )
        self.store.upsert_session(
            peer, last_text=text[:120], last_ts=ts, unread=None, add_unread=0
        )
        if saved:
            self.bus.publish({"type": "message", "data": saved})
        return saved or {}

    # ---------------- 收消息 ----------------
    def record_inbound(self, event) -> dict | None:
        """AstrBot 事件进来时调用，落库并推给面板。"""
        try:
            peer = int(event.get_sender_id())
        except (TypeError, ValueError):
            return None
        if not peer or peer == self.my_uid():
            return None

        text = (event.message_str or "").strip()
        kind = "text"
        if not text:
            text, kind = "[非文本消息]", "other"
        # 适配器把图片下载成 bytes 交给 AstrBot，消息链里没有 URL；
        # 但 raw_message 保留了原始私信数据，可取回 URL 让前端直接渲染。
        message_obj = getattr(event, "message_obj", None)
        for comp in getattr(message_obj, "message", []) or []:
            if type(comp).__name__.lower() == "image":
                kind = "image"
                text = (
                    image_url_from_raw(getattr(message_obj, "raw_message", None))
                    or IMAGE_PLACEHOLDER
                )
                break

        # 分享卡片在 AstrBot 事件里只剩纯文字，封面/作者/链接都丢了。
        # 不落占位行，改为补拉一次历史，让面板直接拿到完整卡片数据。
        if text.startswith("[对方分享了一个") or text.startswith("[分享]"):
            task = asyncio.create_task(self._load_share_card(peer))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return None

        ts = bridge.normalize_ts(
            getattr(getattr(event, "message_obj", None), "timestamp", 0) or time.time()
        )
        msg_id = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")

        saved = self.store.add_message(
            peer_uid=peer,
            sender_uid=peer,
            direction="in",
            ts=ts,
            kind=kind,
            text=text,
            msg_key=msg_id or None,
            raw={"source": "astrbot_event"},
        )
        if saved is None:
            return None
        self.store.bump_session(peer, text, ts, unread=True)
        self._spawn_profile(peer)
        if self._visible(peer):
            self.bus.publish({"type": "message", "data": saved})
            self.bus.publish(
                {"type": "sessions", "data": {"count": len(self.store.list_sessions())}}
            )
        return saved

    # ---------------- 会话刷新 ----------------
    async def refresh_sessions(self) -> int:
        client = self.client
        if client is None:
            raise RuntimeError("B 站适配器还没就绪")
        data = await client.get_new_sessions(0)
        self.last_sync = time.time()
        if not data:
            self.last_error = "拉取会话列表失败"
            return 0

        session_list = data.get("session_list") or []
        count = 0
        for s in session_list:
            try:
                peer = int(s.get("talker_id") or 0)
            except (TypeError, ValueError):
                continue
            if not peer or peer == self.my_uid():
                continue
            last_msg = s.get("last_msg") or {}
            item = bridge.parse_raw(
                {**last_msg, "sender_uid": last_msg.get("sender_uid", peer)}, self.my_uid()
            )
            self.store.upsert_session(
                peer,
                last_text=(item["text"][:120] if item else ""),
                # 会话列表的 session_ts 是微秒，统一成秒再落库
                last_ts=bridge.normalize_ts(
                    s.get("session_ts") or (item["ts"] if item else 0)
                ),
                unread=int(s.get("unread_count") or 0),
            )
            count += 1
        self.last_error = ""
        self.bus.publish({"type": "sessions", "data": {"count": count}})
        return count

    # ---------------- 配置 ----------------
    async def save_config(self, payload: dict) -> None:
        scope = payload.get("scope")
        if scope in ("all", "single"):
            self.cfg.scope = scope
        if "peer_uid" in payload:
            try:
                self.cfg.peer_uid = int(payload.get("peer_uid") or 0)
            except (TypeError, ValueError):
                raise RuntimeError("对端 UID 必须是数字") from None
            if self.cfg.peer_uid and self.cfg.peer_uid == self.my_uid():
                raise RuntimeError("不能把自己设为对端")
        if "panel_refresh" in payload:
            try:
                self.cfg.panel_refresh = max(3.0, float(payload["panel_refresh"]))
            except (TypeError, ValueError):
                raise RuntimeError("刷新间隔不合法") from None
        if "palette_sync" in payload:
            self.cfg.palette_sync = bool(payload["palette_sync"])
            self._palette_cache = None
        self.cfg.save()
        if self.cfg.peer_uid:
            self._spawn_profile(self.cfg.peer_uid)
        self.bus.publish({"type": "status", "data": self.status_dict()})

    def mark_read(self, peer: int) -> None:
        self.store.clear_unread(int(peer))
        self.bus.publish({"type": "status", "data": self.status_dict()})

    # ---------------- 名片 ----------------
    def _spawn_profile(self, peer: int) -> None:
        session = self.store.get_session(peer)
        if session and session.get("name"):
            return
        task = asyncio.create_task(self._fill_profile(peer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fill_profile(self, peer: int) -> None:
        card = await fetch_card(peer, self._cookie())
        if not card.get("name"):
            return
        self.store.set_profile(peer, card["name"], card.get("face") or "")
        with contextlib.suppress(Exception):
            self.bus.publish({"type": "sessions", "data": {"count": len(self.store.list_sessions())}})

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        logger.info("[bili_dm] 面板服务已停止")
