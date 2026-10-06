"""
Once-per-day delivery for the bots' scheduled jobs (Phase 6B).

The jobs used to write their "done today" date only after every message was
sent, so a send that failed partway (part 2 of 3) was repeated from part 1 on the
next loop tick. run_daily splits a job into two steps:

- prepare(): generate the text / file. A failure (or None) claims nothing, so the
  next tick retries: an LLM or Gmail outage costs a delay, not the day.
- deliver(prepared): send it. The day is claimed *before* the first message, and
  a failure here is never retried: at most once. One short notice says so.
"""

import os
from pathlib import Path

SKIPPED, NOT_READY, CLAIM_FAILED, DELIVERED, DELIVER_FAILED = (
    "skipped", "not ready", "claim failed", "delivered", "deliver failed")


def read_state(state_file: Path) -> str:
    try:
        return state_file.read_text(encoding="utf-8").strip()
    except OSError:  # quiet: no state file yet means never delivered
        return ""


def claim(state_file: Path, today: str) -> bool:
    """Write today's date atomically (temp file + os.replace); False if it could not be written."""
    tmp = state_file.with_name(state_file.name + ".tmp")
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(today, encoding="utf-8")
        os.replace(tmp, state_file)
        return True
    except OSError as e:
        print(f"⚠️ [daily_once] could not claim {state_file.name}: {type(e).__name__}: {e}", flush=True)
        return False


async def run_daily(state_file: Path, today: str, label: str, prepare, deliver, notify=None, due=None) -> str:
    """
    Run one scheduled job at most once per day. state_file keeps the last day it
    was claimed (the existing memory/last_*_date.txt files). due(last) decides if
    the job is due (default: last != today); return one of the status constants.
    notify(text) is an optional async best-effort notice after a failed delivery.
    """
    is_due = due or (lambda last: last != today)
    if not is_due(read_state(state_file)):
        return SKIPPED
    try:
        prepared = await prepare()
    except Exception as e:
        print(f"⚠️ [{label}] not ready, will retry on the next check: {type(e).__name__}: {e}", flush=True)
        return NOT_READY
    if prepared is None:
        return NOT_READY
    # Check again: another run of this job may have claimed the day while this one was
    # preparing (Codex 6B B1). There is no await between this check and the write in
    # claim(), so nothing else in the event loop can claim in between; instance_lock
    # keeps the bots to one process.
    if not is_due(read_state(state_file)):
        return SKIPPED
    if not claim(state_file, today):
        return CLAIM_FAILED  # never send without a claim, or a retry could send it twice
    try:
        await deliver(prepared)
        return DELIVERED
    except Exception as e:
        print(f"⚠️ [{label}] delivery failed partway; not resent automatically: {type(e).__name__}: {e}", flush=True)
        if notify:
            try:
                await notify(f"⚠️ Today's {label} stopped partway and will not be resent automatically. "
                             f"Run the command by hand for the full version.")
            except Exception as ne:  # quiet: the notice is best effort; the console line above stands
                print(f"⚠️ [{label}] notice not sent either: {type(ne).__name__}", flush=True)
        return DELIVER_FAILED
