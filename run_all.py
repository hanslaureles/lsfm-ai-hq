import asyncio
import os
import random
import sys
import time
from time import monotonic
import psutil
import discord
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def enforce_single_instance():
    """
    Terminates any previously running instances of run_all.py or individual bot scripts.
    This guarantees that Discord Gateway will only have ONE active session per bot,
    permanently preventing duplicate / double-sent messages.
    """
    current_pid = os.getpid()
    parent_pid = os.getppid()
    terminated_count = 0
    target_scripts = ["run_all.py", "bot_chaewon.py", "bot_yunjin.py", "bot_sakura.py", "bot_kazuha.py", "bot_eunchae.py"]
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            pid = proc.info['pid']
            pname = (proc.info.get('name') or '').lower()
            if pid in (current_pid, parent_pid):
                continue
            # ONLY target Python processes, never kill calling PowerShell or cmd shells
            if not pname.startswith("python"):
                continue
            cmdline = proc.info.get('cmdline') or []
            cmdline_str = " ".join(cmdline).lower()
            if any(ts in cmdline_str for ts in target_scripts):
                print(f"🧹 [Process Guard] Terminating stale bot process (PID {pid}: {pname}) to prevent duplicate messages...", flush=True)
                proc.kill()
                terminated_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            pass
    if terminated_count > 0:
        time.sleep(1.5)

BACKOFF_START = 3.0
BACKOFF_MAX = 15.0
# A session that stayed up this long was healthy, so the next failure starts the
# backoff from scratch instead of from wherever an old outage left it.
HEALTHY_SESSION_S = 60.0

async def run_bot_resilient(bot_instance, token: str, name: str):
    """
    Runs a bot and restarts it after transient gateway errors. discord.py already
    reconnects inside start(); this loop only sees errors that escaped it.
    Credential and intent errors are fatal: retrying a bad token forever only
    hammers Discord's login endpoint.
    """
    backoff = BACKOFF_START
    while True:
        started = monotonic()
        try:
            print(f"🌐 [{name}] Connecting to Discord Gateway...", flush=True)
            await bot_instance.start(token)
            break
        except discord.errors.PrivilegedIntentsRequired:
            print(f"\n⚠️ [{name}] FAILED: 'Privileged Gateway Intents' not enabled in Discord Developer Portal!", flush=True)
            print(f"   👉 Go to: https://discord.com/developers/applications/ -> {name} -> Bot tab", flush=True)
            print(f"   👉 Turn ON: 'Message Content Intent' & 'Server Members Intent', then click Save Changes.\n", flush=True)
            break
        except discord.errors.LoginFailure as e:
            print(f"\n❌ [{name}] FAILED: Discord rejected the bot token ({e}). Not retrying; fix the token in .env.\n", flush=True)
            break
        except Exception as e:
            if monotonic() - started >= HEALTHY_SESSION_S:
                backoff = BACKOFF_START
            delay = backoff * random.uniform(0.8, 1.2)
            print(f"⚠️ [{name}] Gateway retry in {delay:.1f}s (Error: {e})", flush=True)
            await asyncio.sleep(delay)
            backoff = min(backoff * 1.5, BACKOFF_MAX)

async def main():
    # Side effects live here, not at import time, so tests can import this module.
    enforce_single_instance()
    load_dotenv()

    from bot_chaewon import bot as chaewon_bot
    from bot_yunjin import bot as yunjin_bot
    from bot_sakura import bot as sakura_bot
    from bot_kazuha import bot as kazuha_bot
    from bot_eunchae import bot as eunchae_bot

    print("=" * 60, flush=True)
    print("🚀 LAUNCHING FULL LE SSERAFIM AI HQ SQUAD...", flush=True)
    print("=" * 60, flush=True)

    bot_configs = [
        ("Chaewon (Career)", chaewon_bot, os.getenv("CHAEWON_BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")),
        ("Yunjin (Portfolio)", yunjin_bot, os.getenv("YUNJIN_BOT_TOKEN")),
        ("Sakura (Chief of Staff)", sakura_bot, os.getenv("SAKURA_BOT_TOKEN")),
        ("Kazuha (Frontend)", kazuha_bot, os.getenv("KAZUHA_BOT_TOKEN")),
        ("Eunchae (Guardian)", eunchae_bot, os.getenv("EUNCHAE_BOT_TOKEN")),
    ]

    tasks = []
    for name, bot_obj, token in bot_configs:
        if token and token.strip():
            task = asyncio.create_task(run_bot_resilient(bot_obj, token.strip(), name))
            tasks.append(task)
            await asyncio.sleep(1.5)
        else:
            print(f"⚠️ [{name}] Token missing in .env", flush=True)

    if not tasks:
        print("❌ No tokens configured in .env!", flush=True)
        return

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopping all bots.", flush=True)
    except Exception as e:
        print(f"\n❌ Error in multi-bot runner: {e}", flush=True)
