"""配置读写。所有状态落在 <data_dir>/config.json，cookie 也只存本地。

data_dir 由调用方注入：
- AstrBot 插件：data/plugin_data/astrbot_plugin_bili_dm/
- 独立运行：项目下的 data/
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

PLUGIN_NAME = "astrbot_plugin_bili_dm"

# 独立运行时的兜底目录（插件里会被 runtime 覆盖）
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 私信接口必须带的几个头，缺了容易被 -352
REFERER = "https://message.bilibili.com/"
ORIGIN = "https://message.bilibili.com"


@dataclass
class Config:
    # 面板显示范围：all=所有会话，single=只看指定的人
    scope: str = "all"
    # 对端：scope=single 时只看这一个人（独立模式则是唯一聊天对象）
    peer_uid: int = 0
    peer_name: str = ""
    # 自己
    my_uid: int = 0
    my_name: str = ""
    my_face: str = ""
    # 认证
    cookie: dict = field(default_factory=dict)
    dev_id: str = ""
    # 轮询
    poll_interval: float = 8.0
    # 独立运行的端口（插件模式下不用）
    host: str = "127.0.0.1"
    port: int = 8787
    # OneBot（仅独立运行）
    onebot_enabled: bool = True
    onebot_token: str = ""
    # 演示模式（仅独立运行，不落盘）
    demo: bool = False
    # 首次拉取的历史条数
    history_size: int = 30
    # 面板自动刷新间隔（秒），仅影响打开中的会话
    panel_refresh: float = 10.0
    # 跟随 AstrBot 调色盘：透明底 + 玻璃表面 + 主色联动
    palette_sync: bool = True
    # 运行目录，不参与序列化
    data_dir: Path = field(default=DEFAULT_DATA_DIR, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.dev_id:
            self.dev_id = new_dev_id()
        if self.poll_interval < 3:
            self.poll_interval = 3.0

    # ---- 路径 ----
    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "messages.db"

    @property
    def demo_db_path(self) -> Path:
        return self.data_dir / "demo.db"

    # ---- cookie 工具 ----
    @property
    def csrf(self) -> str:
        return str(self.cookie.get("bili_jct", ""))

    @property
    def logged_in(self) -> bool:
        return bool(self.cookie.get("SESSDATA")) and bool(self.csrf)

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookie.items() if v)

    def apply_cookie_string(self, raw: str) -> dict:
        """把浏览器里复制的 cookie 串解析成 dict，同时补出 uid。"""
        parsed: dict[str, str] = {}
        for part in raw.replace("\n", ";").split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            parsed[k.strip()] = v.strip()
        self.cookie = parsed
        try:
            self.my_uid = int(parsed.get("DedeUserID", "0") or 0)
        except ValueError:
            self.my_uid = 0
        return parsed

    def public(self) -> dict:
        """给前端看的状态，绝不外泄 cookie 值。"""
        return {
            "scope": self.scope,
            "peer_uid": self.peer_uid,
            "peer_name": self.peer_name,
            "my_uid": self.my_uid,
            "my_name": self.my_name,
            "my_face": self.my_face,
            "logged_in": self.logged_in,
            "poll_interval": self.poll_interval,
            "panel_refresh": self.panel_refresh,
            "palette_sync": self.palette_sync,
            "onebot_enabled": self.onebot_enabled,
            "onebot_token": "***" if self.onebot_token else "",
            "demo": self.demo,
            "dev_id": self.dev_id,
            "host": self.host,
            "port": self.port,
            "data_dir": str(self.data_dir),
        }

    @classmethod
    def load(cls, data_dir: Path | str | None = None) -> "Config":
        data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)
        cfg = cls(data_dir=data_dir)
        path = cfg.config_path
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            known = {f for f in cls.__dataclass_fields__ if f != "data_dir"}
            raw.pop("demo", None)  # 演示开关只在本次运行有效，不读盘
            for k, v in raw.items():
                if k in known:
                    setattr(cfg, k, v)
        cfg.__post_init__()
        return cfg

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("data_dir", None)  # Path 不可序列化，且由运行环境决定
        data["demo"] = False  # 演示模式是运行时开关，别固化到配置里
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def merge_platform_config(self, platform_config: dict | None) -> bool:
        """把 AstrBot 平台配置里的值作为默认值填进来（仅在本地为空时）。

        返回是否发生变更，便于调用方决定要不要落盘。
        """
        if not platform_config:
            return False
        changed = False

        if not self.cookie and platform_config.get("cookie"):
            self.apply_cookie_string(str(platform_config["cookie"]))
            changed = True
        if not self.peer_uid:
            try:
                uid = int(str(platform_config.get("peer_uid") or "").strip())
            except ValueError:
                uid = 0
            if uid:
                self.peer_uid = uid
                changed = True
        if self.poll_interval == 8.0 and platform_config.get("poll_interval"):
            try:
                interval = float(platform_config["poll_interval"])
            except (TypeError, ValueError):
                interval = 0
            if interval >= 3:
                self.poll_interval = interval
                changed = True
        return changed


def new_dev_id() -> str:
    """B 站私信接口要求 msg[dev_id] 是这种格式的 uuid。"""
    return str(uuid.uuid4()).upper()
