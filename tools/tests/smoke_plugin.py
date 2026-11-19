"""插件冒烟测试（面板模式）：用 AstrBot API 桩 + 假适配器验证桥接逻辑。

跑法：python tools/tests/smoke_plugin.py
不联网、不碰真实数据目录。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS = ROOT / "tools"
DATA = Path(tempfile.mkdtemp(prefix="fake_astrbot_"))
os.environ["FAKE_ASTRBOT_DATA"] = str(DATA)

sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "tests" / "fake_astrbot"))
import _bootstrap  # noqa: E402  仓库根目录即插件目录，注册包别名

from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.platform import (  # noqa: E402
    AstrBotMessage,
    MessageMember,
    PlatformMetadata,
)
from astrbot.api.star import Context  # noqa: E402
from astrbot.api.web import request  # noqa: E402

from astrbot_plugin_bili_dm import bridge  # noqa: E402
from astrbot_plugin_bili_dm.core.config import PLUGIN_NAME  # noqa: E402

PASS = 0
MY_UID = 90001
PEER = 555


def check(cond: bool, label: str) -> None:
    global PASS
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    PASS += 1
    print(f"  ok  {label}")


def text_msg(uid: int, text: str, ts: int, key: str) -> dict:
    return {
        "msg_type": 1,
        "sender_uid": uid,
        "timestamp": ts,
        "msg_key": key,
        "content": json.dumps({"content": text}, ensure_ascii=False),
    }


class FakeClient:
    """模拟 bilibili 适配器里的 BilibiliClient（只实现面板用到的三个方法）。"""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.images: list[tuple[int, str]] = []
        self.messages = {
            PEER: [
                text_msg(PEER, "你好，在吗", 1700000100, "k1"),
                {
                    "msg_type": 2,
                    "sender_uid": MY_UID,
                    "timestamp": 1700000200,
                    "msg_key": "k2",
                    "content": json.dumps({"url": "https://i0.hdslb.com/bfs/x.jpg"}),
                },
            ]
        }

    async def get_messages(self, talker_id, session_type, begin_seqno, size=None):
        return {"messages": self.messages.get(int(talker_id), [])}

    async def send_text_message(self, receiver_id: int, content: str) -> bool:
        self.sent.append((int(receiver_id), content))
        return True

    async def upload_image(self, data, filename="image.png", cache_key=None):
        return {
            "image_url": "https://i0.hdslb.com/bfs/test.gif",
            "image_width": 100,
            "image_height": 100,
        }

    async def send_image_message(self, receiver_id: int, image_info: dict) -> bool:
        self.images.append((int(receiver_id), str(image_info.get("image_url") or "")))
        return True

    async def get_new_sessions(self, begin_ts: int):
        return {
            "session_list": [
                {
                    "talker_id": 777,
                    "unread_count": 2,
                    "session_ts": 1700000300,
                    "last_msg": {
                        "msg_type": 1,
                        "content": json.dumps({"content": "会话列表里的最新一条"}, ensure_ascii=False),
                    },
                }
            ]
        }


class FakeAdapter:
    def __init__(self) -> None:
        self.config = {"SESSDATA": "s", "bili_jct": "j", "buvid3": "b"}
        self._self_uid = MY_UID
        self._running = True
        self.poll_interval = 5
        self.client = FakeClient()

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="bilibili", description="Bilibili 私信适配器", id="bilibili"
        )


async def main() -> None:
    from astrbot_plugin_bili_dm.main import BiliDmPanelPlugin
    from astrbot_plugin_bili_dm import service as service_mod

    # api_state 会顺手补本人名片；测试不能真去请求 B 站
    async def fake_card(uid, cookie):
        return {"uid": uid, "name": "河神的B站", "face": "https://example.com/face.jpg"}

    service_mod.fetch_card = fake_card

    async def fake_emotes(cookie):
        return [{"name": "测试表情包", "items": [{"text": "[笑]", "url": ""}]}]

    service_mod.fetch_emote_packages = fake_emotes

    print("\n[1] 插件装配")
    ctx = Context()
    adapter = FakeAdapter()
    ctx.platform_manager.insts = [adapter]
    plugin = BiliDmPanelPlugin(ctx)

    routes = {r[0]: (r[1], r[2]) for r in ctx.registered_web_apis}
    check(len(routes) == 14, f"注册了 14 个接口（实际 {len(routes)}）")
    check(f"/{PLUGIN_NAME}/sessions" in routes, "路由带插件名前缀")
    check(f"/{PLUGIN_NAME}/theme" in routes, "注册了调色盘主题接口")
    check(f"/{PLUGIN_NAME}/emotes" in routes, "注册了表情接口")
    check(f"/{PLUGIN_NAME}/sticker/send" in routes, "注册了图片表情发送接口")
    check(plugin.svc.cfg.data_dir == DATA / "plugin_data" / PLUGIN_NAME, "数据目录正确")

    print("\n[2] 适配器桥接")
    request.set_state()
    state = (await plugin.api_state())["payload"]
    check(state["adapter"]["found"] is True, "找到了 bilibili 适配器")
    check(state["adapter"]["uid"] == MY_UID, "读到适配器的登录 UID")
    check(state["status"]["state"] == "online", "状态是 online")
    check(state["runtime"]["mode"] == "astrbot-panel", "mode=astrbot-panel")
    check(plugin.svc.my_uid() == MY_UID, "my_uid 取自适配器")

    print("\n[3] 会话与历史")
    request.set_state()
    sessions = (await plugin.api_sessions())["payload"]
    check(sessions["sessions"] == [], "初始没有会话")
    check(sessions["scope"] == "all", "默认显示所有会话")

    request.set_state(query=[("peer", str(PEER)), ("limit", "20")])
    msgs = (await plugin.api_messages())["payload"]["messages"]
    check(len(msgs) == 2, "拉到 2 条历史")
    check(msgs[0]["direction"] == "in" and msgs[0]["text"] == "你好，在吗", "文本入向解析正确")
    check(msgs[1]["direction"] == "out", "自己发的识别为 out")
    check(msgs[1]["kind"] == "image", "图片消息识别为 image")
    check(msgs[1]["text"].startswith("https://"), "图片保留原始 URL")

    sessions = (await plugin.api_sessions())["payload"]["sessions"]
    check(len(sessions) == 1 and sessions[0]["peer_uid"] == PEER, "会话自动入列")
    check(sessions[0]["last_ts"] == 1700000200, "会话摘要取最后一条")

    print("\n[4] 发送")
    request.set_state(body={"peer": PEER, "text": "面板回复一条"})
    res = (await plugin.api_send())["payload"]
    check(adapter.client.sent == [(PEER, "面板回复一条")], "调到了适配器的发送方法")
    check(res["message"]["direction"] == "out", "本地也记了一条 out")

    print("\n[5] 实时收消息")
    abm = AstrBotMessage()
    abm.message_str = "刚发来的消息"
    abm.sender = MessageMember(user_id=str(PEER), nickname="小林")
    abm.timestamp = 1700000400
    abm.message_id = "m-1"
    abm.session_id = str(PEER)
    event = AstrMessageEvent("刚发来的消息", abm, adapter.meta(), str(PEER))

    queue = plugin.svc.bus.subscribe()
    await plugin.on_platform_message(event)
    check(not queue.empty(), "事件推给了面板（SSE）")
    pushed = queue.get_nowait()
    check(pushed["type"] == "message" and pushed["data"]["text"] == "刚发来的消息", "推送内容正确")
    plugin.svc.bus.unsubscribe(queue)

    sess = plugin.svc.store.get_session(PEER)
    check(sess["unread"] >= 1, "未读数 +1")
    check(sess["last_text"] == "刚发来的消息", "会话摘要被更新")

    # 其它平台的事件应被忽略
    other = AstrMessageEvent("qq 消息", abm, PlatformMetadata(name="aiocqhttp", description="", id="q"), "1")
    before = plugin.svc.store.count()
    await plugin.on_platform_message(other)
    check(plugin.svc.store.count() == before, "非 bilibili 事件不记录")

    print("\n[5b] 实时图片取回原始 URL")

    class Image:
        pass

    def image_event(key: str, raw: object) -> AstrMessageEvent:
        ab = AstrBotMessage()
        ab.message_str = "[图片]"
        ab.sender = MessageMember(user_id=str(PEER), nickname="小林")
        ab.timestamp = 1700000500
        ab.message_id = key
        ab.session_id = str(PEER)
        ab.message = [Image()]
        ab.raw_message = raw
        return AstrMessageEvent("[图片]", ab, adapter.meta(), str(PEER))

    good_url = "https://i0.hdslb.com/bfs/private/a.jpg"
    raw_good = {"msg_type": 2, "content": json.dumps({"url": good_url})}
    saved_img = plugin.svc.record_inbound(image_event("m-img-1", raw_good))
    check(bool(saved_img and saved_img["text"] == good_url), "实时图片取回原始 URL")
    check(saved_img["kind"] == "image", "图片类型仍是 image")

    raw_bad = {
        "msg_type": 2,
        "content": json.dumps({"url": "https://evil.example/a.jpg"}),
    }
    saved_bad = plugin.svc.record_inbound(image_event("m-img-2", raw_bad))
    check(bool(saved_bad and saved_bad["text"] == "[图片]"), "非 B 站域名不给浏览器加载")

    saved_no_raw = plugin.svc.record_inbound(image_event("m-img-3", None))
    check(bool(saved_no_raw and saved_no_raw["text"] == "[图片]"), "没有 raw_message 时退回占位")

    print("\n[6] 显示范围：只看一个人")
    request.set_state(body={"scope": "single", "peer_uid": 777, "panel_refresh": 12})
    await plugin.api_config()
    check(plugin.svc.cfg.scope == "single" and plugin.svc.cfg.peer_uid == 777, "设置已保存")

    request.set_state()
    sessions = (await plugin.api_sessions())["payload"]["sessions"]
    check([s["peer_uid"] for s in sessions] == [777], "会话列表只剩指定的人")
    check(sessions[0]["name"].startswith("UID") or sessions[0]["name"], "没有名片时降级显示 UID")

    request.set_state(query=[("peer", str(PEER))])
    blocked = await plugin.api_messages()
    check(blocked["__kind__"] == "error", "看别人被拦（403）")

    request.set_state(body={"scope": "all"})
    await plugin.api_config()

    print("\n[7] 刷新会话列表")
    request.set_state(body={})
    refreshed = (await plugin.api_refresh())["payload"]
    check(refreshed["sessions"] == 1, "从 get_new_sessions 拉到 1 个会话")
    sessions = (await plugin.api_sessions())["payload"]["sessions"]
    check(any(s["peer_uid"] == 777 for s in sessions), "新会话已入库")
    check(any(s["unread"] == 2 for s in sessions if s["peer_uid"] == 777), "未读数沿用接口返回值")

    print("\n[8] 已读 / SSE")
    request.set_state(body={"peer": PEER})
    await plugin.api_read()
    check(plugin.svc.store.get_session(PEER)["unread"] == 0, "标记已读清零")

    events = await plugin.api_events()
    gen = events["gen"]
    first = await gen.__anext__()
    check("hello" in first, "SSE 首帧是 hello")
    await gen.aclose()

    print("\n[9] 调色盘适配")
    request.set_state()
    theme = (await plugin.api_theme())["payload"]
    check(theme["available"] is False, "没装调色盘时不报错，只标记不可用")

    palette_cfg = DATA / "config" / "astrbot_plugin_palette_config.json"
    palette_cfg.parent.mkdir(parents=True, exist_ok=True)
    palette_cfg.write_text(
        json.dumps(
            {
                "enabled": True,
                "theme_primary": "#325893",
                "theme_secondary": "#3c358d",
                "surface_opacity": 0.2,
                "stats_card_blur": 16,
                "text_enhancement_mode": "stroke",
                "text_enhancement_strength": 1.4,
                "background_dim": 0.6,
                "landscape_background_image": "bg.mp4",
                "auto_theme_enabled": True,
            }
        ),
        encoding="utf-8-sig",
    )
    plugin.svc._palette_cache = None
    request.set_state()
    theme = (await plugin.api_theme())["payload"]
    check(theme["available"] is True and theme["enabled"] is True, "读到调色盘配置")
    check(theme["primary"] == "#325893", "主色解析正确")
    check(theme["glass_blur"] == 16, "毛玻璃强度解析正确")
    check(theme["surface_opacity"] == 0.2, "界面透明度解析正确")
    check(theme["text_enhancement"]["mode"] == "stroke", "文字增强模式解析正确")
    check(theme["wallpaper"]["present"] is True, "识别到壁纸")
    check(theme["config_path"].endswith("astrbot_plugin_palette_config.json"), "配置路径正确")

    state = (await plugin.api_state())["payload"]
    check(state["palette"]["primary"] == "#325893", "state 里也带上调色盘信息")

    # 面板关掉跟随：后端直接回不可用，前端退回自己的配色
    request.set_state(body={"palette_sync": False})
    await plugin.api_config()
    theme = (await plugin.api_theme())["payload"]
    check(theme["available"] is False, "关闭跟随后不再下发调色盘")
    request.set_state(body={"palette_sync": True})
    await plugin.api_config()
    check((await plugin.api_theme())["payload"]["available"] is True, "重新开启后恢复")

    print("\n[10] 本人名片 / 禁止把自己当对端")
    try:
        await plugin.svc.ensure_self_profile()
        check(plugin.svc.cfg.my_name == "河神的B站", "本人昵称已缓存")
        check(plugin.svc.cfg.my_face.endswith("face.jpg"), "本人头像已缓存")

        calls = []

        async def counting_card(uid, cookie):
            calls.append(uid)
            return {"name": "不该出现"}

        service_mod.fetch_card = counting_card
        await plugin.svc.ensure_self_profile()
        check(not calls, "已有名片时不重复请求")

        # 配置里拒绝自己
        request.set_state(body={"scope": "single", "peer_uid": MY_UID})
        blocked = await plugin.api_config()
        check(blocked["__kind__"] == "error", "设置里拒绝把自己设为对端")

        # 发消息也拒绝
        try:
            await plugin.svc.send(MY_UID, "给自己发")
            rejected = False
        except RuntimeError:
            rejected = True
        check(rejected, "不能给自己发私信")

        # 会话列表不过滤掉别人，但要滤掉自己
        plugin.svc.store.upsert_session(
            MY_UID, last_text="自己的会话", last_ts=1, unread=1
        )
        request.set_state(body={"scope": "all"})
        await plugin.api_config()
        request.set_state()
        uids = [s["peer_uid"] for s in plugin.svc.visible_sessions()]
        check(MY_UID not in uids, "会话列表不显示自己")
        check(PEER in uids, "会话列表仍显示对方")
    finally:
        service_mod.fetch_card = fake_card

    print("\n[11] 发送去重（本地占位 vs 历史回吐）")
    text = "去重测试"
    sent = await plugin.svc.send(PEER, text)
    check(bool(sent and sent.get("id")), "本地发送先落一条占位消息")

    sent_too = await plugin.svc.send(PEER, text + "2")
    check(bool(sent_too and sent_too.get("id")), "同一秒连发第二条也不会被去重吃掉")

    adapter.client.messages[PEER].append(
        text_msg(MY_UID, text, int(time.time()), "real-out-key-1")
    )
    request.set_state(query=[("peer", str(PEER)), ("limit", "20")])
    history = (await plugin.api_messages())["payload"]["messages"]
    matches = [m for m in history if m["text"] == text and m["direction"] == "out"]
    check(len(matches) == 1, "同一条发出消息只显示一次")
    check(matches[0]["msg_key"] == "real-out-key-1", "占位记录已被真 msg_key 认领")

    request.set_state(query=[("peer", str(PEER)), ("limit", "20")])
    again = (await plugin.api_messages())["payload"]["messages"]
    check(
        len([m for m in again if m["text"] == text and m["direction"] == "out"]) == 1,
        "再次拉取仍不重复",
    )

    # 历史遗留的成对重复：只删占位那条
    legacy_ts = int(time.time())
    for key in (f"local-out-{time.time_ns()}-{PEER}", "legacy-real-key"):
        plugin.svc.store.add_message(
            peer_uid=PEER,
            sender_uid=MY_UID,
            direction="out",
            ts=legacy_ts,
            kind="text",
            text="历史重复",
            msg_key=key,
        )
    before_cleanup = plugin.svc.store.count()
    removed = plugin.svc.store.cleanup_local_duplicates()
    check(removed == 1, "历史重复只删占位行")
    check(plugin.svc.store.count() == before_cleanup - 1, "清理后数量正确")

    print("\n[12] 时间戳精度兼容")
    check(bridge.normalize_ts(1789189798474229) == 1789189798, "微秒时间戳转成秒")
    check(bridge.normalize_ts("1700000300123") == 1700000300, "毫秒字符串转成秒")
    check(bridge.normalize_ts("NaN") == 0, "坏值不变成 NaN")

    plugin.svc.store.upsert_session(
        999999, last_text="历史微秒会话", last_ts=1789189798474229, unread=0
    )
    plugin.svc.store.normalize_timestamps()
    check(
        plugin.svc.store.get_session(999999)["last_ts"] == 1789189798,
        "库里历史微秒时间戳被修正",
    )

    print("\n[13] 分享消息不在事件里落占位")
    share_abm = AstrBotMessage()
    share_abm.message_str = "[对方分享了一个视频《测试视频》BV号：BV1abc]"
    share_abm.sender = MessageMember(user_id=str(PEER), nickname="小林")
    share_abm.timestamp = 1700000500
    share_abm.message_id = "m-share"
    share_abm.session_id = str(PEER)
    share_event = AstrMessageEvent(
        share_abm.message_str, share_abm, adapter.meta(), str(PEER)
    )
    before_share = plugin.svc.store.count()
    check(plugin.svc.record_inbound(share_event) is None, "分享事件不直接落文字占位")
    check(plugin.svc.store.count() == before_share, "等历史回吐的完整卡片数据")

    print("\n[14] 表情包与本地表情")
    request.set_state()
    emotes = (await plugin.api_emotes())["payload"]
    check(emotes["packages"][0]["name"] == "测试表情包", "表情包接口返回可用表情")
    check(emotes["stickers"] == [], "初始没有本地表情")

    encoded = base64.b64encode(b"GIF89a-test-sticker").decode("ascii")
    request.set_state(body={"name": "测试表情", "data": encoded, "ext": "gif"})
    added = (await plugin.api_sticker_add())["payload"]
    check(len(added["stickers"]) == 1, "本地表情已入库")
    sid = added["sticker"]["id"]
    found = plugin.svc.sticker_file(sid)
    check(bool(found and found[1] == "image/gif"), "能按 id 读回图片")

    sticker_msg = await plugin.svc.send_sticker(PEER, sid)
    check(sticker_msg.get("kind") == "image", "图片表情按 image 消息落库")
    check(adapter.client.images and adapter.client.images[-1][0] == PEER, "调到了适配器的图片发送")

    request.set_state(body={"id": sid})
    deleted = (await plugin.api_sticker_delete())["payload"]
    check(deleted["stickers"] == [], "删除后列表清空")

    print("\n[15] 清理")
    await plugin.terminate()
    check(True, "terminate 不抛异常")

    print(f"\n全部通过：{PASS} 项\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\n{exc}\n")
        sys.exit(1)
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
