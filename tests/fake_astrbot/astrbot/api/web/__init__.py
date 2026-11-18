"""astrbot.api.web 的桩实现。"""

from __future__ import annotations


class PluginMultiDict:
    def __init__(self, pairs=None) -> None:
        self._pairs = list(pairs or [])

    def get(self, key, default=None, type=None):
        for k, v in reversed(self._pairs):
            if k != key:
                continue
            if type is None:
                return v
            try:
                return type(v)
            except (TypeError, ValueError):
                return default
        return default

    def getlist(self, key):
        return [v for k, v in self._pairs if k == key]


class PluginUploadFile:
    pass


class _Request:
    """真实实现是 contextvar 代理，测试里用 set_state 手动喂数据。"""

    def __init__(self) -> None:
        self.query = PluginMultiDict()
        self._json: dict = {}
        self.username = "tester"
        self.plugin_name = "astrbot_plugin_bili_dm"

    def set_state(self, query=None, body=None) -> None:
        self.query = PluginMultiDict(query or [])
        self._json = body or {}

    async def json(self, default=None):
        return self._json if self._json is not None else default

    async def body(self) -> bytes:
        return b""


request = _Request()


def json_response(payload, status_code: int = 200):
    return {"__kind__": "json", "payload": payload, "status": status_code}


def error_response(message, status_code: int = 400):
    return {"__kind__": "error", "message": message, "status": status_code}


def file_response(path, filename=None, content_type=None):
    return {"__kind__": "file", "path": path, "filename": filename}


def stream_response(gen):
    return {"__kind__": "stream", "gen": gen}


__all__ = [
    "PluginMultiDict",
    "PluginUploadFile",
    "error_response",
    "file_response",
    "json_response",
    "request",
    "stream_response",
]
