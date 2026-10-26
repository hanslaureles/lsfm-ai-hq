"""
reminders.py - Reminders Hans sets through Ciel or Discord, delivered by Sakura as a Discord message
(assistant B6: the bots run all day, Ciel only while its server is up).

They live in memory/reminders.json. Ciel's server and the bots are two processes, so every change goes
through an OS lock (obsidian_client.try_lock) and a temp-file replace. Sakura claims a due reminder
(status "sent") before sending it: a crash can lose one reminder, never send it twice (the 6B rule).
"""

import contextlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from obsidian_client import try_lock

STORE = Path(__file__).resolve().parent / "memory" / "reminders.json"
LOCK_TIMEOUT_S = 5
MAX_AHEAD = timedelta(days=366)
_UNITS = {"min": "minutes", "minute": "minutes", "mins": "minutes", "minutes": "minutes", "hr": "hours",
          "hour": "hours", "hrs": "hours", "hours": "hours", "day": "days", "days": "days"}


def parse_when(text: str, now: datetime):
    """A future local datetime for "2026-10-26T17:00", "in 20 minutes", "at 5pm", "tomorrow at 9:30",
    "tomorrow"; None when it cannot tell, or the time is not after now and within MAX_AHEAD. Every form
    goes through that one check, and an impossible number is a None, never an exception (Codex QA F2)."""
    try:
        when = _parse(text, now)
    except (ValueError, OverflowError):  # a bad ISO time, too many digits, or past datetime's range
        return None
    return when if when is not None and now < when <= now + MAX_AHEAD else None


def _parse(text: str, now: datetime):
    s = (text or "").strip().lower()
    if re.match(r"^\d{4}-\d\d-\d\d", s):
        when = datetime.fromisoformat(s.upper().replace(" ", "T"))
        return when.astimezone().replace(tzinfo=None) if when.tzinfo else when
    m = re.search(r"\bin\s+(\d+)\s*(mins?|minutes?|hrs?|hours?|days?)\b", s)
    if m:
        return now + timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})
    tomorrow = "tomorrow" in s
    m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\bat\s+(\d{1,2}):(\d{2})\b", s)
    if m:
        hour, minute = (int(m.group(1)), int(m.group(2) or 0)) if m.group(1) else (int(m.group(4)), int(m.group(5)))
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=tomorrow)
        return when if when > now else (when + timedelta(days=1) if not tomorrow else None)
    if tomorrow:
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return None


@contextlib.contextmanager
def _locked(store: Path):
    """The reminder list, read under the lock; what the caller leaves in it is written back."""
    store.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    with open(store.with_name(store.name + ".lock"), "a+b") as fh:
        while not try_lock(fh):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"reminders lock busy for {LOCK_TIMEOUT_S} s")
            time.sleep(0.02)
        try:
            items = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else []
        except ValueError:
            items = []  # ponytail: an unreadable file starts over; it only ever holds Hans's own reminders
        before = json.dumps(items)
        yield items
        if json.dumps(items) != before:
            tmp = store.with_name(store.name + ".tmp")
            tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, store)


def add(text: str, due: datetime, store: Path = None) -> dict:
    item = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "due": due.isoformat(timespec="minutes"),
            "created": datetime.now().isoformat(timespec="minutes"), "status": "pending"}
    with _locked(store or STORE) as items:
        items.append(item)
    return item


def pending(store: Path = None) -> list:
    with _locked(store or STORE) as items:
        return sorted((i for i in items if i["status"] == "pending"), key=lambda i: i["due"])


def cancel(match: str, store: Path = None) -> list:
    """Cancels the pending reminders whose text contains `match` (all of them for "all"); returns them."""
    m = match.strip().lower()
    with _locked(store or STORE) as items:
        hit = [i for i in items if i["status"] == "pending" and (m == "all" or (m and m in i["text"].lower()))]
        for i in hit:
            i["status"] = "cancelled"
    return hit


def claim_due(now: datetime, store: Path = None) -> list:
    """Due pending reminders, marked sent before they are returned for sending; old entries are dropped."""
    with _locked(store or STORE) as items:
        due = [i for i in items if i["status"] == "pending" and datetime.fromisoformat(i["due"]) <= now]
        for i in due:
            i.update(status="sent", sent_at=now.isoformat(timespec="minutes"))
        cutoff = (now - timedelta(days=30)).isoformat()
        items[:] = [i for i in items if i["status"] == "pending" or i.get("sent_at", i["due"]) >= cutoff]
    return due


def mark_failed(item_id: str, reason: str, store: Path = None) -> None:
    with _locked(store or STORE) as items:
        for i in items:
            if i["id"] == item_id:
                i.update(status="failed", reason=reason[:200])
