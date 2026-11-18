"""扫码登录：拿 B 站的登录二维码，轮询结果，成功后把 Cookie 存下来。"""

from __future__ import annotations

import httpx

from .bili import BiliError, PASSPORT
from .config import UA

# 扫码状态码
QR_WAITING = 86101   # 未扫码
QR_SCANNED = 86090   # 已扫码，待确认
QR_EXPIRED = 86038   # 二维码过期
QR_OK = 0

WANT_COOKIES = (
    "SESSDATA",
    "bili_jct",
    "DedeUserID",
    "DedeUserID__ckMd5",
    "sid",
    "buvid3",
    "buvid4",
)

STATUS_TEXT = {
    QR_WAITING: "等待扫码",
    QR_SCANNED: "已扫码，请在手机上确认",
    QR_EXPIRED: "二维码已过期，请刷新",
    QR_OK: "登录成功",
}


class QrLogin:
    """一次扫码会话：generate() 拿二维码，poll() 轮询状态。"""

    def __init__(self) -> None:
        self.key: str = ""
        self.url: str = ""
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def generate(self) -> dict:
        try:
            r = await self._http.get(
                f"{PASSPORT}/x/passport-login/web/qrcode/generate",
                params={"source": "main-fe-header"},
            )
            payload = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BiliError("network", f"二维码获取失败：{exc}", "扫码登录") from exc
        if payload.get("code") != 0:
            raise BiliError(payload.get("code"), payload.get("message", ""), "扫码登录")
        data = payload.get("data") or {}
        self.key = data.get("qrcode_key") or ""
        self.url = data.get("url") or ""
        if not self.key:
            raise BiliError("bad-json", "没拿到 qrcode_key", "扫码登录")
        return {"key": self.key, "url": self.url}

    async def poll(self) -> dict:
        if not self.key:
            raise BiliError(-400, "请先生成二维码", "扫码登录")
        try:
            r = await self._http.get(
                f"{PASSPORT}/x/passport-login/web/qrcode/poll",
                params={"qrcode_key": self.key, "source": "main-fe-header"},
            )
            payload = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BiliError("network", f"轮询失败：{exc}", "扫码登录") from exc

        data = payload.get("data") or {}
        code = int(data.get("code", -1))
        result = {
            "code": code,
            "status": STATUS_TEXT.get(code, data.get("message") or "未知状态"),
            "cookie": {},
        }
        if code == QR_OK:
            jar: dict[str, str] = {}
            for name in WANT_COOKIES:
                v = r.cookies.get(name)
                if v:
                    jar[name] = v
            # 有些版本把 cookie 塞在 url 的 query 里
            if not jar.get("SESSDATA"):
                refresh = data.get("url") or ""
                if "SESSDATA=" in refresh:
                    from urllib.parse import parse_qs, urlparse

                    qs = parse_qs(urlparse(refresh).query)
                    for name in WANT_COOKIES:
                        if qs.get(name):
                            jar[name] = qs[name][0]
            result["cookie"] = jar
            result["uid"] = int(jar.get("DedeUserID", 0) or 0)
        return result


def cookie_string_to_dict(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def render_qr_ascii(text: str) -> str:
    """终端里画二维码（半块字符，两行合一）。"""
    import qrcode

    qr = qrcode.QRCode(border=1, box_size=1)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    lines = []
    for y in range(0, len(matrix), 2):
        row = ""
        for x in range(len(matrix[0])):
            top = matrix[y][x]
            bottom = matrix[y + 1][x] if y + 1 < len(matrix) else False
            if top and bottom:
                row += "█"
            elif top:
                row += "▀"
            elif bottom:
                row += "▄"
            else:
                row += " "
        lines.append(row)
    return "\n".join(lines)


def render_qr_svg(text: str) -> str:
    """给 WebUI 用的 SVG 字符串。"""
    import qrcode
    import qrcode.image.svg

    qr = qrcode.QRCode(border=2, box_size=10, image_factory=qrcode.image.svg.SvgPathImage)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image()
    import io

    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")
