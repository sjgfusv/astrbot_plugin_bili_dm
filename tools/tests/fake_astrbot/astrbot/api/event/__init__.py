"""astrbot.api.event 的桩实现。"""

from __future__ import annotations

from enum import Enum

from astrbot.api.message_components import Plain
from astrbot.api.platform import AstrMessageEvent


class MessageChain:
    def __init__(self, chain=None) -> None:
        self.chain = list(chain or [])

    @staticmethod
    def plain(text: str = "") -> "MessageChain":
        return MessageChain([Plain(text)])


class EventMessageType(Enum):
    ALL = "all"
    GROUP_MESSAGE = "group"
    FRIEND_MESSAGE = "friend"


class _Filter:
    # 真实实现里 filter 是个模块，EventMessageType 挂在它下面
    EventMessageType = EventMessageType

    def command(self, name: str):
        def deco(func):
            func.__command__ = name
            return func

        return deco

    def on_message(self):
        def deco(func):
            return func

        return deco

    def event_message_type(self, types):
        def deco(func):
            func.__event_types__ = types
            return func

        return deco


filter = _Filter()

__all__ = ["AstrMessageEvent", "EventMessageType", "MessageChain", "filter"]
