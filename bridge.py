"""
bridge.py - The LSFM side of the Ciel bridge (Ciel split plan, batch C1, 2026-10-27).

The bots and Ciel are two projects. Ciel asks the agents for work over HTTP on this PC instead of importing
their code; the bots serve it from their own process (run_all.py). With the bots stopped Ciel gets no answer
and says the agents are offline; with Ciel stopped nothing here changes. Nothing in this file needs Ciel.

  GET  /health  -> {"ok": true, "skills": <count>}
  GET  /skills  -> {"skills": [{name, agent, desc, args, keywords, changes_things, confirm_text, timeout_s}]}
  POST /skill   {"calls": [[name, {args}], ...], "confirmed": false} -> {"status", "text", "data"}

Every request needs `Authorization: Bearer <LSFM_BRIDGE_TOKEN>` (no token in .env: the bridge does not start),
a 127.0.0.1 peer and a localhost Host header (a page in a browser cannot rebind a name onto it). Calls are
checked as planner calls (a skill hidden from Ciel's planner is refused) and run through skills.run_calls:
the same per-skill timeouts, heavy pool and confirmation gates as when Ciel imported the skills.
"""

import hmac
import json
import os

from aiohttp import web

import skills

DEFAULT_PORT = 8010
MAX_CALLS = 8
MAX_BODY = 64 * 1024


def bridge_port():
    """LSFM_BRIDGE_PORT (default 8010), or None when it is set but is not a port number (1-65535)."""
    raw = (os.getenv("LSFM_BRIDGE_PORT") or "").strip()
    if not raw:
        return DEFAULT_PORT
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= 65535 else None


def bridge_token() -> str:
    return (os.getenv("LSFM_BRIDGE_TOKEN") or "").strip()


def refusal(request, token: str, port: int):
    """None when the request may use the bridge, else (HTTP status, reason)."""
    peer = (request.transport.get_extra_info("peername") or ("",))[0] if request.transport else ""
    if peer not in ("127.0.0.1", "::1"):
        return 403, "the bridge only answers this PC"
    if request.host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return 403, "unexpected Host header"
    sent = request.headers.get("Authorization", "")
    if not (token and sent.startswith("Bearer ") and hmac.compare_digest(sent[7:].encode(), token.encode())):
        return 401, "missing or wrong bridge token"
    return None


def catalog() -> list:
    """The skills Ciel's planner may call, as plain data (Ciel no longer imports skills.py)."""
    return [{"name": s.name, "agent": s.agent, "desc": s.desc, "args": dict(s.args), "keywords": list(s.keywords),
             "changes_things": s.changes_things, "confirm_text": s.confirm_text, "timeout_s": s.timeout_s}
            for s in skills.planner_skills()]


def parse_calls(body) -> tuple:
    """(calls, confirmed) from a POST /skill body; ValueError with the reason when it is not usable."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    calls, confirmed = body.get("calls"), body.get("confirmed", False)
    if not isinstance(calls, list) or not calls:
        raise ValueError("calls must be a non-empty list of [name, args]")
    if len(calls) > MAX_CALLS:
        raise ValueError(f"at most {MAX_CALLS} calls per request")
    if not isinstance(confirmed, bool):
        raise ValueError("confirmed must be true or false")
    out = []
    for call in calls:
        if not (isinstance(call, list) and len(call) == 2):
            raise ValueError(f"malformed call (expected [name, args]): {call!r:.60}")
        name, args = call
        skills.validate_call(name, args, planner=True)  # ValueError: unknown, hidden or bad args
        out.append((name, args or {}))
    return out, confirmed


def make_app(token: str, port: int) -> web.Application:
    @web.middleware
    async def guard(request, handler):
        refused = refusal(request, token, port)
        if refused:
            return web.json_response({"error": refused[1]}, status=refused[0])
        return await handler(request)

    async def health(_request):
        return web.json_response({"ok": True, "skills": len(skills.planner_skills())})

    async def list_skills(_request):
        return web.json_response({"skills": catalog()})

    async def run(request):
        try:
            calls, confirmed = parse_calls(await request.json())
        except (ValueError, json.JSONDecodeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        result = await skills.run_calls(calls, confirmed=confirmed)
        # Skill data can hold paths and dates; Ciel runs on this PC, so a path is usable as text.
        body = json.dumps({"status": result.status, "text": result.text, "data": result.data},
                          default=str, ensure_ascii=False)
        return web.Response(text=body, content_type="application/json")

    app = web.Application(middlewares=[guard], client_max_size=MAX_BODY)
    app.router.add_get("/health", health)
    app.router.add_get("/skills", list_skills)
    app.router.add_post("/skill", run)
    return app


async def start():
    """Starts the bridge on 127.0.0.1 inside the bots' event loop; the runner (for cleanup), or None when it is
    off (no token) or could not start. A bridge that fails never stops the bots."""
    token, port = bridge_token(), bridge_port()
    if not token:
        print("🌉 [Bridge] off: no LSFM_BRIDGE_TOKEN in .env", flush=True)
        return None
    if port is None:
        print("⚠️ [Bridge] off: LSFM_BRIDGE_PORT is not a port number (1-65535)", flush=True)
        return None
    runner = web.AppRunner(make_app(token, port), access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
    except Exception as e:  # any start failure (busy port, bad config): log it, clean up, bots carry on
        print(f"⚠️ [Bridge] could not listen on 127.0.0.1:{port}: {type(e).__name__}: {e}; "
              "the bots run without it", flush=True)
        await runner.cleanup()
        return None
    print(f"🌉 [Bridge] listening on 127.0.0.1:{port} for Ciel", flush=True)
    return runner
