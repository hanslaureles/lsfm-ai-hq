import os
import sys
import asyncio
from pathlib import Path
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()
TOKEN = os.getenv("KAZUHA_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

from kazuha_engine import (
    inspect_code_file,
    generate_ui_component,
    get_git_status,
    consult_kazuha,
    kazuha_review_changes,
    kazuha_create_commit,
    kazuha_create_pr,
    execute_research_scout
)
from rag_engine import ask_knowledge_base, query_rag, build_index, get_rag_stats
from discord_utils import send_clean_embeds, clip, EMBED_TITLE_MAX
from llm_client import get_brain_status

# Room left for the verdict/branch header or the code fence and footer line
# around LLM text inside one 4096-char embed description.
REVIEW_MAX = 3600

# Agent-Memory Engine Integration
MEMORY_DIR = Path(__file__).parent.parent / "agent-memory"
if str(MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(MEMORY_DIR))

SWARM_MEMORY_DIR = Path(__file__).parent / "memory"
LAST_SCOUT_DATE_FILE = SWARM_MEMORY_DIR / "last_research_scout_date.txt"

try:
    from recall import recall_memories, DEFAULT_STORE  # type: ignore
    from crystallize import crystallize, DEFAULT_RULES_OUTPUT  # type: ignore
    from reflect import record_reflection  # type: ignore
except Exception:
    recall_memories = None
    crystallize = None
    record_reflection = None

async def post_research_scout_if_due(force: bool = False, target_channel=None):
    """
    Cron 4: Kazuha Applied AI Research Scout & Obsidian Ingest (MWF 09:30 AM).
    Fetches trending applied AI papers, generates executive technical briefing, and syncs Obsidian.
    """
    from datetime import datetime
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # MWF: Monday=0, Wednesday=2, Friday=4
    if not force:
        if now.weekday() not in [0, 2, 4]:
            return
        if not (now.hour > 9 or (now.hour == 9 and now.minute >= 30)):
            return
        if LAST_SCOUT_DATE_FILE.exists():
            last_date = LAST_SCOUT_DATE_FILE.read_text(encoding="utf-8").strip()
            if last_date == today_str:
                return

    print(f"💻 [Kazuha] Executing Applied AI Research Scout (Date: {today_str})...", flush=True)
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, execute_research_scout, "applied-ai", True)
        if not res.get("success"):
            print("⚠️ [Kazuha] Research scout synthesis failed.", flush=True)
            return

        channel = target_channel
        if not channel:
            for guild in bot.guilds:
                for ch in guild.text_channels:
                    if ch.name.lower() in ["frontend-lab", "command-center"]:
                        channel = ch
                        break
                if channel:
                    break

        if channel:
            await send_clean_embeds(
                target=channel,
                title=f"🔬 Applied AI Research Digest — {res['date_str']}",
                content=res["digest_text"],
                color=0x00B4D8,
                footer_text=f"Kazuha • Lead Frontend Architect & Applied AI Specialist • {len(res['papers'])} papers • Obsidian Synced"
            )
            print(f"✅ [Kazuha] Delivered Applied AI Research Digest to #{channel.name}!", flush=True)

        SWARM_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        LAST_SCOUT_DATE_FILE.write_text(today_str, encoding="utf-8")
    except Exception as e:
        print(f"⚠️ [Kazuha] Research scout scheduled loop exception: {e}", flush=True)

@tasks.loop(minutes=30)
async def scheduled_scout_loop():
    await post_research_scout_if_due()

@bot.event
async def on_ready():
    print("=" * 60, flush=True)
    print(f"💻 KAZUHA is ONLINE! Logged in as {bot.user.name} (ID: {bot.user.id})", flush=True)
    print(f"🌐 Connected to {len(bot.guilds)} server(s):", flush=True)
    for guild in bot.guilds:
        print(f"   - {guild.name} (ID: {guild.id})", flush=True)
    print("=" * 60, flush=True)

    activity = discord.Activity(type=discord.ActivityType.watching, name="code & design systems 💻 (!help)")
    await bot.change_presence(status=discord.Status.online, activity=activity)

    if not scheduled_scout_loop.is_running():
        scheduled_scout_loop.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    channel_name = getattr(message.channel, "name", "").lower()
    is_mentioned = bot.user in message.mentions
    is_home_channel = any(k in channel_name for k in ["frontend-lab", "git-log", "commands-chat"])
    if not (is_home_channel or is_mentioned):
        return

    clean_content = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not clean_content:
        await message.reply("💻 Yes, Hans? Ask me to inspect code, generate a component, check git, or answer frontend engineering questions!")
        return

    lower = clean_content.lower()
    loop = asyncio.get_running_loop()

    # 1. Git Sentinel conversational triggers
    if any(k in lower for k in ["review code", "review diff", "code review", "review my changes", "review changes", "check diff"]):
        status_msg = await message.reply("🔍 **Kazuha Sentinel is performing architectural code & security review...**")
        async with message.channel.typing():
            res = await loop.run_in_executor(None, kazuha_review_changes, False)
        verdict_color = 0x57F287 if res["score"] >= 90 else (0xFEE75C if res["score"] >= 70 else 0xED4245)
        if res.get("security_alerts"):
            verdict_color = 0xED4245
        embed = discord.Embed(
            title="⚔️ Kazuha Sentinel — Code & Security Review",
            description=f"**Verdict:** {res['verdict']} | **Score:** **{res['score']}%**\n"
                        f"🌿 Branch: `{res['info']['branch']}`\n\n"
                        f"{clip(res['review'], REVIEW_MAX)}",
            color=verdict_color
        )
        embed.set_footer(text="Kazuha • Autonomous Git & PR Sentinel")
        await status_msg.delete()
        await message.reply(embed=embed)
        return

    if any(k in lower for k in ["write commit", "make commit", "generate commit", "commit message"]):
        status_msg = await message.reply("✍️ **Kazuha is synthesizing Conventional Commit message...**")
        async with message.channel.typing():
            res = await loop.run_in_executor(None, kazuha_create_commit, False)
        await status_msg.delete()
        if not res.get("success"):
            await message.reply(f"⚠️ {res.get('error')}")
            return
        embed = discord.Embed(
            title="✍️ Proposed Conventional Commit",
            description=f"```git\n{clip(res['full_message'], REVIEW_MAX)}\n```\n\n*Copy and paste into your terminal, or use `git commit -m \"...\"`*",
            color=0x00B4D8
        )
        embed.set_footer(text="Kazuha • Conventional Commits v1.0.0")
        await message.reply(embed=embed)
        return

    if any(k in lower for k in ["git status", "uncommitted", "repo status", "git changes", "check git"]):
        status = get_git_status()
        embed = discord.Embed(
            title="📁 Antigravity Workspace Git Status",
            description=status,
            color=0x00B4D8
        )
        embed.set_footer(text="Kazuha • Autonomous Git Sentinel")
        await message.reply(embed=embed)
        return

    # 2. File inspection trigger
    for known_file in ["styles.css", "index.html", "script.js", "case-vellum.html", "case-lumina.html", "case-fintrack.html", "case-aura.html"]:
        if known_file in lower:
            status_msg = await message.reply(f"🔍 **Kazuha is performing architectural inspection on `{known_file}`...**")
            try:
                async with message.channel.typing():
                    res = await loop.run_in_executor(None, inspect_code_file, known_file)
                if not res.get("success"):
                    await status_msg.edit(content=f"⚠️ {res.get('error')}")
                    return
                await send_clean_embeds(
                    target=message,
                    title=f"💻 Code & Token Inspection: `{res['filename']}`",
                    content=res["critique"],
                    color=0x00B4D8,
                    footer_text="Kazuha • Lead Frontend Architect • Swiss Editorial Monograph",
                    status_msg_to_delete=status_msg
                )
            except Exception as e:
                await status_msg.edit(content=f"❌ Inspection failed: `{e}`")
            return

    # 3. Component generation trigger
    if any(k in lower for k in ["build a component", "create a component", "generate component", "make a component", "code a component"]):
        status_msg = await message.reply("🛠️ **Kazuha is engineering your component with Swiss Editorial design tokens...**")
        try:
            async with message.channel.typing():
                code_res = await loop.run_in_executor(None, generate_ui_component, clean_content)
            await send_clean_embeds(
                target=message,
                title="🛠️ Generated UI Component",
                content=code_res,
                color=0x00B4D8,
                footer_text="Kazuha • Accessible & Token-Compliant UI",
                status_msg_to_delete=status_msg
            )
        except Exception as e:
            await status_msg.edit(content=f"❌ Component generation failed: `{e}`")
        return

    # 4. General frontend engineering consultation
    async with message.channel.typing():
        try:
            advice = await loop.run_in_executor(None, consult_kazuha, clean_content)
            await send_clean_embeds(
                target=message,
                title="💻 Engineering Advisory from Kazuha",
                content=advice,
                color=0x00B4D8,
                footer_text="Kazuha • Lead Frontend Architect • LE SSERAFIM AI HQ"
            )
        except Exception as e:
            await message.reply(f"❌ Consultation failed: `{e}`")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="💻 Kazuha — Frontend Agent & Knowledge Architect",
        description="I engineer Hans's frontend code, design tokens, and operate the **SQLite Vector RAG Vault**:",
        color=0x00B4D8
    )
    embed.add_field(
        name="🧠 `!ask [question]`",
        value="Asks the knowledge vault a question grounded in your codebase, resume, and design tokens with exact citations.\n• Example: `!ask What is our local inference latency benchmark?`",
        inline=False
    )
    embed.add_field(
        name="🔎 `!search [query]` *(alias: `!rag`)*",
        value="Performs hybrid semantic + BM25 keyword search across all indexed files.\n• Example: `!search Ollama`, `!search --accent`, `!search Patriot Capstone`",
        inline=False
    )
    embed.add_field(
        name="🔄 `!reindex`",
        value="Scans and indexes modified or new files into the vector database in seconds.",
        inline=False
    )
    embed.add_field(
        name="📊 `!ragstats`",
        value="Displays vector vault telemetry (documents, chunks, database size, embedding model).",
        inline=False
    )
    embed.add_field(
        name="⚡ `!recall [query]` *(alias: `!heuristics`)*",
        value="Searches cognitive episodic memory for past post-mortems and permanent rules.\n• Example: `!recall navigation flexbox`, `!recall coffee words`",
        inline=False
    )
    embed.add_field(
        name="📜 `!rules`",
        value="Displays crystallized workspace rules compiled from past failure post-mortems.",
        inline=False
    )
    embed.add_field(
        name="🧠 `!reflect [domain] | [trigger] | [symptom] | [root_cause] | [rule]`",
        value="Logs a new engineering post-mortem directly into episodic memory from Discord.",
        inline=False
    )
    embed.add_field(
        name="🔍 `!inspect [file]`",
        value="Performs architectural code review of any file in `portfolio-site/` (e.g. `!inspect styles.css`, `!inspect index.html`).",
        inline=False
    )
    embed.add_field(
        name="🛠️ `!component [description]`",
        value="Generates production-ready, accessible HTML/CSS or React components adhering to Hans's Swiss Editorial tokens.",
        inline=False
    )
    embed.add_field(
        name="📁 `!git`",
        value="Checks uncommitted changes and repository status.",
        inline=False
    )
    embed.add_field(
        name="🏓 `!ping`",
        value="Check Kazuha's connection latency and compiler readiness.",
        inline=False
    )
    embed.set_footer(text="LE SSERAFIM AI HQ • Kazuha (Knowledge & Frontend)")
    await ctx.reply(embed=embed)

@bot.command(name="ask")
async def ask(ctx, *, question: str = None):
    """Answers questions with grounded citations from the local RAG vault."""
    if not question:
        await ctx.reply("💻 What would you like to ask the knowledge vault? Example: `!ask What is our local inference latency benchmark on the RX 6600 XT?`")
        return
    
    status_msg = await ctx.reply(f"🧠 **Kazuha is querying the Vector RAG Vault...**\n*\"{question[:70]}...\"*")
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await loop.run_in_executor(None, ask_knowledge_base, question)
        
        answer = res["answer"]
        citations = res.get("citations", [])
        await send_clean_embeds(
            target=ctx,
            title=f"💻 Code & System Architecture: {question}",
            content=answer,
            color=0x00B4D8,
            footer_text="Kazuha • SQLite Vector RAG • LE SSERAFIM AI HQ",
            citations=citations,
            status_msg_to_delete=status_msg
        )
    except Exception as e:
        await status_msg.edit(content=f"❌ RAG query error: `{e}`")

@bot.command(name="search", aliases=["rag", "find"])
async def search(ctx, *, query: str = None):
    """Executes hybrid semantic vector search over the codebase and memory."""
    if not query:
        await ctx.reply("🔍 Please provide a search term! Example: `!search Ollama`, `!search --accent`, `!search Patriot Capstone`")
        return
    
    status_msg = await ctx.reply(f"🔎 **Searching RAG vault for:** `{query}`...")
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            chunks = await loop.run_in_executor(None, query_rag, query, 3)
        
        if not chunks:
            await status_msg.edit(content=f"⚠️ No relevant chunks found in the RAG vault for `{query}`. Try `!reindex`!")
            return
        
        embed = discord.Embed(
            title=f"🔎 Semantic Search Results // {clip(query, EMBED_TITLE_MAX - 40)}",
            description="Hybrid retrieval (Dense Cosine + Sparse BM25) across your codebase & memory:",
            color=0x00B4D8
        )
        for i, c in enumerate(chunks, 1):
            title = f"#{i}. {c['rel_path']} · {c['heading']} (Relevance: {c['score']})"
            snippet = c["content"][:280].replace("```", "")
            embed.add_field(
                name=title[:256],
                value=f"```{snippet}...\n```",
                inline=False
            )
        embed.set_footer(text="LE SSERAFIM AI HQ • Kazuha Knowledge Engine")
        await status_msg.delete()
        await ctx.reply(embed=embed)
    except Exception as e:
        await status_msg.edit(content=f"❌ Search error: `{e}`")

@bot.command(name="reindex")
async def reindex(ctx):
    """Incrementally scans and re-indexes new or modified files."""
    status_msg = await ctx.reply("🔄 **Kazuha is performing incremental RAG index scan...**")
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await loop.run_in_executor(None, build_index, False)
        stats = await loop.run_in_executor(None, get_rag_stats)
        
        embed = discord.Embed(
            title="✅ RAG Knowledge Vault Synchronized",
            description=f"Scanned **{res['total_files']}** files.\n"
                        f"• Newly indexed/updated: **{res['indexed_files']}** files ({res['new_chunks']} chunks)\n"
                        f"• Unchanged (cached): **{res['skipped_files']}** files\n\n"
                        f"**Total Vault:** {stats['total_chunks']} chunks ({stats['db_size_kb']} KB)",
            color=0x57F287
        )
        embed.set_footer(text="Kazuha • Vector RAG Engine Ready")
        await status_msg.delete()
        await ctx.reply(embed=embed)
    except Exception as e:
        await status_msg.edit(content=f"❌ Reindex error: `{e}`")

@bot.command(name="ragstats")
async def ragstats(ctx):
    """Displays telemetry on the local SQLite vector database."""
    loop = asyncio.get_running_loop()
    try:
        stats = await loop.run_in_executor(None, get_rag_stats)
        embed = discord.Embed(
            title="📊 Kazuha RAG Vault Telemetry",
            description="Persistent vector & hybrid keyword storage in `memory/rag_vault.sqlite`:",
            color=0x00B4D8
        )
        embed.add_field(name="📄 Indexed Documents", value=f"**{stats['total_documents']}** files", inline=True)
        embed.add_field(name="🧩 Vector Chunks", value=f"**{stats['total_chunks']}** chunks", inline=True)
        embed.add_field(name="💾 Database Size", value=f"**{stats['db_size_kb']} KB**", inline=True)
        embed.add_field(name="🔮 Embedding Model", value=stats['embedding_model'], inline=False)
        sample_str = "\n".join(f"• `{d}`" for d in stats.get("sample_docs", []))
        embed.add_field(name="📂 Sample Indexed Files", value=sample_str or "None", inline=False)
        embed.set_footer(text="LE SSERAFIM AI HQ • Kazuha Knowledge Officer")
        await ctx.reply(embed=embed)
    except Exception as e:
        await ctx.reply(f"❌ Error getting RAG stats: `{e}`")


@bot.command(name="ping")
async def ping(ctx):
    latency_ms = round(bot.latency * 1000)
    loop = asyncio.get_running_loop()
    brain = await loop.run_in_executor(None, get_brain_status, "kazuha")
    embed = discord.Embed(
        title="🏓 Pong!",
        description=(
            f"Latency: **{latency_ms}ms**\n"
            f"Brain: **{brain['active_provider']}** · `{brain['active_model']}` (mode: {brain['mode']})"
        ),
        color=0x00B4D8
    )
    await ctx.reply(embed=embed)

@bot.command(name="git")
async def git_command(ctx, subcmd: str = None, flag: str = None):
    """Git Sentinel umbrella command: !git, !git review, !git commit, !git pr"""
    sub = (subcmd or "").lower()
    loop = asyncio.get_running_loop()

    if sub == "review":
        staged_only = flag in ["--staged", "-s", "staged"]
        status_msg = await ctx.reply("🔍 **Kazuha Sentinel is performing architectural code & security review...**")
        async with ctx.typing():
            res = await loop.run_in_executor(None, kazuha_review_changes, staged_only)
        verdict_color = 0x57F287 if res["score"] >= 90 else (0xFEE75C if res["score"] >= 70 else 0xED4245)
        if res.get("security_alerts"):
            verdict_color = 0xED4245
        embed = discord.Embed(
            title="⚔️ Kazuha Sentinel — Code & Security Review",
            description=f"**Verdict:** {res['verdict']} | **Score:** **{res['score']}%**\n"
                        f"🌿 Branch: `{res['info']['branch']}`\n\n"
                        f"{clip(res['review'], REVIEW_MAX)}",
            color=verdict_color
        )
        embed.set_footer(text="Kazuha • Autonomous Git & PR Sentinel")
        await status_msg.delete()
        await ctx.reply(embed=embed)
        return

    if sub == "commit":
        staged_only = flag in ["--staged", "-s", "staged"]
        status_msg = await ctx.reply("✍️ **Kazuha is synthesizing Conventional Commit message...**")
        async with ctx.typing():
            res = await loop.run_in_executor(None, kazuha_create_commit, staged_only)
        await status_msg.delete()
        if not res.get("success"):
            await ctx.reply(f"⚠️ {res.get('error')}")
            return
        embed = discord.Embed(
            title="✍️ Proposed Conventional Commit",
            description=f"```git\n{clip(res['full_message'], REVIEW_MAX)}\n```\n\n*Copy and paste into terminal, or run: `python git_sentinel.py commit --apply`*",
            color=0x00B4D8
        )
        embed.set_footer(text="Kazuha • Conventional Commits v1.0.0")
        await ctx.reply(embed=embed)
        return

    if sub == "pr":
        status_msg = await ctx.reply("📄 **Kazuha is generating GitHub PR description...**")
        async with ctx.typing():
            res = await loop.run_in_executor(None, kazuha_create_pr)
        await status_msg.delete()
        if not res.get("success"):
            await ctx.reply(f"⚠️ {res.get('error')}")
            return
        embed = discord.Embed(
            title=f"📄 GitHub PR Draft: `{clip(res['title'], EMBED_TITLE_MAX - 30)}`",
            description=f"{res['body'][:3800]}",
            color=0x00B4D8
        )
        embed.set_footer(text="Kazuha • GitHub Pull Request Sentinel")
        await ctx.reply(embed=embed)
        return

    # Default: workspace status
    status_text = get_git_status()
    embed = discord.Embed(
        title="📁 Workspace Git Status — Sentinel Telemetry",
        description=f"{status_text}\n\n*Commands: `!review`, `!commit`, `!pr`, `!git review --staged`*",
        color=0x00B4D8
    )
    embed.set_footer(text="Kazuha • Antigravity Workspace Sentinel")
    await ctx.reply(embed=embed)

@bot.command(name="review")
async def review_command(ctx, flag: str = None):
    """Direct alias to !git review"""
    await git_command(ctx, subcmd="review", flag=flag)

@bot.command(name="commit")
async def commit_command(ctx, flag: str = None):
    """Direct alias to !git commit"""
    await git_command(ctx, subcmd="commit", flag=flag)

@bot.command(name="pr")
async def pr_command(ctx):
    """Direct alias to !git pr"""
    await git_command(ctx, subcmd="pr")

@bot.command(name="inspect")
async def inspect(ctx, filename: str = None):
    if not filename:
        await ctx.reply("💻 Please specify a file to inspect! Examples: `!inspect styles.css`, `!inspect index.html`, `!inspect app.js`")
        return

    status_msg = await ctx.reply(f"🔍 **Kazuha is inspecting `{filename}` in `portfolio-site/`...**")

    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await loop.run_in_executor(None, inspect_code_file, filename)
    except Exception as e:
        await status_msg.edit(content=f"❌ Inspection failed: `{e}`")
        return

    if not res["success"]:
        await status_msg.edit(content=f"⚠️ {res['error']}")
        return

    await send_clean_embeds(
        target=ctx,
        title=f"💻 Code & Token Inspection: `{res['filename']}`",
        content=res["critique"],
        color=0x00B4D8,
        footer_text="Kazuha • Lead Frontend Architect Review",
        status_msg_to_delete=status_msg
    )

@bot.command(name="component")
async def component(ctx, *, description: str = None):
    if not description:
        await ctx.reply("🛠️ Please describe the component you want to build! Example: `!component A responsive pricing card with dark mode HSL tokens and hover micro-interaction`")
        return

    status_msg = await ctx.reply(f"✨ **Kazuha is architecting component:** *\"{description[:60]}...\"*")

    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            code_text = await loop.run_in_executor(None, generate_ui_component, description)
    except Exception as e:
        await status_msg.edit(content=f"❌ Component generation failed: `{e}`")
        return

    await status_msg.delete()
    # Send code in chunks or markdown file if large
    if len(code_text) <= 1900:
        await ctx.reply(code_text)
    else:
        # Save to scratch and attach file
        scratch_dir = Path(__file__).parent / "generated_components"
        scratch_dir.mkdir(exist_ok=True)
        file_path = scratch_dir / "Component.jsx"
        file_path.write_text(code_text, encoding="utf-8")
        
        embed = discord.Embed(
            title="🛠️ Generated UI Component",
            description="The component code is attached as a file below (adhering to Swiss Editorial tokens).",
            color=0x00B4D8
        )
        embed.set_footer(text="Kazuha • Frontend & Builder Agent")
        discord_file = discord.File(str(file_path), filename="Component.jsx")
        await ctx.reply(embed=embed, file=discord_file)


@bot.command(name="recall", aliases=["heuristics", "lessons"])
async def recall_command(ctx, *, query: str = None):
    """Searches cognitive episodic memory for heuristics and past failure post-mortems."""
    if not query:
        await ctx.reply("⚠️ Please provide a query to search memory.\nExample: `!recall navigation flexbox` or `!recall coffee copy`")
        return

    if not recall_memories:
        await ctx.reply("❌ Agent-Memory module not reachable from Kazuha engine.")
        return

    results = recall_memories(query, top_k=3, min_score=0.4)
    if not results:
        embed = discord.Embed(
            title="⚡ Cognitive Memory Recall",
            description=f"No past failure patterns or anti-patterns found for **'{query}'**.\nSafe to proceed with development!",
            color=0x00B4D8
        )
        embed.set_footer(text="Self-Improving Agent Engine • Zero Past Regressions")
        await ctx.reply(embed=embed)
        return

    embed = discord.Embed(
        title="⚡ Cognitive Memory Recall — Active Heuristics",
        description=f"Distilled engineering heuristics and anti-patterns matching: **'{query}'**\n*Distilled from past post-mortems in this workspace:*",
        color=0x00B4D8
    )
    for mem, score in results:
        m_id = mem.get("id", "MEM")
        dom = mem.get("domain", "general").upper()
        rule = mem.get("permanent_rule", "")
        symp = mem.get("symptom", "")
        tags = ", ".join(f"`{t}`" for t in mem.get("tags", []))
        field_val = f"• **Permanent Rule**: {rule}"
        if symp:
            field_val += f"\n• **Avoided Failure**: {symp}"
        if tags:
            field_val += f"\n• **Tags**: {tags}"
        embed.add_field(
            name=f"[{m_id}] {dom} (Relevance: {score:.1f})",
            value=field_val,
            inline=False
        )
    embed.set_footer(text="Self-Improving Developer Agent Engine • agent-memory")
    await ctx.reply(embed=embed)


@bot.command(name="rules", aliases=["guidelines"])
async def rules_command(ctx):
    """Displays crystallized workspace rules compiled from past failure post-mortems."""
    rules_path = Path(__file__).parent.parent / ".agents" / "rules" / "learned_rules.md"
    if not rules_path.exists() and crystallize:
        try:
            crystallize()
        except Exception:
            pass

    if not rules_path.exists():
        await ctx.reply("⚠️ No crystallized rules file found in `.agents/rules/learned_rules.md`.")
        return

    try:
        text = rules_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        summary_blocks = []
        current_block = []
        for l in lines:
            if l.startswith("### **[MEM-"):
                if current_block:
                    summary_blocks.append("\n".join(current_block))
                current_block = [l]
            elif current_block and (l.startswith("- **Mandatory Rule**:") or l.startswith("- **Avoided Failure")):
                current_block.append(l)
        if current_block:
            summary_blocks.append("\n".join(current_block))

        preview = "\n\n".join(summary_blocks[:6])
        embed = discord.Embed(
            title="📜 Workspace Learned Rules & Heuristics",
            description=f"Permanent heuristics auto-compiled from real-world post-mortems:\n\n{preview}\n\n*Full rules file: `.agents/rules/learned_rules.md`*",
            color=0x00B4D8
        )
        embed.set_footer(text="Self-Improving Developer Agent Engine • Antigravity Customizations")
        await ctx.reply(embed=embed)
    except Exception as e:
        await ctx.reply(f"❌ Error reading rules: `{e}`")


@bot.command(name="kreflect", aliases=["kazuha_reflect"])
async def kazuha_reflect_command(ctx, *, args: str = None):
    """Logs a frontend post-mortem into episodic memory: domain | [trigger] | symptom | [root_cause] | rule | [tags]"""
    if not args or "|" not in args:
        await ctx.reply("⚠️ Format: `!kreflect domain | symptom | rule` or `!kreflect domain | trigger | symptom | root_cause | rule | [tags]`\nExample:\n`!kreflect css | Nav buttons wrap | Always enforce white-space: nowrap`")
        return

    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 3:
        await ctx.reply("⚠️ Please provide at least 3 parts: `domain | symptom | rule`")
        return

    if len(parts) == 3:
        domain, symptom, rule = parts[0], parts[1], parts[2]
        trigger = f"manual_feedback_by_{ctx.author.name}"
        root_cause = "Observed UI / frontend defect"
        tags = [domain.lower(), "frontend"]
    elif len(parts) == 4:
        domain, symptom, root_cause, rule = parts[0], parts[1], parts[2], parts[3]
        trigger = f"manual_feedback_by_{ctx.author.name}"
        tags = [domain.lower(), "frontend"]
    else:
        domain, trigger, symptom, root_cause, rule = parts[0], parts[1], parts[2], parts[3], parts[4]
        tag_str = parts[5] if len(parts) > 5 else ""
        tags = [t.strip() for t in tag_str.split(",") if t.strip()] or [domain.lower(), "frontend"]

    if not record_reflection:
        await ctx.reply("❌ Reflection engine not reachable from Kazuha engine.")
        return

    try:
        res = record_reflection(
            domain=domain,
            trigger=trigger,
            symptom=symptom,
            root_cause=root_cause,
            permanent_rule=rule,
            tags=tags,
            severity="high"
        )
        if crystallize:
            try:
                crystallize()
            except Exception:
                pass

        status_str = f"✨ Recorded new rule **[{res['id']}]**" if res["status"] == "created" else f"🔄 Reinforced rule **[{res['id']}]** (Observed {res['frequency']}x)"
        embed = discord.Embed(
            title="🧠 Post-Mortem Reflection Recorded",
            description=f"{status_str}\n\n• **Rule**: {rule}\n• **Domain**: `{domain}`\n• **Auto-Crystallized**: Updated `.agents/rules/learned_rules.md`",
            color=0x00B4D8
        )
        embed.set_footer(text="Self-Improving Developer Agent Engine • Episodic Memory")
        await ctx.reply(embed=embed)
    except Exception as e:
        await ctx.reply(f"❌ Failed to record reflection: `{e}`")


@bot.command(name="scout_research", aliases=["digest", "paper", "research", "ai_scout"])
async def cmd_scout_research(ctx):
    """Scouts latest applied AI papers, generates executive technical briefing & logs to Obsidian."""
    status_msg = await ctx.reply("💻 **Kazuha is scouting latest Applied AI research & analyzing architecture...**")
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, execute_research_scout, "applied-ai", True)
        if not res.get("success"):
            await status_msg.edit(content="❌ Research scout encountered an error.")
            return

        await status_msg.delete()
        await send_clean_embeds(
            target=ctx,
            title=f"🔬 Applied AI Research Digest — {res['date_str']}",
            content=res["digest_text"],
            color=0x00B4D8,
            footer_text=f"Kazuha • Lead Frontend Architect & Applied AI Specialist • {len(res['papers'])} papers • Obsidian Synced"
        )
    except Exception as e:
        await status_msg.edit(content=f"❌ Error during research scout: `{e}`")


if __name__ == "__main__":
    print("🚀 Starting Kazuha (Frontend Agent)...", flush=True)
    if not TOKEN:
        print("❌ Error: KAZUHA_BOT_TOKEN not found in .env file!", flush=True)
        exit(1)
    bot.run(TOKEN)

