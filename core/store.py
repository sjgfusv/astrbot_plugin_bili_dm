"""消息存储：sqlite，单文件，够用且不会丢。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key  TEXT UNIQUE,
    msg_key     TEXT,
    peer_uid    INTEGER,
    sender_uid  INTEGER,
    direction   TEXT,
    ts          INTEGER,
    kind        TEXT,
    text        TEXT,
    raw         TEXT,
    created     REAL
);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_msg_peer ON messages(peer_uid, ts);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    peer_uid   INTEGER PRIMARY KEY,
    name       TEXT,
    face       TEXT,
    last_text  TEXT,
    last_ts    INTEGER,
    unread     INTEGER DEFAULT 0,
    updated    REAL
);
CREATE INDEX IF NOT EXISTS idx_sess_ts ON sessions(last_ts);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # ---------- 消息 ----------
    def add_message(
        self,
        *,
        peer_uid: int,
        sender_uid: int,
        direction: str,
        ts: int,
        kind: str,
        text: str,
        msg_key: str | None = None,
        raw: dict | None = None,
    ) -> dict | None:
        """写入一条消息；已存在（同 dedupe_key）则返回 None。"""
        dedupe = msg_key or f"{peer_uid}:{ts}:{direction}:{hash(text) & 0xFFFFFFFF}"
        raw_json = json.dumps(raw, ensure_ascii=False) if raw else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO messages"
                "(dedupe_key,msg_key,peer_uid,sender_uid,direction,ts,kind,text,raw,created)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (dedupe, msg_key, peer_uid, sender_uid, direction, ts, kind, text, raw_json, time.time()),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
            row_id = cur.lastrowid
        return {
            "id": row_id,
            "msg_key": msg_key,
            "peer_uid": peer_uid,
            "sender_uid": sender_uid,
            "direction": direction,
            "ts": ts,
            "kind": kind,
            "text": text,
            "raw": raw,
        }

    def messages(self, limit: int = 200, before_id: int | None = None) -> list[dict]:
        sql = "SELECT * FROM messages"
        args: list = []
        if before_id:
            sql += " WHERE id < ?"
            args.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def recent(self, limit: int = 50) -> list[dict]:
        return self.messages(limit=limit)

    def message_by_id(self, msg_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        return self._row(row) if row else None

    def has_recent_text(self, text: str, direction: str = "out", within: int = 180) -> bool:
        """本地刚发出去的消息，轮询回来时 msg_key 可能不一致，用文本+时间兜底去重。"""
        since = int(time.time()) - within
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE direction=? AND text=? AND ts>=? LIMIT 1",
                (direction, text, since),
            ).fetchone()
        return row is not None

    def reconcile_outgoing(
        self,
        peer_uid: int,
        text: str,
        ts: int,
        msg_key: str,
        within: int = 30,
    ) -> bool:
        """把本地乐观插入的发出消息认领成服务器那条，避免显示重复。

        发送时先落一条 `local-out-*` 占位的记录让界面立刻有反馈；随后历史拉取会
        拿到 B 站真正的 msg_key。若文本一致且时间接近，就把占位记录改成真记录，
        后续再拉到同一条时 msg_key 去重就能直接命中。
        """
        if not msg_key:
            return False
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts FROM messages WHERE peer_uid=? AND direction='out' "
                "AND text=? AND msg_key LIKE 'local-out-%' ORDER BY id DESC LIMIT 8",
                (peer_uid, text),
            ).fetchall()
            if not rows:
                return False
            row = min(rows, key=lambda r: abs(int(r["ts"]) - int(ts)))
            if abs(int(row["ts"]) - int(ts)) > within:
                return False
            self._conn.execute(
                "UPDATE messages SET msg_key=?, dedupe_key=? WHERE id=?",
                (msg_key, msg_key, row["id"]),
            )
            self._conn.commit()
            return True

    def normalize_timestamps(self) -> int:
        """把历史落库的微秒/毫秒时间戳修正成秒（老数据兼容）。"""
        fixed = 0
        with self._lock:
            # sessions 的主键 peer_uid 本身就是 rowid 的别名，SELECT rowid 会拿不到键名
            for table, column, pk in (
                ("sessions", "last_ts", "peer_uid"),
                ("messages", "ts", "id"),
            ):
                rows = self._conn.execute(
                    f"SELECT {pk} AS pkid, {column} AS ts FROM {table} "
                    f"WHERE {column} >= ?",
                    (10**11,),
                ).fetchall()
                for row in rows:
                    ts = int(row["ts"])
                    seconds = ts // 1_000_000 if ts >= 10**14 else ts // 1_000
                    self._conn.execute(
                        f"UPDATE {table} SET {column}=? WHERE {pk}=?",
                        (seconds, row["pkid"]),
                    )
                    fixed += 1
            if fixed:
                self._conn.commit()
        return fixed

    def cleanup_local_duplicates(self, window: int = 30) -> int:
        """清理历史遗留的「本地占位 + 服务器真消息」成对重复。

        只删带 `local-out-*` 占位 key 且能找到同文本、时间接近的真消息的那条；
        用户真的在 30 秒内连发两条相同内容（两条都有真 key）不会被误删。
        """
        with self._lock:
            local_rows = self._conn.execute(
                "SELECT id, peer_uid, direction, text, ts FROM messages "
                "WHERE msg_key LIKE 'local-out-%'"
            ).fetchall()
            removed = 0
            for row in local_rows:
                real = self._conn.execute(
                    "SELECT id FROM messages WHERE peer_uid=? AND direction=? AND text=? "
                    "AND ts BETWEEN ? AND ? AND msg_key NOT LIKE 'local-out-%' LIMIT 1",
                    (
                        row["peer_uid"],
                        row["direction"],
                        row["text"],
                        int(row["ts"]) - window,
                        int(row["ts"]) + window,
                    ),
                ).fetchone()
                if real:
                    self._conn.execute("DELETE FROM messages WHERE id=?", (row["id"],))
                    removed += 1
            if removed:
                self._conn.commit()
            return removed

    def messages_of(self, peer_uid: int, limit: int = 100) -> list[dict]:
        # 按时间排：上翻补进来的老消息 id 更大，不能拿 id 当先后
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE peer_uid=?"
                " ORDER BY ts DESC, id DESC LIMIT ?",
                (peer_uid, limit),
            ).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def messages_before(
        self,
        peer_uid: int,
        before_ts: int,
        before_id: int,
        limit: int = 100,
    ) -> list[dict]:
        """取严格早于 (before_ts, before_id) 的最近 limit 条（升序），用于上翻历史。

        用 (ts, id) 复合游标而不是单看 id：补拉进来的历史消息 id 更大但时间更早。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE peer_uid=?"
                " AND (ts<? OR (ts=? AND id<?))"
                " ORDER BY ts DESC, id DESC LIMIT ?",
                (peer_uid, int(before_ts), int(before_ts), int(before_id), int(limit)),
            ).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def oldest_remote_message(self, peer_uid: int) -> dict | None:
        """本地库里最早的一条「服务器来的」消息，用来上翻时拿 seqno 游标。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE peer_uid=?"
                " AND msg_key NOT LIKE 'local-out-%' AND raw IS NOT NULL"
                " ORDER BY ts ASC, id ASC LIMIT 1",
                (peer_uid,),
            ).fetchone()
        return self._row(row) if row else None

    # ---------- 会话 ----------
    def upsert_session(
        self,
        peer_uid: int,
        *,
        name: str | None = None,
        face: str | None = None,
        last_text: str | None = None,
        last_ts: int | None = None,
        unread: int | None = None,
        add_unread: int = 0,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(peer_uid,name,face,last_text,last_ts,unread,updated)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(peer_uid) DO UPDATE SET"
                " name=COALESCE(excluded.name, sessions.name),"
                " face=COALESCE(excluded.face, sessions.face),"
                " last_text=COALESCE(excluded.last_text, sessions.last_text),"
                " last_ts=COALESCE(excluded.last_ts, sessions.last_ts),"
                " unread=CASE WHEN ? THEN COALESCE(?, sessions.unread) ELSE sessions.unread + ? END,"
                " updated=excluded.updated",
                (
                    peer_uid,
                    name,
                    face,
                    last_text,
                    last_ts,
                    unread if unread is not None else 0,
                    time.time(),
                    unread is not None,
                    unread,
                    add_unread,
                ),
            )
            self._conn.commit()

    def bump_session(self, peer_uid: int, text: str, ts: int, *, unread: bool = True) -> None:
        """有新消息时更新会话摘要；unread=True 时未读数 +1。"""
        self.upsert_session(
            peer_uid,
            last_text=text[:120],
            last_ts=ts,
            add_unread=1 if unread else 0,
        )

    def clear_unread(self, peer_uid: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET unread=0 WHERE peer_uid=?", (peer_uid,))
            self._conn.commit()

    def list_sessions(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY COALESCE(last_ts,0) DESC, updated DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, peer_uid: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE peer_uid=?", (peer_uid,)
            ).fetchone()
        return dict(row) if row else None

    def set_profile(self, peer_uid: int, name: str, face: str) -> None:
        self.upsert_session(peer_uid, name=name or None, face=face or None)

    def total_unread(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(SUM(unread),0) FROM sessions").fetchone()
        return int(row[0] or 0)

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def last_ts(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT MAX(ts) FROM messages").fetchone()
        return int(row[0] or 0)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        item = dict(row)
        raw = item.pop("raw", None)
        if raw:
            try:
                item["raw"] = json.loads(raw)
            except json.JSONDecodeError:
                item["raw"] = None
        else:
            item["raw"] = None
        return item

    # ---------- kv ----------
    def kv_set(self, k: str, v: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, v))
            self._conn.commit()

    def kv_get(self, k: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return str(row[0]) if row else default

    def close(self) -> None:
        with self._lock:
            self._conn.close()
