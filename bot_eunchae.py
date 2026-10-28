import os
import sys
import asyncio
import datetime
from pathlib import Path
import discord
import health_recorder
import owner_guard
import discord_routing
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # quiet: no console to reconfigure (pythonw, redirected stream)
        pass

load_dotenv()
TOKEN = os.getenv("EUNCHAE_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
health_recorder.attach(bot, "eunchae")  # memory/health/eunchae.json (4B-2)
owner_guard.install(bot)  # commands, chat and reactions: Hans only

from eunchae_engine import (
    get_system_vitals,
    generate_eunchae_report,
    chat_eunchae,
    audit_file_qa,
    record_qa_failure,
    get_all_learned_lessons,
    check_obsidian_heartbeat,
    ALERT_LIMITS,
)
import skills  # !vitals and !test run the same skills Ciel uses (ADR-001 R1)
from discord_utils import send_clean_embeds, update_status_card, start_task_thread
from daily_once import run_daily

MEMORY_DIR = Path(__file__).parent / "memory"
VITALS_DATE_FILE = MEMORY_DIR / "last_vitals_post_date.txt"
VITALS_MESSAGE_FILE = MEMORY_DIR / "discord_vitals_messages.json"
LAST_ALERT_TIME = 0
LAST_OBSIDIAN_STATE = None

def find_channel_by_name(guild: discord.Guild, name: str):
    """Locates a text channel in the guild matching name case-insensitively."""
    if not guild:
        return None
    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            return ch
    return None


@tasks.loop(minutes=5)
async def watchdog_loop():
    """Monitors PC hardware every 5 minutes and dispatches alerts / daily pulse."""
    await bot.wait_until_ready()
    global LAST_ALERT_TIME
    loop = asyncio.get_running_loop()
    v = await loop.run_in_executor(None, get_system_vitals)
    now_ts = asyncio.get_event_loop().time()

    # 1. Check for extreme resource load (eunchae_engine.ALERT_LIMITS) with 30m cooldown
    if (v["ram_pct"] > ALERT_LIMITS["ram_pct"] or v["cpu_pct"] > ALERT_LIMITS["cpu_pct"]) \
            and (now_ts - LAST_ALERT_TIME > 1800):
        for guild in bot.guilds:
            channel = find_channel_by_name(guild, "system-alerts")
            if channel:
                alert_embed = discord.Embed(
                    title="⚠️ EUNCHAE WARNING: High Resource Load Detected!",
                    description=f"Hans, your PC hardware is experiencing heavy strain!\n\n"
                                f"• **RAM Usage:** **{v['ram_pct']}%** ({v['ram_used_gb']} GB / {v['ram_total_gb']} GB)\n"
                                f"• **CPU Load:** **{v['cpu_pct']}%** ({v['cpu_count']} Cores)\n"
                                f"• **C: Drive:** {v['disk_free_gb']} GB Free\n\n"
                                f"💡 **Maknae Advice:**\n"
                                f"If you're launching a game, switch to `!mode cloud` (or run `stop_squad.bat`) to instantly free up your RX 6600 XT and memory!",
                    color=0xED4245
                )
                alert_embed.set_footer(text="Eunchae • System Guardian Watchdog Alert")
                await channel.send(embed=alert_embed)
                LAST_ALERT_TIME = now_ts
                print(f"🛡️ [Eunchae] High resource alert dispatched to #{channel.name}", flush=True)
                break

    # 2. Daily routine vitals card posted to #pc-vitals (once per day after 8am; at most once, 6B)
    now = datetime.datetime.now()
    channel = None
    if now.hour >= 8:
        channel = next((c for c in (find_channel_by_name(g, "pc-vitals") for g in bot.guilds) if c), None)
    if not channel:
        return

    async def prepare():
        return v

    async def deliver(v):
        embed = discord.Embed(
            title=f"🛡️ Daily System Vitals Checkup — {'🟢 Nominal' if v['healthy'] else '⚠️ Heavy Load'}",
            description="Routine hardware diagnostic from Hans's host Windows machine:",
            color=0x57F287 if v["healthy"] else 0xED4245
        )
        embed.add_field(name="🧠 CPU Usage", value=f"**{v['cpu_pct']}%** ({v['cpu_count']} Cores)", inline=True)
        embed.add_field(name="💾 RAM Memory", value=f"**{v['ram_used_gb']} GB** / {v['ram_total_gb']} GB ({v['ram_pct']}%)", inline=True)
        embed.add_field(name="💽 C: Drive Free", value=f"**{v['disk_free_gb']} GB** Free", inline=True)
        embed.add_field(name="⏱️ System Uptime", value=f"**{v['uptime_str']}**", inline=True)
        embed.set_footer(text="Eunchae • Routine System Pulse • LE SSERAFIM AI HQ")
        await update_status_card(channel, embed, VITALS_MESSAGE_FILE)
        print(f"🛡️ [Eunchae] Daily vitals pulse delivered to #{channel.name}", flush=True)

    await run_daily(VITALS_DATE_FILE, now.strftime("%Y-%m-%d"), "vitals card (!vitals)",
                    prepare, deliver, notify=channel.send)


@tasks.loop(minutes=15)
async def obsidian_heartbeat_loop():
    """Cron 5: Obsidian Local REST API Port 27124 Heartbeat & Vault Telemetry Check."""
    await bot.wait_until_ready()
    global LAST_OBSIDIAN_STATE
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, check_obsidian_heartbeat)
        current_state = res["status"]
        state_changed = (LAST_OBSIDIAN_STATE is not None and LAST_OBSIDIAN_STATE != current_state)
        LAST_OBSIDIAN_STATE = current_state

        if state_changed and current_state == "DEGRADED":
            for guild in bot.guilds:
                channel = find_channel_by_name(guild, "system-alerts")
                if channel:
                    embed = discord.Embed(
                        title="🚨 Obsidian Vault Check: Degraded State Detected",
                        description=(
                            f"**Status:** {res['status_text']}\n"
                            f"• Vault Exists: `{res['vault_exists']}` ({res['vault_path']})\n"
                            f"• REST API (27124): `{res['rest_online']}`\n"
                            f"• RAM: `{res['vitals']['ram_pct']}%` | CPU: `{res['vitals']['cpu_pct']}%`"
                        ),
                        color=0xED4245
                    )
                    await channel.send(embed=embed)
                    break
    except Exception as e:
        print(f"⚠️ [Eunchae] Obsidian heartbeat exception: {e}", flush=True)


@bot.event
async def on_ready():
    discord_routing.register("eunchae", bot.user)
    print("=" * 60, flush=True)
    print(f"🛡️ EUNCHAE is ONLINE! Logged in as {bot.user.name} (ID: {bot.user.id})", flush=True)
    print(f"🌐 Connected to {len(bot.guilds)} server(s):", flush=True)
    for guild in bot.guilds:
        print(f"   - {guild.name} (ID: {guild.id})", flush=True)
    print("=" * 60, flush=True)

    activity = discord.Activity(type=discord.ActivityType.watching, name="PC Vitals & Team QA 🛡️ (!help)")
    await bot.change_presence(status=discord.Status.online, activity=activity)

    if not watchdog_loop.is_running():
        watchdog_loop.start()

    if not obsidian_heartbeat_loop.is_running():
        obsidian_heartbeat_loop.start()

@bot.event
async def on_message(message):
    if message.author.bot or not await owner_guard.allowed(bot, message.author):
        return

    clean_content = discord_routing.content_for(message, "eunchae", bot)
    if clean_content is None:
        return
    if clean_content.startswith("!"):
        await discord_routing.process_commands(bot, message, clean_content)
        return
    if not clean_content:
        await message.reply("🛡️ Yes, Hans! Eunchae is on watch! Ask me for vitals, hardware checkup, or just chat!")
        return

    lower = clean_content.lower()
    loop = asyncio.get_running_loop()

    # 1. System vitals checkup trigger
    if any(k in lower for k in ["vitals", "hardware", "cpu", "ram", "storage", "disk", "specs", "checkin", "check in", "how is my pc", "how's my pc"]):
        status_msg = await message.reply("🛡️ **Eunchae is probing system hardware sensors...**")
        try:
            async with message.channel.typing():
                report = await loop.run_in_executor(None, generate_eunchae_report)
            vitals = await loop.run_in_executor(None, get_system_vitals)  # a 0.5 s sample: never on the loop
            await send_clean_embeds(
                target=message,
                title="🛡️ Eunchae's PC Guardian Report",
                content=report,
                color=0x57F287 if vitals["healthy"] else 0xE67E22,
                footer_text="Eunchae • System Guardian & Watchdog",
                status_msg_to_delete=status_msg
            )
        except Exception as e:
            await status_msg.edit(content=f"❌ Vitals check failed: `{e}`")
        return

    # 2. General playful guardian chat
    async with message.channel.typing():
        try:
            reply = await loop.run_in_executor(None, chat_eunchae, clean_content)
            await send_clean_embeds(
                target=message,
                title="🛡️ Eunchae Guardian Check-in",
                content=reply,
                color=0xE67E22,
                footer_text="Eunchae • System Guardian • LE SSERAFIM AI HQ"
            )
        except Exception as e:
            await message.reply(f"❌ Could not talk to Eunchae: `{e}`")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="🛡️ Eunchae — System Guardian & Team Watchdog",
        description="I watch over Hans's PC hardware, background processes, and the team's uptime:",
        color=0xE67E22
    )
    embed.add_field(
        name="⚡ `!vitals` (or `!health`)",
        value="Checks real-time PC metrics: CPU %, RAM usage (GB used/total), C: drive free space, and PC uptime.",
        inline=False
    )
    embed.add_field(
        name="🛡️ `!qa <filename>`",
        value="Performs a Quality Assurance inspection on an application file or markdown doc.",
        inline=False
    )
    embed.add_field(
        name="🧠 `!lessons` (or `!failures`)",
        value="Displays learned failure post-mortems and permanent rules from Memory Core (`agent-memory`).",
        inline=False
    )
    embed.add_field(
        name="✍️ `!reflect <domain> | <symptom> | <rule>`",
        value="Teaches Eunchae a new permanent rule to eliminate future regressions.",
        inline=False
    )
    embed.set_footer(text="LE SSERAFIM AI HQ • Eunchae (Guardian & QA Inspector)")
    await ctx.reply(embed=embed)

@bot.command(name="ping")
async def ping(ctx):
    latency_ms = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latency: **{latency_ms}ms**\nWatchdog: **Active & Monitoring PC**",
        color=0xE67E22
    )
    await ctx.reply(embed=embed)

@bot.command(name="vitals")
async def vitals_command(ctx):
    # The same skill Ciel runs, sampled in a worker thread: the 0.5 s psutil sample used to
    # run on the event loop all five bots share (ADR-001 R1).
    result = await asyncio.to_thread(skills.run_skill, "eunchae.vitals")
    if result.status == "failed":
        await ctx.reply(f"❌ {result.text}")
        return
    v = result.data
    status_icon = "🟢" if v["healthy"] else "⚠️"
    
    embed = discord.Embed(
        title=f"🛡️ Hardware Vitals: {status_icon} {'Healthy' if v['healthy'] else 'Heavy Load'}",
        description="Real-time hardware diagnostics from Hans's host Windows machine:",
        color=0x57F287 if v["healthy"] else 0xED4245
    )
    embed.add_field(
        name="🧠 CPU Usage",
        value=f"**{v['cpu_pct']}%** ({v['cpu_count']} Logical Cores)",
        inline=True
    )
    embed.add_field(
        name="💾 RAM Memory",
        value=f"**{v['ram_used_gb']} GB** / {v['ram_total_gb']} GB ({v['ram_pct']}%)",
        inline=True
    )
    embed.add_field(
        name="💽 C: Drive Free",
        value=f"**{v['disk_free_gb']} GB** Free ({v['disk_pct']}% used)",
        inline=True
    )
    embed.add_field(
        name="⏱️ System Uptime",
        value=f"**{v['uptime_str']}** since last boot",
        inline=True
    )
    embed.add_field(
        name="🤖 5-Agent Team State",
        value="🌸 Sakura • ⭐ Chaewon • 🎨 Yunjin • 💻 Kazuha • 🛡️ Eunchae",
        inline=False
    )
    embed.set_footer(text="Eunchae • System Guardian • psutil diagnostic probe")
    await ctx.reply(embed=embed)

@bot.command(name="health")
async def health_alias(ctx):
    await vitals_command(ctx)

@bot.command(name="checkin")
async def checkin(ctx):
    status_msg = await ctx.reply("🛡️ **Eunchae is checking system vitals and pinging all subsystems...**")
    
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            report_text = await loop.run_in_executor(None, generate_eunchae_report)
    except Exception as e:
        await status_msg.edit(content=f"❌ Checkin failed: `{e}`")
        return

    await send_clean_embeds(
        target=ctx,
        title="🛡️ Eunchae's System Checkin",
        content=report_text,
        color=0xE67E22,
        footer_text="Eunchae • System Guardian • LE SSERAFIM AI HQ",
        status_msg_to_delete=status_msg
    )

@bot.command(name="qa", aliases=["audit"])
async def qa_command(ctx, *, filename: str = None):
    """Audits an application document or file for placeholders, formatting, and QA defects."""
    if not filename:
        await ctx.reply("🔍 Please specify a file in `applications/` to audit! Example: `!qa 2026-09-17_iCXeed_Role.md`")
        return

    # Check in applications directory or relative path
    app_file = Path(__file__).parent / "applications" / filename.strip("` ")
    if not app_file.exists():
        app_file = Path(__file__).parent / filename.strip("` ")
    if not app_file.exists():
        await ctx.reply(f"⚠️ Could not find file `{filename}` in `applications/`!")
        return

    status_msg = await ctx.reply(f"🔍 **Eunchae is auditing `{app_file.name}` for QA defects...**")
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await loop.run_in_executor(None, audit_file_qa, app_file)
        await status_msg.delete()
        if not res.get("success"):
            await ctx.reply(f"❌ QA audit failed: `{res.get('error')}`")
            return

        v_color = 0x57F287 if res["score"] >= 90 else (0xFEE75C if res["score"] >= 75 else 0xED4245)
        embed = discord.Embed(
            title=f"🛡️ Eunchae QA Audit: `{res['filename']}`",
            description=f"**Verdict:** **{res['verdict']}** | **QA Score:** **{res['score']}%**\n\n"
                        f"{res['review'][:3500]}",
            color=v_color
        )
        if res.get("placeholders"):
            embed.add_field(
                name="⚠️ Unfilled Placeholders Flagged",
                value=", ".join(f"`{p}`" for p in res["placeholders"][:5]),
                inline=False
            )
        embed.set_footer(text="Eunchae • Team QA Inspector & Gatekeeper")
        await ctx.reply(embed=embed)
    except Exception as e:
        await status_msg.edit(content=f"❌ Error during QA inspection: `{e}`")

@bot.command(name="lessons", aliases=["failures", "heuristics"])
async def lessons_command(ctx, domain: str = None):
    """Displays learned post-mortems and permanent rules stored in agent-memory."""
    lessons = get_all_learned_lessons(domain)
    if not lessons:
        embed = discord.Embed(
            title="🧠 Team Lessons & Rules",
            description="No failure post-mortems recorded yet. All team runs have been clean!",
            color=0x57F287
        )
        embed.set_footer(text="Eunchae • Self-Improving Agent Memory")
        await ctx.reply(embed=embed)
        return

    embed = discord.Embed(
        title=f"🧠 Team Lessons ({len(lessons)} Active Rules)",
        description="Permanent heuristics distilled from past regressions and user feedback:\n*Eliminates session amnesia across the whole team.*",
        color=0xE67E22
    )
    for entry in lessons[:6]:
        eid = entry.get("id", "MEM")
        dom = entry.get("domain", "general").upper()
        rule = entry.get("permanent_rule", "")
        symp = entry.get("symptom", "")
        embed.add_field(
            name=f"[{eid}] {dom}",
            value=f"• **Rule:** {rule[:160]}\n• **Avoids:** *{symp[:100]}*",
            inline=False
        )
    if len(lessons) > 6:
        embed.set_footer(text=f"Eunchae • Showing 6 of {len(lessons)} learned rules • agent-memory")
    else:
        embed.set_footer(text="Eunchae • Self-Improving Flywheel • agent-memory")
    await ctx.reply(embed=embed)

@bot.command(name="reflect")
async def reflect_command(ctx, *, args_str: str = None):
    """
    Teaches Eunchae a new permanent rule on the spot!
    Usage: !reflect <domain> | <symptom> | <permanent_rule>
    Or:    !reflect <domain> | <trigger> | <symptom> | <root_cause> | <permanent_rule>
    """
    if not args_str or "|" not in args_str:
        await ctx.reply(
            "⚠️ Usage format:\n"
            "`!reflect <domain> | <symptom / what went wrong> | <permanent rule to follow>`\n"
            "*(Or 5-part format: `domain | trigger | symptom | root_cause | rule`)*\n\n"
            "*Example:* `!reflect resume | bullet points too short | Always include quantifiable percentage or metric`"
        )
        return

    parts = [p.strip() for p in args_str.split("|")]
    if len(parts) < 3:
        await ctx.reply("⚠️ Please provide at least 3 parts separated by `|`: `domain | symptom | rule`")
        return

    if len(parts) == 3:
        domain, symptom, rule = parts[0], parts[1], parts[2]
        trigger = f"manual_feedback_by_{ctx.author.name}"
        root_cause = "User correction via Discord"
        tags = [domain.lower(), "qa-sentinel"]
    elif len(parts) == 4:
        domain, symptom, root_cause, rule = parts[0], parts[1], parts[2], parts[3]
        trigger = f"manual_feedback_by_{ctx.author.name}"
        tags = [domain.lower(), "qa-sentinel"]
    else:
        domain, trigger, symptom, root_cause, rule = parts[0], parts[1], parts[2], parts[3], parts[4]
        tag_str = parts[5] if len(parts) > 5 else ""
        tags = [t.strip() for t in tag_str.split(",") if t.strip()] or [domain.lower(), "qa-sentinel"]

    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(
        None,
        record_qa_failure,
        domain,
        trigger,
        symptom,
        root_cause,
        rule,
        "",
        tags
    )

    if not res.get("success"):
        await ctx.reply(f"❌ Could not record lesson: `{res.get('error')}`")
        return

    entry = res["entry"]
    embed = discord.Embed(
        title="🧠 New Team Lesson Logged!",
        description=f"Eunchae recorded this permanent rule to **`agent-memory`**:\n\n"
                    f"• **ID:** `{entry.get('id')}`\n"
                    f"• **Domain:** `{domain}`\n"
                    f"• **Avoided Failure:** *\"{symptom}\"*\n"
                    f"• **Permanent Rule:** **\"{rule}\"**\n\n"
                    f"⚡ *All 5 agents will immediately respect this rule in future runs!*",
        color=0x57F287
    )
    embed.set_footer(text="Eunchae • Self-Improving Flywheel • Zero Future Regressions")
    await ctx.reply(embed=embed)


@bot.command(name="test", aliases=["testcode", "pytest", "runtest"])
async def test_command(ctx, *, filepath: str = None):
    """
    Executes Eunchae's automated code verification, test suite runner, and syntax inspection.
    Usage: !test <path_to_file>
    """
    if not filepath:
        await ctx.reply(
            "🧪 **Eunchae Automated Code QA Runner**\n\n"
            "Usage: `!test <file_path>`\n"
            "*Examples:*\n"
            "• `!test git_sentinel.py`\n"
            "• `!test agent-memory/test_memory.py`\n"
            "• `!test bot_eunchae.py`\n\n"
            "Paths are relative to the projects workspace (the folder that holds HQ); absolute paths and `..` are refused.\n"
            "Validates Python compilation, AST structural metrics, unit test suites, import resolution, and balanced brackets."
        )
        return

    clean_fp = filepath.strip("`'\" ")
    ctx = await start_task_thread(ctx, f"Code verification · {clean_fp}")
    status_msg = await ctx.reply(f"🧪 **Eunchae is testing `{clean_fp}`...**")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, skills.run_skill, "eunchae.test", {"path": clean_fp})
    res = result.data

    try:
        await status_msg.delete()
    except Exception:  # quiet: best-effort delete of a status message
        pass

    if not res.get("success"):
        await ctx.reply(f"❌ {result.text}")
        return

    passed = res.get("passed", False)
    score = res.get("score", 0)
    verdict = res.get("verdict", "INCONCLUSIVE")
    warnings = res.get("warnings", [])
    defects = res.get("defects", [])
    checks = res.get("checks", [])

    color = 0x57F287 if (passed and not warnings) else (0xFEE75C if passed else 0xED4245)

    embed = discord.Embed(
        title=f"🧪 Code QA Test: `{res.get('filename')}`",
        description=f"### {verdict} (Score: {score}/100)\n**File:** `{res.get('path')}`",
        color=color
    )

    if checks:
        embed.add_field(
            name="🔍 Verification Checks",
            value="\n".join(checks)[:1024],
            inline=False
        )

    if defects:
        embed.add_field(
            name="🚨 Defects & Failures",
            value="\n".join(defects)[:1024],
            inline=False
        )

    if warnings:
        embed.add_field(
            name="⚠️ Warnings & Advisories",
            value="\n".join(warnings)[:1024],
            inline=False
        )

    embed.set_footer(text="Eunchae • Code Quality Engine • Zero Regression Guarantee")
    await ctx.reply(embed=embed)

@bot.command(name="heartbeat", aliases=["obsidian", "brain_ping", "vault_status", "telemetry"])
async def cmd_heartbeat(ctx):
    """Probes Obsidian Port 27124 Local REST API, Vault health, and host system vitals."""
    status_msg = await ctx.reply("🛡️ **Eunchae is probing Obsidian Port 27124 & Vault telemetry...**")
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, check_obsidian_heartbeat)
        v = res["vitals"]

        embed = discord.Embed(
            title="🛡️ Obsidian Vault & Telemetry Check (Port 27124)",
            description=f"### {res['status_text']}\n*Monitored by Eunchae · 15-Minute Heartbeat Loop (Cron 5)*",
            color=res["color"]
        )
        embed.add_field(
            name="🧠 Local REST API",
            value=f"**Port 27124:** `{'🟢 ONLINE (HTTPS)' if res['rest_online'] else '🟡 OFFLINE (Direct Disk Fallback Active)'}`",
            inline=True
        )
        embed.add_field(
            name="📂 Second Brain Vault",
            value=f"**Notes:** `{res['total_notes']}` markdown notes\n**Path:** `.../{Path(res['vault_path']).name}`",
            inline=True
        )
        embed.add_field(
            name="🤖 Agent Team",
            value="`5-agent team (LSFM AI HQ)`",
            inline=True
        )
        embed.add_field(
            name="💾 Host RAM",
            value=f"**{v['ram_pct']}%** ({v['ram_used_gb']} / {v['ram_total_gb']} GB)",
            inline=True
        )
        embed.add_field(
            name="⚡ CPU & Uptime",
            value=f"**{v['cpu_pct']}%** ({v['cpu_count']} Cores)\nUptime: `{v['uptime_str']}`",
            inline=True
        )
        embed.add_field(
            name="💽 C: Drive Free",
            value=f"**{v['disk_free_gb']} GB** Free ({v['disk_pct']}% used)",
            inline=True
        )

        if res.get("subdirs_count"):
            folders_summary = " · ".join([f"`{k}`: {v_count}" for k, v_count in res["subdirs_count"].items()])
            embed.add_field(name="📁 Vault Directory Breakdown", value=folders_summary[:1024], inline=False)

        embed.set_footer(text="Eunchae • System Guardian • Cron 5")
        await status_msg.delete()
        await ctx.reply(embed=embed)
    except Exception as e:
        await status_msg.edit(content=f"❌ Error during heartbeat check: `{e}`")


if __name__ == "__main__":
    print("🚀 Starting Eunchae (System Guardian)...", flush=True)
    if not TOKEN:
        print("❌ Error: EUNCHAE_BOT_TOKEN not found in .env file!", flush=True)
        exit(1)
    from instance_lock import require_single_instance
    require_single_instance()  # refuses while run_all.py or another bot runs (6B)
    bot.run(TOKEN)

