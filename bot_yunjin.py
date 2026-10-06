import os
import sys
import asyncio
import datetime
from pathlib import Path
import discord
import health_recorder
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
TOKEN = os.getenv("YUNJIN_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
health_recorder.attach(bot, "yunjin")  # memory/health/yunjin.json (4B-2)

from yunjin_engine import audit_full_portfolio, critique_case_study, consult_yunjin, PORTFOLIO_DIR, MEMORY_DIR
from discord_utils import send_clean_embeds
from daily_once import run_daily
from llm_client import get_brain_status

AUDIT_DATE_FILE = MEMORY_DIR / "last_portfolio_audit_date.txt"

def find_channel_by_name(guild: discord.Guild, name: str):
    """Locates a text channel in the guild matching name case-insensitively."""
    if not guild:
        return None
    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            return ch
    return None

def audit_due(last_date_str: str, today: datetime.date) -> bool:
    """Run once every 7 days; a missing or malformed stored date counts as never run."""
    try:
        return (today - datetime.datetime.strptime(last_date_str, "%Y-%m-%d").date()).days >= 7
    except ValueError:  # quiet: malformed or empty stored date: treated as never run
        return True


async def post_portfolio_audit_if_due():
    # Weekly, at most once (6B).
    now = datetime.datetime.now()
    channel = next((c for c in (find_channel_by_name(g, "portfolio-audits") for g in bot.guilds) if c), None)
    if not channel:
        return

    async def prepare():
        res = await asyncio.get_running_loop().run_in_executor(None, audit_full_portfolio)
        return res if res.get("success") else None

    async def deliver(res):
        score = res["score"]
        color = 0x57F287 if score >= 90 else (0xFEE75C if score >= 80 else 0xED4245)
        embed = discord.Embed(
            title=f"🎨 Scheduled Portfolio Health Audit — Score: {score}%",
            description=f"### Weekly Health Check: **{now.strftime('%A, %b %d')}**\n"
                        f"**Broken Images Detected:** {res['broken_images_count']}\n"
                        f"**Audited Directory:** `portfolio-site/`\n\n"
                        f"{res['report'][:1600]}",
            color=color
        )
        embed.set_footer(text="Yunjin • Senior UX Critic • Scheduled Weekly Review")
        saved_file = Path(res["saved_file"])
        if saved_file.exists():
            discord_file = discord.File(str(saved_file), filename=res["filename"])
            await channel.send(embed=embed, file=discord_file)
        else:
            await channel.send(embed=embed)
        print(f"🎨 [Yunjin] Scheduled weekly portfolio audit delivered to #{channel.name}", flush=True)

    await run_daily(AUDIT_DATE_FILE, now.strftime("%Y-%m-%d"), "portfolio audit (!audit)", prepare, deliver,
                    notify=channel.send, due=lambda last: audit_due(last, now.date()))


@tasks.loop(hours=6)
async def scheduled_audit_loop():
    await bot.wait_until_ready()
    await post_portfolio_audit_if_due()


@bot.event
async def on_ready():
    print("=" * 60, flush=True)
    print(f"🎨 YUNJIN is ONLINE! Logged in as {bot.user.name} (ID: {bot.user.id})", flush=True)
    print(f"🌐 Connected to {len(bot.guilds)} server(s):", flush=True)
    for guild in bot.guilds:
        print(f"   - {guild.name} (ID: {guild.id})", flush=True)
    print("=" * 60, flush=True)

    activity = discord.Activity(type=discord.ActivityType.watching, name="portfolio & UX craft 🎨 (!help)")
    await bot.change_presence(status=discord.Status.online, activity=activity)

    if not scheduled_audit_loop.is_running():
        scheduled_audit_loop.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    channel_name = getattr(message.channel, "name", "").lower()
    is_mentioned = bot.user in message.mentions
    is_home_channel = any(k in channel_name for k in ["portfolio-audits", "project-status", "commands-chat"])
    if not (is_home_channel or is_mentioned):
        return

    clean_content = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not clean_content:
        await message.reply("🎨 Yes, Hans? Ask me for an audit, critique on a case study (LSFM, Lumina, Vellum, FinTrack, Aura, Memory), or design feedback!")
        return

    lower = clean_content.lower()
    loop = asyncio.get_running_loop()

    # 1. Full portfolio audit trigger
    if any(k in lower for k in ["run audit", "portfolio audit", "check portfolio", "broken images", "audit full", "health check"]):
        status_msg = await message.reply("🎨 **Yunjin is scanning `portfolio-site/` assets, images, and UX layout...**")
        try:
            async with message.channel.typing():
                result = await loop.run_in_executor(None, audit_full_portfolio)
            score = result["score"]
            color = 0x57F287 if score >= 90 else (0xFEE75C if score >= 80 else 0xED4245)
            embed = discord.Embed(
                title=f"🎨 Portfolio Health Audit — Score: {score}%",
                description=f"Broken Images: **{result['broken_images_count']}**\nSaved Report: `{result['filename']}`\n\n{result['report'][:1800]}",
                color=color
            )
            embed.set_footer(text="Yunjin • Senior UX Critic • LE SSERAFIM AI HQ")
            await status_msg.delete()
            await message.reply(embed=embed)
        except Exception as e:
            await status_msg.edit(content=f"❌ Audit failed: `{e}`")
        return

    # 2. Specific case study critique
    for proj in ["vellum", "lumina", "fintrack", "aura"]:
        if proj in lower:
            status_msg = await message.reply(f"🔬 **Yunjin is reviewing the case study for {proj.upper()}...**")
            try:
                async with message.channel.typing():
                    res = await loop.run_in_executor(None, critique_case_study, proj)
                if not res.get("success"):
                    await status_msg.edit(content=f"⚠️ {res.get('error')}")
                    return
                await send_clean_embeds(
                    target=message,
                    title=f"🔬 Senior UX Critique: {res['project']}",
                    content=res["critique"],
                    color=0xE91E63,
                    footer_text="Yunjin • Senior UX Critic • Design Director Heuristics",
                    status_msg_to_delete=status_msg
                )
            except Exception as e:
                await status_msg.edit(content=f"❌ Critique failed: `{e}`")
            return

    # 3. General natural design consultation
    async with message.channel.typing():
        try:
            critique = await loop.run_in_executor(None, consult_yunjin, clean_content)
            await send_clean_embeds(
                target=message,
                title="🎨 Design Critique from Yunjin",
                content=critique,
                color=0xE91E63,
                footer_text="Yunjin • Senior UX Critic • LE SSERAFIM AI HQ"
            )
        except Exception as e:
            await message.reply(f"❌ Consultation failed: `{e}`")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="🎨 Yunjin — Portfolio Agent & Senior UX Critic",
        description="I manage and critique Hans's professional design portfolio (`portfolio-site/`). Here's what I can do:",
        color=0xE91E63
    )
    embed.add_field(
        name="🔍 `!audit`",
        value="Runs a full technical & visual health audit: checks for broken images, meta tags, and generates a comprehensive UX critique.",
        inline=False
    )
    embed.add_field(
        name="🔬 `!critique [project]`",
        value="Evaluates a specific case study from a senior design hiring manager's perspective.\n*Examples:* `!critique lsfm`, `!critique lumina`, `!critique vellum`, `!critique fintrack`, `!critique aura`, `!critique memory`",
        inline=False
    )
    embed.add_field(
        name="📊 `!status`",
        value="Lists the flagship case studies and checks that each page exists in `portfolio-site/`.",
        inline=False
    )
    embed.add_field(
        name="🏓 `!ping`",
        value="Check Yunjin's connection latency and brain readiness.",
        inline=False
    )
    embed.set_footer(text="LE SSERAFIM AI HQ • Yunjin (Portfolio Guardian)")
    await ctx.reply(embed=embed)

@bot.command(name="ping")
async def ping(ctx):
    latency_ms = round(bot.latency * 1000)
    loop = asyncio.get_running_loop()
    brain = await loop.run_in_executor(None, get_brain_status, "yunjin")
    embed = discord.Embed(
        title="🏓 Pong!",
        description=(
            f"Latency: **{latency_ms}ms**\n"
            f"Brain: **{brain['active_provider']}** · `{brain['active_model']}` (mode: {brain['mode']})"
        ),
        color=0xE91E63
    )
    await ctx.reply(embed=embed)

# Feature summaries are descriptive only; readiness comes from the file checks in !status.
STATUS_CASE_STUDIES = [
    ("🚀 LSFM AI HQ (Multi-Agent Swarm)", ["case-lsfm.html"],
     ["5 Discord agents on asyncio", "Groq + Gemini routing, optional local Ollama", "Headless-Edge ATS resume compiler"]),
    ("🔮 Manas: Ciel (Voice Copilot & HUD)", ["case-ciel.html"],
     ["Bilingual JA/EN voice pipeline", "Groq Whisper STT + Edge-TTS with FFmpeg filter", "Obsidian vault tools"]),
    ("🧠 Cognitive Memory Core", ["case-memory.html"],
     ["Pure-stdlib BM25 recall", "Reflection log + rule crystallization", "Interactive web simulator"]),
    ("📈 Lumina Analytics (AI Observability)", ["case-lumina.html"],
     ["Dark-mode design system", "KPI-dense dashboard views"]),
    ("📱 Vellum OS (Mental Wellness UX)", ["case-vellum.html"],
     ["Design token architecture", "Reflection cards & micro-interactions"]),
    ("💳 FinTrack (Personal Finance UX)", ["case-fintrack.html"],
     ["Micro-budgeting flows", "Behavioral nudge cards"]),
    ("☕ Aura Coffee & Kitchen (Specialty Commerce)", ["case-aura.html", "aura-store/index.html"],
     ["Drink customizer & slide-over bag", "PH payment rails (GCash, Maya, COD)"]),
]

@bot.command(name="status")
async def status(ctx):
    embed = discord.Embed(
        title="📊 Portfolio Case Study Tracker",
        description="Flagship case studies in `portfolio-site/`. File checks run live on each call:",
        color=0xE91E63
    )
    found = 0
    for name, files, features in STATUS_CASE_STUDIES:
        checks = []
        for rel in files:
            ok = (PORTFOLIO_DIR / rel).exists()
            checks.append(f"{'✅' if ok else '❌'} `{rel}`")
        if all((PORTFOLIO_DIR / rel).exists() for rel in files):
            found += 1
        value = "\n".join([f"• {f}" for f in features] + checks)
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text=f"Monitored by Yunjin • {found}/{len(STATUS_CASE_STUDIES)} case studies found on disk")
    await ctx.reply(embed=embed)

@bot.command(name="audit")
async def audit(ctx):
    status_msg = await ctx.reply("🔍 **Yunjin is scanning your portfolio codebase...**\n*(Checking image assets, HTML markup, typography tokens, and recruiter heuristics)*")
    
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await loop.run_in_executor(None, audit_full_portfolio)
    except Exception as e:
        await status_msg.edit(content=f"❌ Audit failed: `{e}`")
        return

    if not res["success"]:
        await status_msg.edit(content=f"⚠️ {res['error']}")
        return

    score = res["score"]
    broken_count = res["broken_images_count"]
    color = 0x57F287 if score >= 90 else (0xFEE75C if score >= 75 else 0xED4245)

    embed = discord.Embed(
        title="🎨 Portfolio Health & Craft Audit",
        description=f"### Overall Health Score: **{score}%**\n"
                    f"**Broken Images Detected:** {broken_count}\n"
                    f"**Audited Directory:** `portfolio-site/`",
        color=color
    )
    
    if broken_count > 0:
        broken_preview = "\n".join([f"• `{b}`" for b in res["broken_images"][:4]])
        embed.add_field(name="⚠️ Broken Asset Warnings", value=broken_preview, inline=False)
    else:
        embed.add_field(name="✅ Asset Integrity", value="Every image referenced by the audited pages was found on disk.", inline=False)

    embed.add_field(name="📁 Full Audit Report Saved", value=f"`{res['filename']}`", inline=False)
    embed.set_footer(text="Yunjin • Full detailed audit attached below.")

    saved_file = Path(res["saved_file"])
    discord_file = discord.File(str(saved_file), filename=res["filename"])

    await status_msg.delete()
    await ctx.reply(embed=embed, file=discord_file)

@bot.command(name="critique")
async def critique(ctx, project: str = None):
    if not project:
        embed = discord.Embed(
            title="❓ How to use `!critique`",
            description="Specify which case study you want Yunjin to critique:\n\n"
                        "• `!critique lsfm` (Multi-Agent Systems & Local Inference)\n"
                        "• `!critique lumina` (AI Observability & SaaS Dashboard)\n"
                        "• `!critique vellum` (Mental Wellness & Humane AI)\n"
                        "• `!critique fintrack` (Fintech UX & Micro-budgeting)\n"
                        "• `!critique aura` (Specialty Commerce & Philippine Rails)\n"
                        "• `!critique memory` (Cognitive Architecture & BM25 Core)",
            color=0xE91E63
        )
        await ctx.reply(embed=embed)
        return

    status_msg = await ctx.reply(f"🔬 **Yunjin is reading `case-{project.lower()}.html` through a Design Director lens...**")

    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await loop.run_in_executor(None, critique_case_study, project)
    except Exception as e:
        await status_msg.edit(content=f"❌ Critique failed: `{e}`")
        return

    if not res["success"]:
        await status_msg.edit(content=f"⚠️ {res['error']}")
        return

    critique_text = res["critique"]
    
    await send_clean_embeds(
        target=ctx,
        title=f"🎨 UX Heuristic Critique: {res['project']}",
        content=res["critique"],
        color=0xE91E63,
        footer_text="Yunjin • Senior UX & Portfolio Director Review",
        status_msg_to_delete=status_msg
    )

if __name__ == "__main__":
    print("🚀 Starting Yunjin (Portfolio Agent)...", flush=True)
    from instance_lock import require_single_instance
    require_single_instance()  # refuses while run_all.py or another bot runs (6B)
    bot.run(TOKEN)
