"""
Obsidian Second Brain Client for LSFM Multi-Agent Swarm
Enables Sakura, Chaewon, Yunjin, Kazuha, and Eunchae to read, query,
and log operational data directly into the user's Obsidian vault.
Reads try the Local REST API first and fall back to the vault on disk. Writes go
to disk only, under a cross-process lock (6A-1): a REST write that timed out
after it landed was retried on disk, so the entry was written twice.
"""

import contextlib
import os
import sys
import ssl
import time
import socket
import json
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

# Load environment variables if dotenv is available
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

OBSIDIAN_API_KEY = os.getenv("OBSIDIAN_API_KEY", "")
OBSIDIAN_HOST = os.getenv("OBSIDIAN_HOST", "127.0.0.1")
OBSIDIAN_PORT = os.getenv("OBSIDIAN_PORT", "27124")
OBSIDIAN_BASE_URL = f"https://{OBSIDIAN_HOST}:{OBSIDIAN_PORT}"
# Default: an "Obsidian" folder in the workspace that holds Projects/<this repo>.
DEFAULT_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", str(Path(__file__).resolve().parents[2] / "Obsidian"))

# Self-signed certificate context for localhost REST API
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# Every vault location the code uses, and the only place that names a vault
# folder (test_reliability.VaultPathMapTest checks that). Layout from 6A-2.
VAULT_PATHS = {
    "daily_logs": "05 Daily Logs",
    "research": "02 Research/AI Research",
    "profile": "03 Knowledge/Profile.md",
    "preferences": "03 Knowledge/Preferences.md",
    "learned_rules": "03 Knowledge/Learned_Rules.md",
    "agents": "06 Agent Documentation/Agents",
}

if os.name == "nt":
    import msvcrt
else:
    import fcntl

LOCK_TIMEOUT_S = 5.0


@contextlib.contextmanager
def _vault_lock(vault_path):
    """
    Exclusive lock shared by every process that writes the vault (the bots,
    ciel_server, the daily job, vault_log). Each holder opens its own handle,
    so threads in one process are serialised too. The OS drops the lock if a
    holder dies. Raises TimeoutError after LOCK_TIMEOUT_S.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    with open(Path(vault_path) / ".vault-write.lock", "a+b") as fh:
        while True:
            try:
                if os.name == "nt":
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"vault write lock busy for {LOCK_TIMEOUT_S} s")
                time.sleep(0.02)
        try:
            yield
        finally:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


class ObsidianClient:
    """Zero-dependency HTTPS client for Obsidian Local REST API with filesystem fallback."""

    def __init__(self, api_key: str = None, base_url: str = None, vault_path: str = None):
        self.api_key = api_key or OBSIDIAN_API_KEY
        self.base_url = (base_url or OBSIDIAN_BASE_URL).rstrip("/")
        self.vault_path = Path(vault_path or DEFAULT_VAULT_PATH)
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/vnd.olra+json",
        }
        self._online_cache = None
        self._online_cache_time = 0

    def is_rest_api_online(self) -> bool:
        """Fast non-blocking check to verify if Obsidian REST API is listening."""
        now = time.time()
        if self._online_cache is not None and (now - self._online_cache_time) < 5.0:
            return self._online_cache
        try:
            import socket
            u = urllib.parse.urlparse(self.base_url)
            host = u.hostname or "127.0.0.1"
            port = u.port or 27124
            with socket.create_connection((host, port), timeout=0.20):
                self._online_cache = True
        except Exception:  # quiet: REST offline is a normal state, cached and returned
            self._online_cache = False
        self._online_cache_time = now
        return self._online_cache

    def _request(self, method: str, endpoint: str, data: bytes = None, extra_headers: dict = None):
        if not self.is_rest_api_online():
            return {"status": 0, "error": "Obsidian Local REST API offline"}
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        req_headers = dict(self.headers)
        if extra_headers:
            req_headers.update(extra_headers)

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, context=SSL_CONTEXT, timeout=3) as resp:
                status = resp.status
                body = resp.read().decode("utf-8")
                return {"status": status, "body": body}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else ""
            return {"status": e.code, "error": err_body}
        except urllib.error.URLError as e:
            self._online_cache = False
            return {"status": 0, "error": f"Connection failed: {e.reason}"}
        except Exception as e:
            return {"status": 0, "error": str(e)}

    def _vault_rel(self, filepath: str) -> str:
        """
        Normalized vault-relative path (posix), or ValueError if it escapes the vault.
        Ciel's read/append-note tools pass router (LLM) output here, so "../" and
        drive paths must not reach the disk or the REST API. A leading slash means
        "from the vault root", as before.
        """
        clean = filepath.strip("/\\")
        root = self.vault_path.resolve()
        target = (root / clean).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"path escapes the Obsidian vault: {filepath!r}")
        rel = target.relative_to(root).as_posix()
        return "" if rel == "." else rel

    def ping(self) -> bool:
        """Check if Obsidian Local REST API is responding."""
        if not self.is_rest_api_online():
            return False
        res = self._request("GET", "/")
        return res.get("status") == 200

    def get_file(self, filepath: str) -> str:
        """Fetch raw markdown contents of any file in the vault (via REST or disk fallback)."""
        clean_path = self._vault_rel(filepath)
        # Try REST API first if online
        if self.is_rest_api_online():
            try:
                encoded_path = urllib.parse.quote(clean_path, safe="/")
                res = self._request("GET", f"/vault/{encoded_path}", extra_headers={"Accept": "text/markdown"})
                if res.get("status") == 200:
                    return res.get("body", "")
            except Exception as _exc:
                print(f"⚠️ [obsidian_client.get_file] suppressed {type(_exc).__name__}: {_exc}", flush=True)

        # Filesystem fallback
        if self.vault_path and self.vault_path.exists():
            disk_file = self.vault_path / clean_path
            if disk_file.exists():
                return disk_file.read_text(encoding="utf-8")

        raise RuntimeError(f"Failed to get {filepath}: Not reachable via REST API and file not found on disk ({self.vault_path / clean_path})")

    def _disk_write(self, filepath: str, content: str, mode: str) -> bool:
        """
        Write to the vault on disk under the vault lock. mode "w" overwrites,
        "a" appends, "x" creates only (False if the note exists). The lock is
        needed on top of append mode: the Windows CRT seeks to EOF and writes
        as two steps, so two writers can land on the same offset.
        """
        clean_path = self._vault_rel(filepath)
        if not self.vault_path.is_dir():
            print(f"⚠️ [ObsidianClient] vault not found: {self.vault_path}", flush=True)
            return False
        try:
            disk_file = self.vault_path / clean_path
            disk_file.parent.mkdir(parents=True, exist_ok=True)
            with _vault_lock(self.vault_path), open(disk_file, mode, encoding="utf-8") as f:
                f.write(content)
            return True
        except FileExistsError:
            return False
        except Exception as e:
            print(f"⚠️ [ObsidianClient] Disk write error ({mode}) {clean_path}: {type(e).__name__}: {e}", flush=True)
            return False

    def put_file(self, filepath: str, content: str) -> bool:
        """Create or completely overwrite a note. Use create_file where an overwrite would lose data."""
        return self._disk_write(filepath, content, "w")

    def create_file(self, filepath: str, content: str) -> bool:
        """Create a note only if it does not exist yet. Never overwrites; False if it exists."""
        return self._disk_write(filepath, content, "x")

    def append_file(self, filepath: str, content: str) -> bool:
        """Append content to a note (created if missing)."""
        return self._disk_write(filepath, content, "a")

    def check_vault_consistency(self) -> str:
        """
        Startup check that the vault Obsidian has open (REST) is the one at
        OBSIDIAN_VAULT_PATH, which the disk fallback reads and writes. Compares the
        two root listings without writing anything. Returns a one-line verdict.
        """
        if not self.vault_path.is_dir():
            return f"MISMATCH: OBSIDIAN_VAULT_PATH {self.vault_path} does not exist; disk fallback writes will fail"
        if not self.is_rest_api_online():
            return "skipped: REST API offline, only the disk vault is in use"
        try:
            res = self._request("GET", "/vault/")
            rest_names = {f.rstrip("/") for f in json.loads(res.get("body", "{}")).get("files", [])}
        except Exception as e:
            return f"skipped: REST listing failed ({type(e).__name__}: {e})"
        missing = sorted(rest_names - {p.name for p in self.vault_path.iterdir()})
        if missing:
            return (f"MISMATCH: {len(missing)} of {len(rest_names)} entries in Obsidian's open vault are not in "
                    f"{self.vault_path} (e.g. {', '.join(missing[:3])}); REST and disk-fallback writes go to "
                    f"different vaults. Set OBSIDIAN_VAULT_PATH to the vault Obsidian has open.")
        return f"ok: REST vault matches {self.vault_path} ({len(rest_names)} root entries)"

    def list_dir(self, dirpath: str = "") -> list:
        """List files in a vault directory (via REST or disk fallback)."""
        clean_dir = self._vault_rel(dirpath)
        if self.is_rest_api_online():
            try:
                encoded_dir = urllib.parse.quote(clean_dir, safe="/")
                res = self._request("GET", f"/vault/{encoded_dir}/" if clean_dir else "/vault/")
                if res.get("status") == 200:
                    try:
                        data = json.loads(res.get("body", "{}"))
                        return data.get("files", [])
                    except Exception as _exc:
                        print(f"⚠️ [obsidian_client.list_dir] suppressed {type(_exc).__name__}: {_exc}", flush=True)
            except Exception as _exc:
                print(f"⚠️ [obsidian_client.list_dir] suppressed {type(_exc).__name__}: {_exc}", flush=True)

        # Filesystem fallback
        if self.vault_path and self.vault_path.exists():
            target = self.vault_path / clean_dir if clean_dir else self.vault_path
            if target.exists() and target.is_dir():
                return [p.name for p in target.iterdir()]

        return []

    def search_simple(self, query: str) -> list:
        """Run a full-text search across all notes in the vault (via REST or filesystem fallback)."""
        if self.is_rest_api_online():
            encoded_query = urllib.parse.quote(query)
            try:
                res = self._request("GET", f"/search/simple/?query={encoded_query}")
                if res.get("status") == 200:
                    try:
                        return json.loads(res.get("body", "[]"))
                    except Exception as _exc:
                        print(f"⚠️ [obsidian_client.search_simple] suppressed {type(_exc).__name__}: {_exc}", flush=True)
            except Exception as _exc:
                print(f"⚠️ [obsidian_client.search_simple] suppressed {type(_exc).__name__}: {_exc}", flush=True)

        # Filesystem fallback search
        if self.vault_path and self.vault_path.exists():
            matches = []
            q_lower = query.lower().strip()
            if not q_lower:
                return []
            for md_file in self.vault_path.rglob("*.md"):
                if ".obsidian" in md_file.parts:
                    continue
                rel = md_file.relative_to(self.vault_path).as_posix()
                if q_lower in md_file.name.lower():
                    matches.append({"filename": rel, "score": 100})
                    continue
                try:
                    text = md_file.read_text(encoding="utf-8", errors="ignore")
                    if q_lower in text.lower():
                        matches.append({"filename": rel, "score": 50})
                except Exception as _exc:
                    print(f"⚠️ [obsidian_client.search_simple] suppressed {type(_exc).__name__}: {_exc}", flush=True)
            # Sort by score descending
            matches.sort(key=lambda x: x.get("score", 0), reverse=True)
            return matches[:10]

        return []

    # High-level agent helper methods

    def get_agent_profile(self, agent_name: str) -> str:
        """Retrieve the dossier for an agent (e.g., 'Sakura', 'Yunjin')."""
        return self.get_file(f"{VAULT_PATHS['agents']}/{agent_name.capitalize()}.md")

    def get_learned_rules(self) -> str:
        """Retrieve the current learned rules and heuristics."""
        return self.get_file(VAULT_PATHS["learned_rules"])

    def daily_log_path(self, date_str: str = None) -> str:
        """Vault path of the daily log for date_str (YYYY-MM-DD, default today)."""
        return f"{VAULT_PATHS['daily_logs']}/{date_str or datetime.now().strftime('%Y-%m-%d')}.md"

    def ensure_daily_log(self, date_str: str = None) -> str:
        """
        Ensures the daily log exists, creating it with the standard template if missing.
        Returns its vault path (see daily_log_path).
        Create-only: an existing log is never touched, whatever a listing says.
        """
        now = datetime.now()
        today = date_str or now.strftime("%Y-%m-%d")
        filepath = self.daily_log_path(today)

        # Structure only: no status, scores or counts. Anything stated here would be
        # written before any agent has checked it (Phase 3A-2). Real results are
        # appended below by log_session().
        template = f"""# 📅 Daily Log — {today}

> **Date:** {now.strftime('%A, %B %d, %Y')}

---

## 🎯 Priorities
- [ ]

---

## 📝 Observations & Notes

---

## 🤖 Agent Activity
"""
        self.create_file(filepath, template)
        return filepath

    def log_session(self, agent_name: str, summary: str, details: list = None) -> bool:
        """
        Log an operational summary into the daily log for today's date.
        Example:
            client.log_session("Yunjin", "Completed portfolio visual audit", ["Score: 92/100", "0 broken assets"])
        """
        daily_log_path = self.ensure_daily_log()

        # Format log entry
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"\n\n### [{timestamp}] {agent_name} Dispatch\n- **Summary:** {summary}\n"
        if details:
            for item in details:
                entry += f"  - {item}\n"

        return self.append_file(daily_log_path, entry)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    client = ObsidianClient()
    print("Connecting to Obsidian Local REST API on port 27124...")
    if client.ping():
        print("✅ Ping successful! Obsidian REST API is active.")
    else:
        print(f"ℹ️ Obsidian REST API offline. Using filesystem fallback at: {client.vault_path}")

    print("\n--- Testing ensure_daily_log() ---")
    log_path = client.ensure_daily_log()
    print(f"✅ Daily log ensured: {log_path}")
    print("\n--- Testing get_agent_profile('Kazuha') ---")
    try:
        kazuha = client.get_agent_profile("Kazuha")
        print(f"Retrieved {len(kazuha)} bytes. First 120 chars:")
        print(kazuha[:120] + "...")
    except Exception as e:
        print(f"Error fetching Kazuha: {e}")
