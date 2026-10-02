"""
heavy_jobs.py - A separate thread pool for slow, bulky work (3E-4).

PDF builds (headless Edge, up to 25 s), Gmail triage and sweeps, and headless
scraping used to share asyncio's default executor with quick calls (vitals,
chat replies, Obsidian reads). A burst of heavy jobs could take every default
thread and make the quick ones wait. They now queue on their own small pool;
the default executor stays free for everything else.

LLM text calls (query_llm) stay on the default executor: they are network
waits, and on this 2-worker pool a chat reply would queue behind a PDF build.
"""

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor

HEAVY_WORKERS = 2  # one PDF build and one Gmail job can run side by side; more wait their turn
HEAVY_POOL = ThreadPoolExecutor(max_workers=HEAVY_WORKERS, thread_name_prefix="heavy")


async def run_heavy(fn, *args, **kwargs):
    """Runs a blocking heavy job on HEAVY_POOL and returns its result."""
    return await asyncio.get_running_loop().run_in_executor(HEAVY_POOL, functools.partial(fn, *args, **kwargs))
