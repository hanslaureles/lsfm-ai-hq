"""
Per-bot heartbeat files (Phase 4, 4B-2): memory/health/<bot>.json, rewritten on
on_ready and every HEARTBEAT_INTERVAL_S while the bot runs. eunchae_publisher
reads them; a file that stops changing is how a dead bot shows up. Stdlib only.

Each bot module calls attach(bot, "<name>") once, so the files exist whether the
squad runs through run_all.py or a bot runs on its own.
"""

import asyncio
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HEALTH_DIR = Path(__file__).resolve().parent / "memory" / "health"
HEARTBEAT_INTERVAL_S = 60
# Windows: os.replace fails with PermissionError while another process has the target open.
# 20 tries with linearly growing sleeps wait ~1 s at most; writes run in a thread (Heartbeat.beat).
REPLACE_TRIES = 20
_NAME = re.compile(r"^[a-z]+$")


def record_heartbeat(bot_name: str, status: str = "online", metadata: dict | None = None,
                     health_dir: Path | None = None) -> Path:
    """Atomically writes <health_dir>/<bot_name>.json (temp file in the same dir + os.replace)."""
    if not _NAME.match(bot_name or ""):
        raise ValueError(f"bot name must be lowercase letters only, got {bot_name!r}")
    health_dir = Path(health_dir or HEALTH_DIR)
    health_dir.mkdir(parents=True, exist_ok=True)
    payload = {"bot": bot_name, "status": status,
               "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "pid": os.getpid(), **(metadata or {})}
    target = health_dir / f"{bot_name}.json"
    fd, tmp = tempfile.mkstemp(dir=health_dir, prefix=f".{bot_name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, target)
                return target
            except PermissionError:
                if attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(0.005 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class Heartbeat:
    """Counts one bot's gateway events and writes its heartbeat file."""

    def __init__(self, bot, name: str, health_dir: Path | None = None, interval: float = HEARTBEAT_INTERVAL_S):
        self.bot, self.name, self.health_dir, self.interval = bot, name, health_dir, interval
        self.events_processed = 0
        self.last_event_type = None
        self.disconnects = 0
        self._task = None

    async def _on_socket_event_type(self, event_type):
        self.events_processed += 1
        self.last_event_type = event_type

    async def _on_disconnect(self):
        self.disconnects += 1

    async def _on_ready(self):
        # on_ready fires again after every reconnect; keep a single loop.
        await self.beat()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            await asyncio.sleep(self.interval)
            await self.beat()

    async def beat(self):
        status = "online" if self.bot.is_ready() and not self.bot.is_closed() else "not_ready"
        metadata = {"events_processed": self.events_processed, "last_event_type": self.last_event_type,
                    "disconnects": self.disconnects}
        try:
            # A thread: the Windows replace retry may sleep, and the bot's loop must not.
            await asyncio.to_thread(record_heartbeat, self.name, status, metadata, self.health_dir)
        except Exception as _exc:
            print(f"⚠️ [health:{self.name}] heartbeat write failed: {type(_exc).__name__}: {_exc}", flush=True)


def attach(bot, name: str, health_dir: Path | None = None, interval: float = HEARTBEAT_INTERVAL_S) -> Heartbeat:
    """Registers heartbeat listeners on a discord.py bot. Listeners survive Client.clear()."""
    hb = Heartbeat(bot, name, health_dir, interval)
    bot.add_listener(hb._on_socket_event_type, "on_socket_event_type")  # every gateway event (discord.py 2.x)
    bot.add_listener(hb._on_disconnect, "on_disconnect")
    bot.add_listener(hb._on_ready, "on_ready")
    return hb
