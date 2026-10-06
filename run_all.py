import asyncio
import os
import random
import sys
from time import monotonic
import discord
from dotenv import load_dotenv

from instance_lock import require_single_instance

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # quiet: no console to reconfigure (pythonw, redirected stream)
        pass

BACKOFF_START = 3.0
BACKOFF_MAX = 15.0
# A session that stayed up this long was healthy, so the next failure starts the
# backoff from scratch instead of from wherever an old outage left it.
HEALTHY_SESSION_S = 60.0

async def reset_client(bot_instance, name: str):
    """
    Makes a failed client safe to start() again (discord.py 2.x). Each start()
    logs in, and login builds a new aiohttp session without closing the old
    one, so: close() releases the session and gateway socket; close() also shut
    the connector that session owned, and static_login only builds a fresh one
    when http.connector is MISSING; clear() reopens the client. close() reaches
    http.close() only after state and gateway cleanup, so if it raises, the
    session is closed here directly.
    """
    try:
        await bot_instance.close()
    except Exception as e:
        print(f"⚠️ [{name}] Closing the failed session raised {type(e).__name__}: {e}", flush=True)
        await bot_instance.http.close()
    bot_instance.http.connector = discord.utils.MISSING
    bot_instance.clear()


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
            await reset_client(bot_instance, name)  # release the session this bot won't use again
            break
        except discord.errors.LoginFailure as e:
            print(f"\n❌ [{name}] FAILED: Discord rejected the bot token ({e}). Not retrying; fix the token in .env.\n", flush=True)
            await reset_client(bot_instance, name)
            break
        except Exception as e:
            if monotonic() - started >= HEALTHY_SESSION_S:
                backoff = BACKOFF_START
            delay = backoff * random.uniform(0.8, 1.2)
            print(f"⚠️ [{name}] Gateway retry in {delay:.1f}s (Error: {e})", flush=True)
            await reset_client(bot_instance, name)
            await asyncio.sleep(delay)
            backoff = min(backoff * 1.5, BACKOFF_MAX)

async def main():
    # Side effects live here, not at import time, so tests can import this module.
    # One bot process at a time (6B): a second start is refused; nothing is killed.
    require_single_instance()
    load_dotenv()
    from obsidian_client import ObsidianClient
    print(f"🗂️ [Vault] {ObsidianClient().check_vault_consistency()}", flush=True)

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
