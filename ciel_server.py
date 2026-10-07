"""
Ciel Server — High-Speed Async Web & WebSocket Server
Drives the Ciel Web HUD, audio streaming, and multi-agent event orchestration.
Powered by aiohttp.
"""

import os
import sys
import json
import re
import uuid
import asyncio
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
from voice_engine import transcribe_audio, transcribe_audio_groq, prune_audio_cache
from eunchae_engine import get_system_vitals
from llm_client import get_brain_status
from proactive_sentinel import evaluate_proactive_suggestions

ciel = CielOrchestrator()
active_websockets = {}  # socket -> its HUD tab's session key (6C)
ws_missions = set()  # strong refs to missions started over WS (the loop keeps only weak ones)

CIEL_PORT = int(os.getenv("CIEL_PORT", 8000))
ALLOWED_HOSTS = {f"localhost:{CIEL_PORT}", f"127.0.0.1:{CIEL_PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
JSON_POST_ROUTES = {"/api/chat", "/api/memory/clear"}

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


@web.middleware
async def localhost_guard(request, handler):
    """
    Blocks cross-site access to the local HUD server.
    - Host check defeats DNS rebinding (attacker domain resolving to 127.0.0.1).
    - Origin check blocks cross-site WebSocket hijacking and CSRF. Browsers always
      send Origin on WebSocket upgrades and cross-origin POSTs, so a missing Origin
      means a non-browser client (curl, local scripts) and is allowed.
    - JSON Content-Type on command routes rules out CORS "simple" requests.
    """
    if request.host not in ALLOWED_HOSTS:
        return web.json_response({"error": "Forbidden host"}, status=403)

    origin = request.headers.get("Origin")
    is_ws = request.headers.get("Upgrade", "").lower() == "websocket"
    if (is_ws or request.method not in ("GET", "HEAD", "OPTIONS")) and origin is not None:
        if origin not in ALLOWED_ORIGINS:
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
    """Returns real-time proactive suggestions evaluated across sentinels."""
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


async def background_sentinel_worker(app):
    """Periodically monitors workspace sentinels and broadcasts suggestions to HUD."""
    print("👁️ [CielServer] Proactive Sentinel watcher loop initialized.")
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
            print(f"⚠️ [CielServer] Sentinel worker warning: {e}", flush=True)


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


BACKGROUND_TASKS = {"sentinel_task": background_sentinel_worker, "telemetry_task": telemetry_worker}


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
    app.router.add_post("/api/memory/clear", handle_clear_memory)
    app.router.add_get("/api/memory", handle_get_memory)

    # Static audio cache serving
    app.router.add_static("/audio", path=str(AUDIO_DIR), name="audio")

    # Static HUD UI serving
    app.router.add_static("/", path=str(HUD_DIR), name="hud", show_index=True)

    return app


if __name__ == "__main__":
    print(f"✨ Starting Ciel Server on http://localhost:{CIEL_PORT}")
    print(f"🗂️ [Vault] {ciel.obsidian.check_vault_consistency()}", flush=True)
    app = create_app()
    web.run_app(app, host="127.0.0.1", port=CIEL_PORT)
