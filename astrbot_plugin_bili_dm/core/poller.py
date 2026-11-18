"""收消息：轮询单会话；发消息：走 send_msg 后本地入库。都通过事件总线推给前端和 OneBot。

B 站私信没有可供第三方用的长连接推送，Web 端自己也是轮询，所以这里用固定间隔轮询 +
msg_key 去重。间隔默认 8 秒，低于 3 秒容易吃风控。
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time

from .bili import BiliClient, BiliError
from .config import Config
from .store import Store


class EventBus:
    """极简发布订阅：WebUI 的 WebSocket 和 OneBot 的 WS 都订这里。"""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(event)


class BasePoller:
    """对外统一接口：start / stop / send / refresh / status。"""

    def __init__(self, cfg: Config, store: Store, bus: EventBus):
        self.cfg = cfg
        self.store = store
        self.bus = bus
        self.state = "idle"
        self.last_sync = 0
        self.last_error = ""
        self.polls = 0
        self._task: asyncio.Task | None = None
        self._running = False
        self._seen: set[str] = set()
        self._bootstrapped = False

    # --- 生命周期 ---
    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def status_dict(self) -> dict:
        return {
            "state": self.state,
            "last_sync": int(self.last_sync),
            "last_error": self.last_error,
            "polls": self.polls,
            "interval": self.cfg.poll_interval,
            "peer_uid": self.cfg.peer_uid,
            "logged_in": self.cfg.logged_in,
        }

    def reset(self) -> None:
        """换了账号或对端之后，清掉去重状态重新同步。"""
        self._seen.clear()
        self._bootstrapped = False
        self.last_error = ""
        self.last_sync = 0
        self.polls = 0

    def set_state(self, state: str, error: str = "") -> None:
        if state == self.state and error == self.last_error:
            return
        self.state = state
        self.last_error = error
        self.bus.publish({"type": "status", "data": self.status_dict()})

    def publish_message(self, msg: dict) -> None:
        self.bus.publish({"type": "message", "data": msg})

    async def send(self, text: str) -> dict:  # pragma: no cover - 子类实现
        raise NotImplementedError

    async def refresh(self) -> None:  # pragma: no cover
        raise NotImplementedError

    async def _loop(self) -> None:  # pragma: no cover
        raise NotImplementedError


class Poller(BasePoller):
    def __init__(self, cfg: Config, store: Store, bus: EventBus):
        super().__init__(cfg, store, bus)
        self.client = BiliClient(cfg)
        self._fail_streak = 0

    async def _loop(self) -> None:
        while self._running:
            if not self.cfg.logged_in:
                self.set_state("waiting_login", "")
                await asyncio.sleep(2)
                continue
            try:
                await self._sync_once()
                self._fail_streak = 0
                self.set_state("online", "")
            except BiliError as exc:
                self._fail_streak = min(self._fail_streak + 1, 4)
                self.set_state("error", str(exc))
                if exc.code in (-101,):
                    self.set_state("waiting_login", str(exc))
                    await asyncio.sleep(5)
                    continue
            except Exception as exc:  # 兜底，别让轮询线程死掉
                self._fail_streak = min(self._fail_streak + 1, 4)
                self.set_state("error", f"{type(exc).__name__}: {exc}")

            delay = self.cfg.poll_interval * (2 ** self._fail_streak if self._fail_streak else 1)
            await asyncio.sleep(min(delay, 90))

    async def _sync_once(self) -> None:
        await self.client.ensure_buvid()
        if not self.cfg.my_uid:
            me = await self.client.nav()
            self.cfg.my_uid = me["mid"]
            self.cfg.my_name = me["uname"]
            self.cfg.my_face = me["face"]
            self.cfg.save()
            await self._resolve_peer()

        msgs = await self.client.fetch_msgs()
        self.last_sync = time.time()
        self.polls += 1

        fresh: list[dict] = []
        for m in msgs:
            key = m["msg_key"] or f"{m['ts']}:{m['direction']}:{m['text']}"
            if key in self._seen:
                continue
            self._seen.add(key)
            # 自己刚发出去、又被轮询拉回来的，跳过
            if m["direction"] == "out" and self.store.has_recent_text(m["text"], "out"):
                continue
            saved = self.store.add_message(
                peer_uid=m["peer_uid"],
                sender_uid=m["sender_uid"],
                direction=m["direction"],
                ts=m["ts"],
                kind=m["kind"],
                text=m["text"],
                msg_key=m["msg_key"] or None,
                raw=m.get("raw"),
            )
            if saved:
                fresh.append(saved)

        if not self._bootstrapped:
            # 首次同步只补历史，不当作新消息事件推出去，避免刷屏
            self._bootstrapped = True
            self.bus.publish({"type": "history", "data": {"count": len(fresh)}})
            self.set_state("online", "")
            return

        for msg in fresh:
            if msg["direction"] == "in":
                self.publish_message(msg)
        if fresh:
            self.bus.publish({"type": "status", "data": self.status_dict()})

    async def _resolve_peer(self) -> None:
        if not self.cfg.peer_uid or self.cfg.peer_name:
            return
        card = await self.client.user_card(self.cfg.peer_uid)
        if card.get("name"):
            self.cfg.peer_name = card["name"]
            self.cfg.save()

    async def refresh(self) -> None:
        await self._sync_once()

    async def send(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise BiliError(-400, "内容为空", "发送消息")
        res = await self.client.send_text(text=text)
        saved = self.store.add_message(
            peer_uid=self.cfg.peer_uid,
            sender_uid=self.cfg.my_uid,
            direction="out",
            ts=res["ts"],
            kind="text",
            text=text,
            msg_key=res["msg_key"] or None,
        )
        if saved:
            self.publish_message(saved)
        return saved or {}

    async def mark_read(self) -> dict:
        await self.client.mark_read()
        return {"ok": True}


DEMO_LINES = [
    "在吗，看到你昨天那个视频了",
    "这个面板能收到我消息吗？",
    "我刚发的那条你那边显示正常吗",
    "先这样，晚点再聊 👋",
]


class DemoPoller(BasePoller):
    """不连 B 站，用假数据把界面跑起来，方便先看效果。"""

    def __init__(self, cfg: Config, store: Store, bus: EventBus):
        super().__init__(cfg, store, bus)
        self.cfg.peer_uid = self.cfg.peer_uid or 114514
        self.cfg.peer_name = self.cfg.peer_name or "小林同学"
        self.cfg.my_uid = self.cfg.my_uid or 90001
        self.cfg.my_name = self.cfg.my_name or "我的账号（演示）"
        self.state = "demo"

    async def start(self) -> None:
        self._seed()
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def _seed(self) -> None:
        if self.store.count() > 0:
            return
        now = int(time.time())
        seed = [
            ("in", "嗨，上次说的那个直播切片我看了", -3600 * 5),
            ("out", "感觉节奏还行？我剪到 3 分钟", -3600 * 5 + 60),
            ("in", "挺好的，开头那两句可以再往后放一点", -3600 * 5 + 180),
            ("out", "行，我改完发你", -3600 * 5 + 240),
            ("in", "对了，你现在是用什么在收消息？", -1800),
            ("out", "自己写的一个面板，像 NapCat 那种", -1800 + 45),
            ("in", "那个不是 QQ 的么", -1800 + 90),
            ("out", "对，我照它的思路给 B 站私信做了一个", -1800 + 120),
            ("in", "有点东西，回头给我也整一个", -600),
        ]
        for direction, text, delta in seed:
            self.store.add_message(
                peer_uid=self.cfg.peer_uid,
                sender_uid=self.cfg.my_uid if direction == "out" else self.cfg.peer_uid,
                direction=direction,
                ts=now + delta,
                kind="text",
                text=text,
                msg_key=f"demo-{direction}-{abs(delta)}",
            )
        self._bootstrapped = True
        self.last_sync = time.time()

    async def _loop(self) -> None:
        i = 0
        while self._running:
            await asyncio.sleep(random.uniform(50, 80))
            i += 1
            msg = self.store.add_message(
                peer_uid=self.cfg.peer_uid,
                sender_uid=self.cfg.peer_uid,
                direction="in",
                ts=int(time.time()),
                kind="text",
                text=DEMO_LINES[i % len(DEMO_LINES)],
                msg_key=f"demo-live-{i}",
            )
            self.last_sync = time.time()
            if msg:
                self.publish_message(msg)

    async def refresh(self) -> None:
        self.last_sync = time.time()
        self.bus.publish({"type": "status", "data": self.status_dict()})

    async def send(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise BiliError(-400, "内容为空", "发送消息")
        saved = self.store.add_message(
            peer_uid=self.cfg.peer_uid,
            sender_uid=self.cfg.my_uid,
            direction="out",
            ts=int(time.time()),
            kind="text",
            text=text,
            msg_key=f"demo-out-{time.time()}",
        )
        if saved:
            self.publish_message(saved)
        asyncio.create_task(self._fake_reply(text))
        return saved or {}

    async def _fake_reply(self, sent: str) -> None:
        await asyncio.sleep(random.uniform(1.2, 2.4))
        reply = random.choice(
            [
                "收到，面板这边显示正常",
                "嗯嗯，看到了",
                "这条是从你那个面板发出来的？挺快",
                "好，那我先忙去了",
            ]
        )
        msg = self.store.add_message(
            peer_uid=self.cfg.peer_uid,
            sender_uid=self.cfg.peer_uid,
            direction="in",
            ts=int(time.time()),
            kind="text",
            text=reply,
            msg_key=f"demo-reply-{time.time()}",
        )
        if msg:
            self.publish_message(msg)

    async def mark_read(self) -> dict:
        return {"ok": True, "demo": True}
