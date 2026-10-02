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
TOKEN = os.getenv("CHAEWON_BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")

if not TOKEN:
    print("❌ Error: CHAEWON_BOT_TOKEN not found in .env file!", flush=True)
    exit(1)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

from scout import analyze_and_tailor, extract_job_meta, consult_career, answer_screening_questions, MEMORY_DIR
from web_tools import find_url_in_text, scrape_job_url, fetch_live_remote_jobs, get_portal_search_links
from discord_utils import send_clean_embeds
from pdf_engine import compile_master_resume, OWNER_EMAIL
from llm_client import get_brain_status

LAST_REBUILD_DATE_FILE = MEMORY_DIR / "last_resume_rebuild_date.txt"

async def post_weekly_resume_rebuild_if_due(force: bool = False, target_channel=None):
    """
    Cron 3: Sunday 22:00 PM Master Vector Resume Rebuild & Archive.
    Recompiles single-page ATS vector PDF, validates size, updates portfolio-site & Obsidian.
    """
    from datetime import datetime
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # Sunday is weekday 6
    if not force:
        if now.weekday() != 6 or now.hour < 22:
            return
        if LAST_REBUILD_DATE_FILE.exists():
            last_date = LAST_REBUILD_DATE_FILE.read_text(encoding="utf-8").strip()
            if last_date == today_str:
                return

    print(f"⭐ [Chaewon] Executing Weekly Vector Resume Rebuild (Date: {today_str})...", flush=True)
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, compile_master_resume, True, True, True)
        if not res.get("success"):
            print(f"⚠️ [Chaewon] Resume compilation failed: {res.get('error')}", flush=True)
            return

        pdf_path = res["pdf_path"]
        size_kb = res["size_kb"]

        # Channel resolution
        channel = target_channel
        if not channel:
            for guild in bot.guilds:
                for ch in guild.text_channels:
                    if ch.name.lower() in ["application-docs", "job-tailoring", "command-center"]:
                        channel = ch
                        break
                if channel:
                    break

        if channel:
            embed = discord.Embed(
                title="📄 Sunday Master Vector Resume Rebuild & Archive (Cron 3)",
                description=(
                    f"**Hans Aaron Laureles — Applied AI Engineer & Full-Stack Builder**\n\n"
                    f"• **Artifact:** `{pdf_path.name}` ({size_kb} KB)\n"
                    f"• **Text:** Selectable vector text (system sans-serif stack)\n"
                    f"• **Geometry:** 1-page Letter layout\n"
                    f"• **Portfolio Sync:** `portfolio-site/Hans_Laureles_Resume.pdf` {'✅' if res.get('portfolio_pdf') else '⏭️ skipped'}\n"
                    f"• **Weekly Archive:** {('`' + res['archive_pdf'].name + '` ✅') if res.get('archive_pdf') else '⚠️ not archived'}\n"
                    f"• **Obsidian Daily Log:** {'Session logged to vault ✅' if res.get('obsidian_synced') else '⚠️ not logged'}"
                ),
                color=0x57F287
            )
            embed.set_footer(text="Chaewon • Weekly Vector Resume Engine • Production Ready")
            file = discord.File(str(pdf_path), filename=pdf_path.name)
            await channel.send(embed=embed, file=file)
            print(f"✅ [Chaewon] Delivered rebuilt resume to #{channel.name}!", flush=True)

        LAST_REBUILD_DATE_FILE.write_text(today_str, encoding="utf-8")
    except Exception as e:
        print(f"⚠️ [Chaewon] Weekly resume rebuild exception: {e}", flush=True)

@tasks.loop(minutes=30)
async def scheduled_resume_loop():
    await post_weekly_resume_rebuild_if_due()

@bot.event
async def on_ready():
    print("=" * 60, flush=True)
    print(f"⭐ CHAEWON is ONLINE! Logged in as {bot.user.name} (ID: {bot.user.id})", flush=True)
    print(f"🌐 Connected to {len(bot.guilds)} server(s):", flush=True)
    for guild in bot.guilds:
        print(f"   - {guild.name} (ID: {guild.id})", flush=True)
    print("=" * 60, flush=True)

    activity = discord.Activity(type=discord.ActivityType.watching, name="career & applications 💼 (!help)")
    await bot.change_presence(status=discord.Status.online, activity=activity)

    if not scheduled_resume_loop.is_running():
        scheduled_resume_loop.start()

async def handle_tailoring(target_message, job_text: str = "", attachments = None):
    """Core tailoring engine callable by !tailor command or automatic URL detection."""
    if attachments:
        attachment = attachments[0]
        if attachment.filename.endswith((".txt", ".md")):
            job_bytes = await attachment.read()
            job_text = job_bytes.decode("utf-8", errors="ignore")
    
    if not job_text:
        await target_message.reply("⭐ Drop a job link, paste a job description, or attach a `.txt` file!")
        return

    company = "Target Company"
    role = "Design / Engineering Role"

    if ":::" in job_text:
        header_part, body_part = job_text.split(":::", 1)
        job_text = body_part.strip()
        header_clean = header_part.strip()
        if " at " in header_clean:
            r_part, c_part = header_clean.split(" at ", 1)
            role = r_part.strip()
            company = c_part.strip()
        else:
            role = header_clean

    loop = asyncio.get_running_loop()
    detected_url = find_url_in_text(job_text)
    
    if detected_url:
        status_msg = await target_message.reply(f"🌐 **Detected Job URL!**\nScraping posting from: `<{detected_url}>`...")
        scrape_res = await scrape_job_url(detected_url)
        if not scrape_res["success"]:
            await status_msg.edit(content=f"⚠️ **Could not scrape that webpage:** {scrape_res['error']}\n💡 *Tip: If this is a login-walled site (like LinkedIn), simply copy and paste the job description text into Discord!*")
            return
        
        job_text = scrape_res["content"]
        if company in ["Target Company", ""] or role in ["Design / Engineering Role", ""]:
            await status_msg.edit(content="🔍 Identifying Job Title & Company Name from posting...")
            extracted_role, extracted_company = await loop.run_in_executor(None, extract_job_meta, job_text)
            if extracted_role and extracted_role != "Design / Engineering Role":
                role = extracted_role
            if extracted_company and extracted_company != "Target Company":
                company = extracted_company

        await status_msg.edit(content=f"📄 **Scraped Job:** `{role}` at **{company}**\n⭐ **Chaewon is analyzing against Hans's CS degree, ROC internship & portfolio...**")
    else:
        if company in ["Target Company", ""] or role in ["Design / Engineering Role", ""]:
            status_msg = await target_message.reply("🔍 **Identifying Job Title & Company Name from text...**")
            extracted_role, extracted_company = await loop.run_in_executor(None, extract_job_meta, job_text)
            if extracted_role and extracted_role != "Design / Engineering Role":
                role = extracted_role
            if extracted_company and extracted_company != "Target Company":
                company = extracted_company
            await status_msg.edit(content=f"⭐ **Target Identified:** `{role}` at **{company}**\n*(Analyzing against Hans's CS degree, ROC internship, and portfolio projects)*")
        else:
            status_msg = await target_message.reply(f"⭐ **Chaewon is analyzing the job posting for `{role}` at **{company}**...**\n*(Comparing against Hans's CS degree, ROC internship, and portfolio projects)*")

    try:
        async with target_message.channel.typing():
            result = await loop.run_in_executor(None, analyze_and_tailor, job_text, company, role)
    except Exception as e:
        await status_msg.edit(content=f"❌ Error during analysis: `{e}`")
        return

    score = result["score"]
    color = 0x57F287 if score >= 85 else (0xFEE75C if score >= 70 else 0xED4245)

    embed = discord.Embed(
        title=f"🎯 Application Package: {result['role']} @ {result['company']}",
        description=f"### Fit Score: **{score}%** Match\n"
                    f"Generated tailored resume bullet points, custom cover letter, and portfolio pitch strategy.",
        color=color
    )
    embed.add_field(name="📁 Saved To Archive", value=f"`{result['filename']}`", inline=False)
    if result.get("resume_pdf") and Path(result["resume_pdf"]).exists():
        embed.add_field(name="📄 ATS PDF Resume", value=f"`{Path(result['resume_pdf']).name}`", inline=True)
    if result.get("cover_pdf") and Path(result["cover_pdf"]).exists():
        embed.add_field(name="✉️ Cover Letter PDF", value=f"`{Path(result['cover_pdf']).name}`", inline=True)
    embed.set_footer(text="Delivered by Chaewon • React ✅ to Approve & Vault in #application-docs, or ❌ to Discard.")

    saved_file_path = Path(result["saved_file"])
    files_to_send = []
    if saved_file_path.exists():
        files_to_send.append(discord.File(str(saved_file_path), filename=result["filename"]))
    if result.get("resume_pdf") and Path(result["resume_pdf"]).exists():
        files_to_send.append(discord.File(str(result["resume_pdf"]), filename=Path(result["resume_pdf"]).name))
    if result.get("cover_pdf") and Path(result["cover_pdf"]).exists():
        files_to_send.append(discord.File(str(result["cover_pdf"]), filename=Path(result["cover_pdf"]).name))

    await status_msg.delete()
    sent_reply = await target_message.reply(embed=embed, files=files_to_send)

    # Add 1-click approval reaction buttons for Hans
    try:
        await sent_reply.add_reaction("✅")
        await sent_reply.add_reaction("❌")
    except Exception as e:
        print(f"⚠️ [Chaewon] Could not add reactions: {e}", flush=True)

    # Register proposal state so approving with ✅ logs it and dispatches both PDFs to #application-docs
    try:
        from sakura_engine import save_pending_proposal
        save_pending_proposal(sent_reply.id, {
            "company": result["company"],
            "role": result["role"],
            "score": score,
            "filename": result.get("filename", ""),
            "saved_file": str(result.get("saved_file", "")),
            "resume_pdf": str(result.get("resume_pdf", "")) if result.get("resume_pdf") else "",
            "cover_pdf": str(result.get("cover_pdf", "")) if result.get("cover_pdf") else ""
        })
    except Exception as e:
        print(f"⚠️ [Chaewon] Could not save pending proposal: {e}", flush=True)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    channel_name = getattr(message.channel, "name", "").lower()
    is_mentioned = bot.user in message.mentions
    is_home_channel = any(k in channel_name for k in ["job-tailoring", "application-docs", "commands-chat"])
    if not (is_home_channel or is_mentioned):
        return

    clean_content = message.content.replace(f"<@{bot.user.id}>", "").strip()
    has_url = bool(find_url_in_text(clean_content))
    has_attachment = bool(message.attachments)
    is_job_posting = (
        len(clean_content) > 160 or 
        any(k in clean_content.lower() for k in ["responsibilities", "qualifications", "requirements", "looking for", "job description", "years of experience"])
    )

    # Automatic tailoring if URL, attachment, or job spec pasted
    if has_url or has_attachment or is_job_posting:
        await handle_tailoring(message, job_text=clean_content, attachments=message.attachments)
        return

    if not clean_content:
        await message.reply("⭐ Yes, Hans? Drop a job link or ask me any career question!")
        return

    # Natural career coaching conversation
    loop = asyncio.get_running_loop()
    async with message.channel.typing():
        try:
            advice = await loop.run_in_executor(None, consult_career, clean_content)
            await send_clean_embeds(
                target=message,
                title="⭐ Career Guidance from Chaewon",
                content=advice,
                color=0xF1C40F,
                footer_text="Chaewon • Career Copilot • LE SSERAFIM AI HQ"
            )
        except Exception as e:
            await message.reply(f"❌ Could not consult Chaewon: `{e}`")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="⭐ Chaewon — Career & Autonomous Job Scout Agent",
        description="I'm your tactical Career Agent. I scout live opportunities across **LinkedIn**, **Indeed**, and **JobStreet**, score fit %, and compile ready-to-submit ATS application packages:",
        color=0xF1C40F
    )
    embed.add_field(
        name="📡 `!scout [query]`",
        value="Scans **LinkedIn**, **Indeed**, and **JobStreet** (Philippines & Remote).\n• Examples: `!scout ai`, `!scout python`, `!scout fullstack`, `!scout remote`",
        inline=False
    )
    embed.add_field(
        name="🎯 `!tailor [URL or text]`",
        value="1-Command Application Tailoring! Scrapes posting, calculates ATS match score, and outputs:\n• Tailored 1-page ATS Resume PDF\n• Tailored Executive Cover Letter PDF\n• Strategic portfolio talking points\n*(Note: Use `!apply` in `#command-center` for the full multi-agent squad pipeline)*",
        inline=False
    )
    embed.add_field(
        name="📄 `!pdf [target]`",
        value="Instantly compiles a general or role-targeted ATS single-page resume PDF for Hans.",
        inline=False
    )
    embed.add_field(
        name="👤 `!profile`",
        value="Displays active AI Systems Engineer profile, master competencies, and case studies.",
        inline=False
    )
    embed.add_field(
        name="📜 `!history`",
        value="Lists your recently tailored applications and fit scores.",
        inline=False
    )
    embed.add_field(
        name="🏓 `!ping`",
        value="Check latency and bot connection status.",
        inline=False
    )
    embed.set_footer(text="LE SSERAFIM AI HQ • Chaewon (Career & Application Scout)")
    await ctx.reply(embed=embed)

@bot.command(name="ping")
async def ping(ctx):
    latency_ms = round(bot.latency * 1000)
    loop = asyncio.get_running_loop()
    brain = await loop.run_in_executor(None, get_brain_status, "chaewon")
    embed = discord.Embed(
        title="🏓 Pong!",
        description=(
            f"Latency: **{latency_ms}ms**\n"
            f"Brain: **{brain['active_provider']}** · `{brain['active_model']}` (mode: {brain['mode']})"
        ),
        color=0xF1C40F
    )
    await ctx.reply(embed=embed)

@bot.command(name="profile")
async def profile(ctx):
    resume_path = MEMORY_DIR / "master_resume.md"
    if not resume_path.exists():
        await ctx.reply("⚠️ Master resume file not found in `memory/master_resume.md`.")
        return
    
    embed = discord.Embed(
        title="👤 Candidate Profile",
        description="**Title:** AI Systems Engineer & Full-Stack Builder\n**Core Value:** Architecting autonomous agent systems. Engineering production AI with executive design craft.\n**Education:** BS Computer Science",
        color=0xF1C40F
    )
    embed.add_field(
        name="💼 Experience",
        value="• **LSFM AI HQ:** Lead AI Systems Architect (5-agent swarm, Groq + Gemini routing, OAuth2 triage)\n• **Mobile Capstone:** Lead Systems Architect (React Native, AWS Lambda, DynamoDB, Redis)\n• **Frontend Internship:** UI/UX & Frontend Engineer Intern (Figma, React, Tailwind)",
        inline=False
    )
    embed.add_field(
        name="🚀 Flagship Projects",
        value="• **LSFM AI HQ:** Autonomous 5-agent swarm, Groq + Gemini routing & hybrid RAG\n• **Lumina Analytics:** Enterprise AI observability & SaaS telemetry engine\n• **Vellum OS:** Mental wellness & ambient human-AI reflection companion\n• **FinTrack:** Algorithmic personal finance & sub-3s categorization\n• **Aura Coffee & Kitchen:** Artisanal commerce web store & Philippine payment rails\n• **Cognitive Memory Core:** Zero-dependency BM25 self-improving agent flywheel",
        inline=False
    )
    embed.add_field(
        name="🛠️ Capabilities Triad",
        value="• **AI & Agents:** Multi-Agent Swarms, Groq, Gemini, Ollama (local), Hybrid RAG, Tool Calling\n• **Systems & Cloud:** Python (asyncio), Google Workspace API, Headless Edge, AWS, Redis\n• **Interface Craft:** React, TypeScript, Next.js, Design Tokens, Tailwind, WCAG AAA",
        inline=False
    )
    embed.set_footer(text="Memory loaded from lsfm-swarm/memory/master_resume.md")
    await ctx.reply(embed=embed)

@bot.command(name="history")
async def history(ctx):
    log_path = MEMORY_DIR / "applications_log.md"
    if not log_path.exists():
        await ctx.reply("No applications recorded yet!")
        return

    content = log_path.read_text(encoding="utf-8")
    lines = [l for l in content.split("\n") if l.strip().startswith("|") and not l.startswith("| #") and not l.startswith("|---") and not l.startswith("| -")]
    
    if not lines:
        await ctx.reply("No applications logged yet. Try tailoring one with `!tailor <url>` or dispatching the squad with `!apply <url>`!")
        return

    embed = discord.Embed(
        title="📜 Recent Tailored Applications",
        description=f"Total tailored: **{len(lines)}**",
        color=0xF1C40F
    )
    for line in lines[-5:]:
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if len(parts) >= 6:
            date, company, role, _, _, status = parts[:6]
            embed.add_field(
                name=f"{company} — {role}",
                value=f"📅 {date} | Status: **{status}**",
                inline=False
            )
    await ctx.reply(embed=embed)

@bot.command(name="scout", aliases=["radar", "jobs", "findjobs"])
async def cmd_scout(ctx, *, query: str = "ai"):
    """Scans LinkedIn, Indeed, and JobStreet for opportunities and scores them against Hans's profile."""
    status_msg = await ctx.reply(f"🐯 **Chaewon is scanning LinkedIn, Indeed & JobStreet for `{query}` opportunities...**")
    try:
        loc = "Philippines"
        if "remote" in query.lower():
            loc = "Remote"

        jobs = await fetch_live_remote_jobs(query=query, location=loc, limit=5)
        portals = get_portal_search_links(query=query, location=loc)

        embed = discord.Embed(
            title=f"🎯 Live Job Radar // {query.upper()} ({loc})",
            description=f"Targeting **LinkedIn**, **Indeed**, and **JobStreet** · Scored for **Hans Aaron Laureles**.\n"
                        f"*Type `!apply <job_url>` in #command-center for the squad pipeline, or `!tailor <job_url>` to tailor directly!*",
            color=0xF1C40F
        )

        if jobs:
            for i, j in enumerate(jobs, 1):
                score = j["score"]
                score_badge = "🔥" if score >= 85 else "⭐"
                source_badge = f"`{j.get('source', 'Web')}`"
                geo = j.get("geo", loc)
                salary = j.get("salary", "Competitive")
                field_val = (
                    f"🏢 **{j['company']}** | 📍 {geo} | 💰 {salary} | Source: {source_badge}\n"
                    f"🔗 **Posting:** [View Job on {j.get('source', 'Web')}]({j['url']})\n"
                    f"⚡ **Tailor Directly:** `!tailor {j['url']}` | **Squad Apply:** `!apply {j['url']}`"
                )
                embed.add_field(
                    name=f"{score_badge} [{score}% FIT] #{i}. {j['title']}",
                    value=field_val,
                    inline=False
                )
        else:
            embed.add_field(
                name="⚠️ Live Stream Notice",
                value=f"No direct cards returned this moment for `{query}`. Explore the pre-filtered radar links below!",
                inline=False
            )

        # 1-Click Direct Portal Search Links
        portal_text = (
            f"💼 **[Search on JobStreet Philippines]({portals['jobstreet']['search_url']})**\n"
            f"🔍 **[Search on Indeed Philippines]({portals['indeed']['url']})**\n"
            f"🌐 **[Search on LinkedIn Jobs]({portals['linkedin']['url']})**"
        )
        embed.add_field(
            name="🚀 1-Click Search Radar (Pre-Filtered)",
            value=portal_text,
            inline=False
        )

        embed.set_footer(text="LE SSERAFIM AI HQ • LinkedIn, JobStreet & Indeed Radar")
        await status_msg.delete()
        await ctx.reply(embed=embed)
    except Exception as e:
        await status_msg.edit(content=f"❌ Error during job scouting: `{e}`")

@bot.command(name="tailor")
async def cmd_tailor(ctx, *, args: str = None):
    """1-Command Tailored Application Generator. Scrapes posting and produces 1-page ATS PDF + Cover Letter."""
    await handle_tailoring(ctx, job_text=args or "", attachments=ctx.message.attachments)

@bot.command(name="pdf")
async def cmd_pdf(ctx, *, target: str = "General"):
    """Instantly generates and uploads an ATS-compliant PDF Resume & Cover Letter."""
    loop = asyncio.get_running_loop()
    msg = await ctx.reply(f"⭐ **Chaewon is compiling an ATS-compliant PDF Resume...**\n*(Target / Role: `{target}`)*")
    
    try:
        from pdf_engine import generate_tailored_pdf_package, load_master_resume_context
        context = load_master_resume_context()
        cand_name = context.get("name") or os.getenv("OWNER_NAME", "Candidate Name")
        cand_loc = context.get("location") or os.getenv("OWNER_LOCATION", "Remote / Metro Area")
        cand_email = context.get("email") or os.getenv("OWNER_EMAIL", OWNER_EMAIL)
        cand_portfolio = context.get("portfolio_url") or os.getenv("PORTFOLIO_URL", "https://example.com")
        cand_summary = context.get("summary") or os.getenv("OWNER_SUMMARY", "Applied AI Engineer & Full-Stack Builder")
        tailored_dict = {
            "name": cand_name,
            "title": target if target != "General" else "Applied AI Engineer & Full-Stack Builder",
            "company": target,
            "role": target,
            "location": cand_loc,
            "email": cand_email,
            "portfolio_url": cand_portfolio,
            "portfolio_display": os.getenv("PORTFOLIO_DISPLAY", "example.com"),
            "summary": cand_summary
        }
        res = await loop.run_in_executor(None, generate_tailored_pdf_package, tailored_dict, target, target)
        if res.get("error"):
            await msg.edit(content=f"⚠️ Cannot generate PDF package: {res['error']}")
            return
        
        files_to_send = []
        if res.get("resume_pdf") and Path(res["resume_pdf"]).exists():
            files_to_send.append(discord.File(str(res["resume_pdf"]), filename=Path(res["resume_pdf"]).name))
        if res.get("cover_pdf") and Path(res["cover_pdf"]).exists():
            files_to_send.append(discord.File(str(res["cover_pdf"]), filename=Path(res["cover_pdf"]).name))
            
        embed = discord.Embed(
            title=f"📄 ATS-Compliant PDF Package Ready!",
            description=f"Compiled by ⭐ Chaewon for **{target}**.\n\n"
                        f"• **Format:** Single-page executive layout (Swiss-inspired typography)\n"
                        f"• **ATS:** Selectable vector text & semantic headings\n"
                        f"• **Focus:** AI Systems, Multi-Agent Swarms & Full-Stack Architecture",
            color=0x57F287
        )
        embed.set_footer(text="Ready to download and submit directly to job portals.")
        await msg.delete()
        await ctx.reply(embed=embed, files=files_to_send)
    except Exception as e:
        await msg.edit(content=f"⚠️ Failed to generate PDF: `{e}`")


@bot.command(name="screen", aliases=["qa", "question", "screening"])
async def cmd_screen(ctx, *, questions: str = None):
    """Generates high-converting ATS answers to employer screening questionnaire questions."""
    if not questions and ctx.message.reference:
        try:
            ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            questions = ref_msg.content
        except Exception:
            pass

    if not questions:
        await ctx.reply(
            "📋 **Chaewon Screening Questionnaire Solver**\n\n"
            "Usage: `!screen <question 1> | <question 2> | ...`\n"
            "*(Or paste all questions in one message)*\n\n"
            "*Example:*\n"
            "`!screen Do you have 2+ years of Website Development experience?`\n\n"
            "Chaewon will draft punchy, ATS-optimized answers (under 1,500 chars) tailored to Hans's real projects!"
        )
        return

    status_msg = await ctx.reply("⭐ **Chaewon is synthesizing tailored answers for these employer questions...**")
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            response_text = await loop.run_in_executor(None, answer_screening_questions, questions)
        await status_msg.delete()
        await send_clean_embeds(
            target=ctx,
            title="📋 Employer Screening Questionnaire — Tailored Answers",
            content=response_text,
            color=0xF1C40F,
            footer_text="Chaewon • Career Copilot • Ready to copy & paste into Indeed / JobStreet / LinkedIn"
        )
    except Exception as e:
        await status_msg.edit(content=f"❌ Error generating answers: `{e}`")


@bot.command(name="rebuild_resume", aliases=["resume", "compile_resume", "master_resume"])
async def cmd_rebuild_resume(ctx):
    """Compiles Hans's 1-page Master ATS Vector Resume PDF & syncs with Portfolio & Obsidian."""
    msg = await ctx.reply("⭐ **Chaewon is rebuilding Hans's Master ATS Vector Resume...**")
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, compile_master_resume, True, True, True)
        if not res.get("success"):
            await msg.edit(content=f"❌ Error compiling resume: {res.get('error')}")
            return
        
        pdf_path = res["pdf_path"]
        size_kb = res["size_kb"]
        embed = discord.Embed(
            title="📄 Master ATS Vector Resume Rebuilt & Synchronized",
            description=(
                f"**Hans Aaron Laureles — Applied AI Engineer & Full-Stack Builder**\n\n"
                f"• **Artifact:** `{pdf_path.name}` ({size_kb} KB)\n"
                f"• **Text:** Selectable vector text (system sans-serif stack)\n"
                f"• **Geometry:** 1-page Letter layout\n"
                f"• **Portfolio Sync:** `portfolio-site/Hans_Laureles_Resume.pdf` {'✅' if res.get('portfolio_pdf') else '⏭️ skipped'}\n"
                f"• **Weekly Archive:** {('`' + res['archive_pdf'].name + '` ✅') if res.get('archive_pdf') else '⚠️ not archived'}\n"
                f"• **Obsidian Vault:** {'Daily session log written ✅' if res.get('obsidian_synced') else '⚠️ not logged'}"
            ),
            color=0x57F287
        )
        embed.set_footer(text="Chaewon • Weekly Vector Resume Engine • Production Ready")
        file = discord.File(str(pdf_path), filename=pdf_path.name)
        await msg.delete()
        await ctx.reply(embed=embed, file=file)
    except Exception as e:
        await msg.edit(content=f"❌ Error rebuilding resume: `{e}`")


if __name__ == "__main__":
    print("🚀 Starting Chaewon (Career Agent)...", flush=True)
    bot.run(TOKEN)

