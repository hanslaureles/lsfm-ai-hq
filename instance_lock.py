"""
One LSFM bot process at a time (Phase 6B), without killing anything.

run_all.py and every bot_*.py started on its own call require_single_instance()
first: it takes an OS lock on memory/lsfm-bots.lock for the life of the process
(released by the OS when the process ends or is killed) and writes the PID to
memory/lsfm-bots.pid. A second start is refused instead of a first one killed.

    python instance_lock.py stop                  # stop; the LSFM Bots task may start them again
    python instance_lock.py stop --stay-stopped   # what stop_squad.bat runs

The LSFM Bots task re-runs run_all.py every 5 minutes (ADR-001 R-1). A plain `stop` is
for restarts (stop, then Start-ScheduledTask 'LSFM Bots'). `--stay-stopped` also writes
memory/bots.stopped: scheduled starts leave the bots stopped while it exists, and the
next start by hand (start_squad.bat) removes it.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

from obsidian_client import try_lock

MEMORY = Path(__file__).resolve().parent / "memory"
LOCK_FILE = MEMORY / "lsfm-bots.lock"
PID_FILE = MEMORY / "lsfm-bots.pid"
STOP_MARKER = MEMORY / "bots.stopped"  # stopped on purpose: scheduled starts stay away
BOT_MARKERS = ("run_all.py", "bot_sakura.py", "bot_chaewon.py", "bot_yunjin.py", "bot_kazuha.py", "bot_eunchae.py")

_held = None  # the open lock handle; kept for the life of the process


def acquire(lock_file: Path = LOCK_FILE, pid_file: Path = PID_FILE) -> bool:
    """Take the bots' lock; False if another process holds it."""
    global _held
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_file, "a+b")
    if not try_lock(fh):
        fh.close()
        return False
    _held = fh
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    return True


def holder_pid(pid_file: Path = PID_FILE) -> str:
    try:
        return pid_file.read_text(encoding="utf-8").strip()
    except OSError:  # quiet: no PID file, holder unknown
        return "?"


def require_single_instance(lock_file: Path = LOCK_FILE, pid_file: Path = PID_FILE) -> None:
    if not acquire(lock_file, pid_file):
        print(f"⚠️ LSFM bots are already running (PID {holder_pid(pid_file)}). "
              f"Stop them with stop_squad.bat first.", flush=True)
        sys.exit(1)


def stop(lock_file: Path = LOCK_FILE, pid_file: Path = PID_FILE) -> str:
    """
    Stop the process that holds the lock, and only that process and its children.
    The PID file is only a hint: the process must have the lock file open (proof it
    holds the lock, Codex 6B B3) and a bot's command line, so a stale or wrong PID
    file can never kill another process.
    """
    probe = open(lock_file, "a+b") if lock_file.parent.is_dir() else None
    if probe is None or try_lock(probe):
        if probe:
            probe.close()
        return "not running"
    probe.close()
    import psutil
    try:
        proc = psutil.Process(int(holder_pid(pid_file)))
        cmdline = " ".join(proc.cmdline()).lower()
        lock_path = os.path.normcase(os.path.realpath(lock_file))
        holds = any(os.path.normcase(os.path.realpath(f.path)) == lock_path for f in proc.open_files())
    except (ValueError, psutil.Error) as e:
        return f"lock is held but PID {holder_pid(pid_file)} is not readable ({type(e).__name__}); nothing stopped"
    if not holds:
        return f"PID {proc.pid} does not hold {lock_file.name}; nothing stopped"
    if not any(m in cmdline for m in BOT_MARKERS):
        return f"PID {proc.pid} is not an LSFM bot process ({cmdline[:80]}); nothing stopped"
    family = proc.children(recursive=True) + [proc]
    for p in family:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(family, timeout=10)
    return f"stopped PID {proc.pid} ({len(family)} process(es))"


def stop_on_purpose(lock_file: Path = LOCK_FILE, pid_file: Path = PID_FILE, marker: Path = STOP_MARKER) -> str:
    """
    stop() plus the marker that keeps the 5-minute task check from starting the bots again.
    The marker goes first: a scheduled start that takes the lock the moment the old process
    dies already sees it (run_all.prepare_start re-checks it under the lock).
    """
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    result = stop(lock_file, pid_file)
    return f"{result}; scheduled restarts paused until the bots are started by hand (start_squad.bat)"


def cli(argv: list, lock_file: Path = LOCK_FILE, pid_file: Path = PID_FILE, marker: Path = STOP_MARKER) -> str:
    if argv == ["stop"]:
        return stop(lock_file, pid_file)
    if argv == ["stop", "--stay-stopped"]:
        return stop_on_purpose(lock_file, pid_file, marker)
    sys.exit("usage: python instance_lock.py stop [--stay-stopped]")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    print(cli(sys.argv[1:]))
