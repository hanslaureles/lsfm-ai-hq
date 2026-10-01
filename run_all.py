import asyncio
import os
import sys
import time
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

enforce_single_instance()

load_dotenv()

from bot_chaewon import bot as chaewon_bot
from bot_yunjin import bot as yunjin_bot
from bot_sakura import bot as sakura_bot
from bot_kazuha import bot as kazuha_bot
from bot_eunchae import bot as eunchae_bot

CHAEWON_TOKEN = os.getenv("CHAEWON_BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
YUNJIN_TOKEN = os.getenv("YUNJIN_BOT_TOKEN")
SAKURA_TOKEN = os.getenv("SAKURA_BOT_TOKEN")
KAZUHA_TOKEN = os.getenv("KAZUHA_BOT_TOKEN")
EUNCHAE_TOKEN = os.getenv("EUNCHAE_BOT_TOKEN")

async def run_bot_resilient(bot_instance, token: str, name: str):
    """Runs a bot with automatic reconnect/retry so transient Discord gateway errors are handled gracefully."""
    backoff = 3
    while True:
        try:
            print(f"🌐 [{name}] Connecting to Discord Gateway...", flush=True)
            await bot_instance.start(token)
            break
        except discord.errors.PrivilegedIntentsRequired:
            print(f"\n⚠️ [{name}] FAILED: 'Privileged Gateway Intents' not enabled in Discord Developer Portal!", flush=True)
            print(f"   👉 Go to: https://discord.com/developers/applications/ -> {name} -> Bot tab", flush=True)
            print(f"   👉 Turn ON: 'Message Content Intent' & 'Server Members Intent', then click Save Changes.\n", flush=True)
            break
        except Exception as e:
            print(f"⚠️ [{name}] Gateway retry in {backoff}s (Error: {e})", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 15)

async def main():
    print("=" * 60, flush=True)
    print("🚀 LAUNCHING FULL LE SSERAFIM AI HQ SQUAD...", flush=True)
    print("=" * 60, flush=True)
    
    bot_configs = [
        ("Chaewon (Career)", chaewon_bot, CHAEWON_TOKEN),
        ("Yunjin (Portfolio)", yunjin_bot, YUNJIN_TOKEN),
        ("Sakura (Chief of Staff)", sakura_bot, SAKURA_TOKEN),
        ("Kazuha (Frontend)", kazuha_bot, KAZUHA_TOKEN),
        ("Eunchae (Guardian)", eunchae_bot, EUNCHAE_TOKEN),
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
