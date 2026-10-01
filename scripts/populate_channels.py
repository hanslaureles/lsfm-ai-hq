import asyncio
import os
import sys
import discord
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

from sakura_engine import generate_morning_briefing
from eunchae_engine import get_system_vitals

CHAEWON_TOKEN = os.getenv("CHAEWON_BOT_TOKEN")
YUNJIN_TOKEN = os.getenv("YUNJIN_BOT_TOKEN")
SAKURA_TOKEN = os.getenv("SAKURA_BOT_TOKEN")
KAZUHA_TOKEN = os.getenv("KAZUHA_BOT_TOKEN")
EUNCHAE_TOKEN = os.getenv("EUNCHAE_BOT_TOKEN")

async def post_card(token: str, channel_name: str, embed: discord.Embed):
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    
    @client.event
    async def on_ready():
        for guild in client.guilds:
            for channel in guild.text_channels:
                if channel.name.lower() == channel_name.lower():
                    try:
                        await channel.send(embed=embed)
                        print(f"✅ [{client.user.name}] Posted to #{channel.name} in {guild.name}", flush=True)
                    except Exception as e:
                        print(f"⚠️ [{client.user.name}] Failed to post to #{channel.name}: {e}", flush=True)
                    break
        await client.close()

    try:
        await client.start(token)
    except Exception as e:
        print(f"Error starting client: {e}")

async def main():
    print("✨ Populating LSFM HQ channels with official launch cards...", flush=True)

    # 1. Sakura -> #command-center
    sakura_embed_cmd = discord.Embed(
        title="🌸 SAKURA — Chief of Staff Command Center",
        description="Welcome to the central brain of **LE SSERAFIM AI HQ**.\n\n"
                    "I orchestrate tasks across the team, track Hans's high-level career milestones, and manage our daily priorities.",
        color=0xF472B6
    )
    sakura_embed_cmd.add_field(
        name="🎮 Commands & Natural Language",
        value="• `!briefing` (or `!plan`) — Generates your Daily Morning Briefing.\n"
              "• `!goals` — Shows Hans's active career roadmap & sprint targets.\n"
              "• `!ask [question]` — Ask me strategic career, salary, or interview questions.",
        inline=False
    )
    sakura_embed_cmd.add_field(
        name="🤖 The AI Squad Under My Coordination",
        value="⭐ **Chaewon:** Career & Job Tailoring (`#job-tailoring`)\n"
              "🎨 **Yunjin:** Portfolio & UX Critic (`#portfolio-audits`)\n"
              "💻 **Kazuha:** Frontend Engineering (`#frontend-lab`)\n"
              "🛡️ **Eunchae:** System Guardian (`#pc-vitals`)",
        inline=False
    )
    sakura_embed_cmd.set_footer(text="LE SSERAFIM AI HQ • Sakura • Chief of Staff")

    # 2. Sakura -> #daily-briefing (Inaugural briefing)
    briefing_text = generate_morning_briefing()
    sakura_embed_brief = discord.Embed(
        title="🌅 Inaugural Daily Executive Briefing",
        description=briefing_text[:2000],
        color=0xF472B6
    )
    sakura_embed_brief.set_footer(text="Sakura • Chief of Staff • Daily Pulse")

    # 3. Chaewon -> #job-tailoring
    chaewon_embed = discord.Embed(
        title="⭐ CHAEWON — Career & Job Application Engine",
        description="I am your personal talent agent. Drop any job link or text here and I will tailor an application designed to land an interview.",
        color=0xF1C40F
    )
    chaewon_embed.add_field(
        name="🎯 How to Use",
        value="• **Paste a URL:** `!tailor https://boards.greenhouse.io/...`\n"
              "• **Paste text:** `!tailor Product Designer at Stripe ::: [paste text]`\n"
              "• **Upload file:** Attach a `.txt` job spec with `!tailor`",
        inline=False
    )
    chaewon_embed.add_field(
        name="📦 What I Generate",
        value="1. **ATS Fit Score (1–100%)**\n"
              "2. **Tailored Resume Bullets** matching your CS degree & ROC internship\n"
              "3. **Custom, Human Cover Letter** (No robotic cliches)\n"
              "4. **Strategic Portfolio Recommendations** for interviewers",
        inline=False
    )
    chaewon_embed.set_footer(text="Chaewon • Career Agent • 'Designer who ships, developer who designs'")

    # 4. Yunjin -> #portfolio-audits
    yunjin_embed = discord.Embed(
        title="🎨 YUNJIN — Portfolio Agent & Senior UX Critic",
        description="I own the quality, storytelling, and heuristic excellence of your design portfolio (`portfolio-site/`).",
        color=0xE91E63
    )
    yunjin_embed.add_field(
        name="🔍 Commands",
        value="• `!audit` — Full scan of your portfolio: verifies images on disk, SEO tags, and generates an executive UX critique.\n"
              "• `!critique [project]` — Design Director critique of `vellum`, `lumina`, `fintrack`, or `aura`.\n"
              "• `!status` — View the live project completion scorecard.",
        inline=False
    )
    yunjin_embed.set_footer(text="Yunjin • Portfolio Guardian & Senior UX Critic")

    # 5. Yunjin -> #project-status
    status_embed = discord.Embed(
        title="📊 Flagship Case Studies Scorecard",
        description="Current status of Hans's 4 flagship projects in `portfolio-site/`:",
        color=0xE91E63
    )
    status_embed.add_field(
        name="🚀 01 · LSFM AI HQ (Multi-Agent Swarm)",
        value="• Autonomous 5-Agent Swarm: ✅ 100%\n• Hybrid Local/Cloud Router: ✅ Live\n• Headless Edge PDF Compiler: ✅ Automated vector PDF\n• Case Study Storytelling: ✅ Complete",
        inline=False
    )
    status_embed.add_field(
        name="📈 02 · Lumina Analytics (AI Observability)",
        value="• Dark-Mode Design System: ✅ 100%\n• High Data Density / KPIs: ✅ 100%\n• Responsive Desktop Views: ✅ Live\n• Case Study Storytelling: ✅ Complete",
        inline=False
    )
    status_embed.add_field(
        name="📱 03 · Vellum OS (Mental Wellness UX)",
        value="• Design Token Architecture: ✅ 100%\n• Tactile Reflection Cards: ✅ 100%\n• Humane AI Micro-interactions: ✅ Live\n• Case Study Storytelling: ✅ Complete",
        inline=False
    )
    status_embed.add_field(
        name="💳 04 · FinTrack (Personal Finance UX)",
        value="• Micro-budgeting flows: ✅ 100%\n• Behavioral Nudge Cards: ✅ 100%\n• Sub-3s Expense Logging: ✅ Live\n• Case Study Storytelling: ✅ Complete",
        inline=False
    )
    status_embed.add_field(
        name="☕ 05 · Aura Coffee & Kitchen (Specialty Commerce)",
        value="• Artisanal Web Store (/aura-store): ✅ Live\n• Drink Customizer Modal: ✅ 100%\n• Dynamic Slide-over Bag: ✅ Live\n• PH Payment Rails (GCash/Maya): ✅ Complete",
        inline=False
    )
    status_embed.add_field(
        name="🧠 06 · Cognitive Memory Core (Cognitive Architecture)",
        value="• Pure Python BM25 Engine: ✅ In-memory BM25 retrieval\n• Reflection & Post-Mortems: ✅ 100%\n• Workspace Rule Crystallization: ✅ 100%\n• Interactive Web Simulator: ✅ Live",
        inline=False
    )
    status_embed.set_footer(text="Monitored by Yunjin • All 6 case studies live in portfolio-site/")

    # 6. Kazuha -> #frontend-lab
    kazuha_embed = discord.Embed(
        title="💻 KAZUHA — Frontend Agent & UI Architect",
        description="I am your lead software engineer. I inspect your code, enforce Swiss Editorial design tokens, and architect components.",
        color=0x00B4D8
    )
    kazuha_embed.add_field(
        name="🛠️ Commands",
        value="• `!inspect [file]` — Code & token audit of `styles.css`, `index.html`, `app.js`.\n"
              "• `!component [description]` — Generates production-ready, accessible HTML/CSS or React components.\n"
              "• `!git` — Checks uncommitted changes in your repository.",
        inline=False
    )
    kazuha_embed.set_footer(text="Kazuha • Lead Frontend Architect • Clean Code & Zero Bloat")

    # 7. Eunchae -> #pc-vitals
    v = get_system_vitals()
    eunchae_embed = discord.Embed(
        title="🛡️ EUNCHAE — System Guardian & Squad Watchdog",
        description="I keep watch over Hans's host Windows PC, background bot processes, and resource vitals!",
        color=0xE67E22
    )
    eunchae_embed.add_field(
        name="⚡ Live Hardware Diagnostic",
        value=f"• **CPU:** {v['cpu_pct']}% ({v['cpu_count']} cores)\n"
              f"• **RAM:** {v['ram_used_gb']} GB / {v['ram_total_gb']} GB ({v['ram_pct']}%)\n"
              f"• **C: Drive:** {v['disk_free_gb']} GB Free\n"
              f"• **Uptime:** {v['uptime_str']}\n"
              f"• **Status:** {'🟢 All Systems Nominal' if v['healthy'] else '⚠️ High Load'}",
        inline=False
    )
    eunchae_embed.add_field(
        name="🎮 Commands",
        value="• `!vitals` (or `!health`) — Real-time PC vitals\n"
              "• `!checkin` — Full energetic checkup from the maknae",
        inline=False
    )
    eunchae_embed.set_footer(text="Eunchae • System Guardian • Watchdog Probe Active")

    # Execute posts sequentially to ensure clean order
    if SAKURA_TOKEN:
        await post_card(SAKURA_TOKEN, "command-center", sakura_embed_cmd)
        await post_card(SAKURA_TOKEN, "daily-briefing", sakura_embed_brief)

    if CHAEWON_TOKEN:
        await post_card(CHAEWON_TOKEN, "job-tailoring", chaewon_embed)

    if YUNJIN_TOKEN:
        await post_card(YUNJIN_TOKEN, "portfolio-audits", yunjin_embed)
        await post_card(YUNJIN_TOKEN, "project-status", status_embed)

    if KAZUHA_TOKEN:
        await post_card(KAZUHA_TOKEN, "frontend-lab", kazuha_embed)

    if EUNCHAE_TOKEN:
        await post_card(EUNCHAE_TOKEN, "pc-vitals", eunchae_embed)

    print("🎉 All channels successfully populated!", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
