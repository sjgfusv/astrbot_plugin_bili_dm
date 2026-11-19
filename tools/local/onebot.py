"""OneBot 11 适配层（私信子集）。

对外的角色跟 NapCat 一样：把平台消息标准化成 OneBot 事件推出去，同时接受
send_private_msg 之类的动作。这样 NoneBot / Koishi 这些框架可以直接拿来用。
"""

from __future__ import annotations

from typing import Any

from astrbot_plugin_bili_dm.core.bili import BiliError
from astrbot_plugin_bili_dm.core.config import Config
from astrbot_plugin_bili_dm.core.store import Store

VERSION = "0.2.0"

RETCODE_OK = 0
RETCODE_BAD_ACTION = 1404
RETCODE_BAD_PARAM = 1400
RETCODE_FAILED = 1200


def build_event(msg: dict, cfg: Config) -> dict:
    """内部消息 -> OneBot 11 事件。"""
    is_out = msg.get("direction") == "out"
    segments: list[dict[str, Any]] = []
    if msg.get("kind") == "image":
        segments.append({"type": "image", "data": {"file": msg.get("text", ""), "url": msg.get("text", "")}})
    else:
        segments.append({"type": "text", "data": {"text": msg.get("text", "")}})

    return {
        "time": int(msg.get("ts") or 0),
        "self_id": cfg.my_uid,
        "post_type": "message_sent" if is_out else "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": int(msg.get("id") or 0),
        "user_id": cfg.peer_uid,
        "target_id": cfg.my_uid if is_out else cfg.peer_uid,
        "message": segments,
        "raw_message": msg.get("text", ""),
        "font": 0,
        "sender": {
            "user_id": cfg.my_uid if is_out else cfg.peer_uid,
            "nickname": (cfg.my_name if is_out else cfg.peer_name) or str(cfg.peer_uid),
            "card": "",
            "role": "member",
        },
        # 非标准字段，但留着方便排查
        "bili": {"msg_key": msg.get("msg_key"), "kind": msg.get("kind")},
    }


def lifecycle_event(cfg: Config) -> dict:
    return {
        "time": 0,
        "self_id": cfg.my_uid,
        "post_type": "meta_event",
        "meta_event_type": "lifecycle",
        "sub_type": "connect",
        "interval": int(cfg.poll_interval * 1000),
    }


def heartbeat_event(cfg: Config, online: bool) -> dict:
    import time

    return {
        "time": int(time.time()),
        "self_id": cfg.my_uid,
        "post_type": "meta_event",
        "meta_event_type": "heartbeat",
        "status": {"online": online, "good": online},
        "interval": int(cfg.poll_interval * 1000),
    }


def extract_text(message: Any) -> str:
    """OneBot 的 message 可能是字符串，也可能是消息段数组。"""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "text":
                parts.append(str((seg.get("data") or {}).get("text", "")))
            else:
                raise BiliError(
                    RETCODE_BAD_PARAM,
                    f"这个面板只发文本，收到 {seg.get('type')} 类型消息段",
                    "OneBot",
                )
        return "".join(parts)
    raise BiliError(RETCODE_BAD_PARAM, "message 字段格式不对", "OneBot")


async def handle_action(action: str, params: dict, poller, cfg: Config, store: Store) -> dict:
    """执行一个 OneBot 动作，返回标准响应体。"""
    params = params or {}

    try:
        if action == "get_login_info":
            return ok({"user_id": cfg.my_uid, "nickname": cfg.my_name or "bili-dm"})

        if action == "get_version_info":
            return ok(
                {
                    "app_name": "bili-dm",
                    "app_version": VERSION,
                    "protocol_version": "v11",
                    "app_full_name": "bili-dm private message bridge",
                }
            )

        if action in ("get_status", "get_runtime_status"):
            st = poller.status_dict()
            online = st["state"] in ("online", "demo")
            return ok(
                {
                    "online": online,
                    "good": online,
                    "stat": {
                        "packet_received": st["polls"],
                        "packet_sent": 0,
                        "message_received": store.count(),
                        "last_sync": st["last_sync"],
                    },
                }
            )

        if action == "get_friend_list":
            return ok(
                [
                    {
                        "user_id": cfg.peer_uid,
                        "nickname": cfg.peer_name or str(cfg.peer_uid),
                        "remark": "",
                    }
                ]
                if cfg.peer_uid
                else []
            )

        if action == "get_stranger_info":
            uid = int(params.get("user_id") or 0)
            return ok({"user_id": uid, "nickname": cfg.peer_name if uid == cfg.peer_uid else str(uid)})

        if action in ("get_group_list", "get_group_info"):
            # 只做私信，群相关统一返回空
            return ok([])

        if action == "can_send_image":
            return ok({"yes": False})
        if action == "can_send_record":
            return ok({"yes": False})

        if action in ("send_private_msg", "send_msg"):
            text = extract_text(params.get("message"))
            if not text.strip():
                return fail(RETCODE_BAD_PARAM, "message 为空")
            saved = await poller.send(text)
            return ok({"message_id": int(saved.get("id") or 0)})

        if action == "get_msg":
            msg_id = int(params.get("message_id") or 0)
            row = store.message_by_id(msg_id)
            if not row:
                return fail(RETCODE_BAD_PARAM, f"找不到 message_id={msg_id}")
            return ok(
                {
                    "message_id": row["id"],
                    "real_id": row["id"],
                    "sender": {"user_id": row["sender_uid"]},
                    "time": row["ts"],
                    "message_type": "private",
                    "message": [{"type": "text", "data": {"text": row["text"]}}],
                    "raw_message": row["text"],
                }
            )

        return fail(RETCODE_BAD_ACTION, f"不支持的动作：{action}")

    except BiliError as exc:
        return fail(RETCODE_FAILED, str(exc))
    except Exception as exc:  # 别把异常抛回给框架
        return fail(RETCODE_FAILED, f"{type(exc).__name__}: {exc}")


def ok(data: Any) -> dict:
    return {"status": "ok", "retcode": RETCODE_OK, "data": data}


def fail(retcode: int, message: str) -> dict:
    return {"status": "failed", "retcode": retcode, "data": None, "message": message}
