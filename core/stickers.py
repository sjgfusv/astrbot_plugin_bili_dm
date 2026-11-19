"""本地表情包存储：文件落在插件数据目录，不依赖浏览器 localStorage。

插件视图 iframe 是 sandbox 且没有 allow-same-origin，浏览器里 localStorage 用不了，
所以自定义表情必须存服务端。
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}

MAX_BYTES = 2 * 1024 * 1024  # 单个表情 2MB 上限


class StickerStore:
    def __init__(self, data_dir: Path | str) -> Path:
        self.root = Path(data_dir) / "stickers"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    def _load(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, items: list[dict]) -> None:
        self.index_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list(self) -> list[dict]:
        return [
            item
            for item in self._load()
            if isinstance(item, dict)
            and (self.root / str(item.get("file") or "")).is_file()
        ]

    def list_with_data(self) -> list[dict]:
        """给面板用：带上 base64，iframe 里没法直接访问插件的静态文件。"""
        items = self.list()
        for item in items:
            try:
                item["data"] = base64.b64encode(
                    (self.root / item["file"]).read_bytes()
                ).decode("ascii")
            except OSError:
                item["data"] = ""
        return items

    def add(self, name: str, raw: bytes, ext: str) -> dict:
        ext = str(ext or "").lower().lstrip(".")
        if ext not in MIME_BY_EXT:
            raise ValueError("只支持 jpg / jpeg / png / gif / webp / bmp")
        if not raw:
            raise ValueError("文件是空的")
        if len(raw) > MAX_BYTES:
            raise ValueError("单个表情不能超过 2MB")

        sid = uuid.uuid4().hex[:12]
        filename = f"{sid}.{ext}"
        (self.root / filename).write_bytes(raw)
        item = {
            "id": sid,
            "name": str(name or filename).strip()[:40] or filename,
            "file": filename,
            "mime": MIME_BY_EXT[ext],
            "size": len(raw),
        }
        items = self._load()
        items.append(item)
        self._save(items)
        return item

    def get(self, sid: str) -> dict | None:
        for item in self.list():
            if item.get("id") == sid:
                return item
        return None

    def delete(self, sid: str) -> bool:
        items = self._load()
        kept = []
        removed = False
        for item in items:
            if isinstance(item, dict) and item.get("id") == sid:
                target = self.root / str(item.get("file") or "")
                if target.is_file():
                    target.unlink()
                removed = True
            else:
                kept.append(item)
        if removed:
            self._save(kept)
        return removed
