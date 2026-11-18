"""独立运行模式：不装 AstrBot 也能用的本地服务。

路由与插件视图完全一致（/api/*），所以 views/panel 里那份前端两边都能跑。
额外提供 OneBot 11 的 HTTP/WS，方便接别的框架。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from astrbot_plugin_bili_dm.core.bili import BiliError
from astrbot_plugin_bili_dm.core.login import render_qr_svg
from astrbot_plugin_bili_dm.core.poller import Poller
from astrbot_plugin_bili_dm.core.runtime import MODE_STANDALONE, BiliRuntime

from .onebot import build_event, handle_action, heartbeat_event, lifecycle_event

ROOT = Path(__file__).resolve().parent.parent
VIEW_DIR = ROOT / "astrbot_plugin_bili_dm" / "views" / "panel"


def create_app(rt: BiliRuntime, host: str, port: int) -> FastAPI:
    cfg = rt.cfg

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await rt.ensure_started()
        try:
            yield
        finally:
            await rt.stop()

    app = FastAPI(title="bili-dm", version="0.2.0", lifespan=lifespan, docs_url="/api/docs")
    app.state.rt = rt
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    # ---------------- 页面 ----------------
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(VIEW_DIR / "index.html", headers={"Cache-Control": "no-store"})

    # ---------------- 与插件一致的 REST ----------------
    def _adapter_shim() -> dict:
        """独立模式下把本地轮询器伪装成"适配器"，让同一份前端能跑。"""
        return {
            "found": True,
            "name": "standalone",
            "display": "独立运行（自建轮询）",
            "ready": bool(cfg.demo or cfg.logged_in),
            "uid": cfg.my_uid,
            "running": True,
            "poll_interval": cfg.poll_interval,
            "status": "running",
            "error": "",
        }

    def _session_dict() -> dict:
        if not cfg.peer_uid:
            return {}
        row = rt.store.get_session(cfg.peer_uid) or {}
        last = rt.store.messages_of(cfg.peer_uid, 1)
        return {
            "peer_uid": cfg.peer_uid,
            "name": row.get("name") or cfg.peer_name or f"UID {cfg.peer_uid}",
            "face": row.get("face") or cfg.my_face,
            "last_text": row.get("last_text") or (last[0]["text"] if last else ""),
            "last_ts": row.get("last_ts") or (last[0]["ts"] if last else 0),
            "unread": 0,
        }

    @app.get("/api/state")
    async def api_state() -> dict:
        state = rt.public_state()
        state["adapter"] = _adapter_shim()
        state["sessions"] = {"count": 1 if cfg.peer_uid else 0}
        return state

    @app.get("/api/theme")
    async def api_theme() -> dict:
        """与插件模式同名的接口，前端一套代码两种宿主都能跑。"""
        return rt.palette_dict()

    @app.get("/api/emotes")
    async def api_emotes() -> dict:
        return rt.emote_data()

    @app.get("/api/sticker/file")
    async def api_sticker_file(id: str = ""):
        found = rt.sticker_file(id)
        if not found:
            return JSONResponse({"error": "表情不存在"}, status_code=404)
        raw, mime = found
        return Response(
            content=raw,
            media_type=mime,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.post("/api/sticker/add")
    async def api_sticker_add(req: Request):
        payload = await req.json()
        try:
            return rt.add_sticker(
                str(payload.get("name") or ""),
                str(payload.get("data") or ""),
                str(payload.get("ext") or ""),
            )
        except BiliError as exc:
            return JSONResponse({"error": exc.raw_msg}, status_code=400)

    @app.post("/api/sticker/delete")
    async def api_sticker_delete(req: Request):
        payload = await req.json()
        return rt.delete_sticker(str(payload.get("id") or ""))

    @app.post("/api/sticker/send")
    async def api_sticker_send(req: Request):
        payload = await req.json()
        try:
            return await rt.send_sticker(
                int(payload.get("peer") or 0), str(payload.get("id") or "")
            )
        except BiliError as exc:
            return JSONResponse({"error": exc.raw_msg}, status_code=400)

    @app.get("/api/sessions")
    async def api_sessions() -> dict:
        items = [_session_dict()] if cfg.peer_uid else []
        return {"sessions": items, "scope": "single", "peer_uid": cfg.peer_uid}

    @app.get("/api/messages")
    async def api_messages(peer: int = 0, limit: int = 60, before_id: int | None = None) -> dict:
        uid = int(peer or cfg.peer_uid or 0)
        return {
            "messages": rt.store.messages_of(uid, min(limit, 500)) if uid else [],
            "peer": rt.peer_card() if uid else {"uid": 0, "name": ""},
            "me": {"uid": cfg.my_uid, "name": cfg.my_name, "face": cfg.my_face},
        }

    @app.post("/api/send")
    async def api_send(payload: dict) -> Any:
        try:
            saved = await rt.send(str(payload.get("text") or ""))
        except BiliError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        rt.store.upsert_session(
            cfg.peer_uid,
            name=cfg.peer_name or None,
            last_text=str(payload.get("text") or "")[:120],
            unread=None,
        )
        return {"ok": True, "message": saved}

    @app.post("/api/refresh")
    async def api_refresh(payload: dict | None = None) -> Any:
        try:
            await rt.refresh()
        except BiliError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, "sessions": 1 if cfg.peer_uid else 0, "status": rt.poller.status_dict()}

    @app.post("/api/read")
    async def api_read(payload: dict | None = None) -> dict:
        return {"ok": True}

    @app.post("/api/mark_read")
    async def api_mark_read() -> Any:
        try:
            await rt.mark_read()
        except BiliError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True}

    @app.post("/api/config")
    async def api_config(payload: dict) -> Any:
        try:
            await rt.save_config(payload)
        except BiliError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, "state": rt.public_state()}

    @app.post("/api/logout")
    async def api_logout() -> dict:
        await rt.logout()
        return {"ok": True}

    @app.post("/api/login/qr")
    async def api_login_qr() -> Any:
        try:
            gen = await rt.new_qr()
        except BiliError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, "key": gen["key"], "url": gen["url"], "svg": render_qr_svg(gen["url"])}

    @app.get("/api/login/qr/poll")
    async def api_qr_poll(key: str = "") -> Any:
        try:
            res = await rt.poll_qr(key)
        except BiliError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if res["code"] == 0:
            jar = res.get("cookie") or {}
            if not jar.get("SESSDATA"):
                return JSONResponse(
                    {"error": "登录成功但没拿到 SESSDATA，请改用 Cookie 登录"}, status_code=400
                )
            cfg.cookie = jar
            if jar.get("DedeUserID"):
                cfg.my_uid = int(jar["DedeUserID"])
            cfg.save()
            rt.poller.reset()
            if isinstance(rt.poller, Poller):
                rt.poller.client.sync_cookies()
            with contextlib.suppress(BiliError):
                me = await rt.poller.client.nav()
                cfg.my_uid, cfg.my_name, cfg.my_face = me["mid"], me["uname"], me["face"]
                cfg.save()
            rt.bus.publish({"type": "status", "data": rt.poller.status_dict()})
            return {"ok": True, "done": True, "state": rt.public_state()}
        return {"ok": True, "done": False, "code": res["code"], "status": res["status"]}

    @app.get("/api/events")
    async def api_events() -> StreamingResponse:
        queue = rt.bus.subscribe()

        async def gen():
            try:
                yield f"data: {json.dumps({'type': 'hello'})}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=20)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                    except asyncio.CancelledError:
                        break
            finally:
                rt.bus.unsubscribe(queue)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------------- OneBot ----------------
    def check_token(conn: Request | WebSocket) -> bool:
        if not cfg.onebot_token:
            return True
        auth = conn.headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and auth[7:] == cfg.onebot_token:
            return True
        return conn.query_params.get("access_token") == cfg.onebot_token

    @app.post("/onebot")
    async def onebot_http(payload: dict, request: Request) -> Any:
        if not cfg.onebot_enabled:
            return JSONResponse(
                {"status": "failed", "retcode": 1403, "data": None, "message": "OneBot 已关闭"},
                status_code=403,
            )
        if not check_token(request):
            return JSONResponse(
                {"status": "failed", "retcode": 1403, "data": None, "message": "token 不对"},
                status_code=401,
            )
        resp = await handle_action(
            str(payload.get("action") or ""), payload.get("params") or {}, rt.poller, cfg, rt.store
        )
        resp["echo"] = payload.get("echo")
        return resp

    @app.get("/onebot")
    async def onebot_http_get(request: Request) -> Any:
        if not cfg.onebot_enabled:
            return JSONResponse(
                {"status": "failed", "retcode": 1403, "data": None, "message": "OneBot 已关闭"},
                status_code=403,
            )
        if not check_token(request):
            return JSONResponse(
                {"status": "failed", "retcode": 1403, "data": None, "message": "token 不对"},
                status_code=401,
            )
        qp = dict(request.query_params)
        action = qp.pop("action", "")
        echo = qp.pop("echo", None)
        params: dict[str, Any] = {}
        if "params" in qp:
            with contextlib.suppress(json.JSONDecodeError):
                params = json.loads(qp.pop("params"))
        params.update(qp)
        resp = await handle_action(action, params, rt.poller, cfg, rt.store)
        resp["echo"] = echo
        return resp

    @app.websocket("/onebot/ws")
    async def onebot_ws(ws: WebSocket) -> None:
        if not cfg.onebot_enabled or not check_token(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = rt.bus.subscribe()
        await ws.send_json(lifecycle_event(cfg))
        await ws.send_json(
            heartbeat_event(cfg, rt.poller.status_dict()["state"] in ("online", "demo"))
        )

        async def pump_events() -> None:
            while True:
                event = await queue.get()
                if event.get("type") == "message":
                    await ws.send_json(build_event(event["data"], cfg))
                elif event.get("type") == "status":
                    await ws.send_json(
                        heartbeat_event(cfg, event["data"]["state"] in ("online", "demo"))
                    )

        async def pump_actions() -> None:
            while True:
                raw = await ws.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json(
                        {"status": "failed", "retcode": 1400, "data": None, "message": "不是合法 JSON"}
                    )
                    continue
                resp = await handle_action(
                    str(payload.get("action") or ""), payload.get("params") or {}, rt.poller, cfg, rt.store
                )
                resp["echo"] = payload.get("echo")
                await ws.send_json(resp)

        try:
            await asyncio.gather(pump_events(), pump_actions())
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            rt.bus.unsubscribe(queue)

    # 面板资源用相对路径（插件视图的要求），这里挂在根上兜底。
    # 必须放在所有路由之后，否则会抢 /api 的请求。
    app.mount("/", StaticFiles(directory=VIEW_DIR, html=True), name="panel")

    return app


def serve(data_dir, demo: bool = False, host: str = "127.0.0.1", port: int = 8787) -> None:
    import uvicorn

    rt = BiliRuntime(data_dir, mode=MODE_STANDALONE, demo=demo)
    rt.cfg.host, rt.cfg.port = host, port
    app = create_app(rt, host, port)

    print("\n  bili-dm 已启动")
    print(f"  面板   http://{host}:{port}/")
    print(f"  OneBot http://{host}:{port}/onebot  |  ws://{host}:{port}/onebot/ws")
    if demo:
        print("  模式   演示（不连 B 站，用的是假数据）")
    elif not rt.cfg.logged_in:
        print("  状态   未登录，打开面板扫码或粘贴 Cookie")
    print()
    uvicorn.run(app, host=host, port=port, log_level="warning")
