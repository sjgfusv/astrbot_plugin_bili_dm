"""运行时单例：一个进程里只应有一份轮询器，插件和独立服务共用。"""

from __future__ import annotations

import base64
import contextlib
import time
from pathlib import Path

from .bili import BiliClient, BiliError
from .config import PLUGIN_NAME, Config
from .emotes import BUILTIN_PACKAGE
from .login import QrLogin
from .palette import read_palette
from .stickers import StickerStore
from .poller import DemoPoller, EventBus, Poller
from .store import Store

MODE_ASTRBOT = "astrbot"
MODE_STANDALONE = "standalone"


class BiliRuntime:
    def __init__(self, data_dir: Path | str, mode: str = MODE_STANDALONE, demo: bool = False):
        self.mode = mode
        self.cfg = Config.load(data_dir)
        self.cfg.demo = demo
        self.store = Store(self.cfg.demo_db_path if demo else self.cfg.db_path)
        self.stickers = StickerStore(self.cfg.data_dir)
        self.bus = EventBus()
        self.poller = (
            DemoPoller(self.cfg, self.store, self.bus)
            if demo
            else Poller(self.cfg, self.store, self.bus)
        )
        self.qr: QrLogin | None = None
        self._started = False

    # ---------------- 生命周期 ----------------
    async def ensure_started(self) -> None:
        """幂等启动轮询；插件适配器和面板谁先起来都行。"""
        if self._started:
            return
        self._started = True
        await self.poller.start()

    async def stop(self) -> None:
        self._started = False
        await self.poller.stop()
        if self.qr:
            with contextlib.suppress(Exception):
                await self.qr.aclose()
            self.qr = None
        if isinstance(self.poller, Poller):
            with contextlib.suppress(Exception):
                await self.poller.client.aclose()

    # ---------------- 状态 ----------------
    def public_state(self) -> dict:
        cfg = self.cfg
        base = f"http://{cfg.host}:{cfg.port}"
        state = {
            "config": cfg.public(),
            "status": self.poller.status_dict(),
            "messages": {"count": self.store.count(), "last_ts": self.store.last_ts()},
            "palette": self.palette_dict(),
            "runtime": {
                "mode": self.mode,
                "demo": bool(cfg.demo),
                "data_dir": str(cfg.data_dir),
            },
        }
        if self.mode == MODE_ASTRBOT:
            state["astrbot"] = {
                "platform_name": PLUGIN_NAME,
                "session_id": f"{PLUGIN_NAME}:FriendMessage:{cfg.peer_uid}" if cfg.peer_uid else "",
                "umop": f"{PLUGIN_NAME}:FriendMessage:{cfg.peer_uid}" if cfg.peer_uid else "",
            }
        else:
            state["onebot"] = {
                "enabled": cfg.onebot_enabled,
                "http": f"{base}/onebot",
                "ws": f"ws://{cfg.host}:{cfg.port}/onebot/ws",
                "token_set": bool(cfg.onebot_token),
            }
        return state

    # ---------------- 表情 ----------------
    def emote_data(self) -> dict:
        # 独立模式不拉账号表情包，直接用内置表情码 + 本地导入的表情
        return {
            "packages": [BUILTIN_PACKAGE],
            "stickers": self.stickers.list_with_data(),
            "error": "",
            "synced_at": int(time.time()),
        }

    def add_sticker(self, name: str, encoded: str, ext: str) -> dict:
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise BiliError(-400, "图片数据不是合法的 base64", "导入表情") from None
        try:
            item = self.stickers.add(name, raw, ext)
        except ValueError as exc:
            raise BiliError(-400, str(exc), "导入表情") from None
        return {"sticker": item, "stickers": self.stickers.list()}

    def delete_sticker(self, sid: str) -> dict:
        return {"ok": self.stickers.delete(sid), "stickers": self.stickers.list()}

    def sticker_file(self, sid: str) -> tuple[bytes, str] | None:
        item = self.stickers.get(sid)
        if not item:
            return None
        try:
            return (self.stickers.root / item["file"]).read_bytes(), item["mime"]
        except OSError:
            return None

    async def send_sticker(self, peer: int, sid: str) -> dict:
        client = getattr(self.poller, "client", None)
        upload = getattr(client, "upload_image", None)
        send_image = getattr(client, "send_image_message", None)
        if not callable(upload) or not callable(send_image):
            raise BiliError(-400, "当前模式不支持发送图片表情", "发送表情")
        item = self.stickers.get(sid)
        if not item:
            raise BiliError(-400, "表情不存在", "发送表情")
        raw = (self.stickers.root / item["file"]).read_bytes()
        info = await upload(raw, item["file"], f"sticker:{sid}")
        if not info:
            raise BiliError(-400, "图片上传失败", "发送表情")
        if not await send_image(int(peer), info):
            raise BiliError(-400, "表情发送失败", "发送表情")
        return {"ok": True, "image": str(info.get("image_url") or "")}

    def palette_dict(self) -> dict:
        """独立模式也读调色盘配置，方便本地预览同一套观感。"""
        if not self.cfg.palette_sync:
            return {"available": False, "enabled": False, "reason": "面板已关闭跟随"}
        try:
            return read_palette(self.cfg.data_dir)
        except Exception:  # 预览用，失败就当没装
            return {"available": False, "enabled": False, "reason": "读取异常"}

    # ---------------- 动作 ----------------
    async def send(self, text: str) -> dict:
        return await self.poller.send(text)

    async def refresh(self) -> None:
        await self.poller.refresh()

    async def mark_read(self) -> dict:
        return await self.poller.mark_read()

    def peer_card(self) -> dict:
        return {"uid": self.cfg.peer_uid, "name": self.cfg.peer_name}

    async def save_config(self, payload: dict) -> None:
        """面板/接口改配置的统一入口。"""
        cfg = self.cfg
        changed_peer = False

        if "peer_uid" in payload:
            try:
                uid = int(payload.get("peer_uid") or 0)
            except (TypeError, ValueError):
                raise BiliError(-400, "peer_uid 必须是数字", "保存配置") from None
            if uid and uid == cfg.my_uid:
                raise BiliError(-400, "不能把自己设为对端", "保存配置")
            if uid != cfg.peer_uid:
                cfg.peer_uid = uid
                cfg.peer_name = ""
                changed_peer = True
                if uid and not cfg.demo:
                    probe = BiliClient(cfg)
                    try:
                        card = await probe.user_card(uid)
                        cfg.peer_name = card.get("name") or ""
                    finally:
                        await probe.aclose()

        if payload.get("cookie_string"):
            cfg.apply_cookie_string(str(payload["cookie_string"]))
            self.poller.reset()
            if isinstance(self.poller, Poller):
                self.poller.client.sync_cookies()
            if not cfg.demo:
                with contextlib.suppress(BiliError):
                    me = await self.poller.client.nav()
                    cfg.my_uid, cfg.my_name, cfg.my_face = me["mid"], me["uname"], me["face"]

        if "poll_interval" in payload:
            try:
                cfg.poll_interval = max(3.0, float(payload["poll_interval"]))
            except (TypeError, ValueError):
                raise BiliError(-400, "poll_interval 不合法", "保存配置") from None
        if payload.get("scope") in ("all", "single"):
            cfg.scope = str(payload["scope"])
        if "panel_refresh" in payload:
            try:
                cfg.panel_refresh = max(3.0, float(payload["panel_refresh"]))
            except (TypeError, ValueError):
                raise BiliError(-400, "panel_refresh 不合法", "保存配置") from None
        if "onebot_token" in payload:
            cfg.onebot_token = str(payload["onebot_token"] or "")
        if "onebot_enabled" in payload:
            cfg.onebot_enabled = bool(payload["onebot_enabled"])
        if "palette_sync" in payload:
            cfg.palette_sync = bool(payload["palette_sync"])

        cfg.save()
        if changed_peer:
            self.poller.reset()
        self.bus.publish({"type": "status", "data": self.poller.status_dict()})

    async def logout(self) -> None:
        self.cfg.cookie = {}
        self.cfg.my_uid = 0
        self.cfg.my_name = ""
        self.cfg.my_face = ""
        self.cfg.save()
        self.poller.reset()
        if isinstance(self.poller, Poller):
            self.poller.client.sync_cookies()
        self.bus.publish({"type": "status", "data": self.poller.status_dict()})

    # ---------------- 扫码登录 ----------------
    async def new_qr(self) -> dict:
        if self.cfg.demo:
            raise BiliError(-400, "演示模式不能登录", "扫码登录")
        if self.qr:
            with contextlib.suppress(Exception):
                await self.qr.aclose()
        self.qr = QrLogin()
        gen = await self.qr.generate()
        return gen

    async def poll_qr(self, key: str = "") -> dict:
        if not self.qr or (key and key != self.qr.key):
            raise BiliError(-400, "二维码会话已失效，请刷新", "扫码登录")
        return await self.qr.poll()


_runtime: BiliRuntime | None = None


def get_runtime(
    data_dir: Path | str | None = None,
    mode: str = MODE_STANDALONE,
    demo: bool = False,
) -> BiliRuntime:
    """取全局运行时；首次调用时创建。"""
    global _runtime
    if _runtime is None:
        _runtime = BiliRuntime(data_dir or Config.load().data_dir, mode=mode, demo=demo)
    return _runtime


def peek_runtime() -> BiliRuntime | None:
    return _runtime


def reset_runtime() -> None:
    global _runtime
    _runtime = None


async def shutdown_runtime() -> None:
    global _runtime
    if _runtime is not None:
        await _runtime.stop()
        _runtime = None


__all__ = [
    "BiliRuntime",
    "MODE_ASTRBOT",
    "MODE_STANDALONE",
    "get_runtime",
    "peek_runtime",
    "reset_runtime",
    "shutdown_runtime",
]
