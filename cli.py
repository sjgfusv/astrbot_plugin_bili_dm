"""命令行入口：不打开 AstrBot 也能登录、收发、盯消息。"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from astrbot_plugin_bili_dm.core.bili import BiliClient, BiliError
from astrbot_plugin_bili_dm.core.config import DEFAULT_DATA_DIR, Config
from astrbot_plugin_bili_dm.core.login import QR_EXPIRED, QR_OK, QrLogin, render_qr_ascii
from astrbot_plugin_bili_dm.core.store import Store


def _fmt_ts(ts: int) -> str:
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


def _data_dir(args: argparse.Namespace) -> Path:
    return Path(args.data) if getattr(args, "data", "") else DEFAULT_DATA_DIR


def _require_login(cfg: Config) -> None:
    if not cfg.logged_in:
        print("还没登录。先跑 `python run.py login` 扫码，或用 --cookie 粘贴浏览器 Cookie。")
        sys.exit(2)


def _apply_common(cfg: Config, args: argparse.Namespace) -> Config:
    if getattr(args, "peer", None):
        cfg.peer_uid = int(args.peer)
        cfg.peer_name = ""
    if getattr(args, "cookie", None):
        cfg.apply_cookie_string(args.cookie)
    if getattr(args, "interval", None):
        cfg.poll_interval = max(3.0, float(args.interval))
    return cfg


# ---------------- 子命令 ----------------
def cmd_serve(args: argparse.Namespace) -> None:
    from local import server

    cfg = _apply_common(Config.load(_data_dir(args)), args)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = int(args.port)
    cfg.save()
    server.serve(cfg.data_dir, demo=bool(args.demo), host=cfg.host, port=cfg.port)


def cmd_login(args: argparse.Namespace) -> None:
    cfg = _apply_common(Config.load(_data_dir(args)), args)

    if args.cookie:
        cfg.save()
        asyncio.run(_after_login(cfg))
        return

    async def flow() -> None:
        qr = QrLogin()
        try:
            gen = await qr.generate()
            print("\n用 B 站手机客户端扫码登录：\n")
            print(render_qr_ascii(gen["url"]))
            print(f"\n扫不动就用链接：{gen['url']}\n")
            last = None
            deadline = time.time() + 180
            while time.time() < deadline:
                res = await qr.poll()
                if res["status"] != last:
                    print(f"  · {res['status']}")
                    last = res["status"]
                if res["code"] == QR_OK:
                    cfg.cookie = res["cookie"]
                    if res["cookie"].get("DedeUserID"):
                        cfg.my_uid = int(res["cookie"]["DedeUserID"])
                    cfg.save()
                    await _after_login(cfg)
                    return
                if res["code"] == QR_EXPIRED:
                    print("二维码过期了，重新跑一次 login")
                    return
                await asyncio.sleep(2)
            print("超时没扫上，重新跑一次 login")
        finally:
            await qr.aclose()

    asyncio.run(flow())


async def _after_login(cfg: Config) -> None:
    client = BiliClient(cfg)
    try:
        me = await client.nav()
        cfg.my_uid, cfg.my_name, cfg.my_face = me["mid"], me["uname"], me["face"]
        if cfg.peer_uid and not cfg.peer_name:
            card = await client.user_card(cfg.peer_uid)
            cfg.peer_name = card.get("name") or ""
        cfg.save()
        print(f"\n登录成功：{cfg.my_name}（{cfg.my_uid}）")
        if cfg.peer_uid:
            print(f"对端：{cfg.peer_name or cfg.peer_uid}（{cfg.peer_uid}）")
        else:
            print("还没设置对端，跑 `python run.py peer <UID>` 指定要聊天的人")
    except BiliError as exc:
        print(f"登录信息校验失败：{exc}")
    finally:
        await client.aclose()


def cmd_status(args: argparse.Namespace) -> None:
    cfg = Config.load(_data_dir(args))
    store = Store(cfg.db_path)
    print(f"账号    {'已登录' if cfg.logged_in else '未登录'}")
    if cfg.my_uid:
        print(f"        {cfg.my_name or '-'}（{cfg.my_uid}）")
    print(f"对端    {cfg.peer_name or '-'}（{cfg.peer_uid or '未设置'}）")
    print(f"轮询    {cfg.poll_interval:g}s")
    print(f"数据    {cfg.data_dir}")
    print(f"消息    {store.count()} 条，最近 {_fmt_ts(store.last_ts())}")

    if cfg.logged_in:
        async def check() -> None:
            client = BiliClient(cfg)
            try:
                unread = await client.single_unread()
                print(f"未读    {unread.get('follow_unread', 0)}")
                msgs = await client.fetch_msgs(size=1)
                if msgs:
                    print(f"会话    正常，最后一条 {_fmt_ts(msgs[-1]['ts'])}")
            except BiliError as exc:
                print(f"接口    {exc}")
            finally:
                await client.aclose()

        asyncio.run(check())
    store.close()


def cmd_peer(args: argparse.Namespace) -> None:
    cfg = Config.load(_data_dir(args))
    cfg.peer_uid = int(args.uid)
    cfg.peer_name = ""
    if cfg.logged_in:
        async def resolve() -> None:
            client = BiliClient(cfg)
            try:
                card = await client.user_card(cfg.peer_uid)
                cfg.peer_name = card.get("name") or ""
            finally:
                await client.aclose()

        asyncio.run(resolve())
    cfg.save()
    print(f"对端已设为 {cfg.peer_name or cfg.peer_uid}（{cfg.peer_uid}）")


def cmd_send(args: argparse.Namespace) -> None:
    cfg = _apply_common(Config.load(_data_dir(args)), args)
    _require_login(cfg)
    if not cfg.peer_uid:
        print("还没设置对端，先跑 `python run.py peer <UID>`")
        sys.exit(2)
    text = " ".join(args.text) if args.text else ""

    async def flow() -> None:
        client = BiliClient(cfg)
        store = Store(cfg.db_path)
        try:
            res = await client.send_text(text=text)
            saved = store.add_message(
                peer_uid=cfg.peer_uid,
                sender_uid=cfg.my_uid,
                direction="out",
                ts=res["ts"],
                kind="text",
                text=text,
                msg_key=res["msg_key"] or None,
            )
            print(f"已发送 -> {cfg.peer_name or cfg.peer_uid}  id={saved['id'] if saved else '-'}")
        except BiliError as exc:
            print(f"发送失败：{exc}")
            sys.exit(1)
        finally:
            await client.aclose()
            store.close()

    asyncio.run(flow())


def cmd_tail(args: argparse.Namespace) -> None:
    cfg = _apply_common(Config.load(_data_dir(args)), args)
    _require_login(cfg)
    if not cfg.peer_uid:
        print("还没设置对端，先跑 `python run.py peer <UID>`")
        sys.exit(2)

    async def flow() -> None:
        client = BiliClient(cfg)
        seen: set[str] = set()
        print(f"跟随 {cfg.peer_name or cfg.peer_uid} 的私信，Ctrl+C 退出\n")
        try:
            while True:
                try:
                    msgs = await client.fetch_msgs()
                except BiliError as exc:
                    print(f"[!] {exc}")
                    await asyncio.sleep(cfg.poll_interval * 2)
                    continue
                for m in msgs:
                    key = m["msg_key"] or f"{m['ts']}:{m['text']}"
                    if key in seen:
                        continue
                    seen.add(key)
                    if not args.history and m["ts"] < time.time() - 60:
                        continue
                    who = "我" if m["direction"] == "out" else (cfg.peer_name or str(cfg.peer_uid))
                    print(f"[{_fmt_ts(m['ts'])}] {who}：{m['text']}")
                await asyncio.sleep(cfg.poll_interval)
        except KeyboardInterrupt:
            print("\n已退出")
        finally:
            await client.aclose()

    asyncio.run(flow())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bili-dm",
        description="B站私信面板（AstrBot 插件的独立运行模式）",
    )
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("serve", help="启动本地面板与 OneBot 服务")
    sp.add_argument("--demo", action="store_true", help="演示模式，用假数据看界面")
    sp.add_argument("--host", default="", help="监听地址，默认 127.0.0.1")
    sp.add_argument("--port", type=int, default=0, help="端口，默认 8787")
    sp.add_argument("--peer", default="", help="对端 UID")
    sp.add_argument("--interval", type=float, default=0, help="轮询间隔（秒）")
    sp.add_argument("--data", default="", help="数据目录，默认 ./data")
    sp.set_defaults(func=cmd_serve)

    lp = sub.add_parser("login", help="扫码登录，或直接粘贴 Cookie")
    lp.add_argument("--cookie", default="", help="浏览器里复制的整段 Cookie")
    lp.add_argument("--peer", default="", help="顺便设置对端 UID")
    lp.add_argument("--data", default="", help="数据目录")
    lp.set_defaults(func=cmd_login)

    stp = sub.add_parser("status", help="查看登录与连接状态")
    stp.add_argument("--data", default="", help="数据目录（插件模式可指向 plugin_data）")
    stp.set_defaults(func=cmd_status)

    pp = sub.add_parser("peer", help="设置唯一的聊天对象")
    pp.add_argument("uid", help="对方 UID（空间地址里的那串数字）")
    pp.add_argument("--data", default="", help="数据目录")
    pp.set_defaults(func=cmd_peer)

    sdp = sub.add_parser("send", help="发一条私信")
    sdp.add_argument("text", nargs="+", help="消息内容")
    sdp.add_argument("--peer", default="", help="临时指定对端 UID")
    sdp.add_argument("--cookie", default="", help="临时指定 Cookie")
    sdp.add_argument("--data", default="", help="数据目录")
    sdp.set_defaults(func=cmd_send)

    tp = sub.add_parser("tail", help="在终端里实时看消息")
    tp.add_argument("--history", action="store_true", help="连历史记录一起打印")
    tp.add_argument("--interval", type=float, default=0, help="轮询间隔（秒）")
    tp.add_argument("--peer", default="", help="临时指定对端 UID")
    tp.add_argument("--data", default="", help="数据目录")
    tp.set_defaults(func=cmd_tail)

    hp = sub.add_parser("help", help="显示帮助（help <命令> 看某个命令的详细用法）")
    hp.add_argument("topic", nargs="?", default="", help="命令名，例如 serve / login / send")
    hp.set_defaults(func=cmd_help)

    return p


def cmd_help(args: argparse.Namespace) -> None:
    """不带参数显示总览；带命令名显示该命令的详细参数。"""
    parser = build_parser()
    topic = (getattr(args, "topic", "") or "").strip()
    if not topic:
        parser.print_help()
        print("\n典型流程：")
        print("  python run.py login             # 扫码登录（通常只需一次）")
        print("  python run.py peer <对方UID>    # 指定要聊天的人")
        print("  python run.py serve             # 启动面板，浏览器开 http://127.0.0.1:8787/")
        print("  python run.py serve --demo      # 不登录也能先看界面")
        print("\n查看某个命令的详细用法：python run.py help <命令>")
        return

    # argparse 把子解析器挂在 _SubParsersAction 上，用它精确打印对应帮助
    sub_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    choices = sub_action.choices if sub_action is not None else {}
    if topic not in choices:
        print(f"没有这个命令：{topic}")
        print("可用命令：" + ", ".join(choices))
        return
    choices[topic].print_help()


def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
