"""astrbot.api.platform 的桩实现（对齐 4.29.4 的真实签名）。"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MessageType(Enum):
    GROUP_MESSAGE = "GroupMessage"
    FRIEND_MESSAGE = "FriendMessage"
    OTHER_MESSAGE = "OtherMessage"


@dataclass
class MessageMember:
    user_id: str
    nickname: str | None = None


@dataclass
class Group:
    group_id: str
    group_name: str | None = None


class AstrBotMessage:
    def __init__(self) -> None:
        self.type = MessageType.FRIEND_MESSAGE
        self.self_id = ""
        self.session_id = ""
        self.message_id = ""
        self.group: Group | None = None
        self.sender: MessageMember | None = None
        self.message: list[Any] = []
        self.message_str = ""
        self.raw_message: object = None
        self.timestamp = 0


@dataclass
class PlatformMetadata:
    name: str
    description: str
    id: str
    default_config_tmpl: dict | None = None
    adapter_display_name: str | None = None
    logo_path: str | None = None
    support_streaming_message: bool = True
    support_proactive_message: bool = True
    module_path: str | None = None
    i18n_resources: dict | None = None
    config_metadata: dict | None = None


# 注册表：真实实现里是 list + dict
platform_registry: list[PlatformMetadata] = []
platform_cls_map: dict[str, type] = {}


def register_platform_adapter(
    adapter_name: str,
    desc: str,
    default_config_tmpl: dict | None = None,
    adapter_display_name: str | None = None,
    logo_path: str | None = None,
    support_streaming_message: bool = True,
    i18n_resources: dict | None = None,
    config_metadata: dict | None = None,
):
    def decorator(cls):
        if default_config_tmpl:
            default_config_tmpl.setdefault("type", adapter_name)
            default_config_tmpl.setdefault("enable", False)
            default_config_tmpl.setdefault("id", adapter_name)
        pm = PlatformMetadata(
            name=adapter_name,
            description=desc,
            id=adapter_name,
            default_config_tmpl=default_config_tmpl,
            adapter_display_name=adapter_display_name,
            logo_path=logo_path,
            support_streaming_message=support_streaming_message,
            module_path=cls.__module__,
            i18n_resources=i18n_resources,
            config_metadata=config_metadata,
        )
        platform_registry.append(pm)
        platform_cls_map[adapter_name] = cls
        return cls

    return decorator


class AstrMessageEvent:
    def __init__(self, message_str, message_obj, platform_meta, session_id) -> None:
        self.message_str = message_str
        self.message_obj = message_obj
        self.platform_meta = platform_meta
        self.session_id = session_id
        self.role = "member"
        self.is_wake = False
        self._has_send_oper = False

    async def send(self, message) -> None:
        self._has_send_oper = True

    def get_sender_id(self) -> str:
        sender = getattr(self.message_obj, "sender", None)
        return str(getattr(sender, "user_id", "") or "")

    def get_platform_name(self) -> str:
        return getattr(self.platform_meta, "name", "")

    def get_session_id(self) -> str:
        return self.session_id

    def plain_result(self, text: str):
        return ("plain", text)


class Platform:
    def __init__(self, config: dict, event_queue: asyncio.Queue) -> None:
        self.config = config
        self._event_queue = event_queue
        self.client_self_id = uuid.uuid4().hex

    async def run(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def meta(self) -> PlatformMetadata:  # pragma: no cover - 抽象
        raise NotImplementedError

    async def terminate(self) -> None:
        return None

    async def send_by_session(self, session, message_chain) -> None:
        return None

    def commit_event(self, event) -> None:
        self._event_queue.put_nowait(event)
