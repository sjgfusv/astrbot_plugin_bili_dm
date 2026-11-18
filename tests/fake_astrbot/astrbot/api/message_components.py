"""astrbot.api.message_components 的桩实现。"""

from __future__ import annotations


class BaseMessageComponent:
    type = "base"


class Plain(BaseMessageComponent):
    type = "plain"

    def __init__(self, text: str = "") -> None:
        self.text = text


class Image(BaseMessageComponent):
    type = "image"

    def __init__(self, file: str = "", url: str = "") -> None:
        self.file = file
        self.url = url


class Record(BaseMessageComponent):
    type = "record"

    def __init__(self, file: str = "") -> None:
        self.file = file


class Video(BaseMessageComponent):
    type = "video"

    def __init__(self, file: str = "") -> None:
        self.file = file


class File(BaseMessageComponent):
    type = "file"

    def __init__(self, name: str = "", file: str = "") -> None:
        self.name = name
        self.file = file


class At(BaseMessageComponent):
    type = "at"

    def __init__(self, qq: str = "") -> None:
        self.qq = qq
