import os
import sys
import asyncio
import discord
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()
TOKEN = os.getenv("SAKURA_BOT_TOKEN")

CATEGORY_NAME = "📖 00 · COMMAND DIRECTORY"
CHANNELS = ["all-commands", "commands-chat"]

intents = discord.Intents.default()
intents.guilds = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"🌸 Connected as {client.user.name}", flush=True)
    for guild in client.guilds:
        print(f"🏰 Guild: {guild.name} (ID: {guild.id})", flush=True)
        me = guild.me
        print(f"   Bot permissions: Manage Channels = {me.guild_permissions.manage_channels}, Admin = {me.guild_permissions.administrator}", flush=True)
        
        # Check if category exists
        target_cat = None
        for cat in guild.categories:
            if "COMMAND DIRECTORY" in cat.name.upper() or cat.name == CATEGORY_NAME:
                target_cat = cat
                print(f"   Existing category found: {cat.name}", flush=True)
                break
        
        if not target_cat:
            try:
                target_cat = await guild.create_category(CATEGORY_NAME, position=0)
                print(f"✅ Created Category: {CATEGORY_NAME}", flush=True)
            except discord.Forbidden:
                print(f"❌ Forbidden: Bot lacks 'Manage Channels' permission to create category.", flush=True)
                await client.close()
                return
            except Exception as e:
                print(f"❌ Error creating category: {e}", flush=True)
                await client.close()
                return

        # Check / create channels inside category
        created_channels = {}
        for ch_name in CHANNELS:
            existing_ch = None
            for ch in target_cat.text_channels:
                if ch.name.lower() == ch_name.lower():
                    existing_ch = ch
                    break
            
            if not existing_ch:
                # Also check in entire guild in case it was created elsewhere
                for ch in guild.text_channels:
                    if ch.name.lower() == ch_name.lower():
                        existing_ch = ch
                        await existing_ch.edit(category=target_cat)
                        print(f"   Moved existing #{ch.name} into {target_cat.name}", flush=True)
                        break

            if not existing_ch:
                try:
                    existing_ch = await guild.create_text_channel(ch_name, category=target_cat)
                    print(f"✅ Created #{ch_name} under {target_cat.name}", flush=True)
                except Exception as e:
                    print(f"❌ Error creating #{ch_name}: {e}", flush=True)
            else:
                print(f"   Channel #{ch_name} already exists.", flush=True)

            created_channels[ch_name] = existing_ch

        # Now post Master Command Directory into #all-commands
        all_cmd_ch = created_channels.get("all-commands")
        if all_cmd_ch:
            print(f"📜 Posting Master Command Directory to #{all_cmd_ch.name}...", flush=True)
            
            # 1. Header Card
            header_embed = discord.Embed(
                title="📖 LE SSERAFIM AI HQ — Master Command Directory",
                description="Welcome to the universal command guide for Hans Aaron Laureles's personal AI squad.\n\n"
                            "• Use **`#all-commands`** as your permanent reference guide.\n"
                            "• Use **`#commands-chat`** as a shared universal playground where you can invoke **any** agent or command at any time!",
                color=0xF472B6
            )
            header_embed.add_field(
                name="⚡ Universal Quick Access",
                value="Every agent responds in `#commands-chat` to their commands and direct mentions.",
                inline=False
            )
            header_embed.set_footer(text="LE SSERAFIM AI HQ • Unified Command System")
            await all_cmd_ch.send(embed=header_embed)

            # 2. Sakura Card
            sakura_embed = discord.Embed(
                title="🌸 01 · SAKURA — Chief of Staff & Orchestrator",
                description="Central brain, roadmap strategist, multi-agent pipeline orchestrator, and inference mode switcher.",
                color=0xF472B6
            )
            sakura_embed.add_field(
                name="🤝 `!apply [URL or job text]`",
                value="Dispatches the full squad pipeline: Chaewon tailors resume & letter, Yunjin curates portfolio case studies, Kazuha generates the tech pitch, and Sakura posts the Master Proposal to `#approvals` for your 1-click reaction approval (`✅`/`❌`).",
                inline=False
            )
            sakura_embed.add_field(
                name="🧠 `!mode [auto|cloud|local]` & `!brain`",
                value="Switch AI inference engine:\n• `!mode cloud` — 100% Groq Cloud, zero GPU load for gaming\n• `!mode local` — 100% Local Ollama on RX 6600 XT\n• `!mode auto` — Smart dual-brain auto-detection\n• `!brain` — Real-time hardware & inference diagnostic",
                inline=False
            )
            sakura_embed.add_field(
                name="🌅 `!briefing` & 🎯 `!goals`",
                value="• `!briefing` (or `!plan`) — Assembles today's executive morning briefing & top 3 priorities.\n• `!goals` — Shows your active career roadmap and sprint targets.",
                inline=False
            )
            sakura_embed.add_field(
                name="💬 `!ask [question]`",
                value="Strategic coaching on interviews, negotiation, and day-to-day focus.",
                inline=False
            )
            sakura_embed.set_footer(text="Sakura • Chief of Staff • Command Directory")
            await all_cmd_ch.send(embed=sakura_embed)

            # 3. Chaewon Card
            chaewon_embed = discord.Embed(
                title="⭐ 02 · CHAEWON — Career & Application Agent",
                description="Talent agent, ATS keyword optimization, and high-impact application tailoring.",
                color=0xF1C40F
            )
            chaewon_embed.add_field(
                name="🎯 `!tailor [URL or text]`",
                value="Tailors your resume & cover letter to match any job posting:\n• **Link:** `!tailor https://boards.greenhouse.io/...`\n• **Text:** `!tailor Product Designer at Stripe ::: [paste text]`\n• **File:** Attach a `.txt` or `.md` job spec",
                inline=False
            )
            chaewon_embed.add_field(
                name="👤 `!profile` & 📜 `!history`",
                value="• `!profile` — Inspects your active master resume, skills, and portfolio projects in memory.\n• `!history` — Lists your recently tailored applications and fit scores.",
                inline=False
            )
            chaewon_embed.set_footer(text="Chaewon • Career Copilot • Command Directory")
            await all_cmd_ch.send(embed=chaewon_embed)

            # 4. Yunjin Card
            yunjin_embed = discord.Embed(
                title="🎨 03 · YUNJIN — Portfolio Agent & Senior UX Critic",
                description="Design director heuristics, design system librarian, and portfolio craft guardian.",
                color=0xE91E63
            )
            yunjin_embed.add_field(
                name="🔍 `!audit`",
                value="Scans the entire `portfolio-site/` directory, checks all image links on disk, validates HTML/meta tags, and produces an executive UX craft score.",
                inline=False
            )
            yunjin_embed.add_field(
                name="🔬 `!critique [project]`",
                value="Deep design director review of a specific case study:\n• `!critique lsfm` (Autonomous Multi-Agent Swarm)\n• `!critique lumina` (AI Observability SaaS)\n• `!critique vellum` (Mental Wellness UX)\n• `!critique fintrack` (Fintech Personal Finance)\n• `!critique aura` (Specialty Commerce & PH Rails)\n• `!critique memory` (Cognitive Architecture & BM25)",
                inline=False
            )
            yunjin_embed.add_field(
                name="📊 `!status`",
                value="Displays the live completion and polish scorecard for all 6 flagship case studies.",
                inline=False
            )
            yunjin_embed.set_footer(text="Yunjin • Portfolio Guardian • Command Directory")
            await all_cmd_ch.send(embed=yunjin_embed)

            # 5. Kazuha Card
            kazuha_embed = discord.Embed(
                title="💻 04 · KAZUHA — Frontend Agent & UI Architect",
                description="Lead engineer, CSS design token compliance, and accessible component architect.",
                color=0x00B4D8
            )
            kazuha_embed.add_field(
                name="🛠️ `!inspect [filename]`",
                value="Performs architectural & token adherence code inspection on files like `styles.css`, `index.html`, `app.js`.",
                inline=False
            )
            kazuha_embed.add_field(
                name="🧩 `!component [description]`",
                value="Generates production-grade, accessible (WCAG AA) HTML5/CSS custom property or React components matching your Swiss Editorial design system.",
                inline=False
            )
            kazuha_embed.add_field(
                name="🌿 `!git`",
                value="Checks git status and uncommitted files in your Antigravity workspace.",
                inline=False
            )
            kazuha_embed.set_footer(text="Kazuha • Lead Frontend Architect • Command Directory")
            await all_cmd_ch.send(embed=kazuha_embed)

            # 6. Eunchae Card
            eunchae_embed = discord.Embed(
                title="🛡️ 05 · EUNCHAE — System Guardian & PC Watchdog",
                description="Hardware health monitoring, gaming resource protection, and uptime watchdog.",
                color=0xE67E22
            )
            eunchae_embed.add_field(
                name="⚡ `!vitals` (or `!health`)",
                value="Real-time CPU %, RAM usage (GB used/total), C: drive free space, and PC uptime.",
                inline=False
            )
            eunchae_embed.add_field(
                name="🛡️ `!checkin`",
                value="Generates a full, energetic hardware and squad status checkup from the maknae.",
                inline=False
            )
            eunchae_embed.add_field(
                name="🏓 `!ping`",
                value="Tests gateway latency and bot response times.",
                inline=False
            )
            eunchae_embed.set_footer(text="Eunchae • System Guardian • Command Directory")
            await all_cmd_ch.send(embed=eunchae_embed)

            print("🎉 Master Command Directory successfully posted to #all-commands!", flush=True)

    await client.close()

if __name__ == "__main__":
    client.run(TOKEN)
