"""
One LSFM bot process at a time (Phase 6B), without killing anything.

run_all.py and every bot_*.py started on its own call require_single_instance()
first: it takes an OS lock on memory/lsfm-bots.lock for the life of the process
(released by the OS when the process ends or is killed) and writes the PID to
memory/lsfm-bots.pid. A second start is refused instead of a first one killed.

    python instance_lock.py stop    # what stop_squad.bat runs
"""

import os
import sys
from pathlib import Path

from obsidian_client import try_lock

MEMORY = Path(__file__).resolve().parent / "memory"
LOCK_FILE = MEMORY / "lsfm-bots.lock"
PID_FILE = MEMORY / "lsfm-bots.pid"
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
    The PID is checked against the bots' command line first, so a stale PID file
    can never kill an unrelated process that reused the number.
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
    except (ValueError, psutil.Error) as e:
        return f"lock is held but PID {holder_pid(pid_file)} is not readable ({type(e).__name__}); nothing stopped"
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


if __name__ == "__main__":
    if sys.argv[1:] != ["stop"]:
        sys.exit("usage: python instance_lock.py stop")
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    print(stop())
