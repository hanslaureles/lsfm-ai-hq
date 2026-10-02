"""
Eunchae status aggregator (Phase 4, 4B-4): builds the public status.json the
portfolio's Sentinel panel reads (4C). Every value comes from this machine:

- swarm:       memory/health/<agent>.json heartbeats (health_recorder). A file older
               than STALE_AFTER_S is "stale", a missing one "offline"; neither is
               ever reported "online".
- llm_latency: the newest non-mock bench/results/*-telemetry.json (bench_telemetry),
               with its sample count, date and source file. None if there is none.
- missions:    job-application packages logged in memory/applications_log.md in the
               last 7 days (one row per package scout.py builds). Counts only.
- vitals:      CPU / RAM / disk percentages from psutil at generation time.

Only allowlisted fields are copied, and validate_status() rejects any other key,
any unknown status and anything that looks like a path, account name or secret.

Usage:
    python -m eunchae_publisher --dry-run              # print the payload
    python -m eunchae_publisher [--target <path>]      # write it (default: portfolio-site/data/status.json)
Writing the file does not commit it; the portfolio repo's workflow does that (4C).
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import health_recorder
from llm_client import AGENTS

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "bench" / "results"
APPLICATIONS_LOG = BASE_DIR / "memory" / "applications_log.md"
DEFAULT_TARGET = BASE_DIR.parent / "portfolio-site" / "data" / "status.json"

SCHEMA_VERSION = 1
STALE_AFTER_S = 900          # 15 heartbeat intervals
MISSION_WINDOW_DAYS = 7
MAX_LATENCY_MS = 600_000     # 10 min: anything larger is not a real round trip
LATENCY_SOURCE = re.compile(r"^bench/results/\d{4}-\d{2}-\d{2}-telemetry\.json$")
MISSIONS_SOURCE = "memory/applications_log.md"
MAX_BYTES = 4096
STATUSES = {"online", "not_ready", "stale", "offline", "unknown"}
TOP_KEYS = {"schema_version", "generated_at", "swarm", "llm_latency", "missions", "vitals"}
LATENCY_KEYS = {"p50_ms", "p95_ms", "sample_count", "measured_on", "source"}
MISSION_KEYS = {"application_packages", "window_days", "source"}
VITALS_KEYS = {"cpu_pct", "ram_pct", "disk_pct", "sampled_at"}
# Paths, account names and secret-ish words must never reach a public file.
LEAK = re.compile(r"(?i)[a-z]:\\|users|hansl|token|key|secret|password|@|\\\\")
_ROW_DATE = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def read_swarm(health_dir: Path, now: datetime) -> dict:
    swarm = {}
    for agent in AGENTS:
        path = Path(health_dir) / f"{agent}.json"
        try:
            beat = json.loads(path.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(beat["timestamp_utc"])
            age = (now - ts).total_seconds()  # TypeError if the file's time has no timezone
        except FileNotFoundError:
            swarm[agent] = {"status": "offline", "last_heartbeat_utc": None}
            continue
        except (OSError, ValueError, KeyError, TypeError) as _exc:
            print(f"⚠️ [publisher] unreadable heartbeat {path.name}: {type(_exc).__name__}", flush=True)
            swarm[agent] = {"status": "unknown", "last_heartbeat_utc": None}
            continue
        if age < 0:
            # health_recorder writes on this machine and truncates to the second, so a real
            # heartbeat is never ahead of now: a future one is clock skew or a forged file.
            status = "unknown"
        elif age > STALE_AFTER_S:
            status = "stale"
        else:
            status = "online" if beat.get("status") == "online" else "not_ready"
        swarm[agent] = {"status": status, "last_heartbeat_utc": _iso(ts)}
    return swarm


def read_latency(results_dir: Path) -> dict | None:
    for path in sorted(Path(results_dir).glob("*-telemetry.json"), reverse=True):  # names start with the date
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("mock"):
                continue  # fake providers: not a measurement
            overall = report["overall"]
            if not overall.get("total_ms"):
                continue
            return {"p50_ms": overall["total_ms"]["p50"], "p95_ms": overall["total_ms"]["p95"],
                    "sample_count": overall["n_ok"], "measured_on": report["date"],
                    "source": f"bench/results/{path.name}"}
        except (OSError, ValueError, KeyError, TypeError) as _exc:
            print(f"⚠️ [publisher] skipped {path.name}: {type(_exc).__name__}", flush=True)
    return None


def read_missions(applications_log: Path, now: datetime) -> dict:
    count = None
    try:
        text = Path(applications_log).read_text(encoding="utf-8")
        cutoff = (now - timedelta(days=MISSION_WINDOW_DAYS)).date()
        dates = [datetime.strptime(m.group(1), "%Y-%m-%d").date()
                 for m in map(_ROW_DATE.match, text.splitlines()) if m]
        count = sum(cutoff < d <= now.date() for d in dates)
    except FileNotFoundError:
        pass  # no log yet: the count stays None, not 0
    return {"application_packages": count, "window_days": MISSION_WINDOW_DAYS, "source": MISSIONS_SOURCE}


def read_vitals() -> dict:
    from eunchae_engine import get_system_vitals
    # 0.5 s sample: with interval=None, the first call in a fresh process reports 0.0 CPU.
    v = get_system_vitals(cpu_interval=0.5)
    return {"cpu_pct": v["cpu_pct"], "ram_pct": v["ram_pct"], "disk_pct": v["disk_pct"]}


def generate_status_payload(now: datetime | None = None, health_dir: Path = health_recorder.HEALTH_DIR,
                            results_dir: Path = RESULTS_DIR, applications_log: Path = APPLICATIONS_LOG,
                            vitals=read_vitals) -> dict:
    now = now or datetime.now(timezone.utc)
    v = vitals()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "swarm": read_swarm(health_dir, now),
        "llm_latency": read_latency(results_dir),
        "missions": read_missions(applications_log, now),
        "vitals": {k: round(float(v[k]), 1) for k in ("cpu_pct", "ram_pct", "disk_pct")} | {"sampled_at": _iso(now)},
    }
    validate_status(payload)
    return payload


def _is_num(x, lo, hi) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and lo <= x <= hi


def _is_count(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _is_utc(x) -> bool:
    """An ISO timestamp with an explicit UTC offset (what _iso writes)."""
    try:
        return isinstance(x, str) and datetime.fromisoformat(x).utcoffset() == timedelta(0)
    except ValueError:
        return False


def _is_date(x) -> bool:
    try:
        return isinstance(x, str) and len(x) == 10 and bool(datetime.strptime(x, "%Y-%m-%d"))
    except ValueError:
        return False


def validate_status(p: dict) -> None:
    """
    Raises ValueError unless p is exactly the public schema: the keys at every level, the
    type and range of every value, fixed source paths, and nothing that looks private.
    """
    def need(cond, why):
        if not cond:
            raise ValueError(f"status payload rejected: {why}")

    need(isinstance(p, dict) and set(p) == TOP_KEYS, f"top-level keys {sorted(p) if isinstance(p, dict) else p}")
    need(p["schema_version"] == SCHEMA_VERSION, "schema_version")
    need(_is_utc(p["generated_at"]), "generated_at")

    need(isinstance(p["swarm"], dict) and set(p["swarm"]) == set(AGENTS), "swarm agents")
    for agent, s in p["swarm"].items():
        need(isinstance(s, dict) and set(s) == {"status", "last_heartbeat_utc"}, f"swarm.{agent} keys")
        need(s["status"] in STATUSES, f"swarm.{agent}.status")
        need(s["last_heartbeat_utc"] is None or _is_utc(s["last_heartbeat_utc"]), f"swarm.{agent}.last_heartbeat_utc")

    lat = p["llm_latency"]
    if lat is not None:
        need(isinstance(lat, dict) and set(lat) == LATENCY_KEYS, "llm_latency keys")
        need(_is_num(lat["p50_ms"], 0, MAX_LATENCY_MS) and _is_num(lat["p95_ms"], 0, MAX_LATENCY_MS),
             "llm_latency p50/p95 must be numbers in range")
        need(lat["p50_ms"] <= lat["p95_ms"], "llm_latency p50 > p95")
        need(_is_count(lat["sample_count"]) and lat["sample_count"] > 0, "llm_latency.sample_count")
        need(_is_date(lat["measured_on"]), "llm_latency.measured_on")
        need(isinstance(lat["source"], str) and LATENCY_SOURCE.match(lat["source"]) is not None,
             "llm_latency.source must be bench/results/YYYY-MM-DD-telemetry.json")

    m = p["missions"]
    need(isinstance(m, dict) and set(m) == MISSION_KEYS, "missions keys")
    need(m["application_packages"] is None or _is_count(m["application_packages"]), "missions.application_packages")
    need(m["window_days"] == MISSION_WINDOW_DAYS and m["source"] == MISSIONS_SOURCE, "missions window/source")

    v = p["vitals"]
    need(isinstance(v, dict) and set(v) == VITALS_KEYS, "vitals keys")
    need(all(_is_num(v[k], 0, 100) for k in ("cpu_pct", "ram_pct", "disk_pct")), "vitals must be 0-100 %")
    need(_is_utc(v["sampled_at"]), "vitals.sampled_at")

    text = json.dumps(p)
    need(len(text) < MAX_BYTES, f"{len(text)} bytes")
    leak = LEAK.search(text)
    need(leak is None, f"looks private: {leak.group(0)!r}" if leak else "")


def publish_status(target_path: Path, payload: dict | None = None) -> Path:
    """Validates, then writes target_path atomically (temp file in the same dir + os.replace)."""
    payload = payload or generate_status_payload()
    validate_status(payload)
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".status.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the public status.json from local telemetry.")
    parser.add_argument("--dry-run", action="store_true", help="print the payload, write nothing")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args(argv)
    payload = generate_status_payload()
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return payload
    print(f"Wrote {publish_status(args.target, payload)}")
    return payload


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
