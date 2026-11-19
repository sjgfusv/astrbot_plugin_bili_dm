"""astrbot.api.star 的桩实现。"""

from __future__ import annotations


class _PlatformManager:
    def __init__(self) -> None:
        self.insts: list = []

    def get_insts(self) -> list:
        return self.insts


class Context:
    def __init__(self) -> None:
        self.registered_web_apis: list[tuple] = []
        self.platform_manager = _PlatformManager()

    def register_web_api(self, route, view_handler, methods, desc) -> None:
        for idx, api in enumerate(self.registered_web_apis):
            if api[0] == route and methods == api[2]:
                self.registered_web_apis[idx] = (route, view_handler, methods, desc)
                return
        self.registered_web_apis.append((route, view_handler, methods, desc))


class Star:
    def __init__(self, context: Context) -> None:
        self.context = context
        self.name = "astrbot_plugin_bili_dm"


def register(name: str, author: str, desc: str, version: str):
    def deco(cls):
        cls.__plugin_meta__ = {
            "name": name,
            "author": author,
            "desc": desc,
            "version": version,
        }
        return cls

    return deco


__all__ = ["Context", "Star", "register"]
