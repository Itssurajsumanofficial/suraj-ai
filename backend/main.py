"""
Suraj AI server — FastAPI app exposing REST + WebSocket endpoints
for phone control, chat, and live screen mirroring.

Supports multiple simultaneous users and devices:
  - Optional password protection (SURAJ_PASSWORD env var)
  - Per-session device selection (each user picks their phone)
  - Multiple ADB devices connected at once

Run:   python3 server.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Make the backend package importable when running with uvicorn
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.adb.client import ADBClient, ADBError  # noqa: E402
from backend.agent.brain import Brain  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("suraj")

# --------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------- #
PASSWORD = os.environ.get("SURAJ_PASSWORD", "")  # empty = no auth
SESSION_TTL = 60 * 60 * 24  # 24 hours

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# --------------------------------------------------------------------- #
#  Device Manager — tracks multiple phones
# --------------------------------------------------------------------- #
class DeviceManager:
    """
    Manages multiple ADB-connected devices.  Each user session selects
    which device to control.  This lets one server control many phones
    simultaneously (great for shared/family deployment).
    """

    def __init__(self):
        self._clients: dict[str, ADBClient] = {}  # serial → client
        self._scanner = ADBClient()  # generic client for device scanning

    def scan(self) -> list[dict[str, str]]:
        """Return all connected devices."""
        try:
            return self._scanner.list_devices()
        except ADBError:
            return []

    def connect_wireless(self, host: str) -> str:
        """Connect to a phone over TCP."""
        out = self._scanner.connect(host)
        self.refresh()
        return out

    def refresh(self):
        """Rebuild the client map from connected devices."""
        for dev in self.scan():
            serial = dev["serial"]
            if serial not in self._clients:
                self._clients[serial] = ADBClient(serial=serial)

    def get_client(self, serial: str | None = None) -> ADBClient:
        """Get the ADB client for a device.  Falls back to the first."""
        self.refresh()
        if serial and serial in self._clients:
            return self._clients[serial]
        if self._clients:
            return next(iter(self._clients.values()))
        return self._scanner  # no device — returns graceful errors

    def list_clients(self) -> list[dict]:
        """List all known devices with status."""
        self.refresh()
        out = []
        for serial, client in self._clients.items():
            connected = client.is_connected()
            out.append({"serial": serial, "connected": connected})
        return out


devices = DeviceManager()

# Per-session state: session_id → {device_serial, password_ok}
sessions: dict[str, dict[str, Any]] = {}


def _new_session() -> str:
    sid = secrets.token_hex(16)
    sessions[sid] = {"device": None, "authed": not bool(PASSWORD)}
    return sid


def _get_session(request: Request) -> dict | None:
    sid = None
    # Check cookie
    sid = request.cookies.get("suraj_session")
    if sid and sid in sessions:
        return sessions[sid]
    # Check Authorization header (for API clients)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        if token in sessions:
            return sessions[token]
    return None


def _check_auth(request: Request) -> bool:
    if not PASSWORD:
        return True
    sess = _get_session(request)
    return sess is not None and sess.get("authed", False)


# --------------------------------------------------------------------- #
#  Lifespan
# --------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Suraj AI server starting…")
    if PASSWORD:
        log.info("Password protection: ENABLED")
    else:
        log.info("Password protection: disabled (set SURAJ_PASSWORD to enable)")
    devs = devices.scan()
    log.info("ADB devices found: %d", len(devs))
    yield
    log.info("Suraj AI server stopped.")


app = FastAPI(title="Suraj AI", version="1.0.0", lifespan=lifespan)

# serve static frontend
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# --------------------------------------------------------------------- #
#  Auth
# --------------------------------------------------------------------- #
@app.get("/login", response_class=HTMLResponse)
async def login_page():
    if not PASSWORD:
        return RedirectResponse("/")
    html = FRONTEND_DIR / "login.html"
    if html.exists():
        return html.read_text(encoding="utf-8")
    return HTMLResponse("login.html not found", 404)


@app.post("/api/login")
async def do_login(request: Request, password: str = ""):
    if not PASSWORD:
        return RedirectResponse("/")
    if password == PASSWORD:
        sid = _new_session()
        sessions[sid]["authed"] = True
        resp = JSONResponse({"ok": True})
        resp.set_cookie("suraj_session", sid, max_age=SESSION_TTL,
                        httponly=True, samesite="lax")
        return resp
    return JSONResponse({"ok": False, "error": "Wrong password"}, status_code=401)


@app.post("/api/logout")
async def logout(request: Request):
    sid = request.cookies.get("suraj_session")
    if sid and sid in sessions:
        del sessions[sid]
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("suraj_session")
    return resp


# --------------------------------------------------------------------- #
#  Auth middleware
# --------------------------------------------------------------------- #
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Allow login endpoints and static files without auth
    path = request.url.path
    if path in ("/login", "/api/login", "/api/logout") or path.startswith("/static"):
        return await call_next(request)

    if PASSWORD and not _check_auth(request):
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login")

    return await call_next(request)


# --------------------------------------------------------------------- #
#  Page route
# --------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return HTMLResponse("<h1>frontend/index.html not found</h1>", 404)


# --------------------------------------------------------------------- #
#  REST API — device management
# --------------------------------------------------------------------- #
@app.get("/api/devices")
async def list_devices(request: Request):
    """List all connected ADB devices."""
    sess = _get_session(request)
    return {"devices": devices.scan(),
            "active": sess.get("device") if sess else None}


@app.post("/api/connect/{host}")
async def connect_device(request: Request, host: str):
    """Connect to a device over wireless ADB (host:port)."""
    try:
        out = devices.connect_wireless(host)
        sess = _get_session(request)
        if sess:
            sess["device"] = host
        return {"message": out, "connected": True}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/select-device/{serial}")
async def select_device(request: Request, serial: str):
    """Select which device this session controls."""
    sess = _get_session(request)
    if sess:
        sess["device"] = serial
        return {"ok": True, "device": serial}
    return JSONResponse({"error": "no session"}, status_code=403)


@app.get("/api/status")
async def device_status(request: Request):
    """Get current device info."""
    sess = _get_session(request) or {}
    serial = sess.get("device")
    client = devices.get_client(serial)
    if not client.is_connected():
        return JSONResponse(
            {"error": "no device connected", "connected": False},
            status_code=503,
        )
    try:
        return client.get_info().to_dict()
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# --------------------------------------------------------------------- #
#  REST API — actions
# --------------------------------------------------------------------- #
def _adb(request: Request) -> ADBClient:
    sess = _get_session(request) or {}
    return devices.get_client(sess.get("device"))


@app.get("/api/screenshot")
async def screenshot(request: Request):
    try:
        png = _adb(request).screenshot()
        return Response(content=png, media_type="image/png")
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/tap")
async def tap(request: Request, x: int, y: int):
    try:
        return {"result": _adb(request).tap(x, y)}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/swipe")
async def swipe(request: Request, x1: int, y1: int, x2: int, y2: int, duration: int = 300):
    try:
        return {"result": _adb(request).swipe(x1, y1, x2, y2, duration)}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/type")
async def type_text(request: Request, text: str):
    try:
        return {"result": _adb(request).type_text(text)}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/keyevent/{key}")
async def keyevent(request: Request, key: str):
    try:
        return {"result": _adb(request).keyevent(key)}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/app/open")
async def open_app(request: Request, package: str):
    try:
        return {"result": _adb(request).open_app(package)}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/apps")
async def list_apps(request: Request, third_party: bool = True):
    try:
        return {"apps": _adb(request).list_packages(third_party_only=third_party)}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/notifications")
async def notifications(request: Request):
    try:
        return {"notifications": _adb(request).get_notifications()}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/sms")
async def send_sms(request: Request, number: str, message: str):
    try:
        return {"result": _adb(request).send_sms(number, message)}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/call")
async def make_call(request: Request, number: str):
    try:
        return {"result": _adb(request).make_call(number)}
    except ADBError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# --------------------------------------------------------------------- #
#  WebSocket — chat + live mirror (per-session device)
# --------------------------------------------------------------------- #
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Bidirectional channel.  Each connection is tied to a session
    (via cookie or ?token= param) so it controls the right phone.
    """
    # Auth via cookie or token param (browsers auto-send cookies)
    cookie_sid = ws.cookies.get("suraj_session", "")
    token = ws.query_params.get("token", "")
    sess = None
    if not PASSWORD:
        sess = {"device": None}
    else:
        sid = cookie_sid or token
        if sid and sid in sessions:
            sess = sessions[sid]
    if sess is None:
        await ws.close(code=4001, reason="unauthorized")
        return

    await ws.accept()
    log.info("WebSocket client connected (device=%s)", sess.get("device"))

    mirror_task: asyncio.Task | None = None
    client = devices.get_client(sess.get("device"))
    brain = Brain(client)

    async def run_mirror(interval: float):
        try:
            while True:
                try:
                    png = client.screenshot()
                    b64 = base64.b64encode(png).decode()
                    await ws.send_json({"type": "screenshot", "image": b64})
                except ADBError as e:
                    await ws.send_json({"type": "error", "message": str(e)})
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")
            # Re-fetch client in case the user switched devices
            client = devices.get_client(sess.get("device"))
            brain = Brain(client)

            if msg_type == "chat":
                await ws.send_json({"type": "thinking"})
                user_text = data.get("text", "")
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, brain.handle, user_text, None
                )
                await ws.send_json(result | {"type": "chat_reply"})

            elif msg_type == "select_device":
                serial = data.get("serial")
                sess["device"] = serial
                client = devices.get_client(serial)
                brain = Brain(client)
                await ws.send_json({"type": "info",
                                    "message": f"switched to {serial}"})

            elif msg_type == "list_devices":
                await ws.send_json({"type": "devices",
                                    "devices": devices.scan(),
                                    "active": sess.get("device")})

            elif msg_type == "mirror":
                if mirror_task and not mirror_task.done():
                    mirror_task.cancel()
                interval = float(data.get("interval", 1.5))
                mirror_task = asyncio.create_task(run_mirror(interval))
                await ws.send_json({"type": "info", "message": "mirror started"})

            elif msg_type == "mirror_stop":
                if mirror_task and not mirror_task.done():
                    mirror_task.cancel()
                await ws.send_json({"type": "info", "message": "mirror stopped"})

            elif msg_type == "status":
                if client.is_connected():
                    info = await _run_in_thread(client.get_info)
                    await ws.send_json({"type": "status", "info": info.to_dict()})
                else:
                    await ws.send_json({"type": "status",
                                        "info": {"connected": False}})

            elif msg_type == "tap":
                try:
                    client.tap(data["x"], data["y"])
                    await ws.send_json({"type": "info", "message": "tapped"})
                except ADBError as e:
                    await ws.send_json({"type": "error", "message": str(e)})

            elif msg_type == "swipe":
                try:
                    client.swipe(data["x1"], data["y1"], data["x2"], data["y2"])
                    await ws.send_json({"type": "info", "message": "swiped"})
                except ADBError as e:
                    await ws.send_json({"type": "error", "message": str(e)})

            elif msg_type == "screenshot":
                try:
                    png = client.screenshot()
                    b64 = base64.b64encode(png).decode()
                    await ws.send_json({"type": "screenshot", "image": b64})
                except ADBError as e:
                    await ws.send_json({"type": "error", "message": str(e)})

            else:
                await ws.send_json({"type": "error",
                                    "message": f"unknown type: {msg_type}"})

    except WebSocketDisconnect:
        log.info("WebSocket client disconnected")
    finally:
        if mirror_task and not mirror_task.done():
            mirror_task.cancel()


async def _run_in_thread(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)


# --------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
