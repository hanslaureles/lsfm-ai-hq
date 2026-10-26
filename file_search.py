"""
file_search.py - Local keyword search (BM25) over Hans's notes: archive/reports and the Obsidian vault.

Nothing leaves the machine: files are read from disk and ranked here, with no embeddings API (assistant B5,
Hans's choice 2026-10-26). The index lives in memory and is refreshed on each search from every file's
modification time, so a new or edited note is found on the next search. A file whose bytes repeat an
earlier root's (the vault's mirror of archive/reports) is skipped. Pure stdlib.
"""

import hashlib
import math
import os
import re
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

HQ_DIR = Path(__file__).resolve().parent
EXTENSIONS = (".md", ".txt")
SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules"}
CHUNK_CHARS = 1200  # a heading's section, cut at paragraph breaks past this
SNIPPET_CHARS = 320
K1, B = 1.5, 0.75   # the usual BM25 constants
STOPWORDS = frozenset(
    "a an and are as at be by did do does for from had has have how i in is it its me my of on or our that "
    "the this to was we were what when where which who why will with you your about".split())
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def default_roots() -> list:
    """CIEL_FILE_ROOTS from .env (os.pathsep-separated), else archive/reports then the Obsidian vault.
    Reports come first so a mirrored copy in the vault is the one skipped."""
    env = os.getenv("CIEL_FILE_ROOTS", "").strip()
    if env:
        return [Path(p.strip()) for p in env.split(os.pathsep) if p.strip()]
    from obsidian_client import DEFAULT_VAULT_PATH
    return [HQ_DIR.parents[1] / "archive" / "reports", Path(DEFAULT_VAULT_PATH)]


def tokens(text: str) -> list:
    return re.findall(r"\w+", text.lower())


def sections(text: str) -> list:
    """(heading, text) per markdown section; a long section is cut at paragraph breaks."""
    out, heading, lines = [], "", []

    def flush():
        body = "\n".join(lines).strip()
        piece = ""
        for para in re.split(r"\n\s*\n", body):
            if piece and len(piece) + len(para) > CHUNK_CHARS:
                out.append((heading, piece))
                piece = ""
            piece = f"{piece}\n\n{para}" if piece else para
        if piece.strip():
            out.append((heading, piece))

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            heading, lines = m.group(1).strip(), []
        else:
            lines.append(line)
    flush()
    return out


def _snippet(text: str, terms: set) -> str:
    flat = " ".join(text.split())
    hits = [m.start() for m in re.finditer(r"\w+", flat.lower()) if m.group(0) in terms]
    start = max(0, (hits[0] if hits else 0) - SNIPPET_CHARS // 4)
    cut = flat[start:start + SNIPPET_CHARS]
    return ("…" if start else "") + cut + ("…" if start + SNIPPET_CHARS < len(flat) else "")


class Index:
    def __init__(self, roots):
        self.roots = [Path(r) for r in roots]
        self.files = {}  # path -> {"mtime", "hash", "chunks"}
        self.active = []  # paths searched, in root order, duplicates left out
        self.lock = threading.Lock()

    def available(self) -> bool:
        return any(r.is_dir() for r in self.roots)

    def _walk(self, root: Path):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                if name.lower().endswith(EXTENSIONS):
                    yield Path(dirpath) / name

    def refresh(self):
        seen, active, hashes = set(), [], set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in self._walk(root):
                key = str(path)
                try:
                    mtime = path.stat().st_mtime_ns
                    entry = self.files.get(key)
                    if not entry or entry["mtime"] != mtime:
                        raw = path.read_bytes()
                        label = f"{root.name}/{path.relative_to(root).as_posix()}"
                        chunks = [{"file": label, "heading": h, "text": t, "tf": Counter(tokens(f"{h} {t}"))}
                                  for h, t in sections(raw.decode("utf-8", "replace"))]
                        for c in chunks:
                            c["len"] = sum(c["tf"].values())
                        entry = self.files[key] = {"mtime": mtime, "hash": hashlib.sha1(raw).hexdigest(),
                                                   "chunks": chunks, "modified": datetime.fromtimestamp(
                                                       mtime / 1e9).strftime("%Y-%m-%d")}
                except OSError:
                    continue  # unreadable or vanished mid-walk: left out of this search
                seen.add(key)
                if entry["hash"] not in hashes:
                    hashes.add(entry["hash"])
                    active.append(key)
        for key in set(self.files) - seen:
            del self.files[key]
        self.active = active

    def chunks(self):
        return [dict(c, modified=self.files[k]["modified"]) for k in self.active for c in self.files[k]["chunks"]]

    def search(self, query: str, k: int = 5) -> list:
        """The k best passages: {"file", "heading", "snippet", "modified", "score"}; [] when nothing matches."""
        with self.lock:
            self.refresh()
            terms = [t for t in tokens(query) if t not in STOPWORDS] or tokens(query)
            docs = [(c, self.files[key]["modified"]) for key in self.active for c in self.files[key]["chunks"]]
            if not docs or not terms:
                return []
            n = len(docs)
            avg = sum(c["len"] for c, _ in docs) / n or 1.0
            df = {t: sum(1 for c, _ in docs if t in c["tf"]) for t in set(terms)}
            scored = []
            for c, modified in docs:
                score = 0.0
                for t in terms:
                    f = c["tf"].get(t)
                    if f:
                        idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                        score += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * c["len"] / avg))
                if score > 0:
                    scored.append((score, c, modified))
            scored.sort(key=lambda s: -s[0])
            return [{"file": c["file"], "heading": c["heading"], "snippet": _snippet(c["text"], set(terms)),
                     "modified": modified, "score": round(score, 2)} for score, c, modified in scored[:k]]


_INDEXES = {}


def search(query: str, k: int = 5, roots=None) -> list:
    """Search the default roots (or `roots`); the index for each set of roots is kept between searches."""
    key = tuple(str(r) for r in (roots or default_roots()))
    index = _INDEXES.setdefault(key, Index(key))
    return index.search(query, k)


def available(roots=None) -> bool:
    return Index(roots or default_roots()).available()
