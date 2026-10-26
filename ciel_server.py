"""
Ciel Server — High-Speed Async Web & WebSocket Server
Drives the Ciel Web HUD, audio streaming, and multi-agent event orchestration.
Powered by aiohttp.
"""

import os
import sys
import json
import re
import hmac
import uuid
import asyncio
import mimetypes
import urllib.parse
from pathlib import Path
from aiohttp import web

# The log lines carry emoji; a default Windows console is cp1252 and would raise
# UnicodeEncodeError on them. Captured or redirected streams may lack reconfigure().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
WORKSPACE_DIR = BASE_DIR.parent
HUD_DIR = WORKSPACE_DIR / "ciel-hud"
AUDIO_DIR = BASE_DIR / "audio_cache"

AUDIO_DIR.mkdir(exist_ok=True)
HUD_DIR.mkdir(exist_ok=True)

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ciel_orchestrator import CielOrchestrator
from voice_engine import transcribe_audio, prune_audio_cache
from eunchae_engine import get_system_vitals
from llm_client import get_brain_status
from proactive_sentinel import evaluate_proactive_suggestions
import wake_word  # light: openWakeWord and numpy load only when a HUD turns the wake word on

ciel = CielOrchestrator(store=BASE_DIR / "memory" / "ciel_conversations.json")  # tabs keep their turns over a restart
active_websockets = {}  # socket -> its HUD tab's session key (6C)
ws_missions = set()  # strong refs to missions started over WS (the loop keeps only weak ones)

CIEL_PORT = int(os.getenv("CIEL_PORT", 8000))
ALLOWED_HOSTS = {f"localhost:{CIEL_PORT}", f"127.0.0.1:{CIEL_PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
JSON_POST_ROUTES = {"/api/chat", "/api/memory/clear"}

# Phone access (assistant B8): `tailscale serve` gives this PC an HTTPS name on Hans's tailnet and forwards to
# 127.0.0.1:CIEL_PORT. Such a request is "remote": a host other than localhost, or any proxy header. Remote
# requests need CIEL_ACCESS_TOKEN (none set: remote access is off). The phone opens /?token=<token> once; the
# server answers with an HttpOnly, Secure cookie and drops the token from the address.
ACCESS_COOKIE = "ciel_access"
ACCESS_COOKIE_DAYS = 180
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "Tailscale-User-Login")
# What a browser fetches without cookies to install the web app; nothing in them is private.
PUBLIC_PATHS = {"/manifest.webmanifest", "/sw.js", "/icons/ciel-192.png", "/icons/ciel-512.png",
                "/icons/apple-touch-icon.png"}
# The Windows registry can map .js to text/plain, and a service worker served that way never registers.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/manifest+json", ".webmanifest")

# Each mission can hold several worker threads (agents run in parallel), so cap
# how many run at once. A few more wait their turn; past that, refuse with 503.
MAX_CONCURRENT_MISSIONS = 2
MAX_QUEUED_MISSIONS = 8
AUDIO_CACHE_MAX_FILES = 150


class MissionGate:
    """Admission control shared by REST and WS chat."""

    def __init__(self, slots: int, max_queued: int):
        self._slots = asyncio.Semaphore(slots)
        self.slots = slots
        self.max_queued = max_queued
        self.queued = 0

    async def run(self, prompt: str, mission_id: str, session: str = "default") -> dict:
        """A mission's events go only to the tabs of the session that sent it (6C)."""
        async def send(payload):
            await broadcast_ws(payload, session)

        must_wait = self._slots.locked()
        if must_wait and self.queued >= self.max_queued:
            busy = {
                "mission_id": mission_id,
                "error": f"Ciel is busy: {self.slots} missions running and {self.queued} queued. "
                         "Mission not started; try again shortly.",
                "elapsed_ms": 0,
                "busy": True,
            }
            await send({"type": "ciel_error", **busy})
            return busy
        # Count before any await, so concurrent arrivals see each other.
        self.queued += 1
        try:
            if must_wait:
                await send({"type": "ciel_state", "state": "analyzing", "mission_id": mission_id,
                                    "message": "Queued: waiting for a free mission slot..."})
            await self._slots.acquire()
        finally:
            self.queued -= 1
        try:
            # Streamed speech leaves one clip per sentence; keep the newest few
            # missions' worth (a HUD may still be playing the previous one).
            await asyncio.to_thread(prune_audio_cache, AUDIO_CACHE_MAX_FILES)
            return await ciel.execute_mission(prompt, event_callback=send, mission_id=mission_id, session_id=session)
        finally:
            self._slots.release()


MISSION_GATE = web.AppKey("mission_gate", MissionGate)


def access_token() -> str:
    return (os.getenv("CIEL_ACCESS_TOKEN") or "").strip()


def is_remote(request) -> bool:
    """Not this PC's own browser: another host name, or forwarded by a proxy such as tailscale serve."""
    return request.host not in ALLOWED_HOSTS or any(h in request.headers for h in PROXY_HEADERS)


def _same(given: str, token: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), token.encode())


LOGIN_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ciel</title><body style="background:#06110f;color:#e8f6f3;font:16px system-ui;padding:24px;line-height:1.5">
<h1 style="font-weight:600">Ciel needs its access link</h1>
<p>Open the link from <code>python ciel_server.py --phone-link</code> (run on the PC) once on this device.</p></body>"""


def remote_refusal(request):
    """None when a remote request may go on; else the response that ends it (login, refusal, or the
    redirect that turns a valid ?token= into the cookie)."""
    token = access_token()
    if not token:
        return web.json_response({"error": "Remote access is off: CIEL_ACCESS_TOKEN is not set"}, status=403)
    if request.path in PUBLIC_PATHS:
        return None
    given = request.query.get("token")
    if given is not None and request.method == "GET":
        if not _same(given, token):
            return web.Response(text=LOGIN_PAGE, content_type="text/html", status=401)
        rest = [(k, v) for k, v in request.query.items() if k != "token"]
        location = request.path + ("?" + urllib.parse.urlencode(rest) if rest else "")
        resp = web.Response(status=302, headers={"Location": location})
        resp.set_cookie(ACCESS_COOKIE, token, max_age=ACCESS_COOKIE_DAYS * 86400, path="/", secure=True,
                        httponly=True, samesite="Lax")
        return resp
    auth = request.headers.get("Authorization", "")
    if _same(request.cookies.get(ACCESS_COOKIE, ""), token) or (
            auth.startswith("Bearer ") and _same(auth[7:].strip(), token)):
        return None
    if request.method == "GET" and not request.path.startswith(("/api/", "/ws", "/audio/")):
        return web.Response(text=LOGIN_PAGE, content_type="text/html", status=401)
    return web.json_response({"error": "Access token required"}, status=401)


@web.middleware
async def localhost_guard(request, handler):
    """
    Blocks cross-site access to the local HUD server.
    - Host check defeats DNS rebinding (attacker domain resolving to 127.0.0.1).
    - Origin check blocks cross-site WebSocket hijacking and CSRF. Browsers always
      send Origin on WebSocket upgrades and cross-origin POSTs, so a missing Origin
      means a non-browser client (curl, local scripts) and is allowed.
    - JSON Content-Type on command routes rules out CORS "simple" requests.
    - Remote (phone) requests need the access token, then pass the same Origin and JSON checks
      against their own https origin (assistant B8).
    """
    origin = request.headers.get("Origin")
    is_ws = request.headers.get("Upgrade", "").lower() == "websocket"
    if is_remote(request):
        refused = remote_refusal(request)
        if refused is not None:
            return refused
        host = request.headers.get("X-Forwarded-Host") or request.host
        allowed_origins = {f"https://{host}"}
    else:
        allowed_origins = ALLOWED_ORIGINS
    if (is_ws or request.method not in ("GET", "HEAD", "OPTIONS")) and origin is not None:
        if origin not in allowed_origins:
            return web.json_response({"error": "Forbidden origin"}, status=403)

    if request.method == "POST" and request.path in JSON_POST_ROUTES:
        if request.content_type != "application/json":
            return web.json_response({"error": "Content-Type must be application/json"}, status=415)

    return await handler(request)


async def broadcast_ws(payload: dict, session: str = None):
    """Sends live state updates to every open HUD tab, or only to one session's tabs (6C)."""
    # Copy: missions now run concurrently, so a client can connect or drop mid-loop.
    targets = [ws for ws, s in list(active_websockets.items()) if session is None or s == session]
    if not targets:
        return
    msg = json.dumps(payload)
    for ws in targets:
        try:
            await ws.send_str(msg)
        except Exception:  # quiet: a closed socket is dropped from the client set
            active_websockets.pop(ws, None)


async def handle_telemetry(request):
    """
    Returns real-time system, memory, and AI brain status. The HUD calls it once per
    (re)connect; after that, telemetry_worker pushes vitals over the WebSocket.
    """
    # Both block (psutil samples CPU for 0.5 s; the brain status probes Ollama over
    # HTTP), so run them in threads, side by side.
    vitals, brain = await asyncio.gather(
        asyncio.to_thread(get_system_vitals) if get_system_vitals else asyncio.sleep(0, {}),
        asyncio.to_thread(get_brain_status) if get_brain_status else asyncio.sleep(0, {}),
    )
    return web.json_response({
        "status": "online",
        "name": "Ciel",
        "vitals": vitals,
        "brain": brain,
        "memory_turns": len(ciel.history(session_from(request.query.get("session"))))
    })


async def handle_clear_memory(request):
    """Resets one HUD tab's working conversation memory (body {"session_id": ...}; none = default)."""
    try:
        data = await request.json()
    except ValueError:
        data = {}
    session = session_from(data.get("session_id") if isinstance(data, dict) else None)
    ciel.reset_memory(session)
    await broadcast_ws({"type": "memory_cleared", "message": "Session memory cleared."}, session)
    return web.json_response({"status": "cleared", "turns": 0})


async def handle_get_memory(request):
    """Returns one HUD tab's conversation memory turns (?session=...; none = default)."""
    history = ciel.history(session_from(request.query.get("session")))
    return web.json_response({"turns": len(history), "history": history})


MISSION_ID_RE = re.compile(r"[0-9a-f]{32}")


def session_from(value) -> str:
    """A HUD tab's session id (the mission-id shape), or "default" for callers that name none (6C)."""
    return value if isinstance(value, str) and MISSION_ID_RE.fullmatch(value) else "default"


def mission_id_from(data: dict) -> str:
    """
    The HUD names each mission before sending it (so it knows which result is the
    newest even if an older mission finishes later). Accept that id only in the
    exact uuid4-hex shape: it becomes part of the audio filename, so anything else
    (path separators, dots, overlong strings) is replaced with a fresh id.
    """
    candidate = data.get("mission_id")
    if isinstance(candidate, str) and MISSION_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


async def handle_chat(request):
    """Standard REST chat dispatch for Ciel."""
    try:
        data = await request.json()
        user_prompt = data.get("prompt", "").strip()
        if not user_prompt:
            return web.json_response({"error": "Empty prompt"}, status=400)
        mission_id = mission_id_from(data)
        session = session_from(data.get("session_id"))

        # Tell the sending tab's sockets it started (other tabs never see it, 6C)
        await broadcast_ws({"type": "chat_received", "prompt": user_prompt, "mission_id": mission_id}, session)

        # Execute mission with live WS events
        result = await request.app[MISSION_GATE].run(user_prompt, mission_id, session)
        # execute_mission reports mission-level failures as {"error": ...} rather than raising.
        status = 503 if result.get("busy") else 500 if result.get("error") else 200
        return web.json_response(result, status=status)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_suggestions(request):
    """Returns the current suggestions from the background checks."""
    try:
        suggs = await asyncio.to_thread(evaluate_proactive_suggestions)
        return web.json_response({
            "suggestions": suggs,
            "count": len(suggs),
            "timestamp": asyncio.get_event_loop().time()
        })
    except Exception as e:
        return web.json_response({"error": str(e), "suggestions": []}, status=500)


async def handle_transcribe(request):
    """Receives voice audio from HUD microphone and returns transcribed text."""
    try:
        reader = await request.multipart()
        audio_bytes = None
        filename = "recording.wav"

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                filename = part.filename or "recording.wav"
                audio_bytes = await part.read()
                break

        if not audio_bytes:
            return web.json_response({"error": "No audio received"}, status=400)

        result = await asyncio.to_thread(transcribe_audio, audio_bytes, filename=filename)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def websocket_handler(request):
    """Maintains a persistent bi-directional connection for live agent pulsing."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    # The tab names its session in the URL (/ws?session=<id>); its missions use it too.
    session = session_from(request.query.get("session"))
    active_websockets[ws] = session

    # Send initial welcome telemetry
    await ws.send_str(json.dumps({
        "type": "ciel_connected",
        "message": "Ciel Core linked to HUD.",
        "agents": ["sakura", "chaewon", "kazuha", "yunjin", "eunchae"]
    }))

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                action = data.get("action")
                if action == "chat":
                    prompt = data.get("prompt", "")
                    mission_id = mission_id_from(data)
                    await broadcast_ws({"type": "chat_received", "prompt": prompt, "mission_id": mission_id}, session)
                    # Run the mission as a task so this socket keeps answering pings
                    # while it runs. Results reach this session's tabs through broadcast_ws.
                    task = asyncio.create_task(request.app[MISSION_GATE].run(prompt, mission_id, session))
                    ws_missions.add(task)
                    task.add_done_callback(ws_missions.discard)
                elif action == "ping":
                    await ws.send_str(json.dumps({"type": "pong", "time": asyncio.get_event_loop().time()}))
    finally:
        active_websockets.pop(ws, None)

    return ws


MAX_WAKE_SOCKETS = 4  # one per HUD tab with the wake word on; each holds an openWakeWord model
wake_sockets = set()


async def wake_handler(request):
    """
    Assistant B7: while "wake word" is on, the HUD streams its microphone here as binary frames of 16 kHz
    mono 16-bit PCM. One detector per socket (openWakeWord keeps per-stream state); "wake" goes back on
    this socket when the phrase is heard. Same Host/Origin guard as /ws. Audio stays on this machine.
    """
    ws = web.WebSocketResponse(max_msg_size=64 * 1024)
    await ws.prepare(request)
    if len(wake_sockets) >= MAX_WAKE_SOCKETS:
        await ws.send_str(json.dumps({"type": "wake_unavailable", "error": "too many listening tabs"}))
        await ws.close()
        return ws
    wake_sockets.add(ws)
    try:
        try:
            detector = await asyncio.to_thread(wake_word.WakeDetector)  # loading the model takes ~2 s
        except Exception as e:  # not installed or the model was never downloaded
            await ws.send_str(json.dumps({"type": "wake_unavailable", "error": f"{type(e).__name__}: {e}"}))
            await ws.close()
            return ws
        await ws.send_str(json.dumps({"type": "wake_ready", "model": wake_word.model_name()}))
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                score = await asyncio.to_thread(detector.feed, msg.data)
                if score:
                    await ws.send_str(json.dumps({"type": "wake", "score": score}))
    finally:
        wake_sockets.discard(ws)
    return ws


async def background_sentinel_worker(app):
    """Runs the background checks every 45 s and broadcasts suggestions to the HUD."""
    print("👁️ [CielServer] Background checks loop started.")
    while True:
        try:
            await asyncio.sleep(45)
            if active_websockets:
                suggs = await asyncio.to_thread(evaluate_proactive_suggestions)
                await broadcast_ws({
                    "type": "ciel_suggestion",
                    "suggestions": suggs,
                    "count": len(suggs)
                })
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ [CielServer] Background checks warning: {e}", flush=True)


TELEMETRY_INTERVAL_S = 5


async def telemetry_worker(app):
    """
    Pushes vitals to connected HUDs every TELEMETRY_INTERVAL_S, replacing the HUD's
    HTTP poll. Samples only while a client is connected. cpu_interval=None never
    sleeps; it reports CPU use since the previous sample.
    """
    # ponytail: after an idle stretch the first CPU figure averages the whole gap.
    while True:
        try:
            await asyncio.sleep(TELEMETRY_INTERVAL_S)
            if active_websockets and get_system_vitals:
                vitals = await asyncio.to_thread(get_system_vitals, cpu_interval=None)
                # Same vitals for every tab; memory_turns is each tab's own (6C).
                for session in set(active_websockets.values()):
                    await broadcast_ws({"type": "telemetry", "vitals": vitals,
                                        "memory_turns": len(ciel.history(session))}, session)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ [CielServer] Telemetry worker warning: {e}", flush=True)


# Typed keys like MISSION_GATE (plain string keys raise aiohttp's NotAppKeyWarning).
BACKGROUND_TASKS = {
    web.AppKey("background_checks_task", asyncio.Task): background_sentinel_worker,
    web.AppKey("telemetry_task", asyncio.Task): telemetry_worker,
}


async def start_background_tasks(app):
    for key, worker in BACKGROUND_TASKS.items():
        app[key] = asyncio.create_task(worker(app))


async def cleanup_background_tasks(app):
    for key in BACKGROUND_TASKS:
        if key in app:
            app[key].cancel()
            try:
                await app[key]
            except asyncio.CancelledError:
                pass


async def handle_hud(request):
    """
    Serves the HUD's files, with / as index.html. No directory listing, and nothing
    under a dot-path (.git, .github, .gitignore). The check runs on the resolved path,
    so Windows 8.3 aliases (GIT~1 is .git) and traversal are refused too.
    """
    try:
        target = (HUD_DIR / (request.match_info["path"] or "index.html")).resolve(strict=True)
        rel = target.relative_to(HUD_DIR.resolve())
    except (OSError, ValueError):
        raise web.HTTPNotFound()
    if not target.is_file() or any(part.startswith(".") for part in rel.parts):
        raise web.HTTPNotFound()
    return web.FileResponse(target)


def create_app():
    # Auto-prune audio cache on server spin-up
    pruned = prune_audio_cache()
    if pruned:
        print(f"🧹 [CielServer] Pruned {pruned} old audio cache files.")

    app = web.Application(middlewares=[localhost_guard])
    # Built per app: a Semaphore binds to the event loop that first waits on it.
    app[MISSION_GATE] = MissionGate(MAX_CONCURRENT_MISSIONS, MAX_QUEUED_MISSIONS)
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    
    # API endpoints
    app.router.add_get("/api/telemetry", handle_telemetry)
    app.router.add_get("/api/suggestions", handle_get_suggestions)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_post("/api/transcribe", handle_transcribe)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_get("/ws/wake", wake_handler)
    app.router.add_post("/api/memory/clear", handle_clear_memory)
    app.router.add_get("/api/memory", handle_get_memory)

    # Static audio cache serving
    app.router.add_static("/audio", path=str(AUDIO_DIR), name="audio")

    # Static HUD UI serving (last: the catch-all must not shadow the routes above)
    app.router.add_get("/{path:.*}", handle_hud, name="hud")

    return app


def phone_link() -> str:
    """The one-time link for the phone: this PC's tailnet name (from `tailscale status --json`) plus the token.
    Prints only to the terminal of whoever runs it; nothing is logged."""
    import subprocess
    token = access_token()
    if not token:
        return "CIEL_ACCESS_TOKEN is not set in .env: remote access is off."
    try:
        status = json.loads(subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True,
                                           timeout=10, check=True).stdout)
        name = status["Self"]["DNSName"].rstrip(".")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        return f"Could not read this PC's Tailscale name ({type(e).__name__}); is Tailscale installed and signed in?"
    return f"https://{name}/?token={urllib.parse.quote(token)}"


if __name__ == "__main__":
    if "--phone-link" in sys.argv:
        print(phone_link())
        sys.exit(0)
    print(f"✨ Starting Ciel Server on http://localhost:{CIEL_PORT}")
    print(f"🗂️ [Vault] {ciel.obsidian.check_vault_consistency()}", flush=True)
    app = create_app()
    web.run_app(app, host="127.0.0.1", port=CIEL_PORT)
