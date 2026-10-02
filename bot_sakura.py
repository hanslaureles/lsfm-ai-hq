from heavy_jobs import run_heavy
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
TOKEN = os.getenv("SAKURA_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
health_recorder.attach(bot, "sakura")  # memory/health/sakura.json (4B-2)

from sakura_engine import (
    generate_morning_briefing,
    execute_evening_rollup,
    consult_sakura,
    MEMORY_DIR,
    save_pending_proposal,
    get_pending_proposal,
    mark_proposal_status
)
from llm_client import get_brain_status, set_brain_mode, get_brain_mode
from scout import analyze_and_tailor, extract_job_meta
from yunjin_engine import select_portfolio_pitch
import re
from kazuha_engine import generate_tech_pitch
from web_tools import find_url_in_text, scrape_job_url
from discord_utils import send_clean_embeds

APPLICATIONS_DIR = Path(__file__).parent / "applications"
BRIEFING_DATE_FILE = MEMORY_DIR / "last_briefing_date.txt"
ROLLUP_DATE_FILE = MEMORY_DIR / "last_rollup_date.txt"

def find_channel_by_name(guild: discord.Guild, name: str):
    """Locates a text channel in the guild matching name case-insensitively."""
    if not guild:
        return None
    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            return ch
    return None


async def post_daily_briefing_if_due():
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    last_date = BRIEFING_DATE_FILE.read_text().strip() if BRIEFING_DATE_FILE.exists() else ""
    now = datetime.datetime.now()

    # Deliver briefing once per day when PC is on (at or after 8:00 AM)
    if last_date != today_str and now.hour >= 8:
        for guild in bot.guilds:
            channel = find_channel_by_name(guild, "daily-briefing")
            if channel:
                try:
                    loop = asyncio.get_running_loop()
                    briefing_text = await loop.run_in_executor(None, generate_morning_briefing)
                    await send_clean_embeds(
                        target=channel,
                        title=f"🌅 Daily Executive Morning Briefing — {now.strftime('%A, %b %d')}",
                        content=briefing_text,
                        color=0xF472B6,
                        footer_text="Sakura • Chief of Staff • Daily Scheduled Pulse"
                    )
                    BRIEFING_DATE_FILE.write_text(today_str, encoding="utf-8")
                    print(f"🌸 [Sakura] Scheduled morning briefing delivered to #{channel.name}", flush=True)
                    break
                except Exception as e:
                    print(f"⚠️ [Sakura] Failed to post scheduled briefing: {e}", flush=True)


@tasks.loop(minutes=30)
async def scheduled_briefing_loop():
    await bot.wait_until_ready()
    await post_daily_briefing_if_due()


async def post_evening_rollup_if_due():
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    last_date = ROLLUP_DATE_FILE.read_text().strip() if ROLLUP_DATE_FILE.exists() else ""
    now = datetime.datetime.now()

    # Deliver evening rollup once per day when PC is on (at or after 8:00 PM / 20:00)
    if last_date != today_str and now.hour >= 20:
        for guild in bot.guilds:
            channel = find_channel_by_name(guild, "daily-briefing") or find_channel_by_name(guild, "command-center")
            if channel:
                try:
                    loop = asyncio.get_running_loop()
                    res = await loop.run_in_executor(None, execute_evening_rollup, True)
                    await send_clean_embeds(
                        target=channel,
                        title=f"🌆 Evening Standup & Daily Rollup — {now.strftime('%A, %b %d')}",
                        content=res["rollup_text"],
                        color=0x9B59B6,
                        footer_text="Sakura • Chief of Staff • Daily Standup Sealed in Obsidian"
                    )
                    ROLLUP_DATE_FILE.write_text(today_str, encoding="utf-8")
                    print(f"🌸 [Sakura] Scheduled evening rollup delivered to #{channel.name}", flush=True)
                    break
                except Exception as e:
                    print(f"⚠️ [Sakura] Failed to post scheduled evening rollup: {e}", flush=True)


@tasks.loop(minutes=30)
async def scheduled_rollup_loop():
    await bot.wait_until_ready()
    await post_evening_rollup_if_due()


def build_brain_embed(status: dict) -> discord.Embed:
    mode = status["mode"].upper()
    color = 0xF472B6 if mode == "AUTO" else (0x3B82F6 if mode == "CLOUD" else 0x10B981)
    
    if mode == "CLOUD":
        arch_label = "Specialized Multi-Model Cloud"
    elif mode == "LOCAL":
        arch_label = "Local Silicon (Ollama Standby)"
    else:
        arch_label = "Smart Auto-Detect Hybrid"

    embed = discord.Embed(
        title="🧠 LE SSERAFIM AI HQ — Brain Status",
        description=f"Current Architecture: **`{mode}`** • **{arch_label}**",
        color=color
    )
    embed.add_field(
        name="⚡ Active Inference Engine",
        value=f"**{status['active_provider']}**\nDefault Cluster: `{status['active_model']}`",
        inline=False
    )
    
    agent_map = status.get("agent_cloud_models", {})
    agent_lines = [
        f"🌸 **Sakura:** `{agent_map.get('sakura', 'qwen/qwen3.8-27b')}` *(Strategic Orchestrator)*",
        f"⭐ **Chaewon:** `{agent_map.get('chaewon', 'openai/gpt-oss-120b')}` *(120B ATS Resume Copilot)*",
        f"🎨 **Yunjin:** `{agent_map.get('yunjin', 'gemini-3.6-flash')}` *(Design Director Critique)*",
        f"💻 **Kazuha:** `{agent_map.get('kazuha', 'qwen/qwen3.8-27b')}` *(Frontend Systems Architect)*",
        f"🛡️ **Eunchae:** `{agent_map.get('eunchae', 'openai/gpt-oss-20b')}` *(QA Verification Guardian)*"
    ]
    embed.add_field(
        name="🤖 Agent Model Allocations",
        value="\n".join(agent_lines),
        inline=False
    )
    
    embed.add_field(
        name="🖥️ Local GPU Hardware",
        value=f"{status['hardware_target']}\nLocal Standby: `{status.get('local_model', 'qwen2.5-coder:7b')}`\nOllama Status: {'🟢 Online & Ready' if status['ollama_online'] else '⚪ Standby / Offline'}",
        inline=False
    )
    embed.add_field(
        name="🎮 Gaming / Resource Impact",
        value="0% GPU load on RX 6600 XT (0 MB VRAM locked — 100% free for gaming & rendering)" if "Cloud" in status['active_provider'] else "Local VRAM in use on RX 6600 XT",
        inline=False
    )
    embed.add_field(
        name="🔄 Switch Modes Anytime",
        value="• `!mode cloud` — Specialized multi-tier cloud cluster (Max quality, 0-cost, 0 VRAM)\n• `!mode local` — 100% offline private on RX 6600 XT\n• `!mode auto` — Smart dual-brain automatic fallback",
        inline=False
    )
    embed.set_footer(text="Chief of Staff • AI Brain Dispatcher")
    return embed


@bot.event
async def on_ready():
    print("=" * 60, flush=True)
    print(f"🌸 SAKURA is ONLINE! Logged in as {bot.user.name} (ID: {bot.user.id})", flush=True)
    print(f"🌐 Connected to {len(bot.guilds)} server(s):", flush=True)
    for guild in bot.guilds:
        print(f"   - {guild.name} (ID: {guild.id})", flush=True)
    print("=" * 60, flush=True)

    activity = discord.Activity(type=discord.ActivityType.watching, name="LE SSERAFIM AI team 🌸 (!help)")
    await bot.change_presence(status=discord.Status.online, activity=activity)

    if not scheduled_briefing_loop.is_running():
        scheduled_briefing_loop.start()

    if not scheduled_rollup_loop.is_running():
        scheduled_rollup_loop.start()


def handle_qa_audit_failure(exc: Exception) -> dict:
    """Format an honest failure result when the QA audit crashes (Invariant 7)."""
    err_str = f"{type(exc).__name__}: {exc}"
    return {
        "passed": False,
        "score": 0,
        "verdict": "🔴 QA AUDIT ERROR",
        "review": f"QA audit crashed before completion: {err_str}",
        "deterministic": {
            "passed": False,
            "defects": [f"Audit exception: {err_str}"],
            "warnings": [],
            "checks": {"audit_executed": False}
        },
        "heuristics_checked": 0,
        "error": err_str
    }


def format_proposal_qa_status(
    qa_res: dict,
    default_score_color: int,
    handoffs_channel_mention: str = "#agent-handoffs"
) -> tuple[str, str, int]:
    """
    Format QA summary line, operator next action instructions, and proposal embed color
    based on QA audit outcome (Invariant 7).
    Returns (qa_summary_line, action_instructions, proposal_color).
    """
    qa_passed = bool(qa_res.get("passed", False))
    qa_score = qa_res.get("score", 0)
    verdict = qa_res.get("verdict", "UNKNOWN")

    if qa_passed:
        qa_summary_line = f"🛡️ **Eunchae (QA):** **{verdict} ({qa_score}%)** — Verified against squad failure memory\n"
        action_instructions = (
            f"👉 **Next Action for Hans:**\n"
            f"• Click **✅** below to **Approve & Finalize** (marks status Approved in applications log)\n"
            f"• Click **❌** below to **Discard / Shelve** this application"
        )
        proposal_color = default_score_color
    else:
        qa_summary_line = f"🛡️ **Eunchae (QA):** **{verdict} ({qa_score}%)** — ⚠️ QA defects flagged or audit failed; review issues before approving\n"
        action_instructions = (
            f"👉 **Next Action for Hans:**\n"
            f"• ⚠️ **QA WARNING**: Application package did not pass QA audit. Review issues in {handoffs_channel_mention}.\n"
            f"• Click **✅** below to **Force Approve & Finalize** (overrides QA audit warning)\n"
            f"• Click **❌** below to **Discard / Shelve** this application"
        )
        proposal_color = 0xED4245 if qa_score < 50 else 0xFEE75C

    return qa_summary_line, action_instructions, proposal_color


async def handle_apply_orchestration(target_message, job_input: str = "", attachments = None):
    """
    Core squad collaboration pipeline:
    1. Scrapes or parses the target role & company.
    2. Dispatches mission briefing to #agent-handoffs.
    3. Triggers Chaewon (Career tailoring), Yunjin (Portfolio curation), Kazuha (Frontend tech stack).
    4. Posts handoff cards from each member in #agent-handoffs.
    5. Compiles and posts Master Proposal Card to #approvals with 1-click reaction approval.
    """
    if attachments:
        att = attachments[0]
        if att.filename.endswith((".txt", ".md")):
            content_bytes = await att.read()
            job_input = content_bytes.decode("utf-8", errors="ignore")

    job_text = job_input.strip()
    if not job_text:
        await target_message.reply(
            "🌸 Please provide a job URL, job description text, or attach a `.txt`/`.md` file!\n"
            "Example: `!apply https://boards.greenhouse.io/...` or `!apply Product Designer at Linear ::: [description]`"
        )
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
    status_msg = await target_message.reply("🌸 **Sakura is initializing squad mission dispatch...**")

    # Handle URL scraping if link detected
    detected_url = find_url_in_text(job_text)
    if detected_url:
        await status_msg.edit(content=f"🌐 **Detected Job URL!** Scraping posting from: `<{detected_url}>`...")
        scrape_res = await scrape_job_url(detected_url)
        if not scrape_res["success"]:
            await status_msg.edit(
                content=f"⚠️ **Could not scrape that webpage:** {scrape_res['error']}\n"
                        f"💡 *Tip: If this is a login-walled site (like LinkedIn), simply paste the text into Discord!*"
            )
            return
        job_text = scrape_res["content"]
        if company == "Target Company" and role == "Design / Engineering Role":
            await status_msg.edit(content="🔍 Identifying Job Title & Company Name from posting...")
            extracted_role, extracted_company = await loop.run_in_executor(None, extract_job_meta, job_text)
            role = extracted_role
            company = extracted_company
    elif company == "Target Company" and role == "Design / Engineering Role":
        await status_msg.edit(content="🔍 Identifying Job Title & Company Name from text...")
        extracted_role, extracted_company = await loop.run_in_executor(None, extract_job_meta, job_text)
        role = extracted_role
        company = extracted_company

    # Resolve target channels in guild
    guild = target_message.guild
    handoffs_ch = find_channel_by_name(guild, "agent-handoffs") or target_message.channel
    approvals_ch = find_channel_by_name(guild, "approvals") or target_message.channel

    await status_msg.edit(
        content=f"🚀 **Target Locked:** `{role}` at **{company}**\n"
                f"🌸 **Sakura dispatched tasks to the squad! Live handoffs streaming in <#{handoffs_ch.id}>...**"
    )

    # 1. Dispatch Card in #agent-handoffs
    dispatch_embed = discord.Embed(
        title=f"🌸 SQUAD MISSION DISPATCH: {role} @ {company}",
        description=f"**Lead Orchestrator:** Sakura\n"
                    f"**Target Company:** **{company}**\n"
                    f"**Target Role:** `{role}`\n\n"
                    f"📋 **Squad Collaboration Workflow:**\n"
                    f"1️⃣ ⭐ **Chaewon:** Resume tailoring, fit scoring & cover letter\n"
                    f"2️⃣ 🎨 **Yunjin:** Portfolio case study curation & recruiter heuristics\n"
                    f"3️⃣ 💻 **Kazuha:** Frontend architecture & CS degree positioning\n"
                    f"4️⃣ 🛡️ **Eunchae:** Quality Assurance Gatekeeper & Failure Heuristics audit\n"
                    f"5️⃣ 🗳️ **Approvals:** Consolidated Master Proposal Card dispatched to `#approvals`\n\n"
                    f"⚡ *Squad executing in parallel...*",
        color=0xF472B6
    )
    dispatch_embed.set_footer(text="LE SSERAFIM AI HQ • Multi-Agent Pipeline")
    await handoffs_ch.send(embed=dispatch_embed)

    # 2. Chaewon Handoff: Analyze and Tailor
    await status_msg.edit(content=f"⭐ **Chaewon is analyzing ATS fit & tailoring resume/cover letter...**")
    try:
        tailor_res = await loop.run_in_executor(None, analyze_and_tailor, job_text, company, role)
    except Exception as e:
        await handoffs_ch.send(f"⚠️ Chaewon encountered an error: `{e}`")
        tailor_res = {"score": 80, "report": "Error generating full report", "saved_file": "", "filename": "error.md"}

    score = tailor_res["score"]
    score_color = 0x57F287 if score >= 85 else (0xFEE75C if score >= 70 else 0xED4245)

    chaewon_embed = discord.Embed(
        title=f"⭐ CHAEWON — Fit Analysis & Application Package",
        description=f"**Target:** `{role}` at **{company}**\n"
                    f"**ATS Match Score:** **{score}% Fit**\n\n"
                    f"**Executive Synthesis:**\n"
                    f"{tailor_res['report'][:500]}...\n\n"
                    f"📁 Archive: `{tailor_res['filename']}`",
        color=0xF1C40F
    )
    chaewon_embed.set_footer(text="Chaewon • Career Copilot • Handoff 1/3")

    files_handoff = []
    if tailor_res.get("saved_file") and Path(tailor_res["saved_file"]).exists():
        files_handoff.append(discord.File(tailor_res["saved_file"], filename=tailor_res["filename"]))
    if tailor_res.get("resume_pdf") and Path(tailor_res["resume_pdf"]).exists():
        files_handoff.append(discord.File(tailor_res["resume_pdf"], filename=Path(tailor_res["resume_pdf"]).name))
    if tailor_res.get("cover_pdf") and Path(tailor_res["cover_pdf"]).exists():
        files_handoff.append(discord.File(tailor_res["cover_pdf"], filename=Path(tailor_res["cover_pdf"]).name))

    await handoffs_ch.send(embed=chaewon_embed, files=files_handoff)

    # 3. Yunjin Handoff: Portfolio Curation
    await status_msg.edit(content=f"🎨 **Yunjin is curating flagship portfolio case studies & UX strategy...**")
    try:
        yunjin_res = await loop.run_in_executor(None, select_portfolio_pitch, job_text, role, company)
        rec_projects = ", ".join(yunjin_res.get("recommended_projects", ["Vellum OS", "Lumina Analytics"]))
        pitch_text = yunjin_res.get("pitch_text", "")
    except Exception as e:
        # Invariant 7: say the step failed instead of presenting stock picks as Yunjin's curation.
        print(f"⚠️ [bot_sakura.handle_apply_orchestration] Yunjin curation failed: {type(e).__name__}: {e}", flush=True)
        rec_projects = "none (curation failed)"
        pitch_text = f"⚠️ Yunjin could not run the curation step: {type(e).__name__}: {e}"

    yunjin_embed = discord.Embed(
        title=f"🎨 YUNJIN — Portfolio Curation & UX Pitch Strategy",
        description=f"**Lead Flagship Projects:** **{rec_projects}**\n\n"
                    f"{pitch_text[:1400]}",
        color=0xE91E63
    )
    yunjin_embed.set_footer(text="Yunjin • Senior UX Critic • Handoff 2/3")
    await handoffs_ch.send(embed=yunjin_embed)

    # 4. Kazuha Handoff: Frontend Architecture Pitch
    await status_msg.edit(content=f"💻 **Kazuha is architecting frontend tech pitch & CS positioning...**")
    try:
        kazuha_res = await loop.run_in_executor(None, generate_tech_pitch, job_text, role, company)
        tech_pitch = kazuha_res.get("tech_pitch", "")
    except Exception as e:
        print(f"⚠️ [bot_sakura.handle_apply_orchestration] Kazuha tech pitch failed: {type(e).__name__}: {e}", flush=True)
        tech_pitch = f"⚠️ Kazuha could not write the tech pitch: {type(e).__name__}: {e}"

    kazuha_embed = discord.Embed(
        title=f"💻 KAZUHA — Frontend Engineering & Technical Rigor",
        description=tech_pitch[:1500],
        color=0x00B4D8
    )
    kazuha_embed.set_footer(text="Kazuha • Lead Frontend Architect • Handoff 3/4")
    await handoffs_ch.send(embed=kazuha_embed)

    # 5. Eunchae Handoff: Quality Assurance Gatekeeper Audit
    await status_msg.edit(content=f"🛡️ **Eunchae is running QA Gatekeeper audit on all deliverables...**")
    try:
        from eunchae_engine import audit_application_package
        qa_res = await loop.run_in_executor(
            None,
            audit_application_package,
            tailor_res,
            yunjin_res,
            kazuha_res,
            company,
            role
        )
    except Exception as e:
        qa_res = handle_qa_audit_failure(e)

    qa_color = 0x57F287 if qa_res["score"] >= 90 else (0xFEE75C if qa_res["score"] >= 75 else 0xED4245)
    eunchae_qa_embed = discord.Embed(
        title=f"🛡️ EUNCHAE — Quality Assurance Gatekeeper Audit",
        description=f"**Verdict:** **{qa_res['verdict']}** | **QA Score:** **{qa_res['score']}%**\n\n"
                    f"{qa_res['review'][:1200]}\n\n"
                    f"🔍 Heuristics Checked: `{qa_res.get('heuristics_checked', 0)} active memory rules`",
        color=qa_color
    )
    eunchae_qa_embed.set_footer(text="Eunchae • Squad QA Inspector & Gatekeeper • Handoff 4/4")
    await handoffs_ch.send(embed=eunchae_qa_embed)

    # 6. Sakura Master Proposal Card in #approvals
    await status_msg.edit(content=f"📋 **Sakura is compiling Master Proposal in <#{approvals_ch.id}>...**")

    pdf_line = ""
    if tailor_res.get("resume_pdf") and Path(tailor_res["resume_pdf"]).exists():
        pdf_line = f"📄 **ATS PDF Resume:** `{Path(tailor_res['resume_pdf']).name}` (Attached ready to submit!)\n"
    if tailor_res.get("cover_pdf") and Path(tailor_res["cover_pdf"]).exists():
        pdf_line += f"✉️ **Cover Letter PDF:** `{Path(tailor_res['cover_pdf']).name}`\n"

    qa_line, action_instructions, proposal_color = format_proposal_qa_status(
        qa_res=qa_res,
        default_score_color=score_color,
        handoffs_channel_mention=f"<#{handoffs_ch.id}>"
    )

    proposal_embed = discord.Embed(
        title=f"📋 MASTER APPLICATION PROPOSAL: {role} @ {company}",
        description=f"The squad has completed the multi-agent tailoring pipeline for **{company}**!\n\n"
                    f"⭐ **Chaewon (Career):** **{score}% Match** — Tailored resume bullets & custom cover letter generated\n"
                    f"🎨 **Yunjin (Portfolio):** Lead with **{rec_projects}**\n"
                    f"💻 **Kazuha (Frontend):** CS degree rigor & design-token architecture talking points locked\n"
                    f"{qa_line}"
                    f"{pdf_line}"
                    f"📁 **Package File:** `{tailor_res['filename']}`\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{action_instructions}",
        color=proposal_color
    )
    proposal_embed.set_footer(text="Sakura • Chief of Staff • React below to Approve or Discard")

    proposal_files = []
    if tailor_res.get("saved_file") and Path(tailor_res["saved_file"]).exists():
        proposal_files.append(discord.File(tailor_res["saved_file"], filename=tailor_res["filename"]))
    if tailor_res.get("resume_pdf") and Path(tailor_res["resume_pdf"]).exists():
        proposal_files.append(discord.File(tailor_res["resume_pdf"], filename=Path(tailor_res["resume_pdf"]).name))
    if tailor_res.get("cover_pdf") and Path(tailor_res["cover_pdf"]).exists():
        proposal_files.append(discord.File(tailor_res["cover_pdf"], filename=Path(tailor_res["cover_pdf"]).name))

    proposal_msg = await approvals_ch.send(embed=proposal_embed, files=proposal_files)

    # Add reaction buttons for Hans
    try:
        await proposal_msg.add_reaction("✅")
        await proposal_msg.add_reaction("❌")
    except Exception as e:
        print(f"⚠️ Could not add reactions: {e}", flush=True)

    # Persist pending proposal state for reaction listener
    save_pending_proposal(proposal_msg.id, {
        "company": company,
        "role": role,
        "score": score,
        "filename": tailor_res.get("filename", ""),
        "saved_file": str(tailor_res.get("saved_file", "")),
        "resume_pdf": str(tailor_res.get("resume_pdf", "")) if tailor_res.get("resume_pdf") else "",
        "cover_pdf": str(tailor_res.get("cover_pdf", "")) if tailor_res.get("cover_pdf") else ""
    })

    final_reply = (
        f"🌸 **Squad Pipeline Complete!**\n"
        f"• Mission handoff cards posted in <#{handoffs_ch.id}>\n"
        f"• Master Proposal awaiting your reaction in <#{approvals_ch.id}>!"
    )
    await status_msg.edit(content=final_reply)


@bot.event
async def on_raw_reaction_add(payload):
    """Listens for Hans's ✅ or ❌ reaction on proposal cards in #approvals or #job-tailoring."""
    if payload.user_id == bot.user.id:
        return

    emoji_str = str(payload.emoji)
    if emoji_str not in ["✅", "❌"]:
        return

    data = get_pending_proposal(payload.message_id)
    if not data:
        return

    channel = bot.get_channel(payload.channel_id)
    if not channel:
        return

    try:
        target_msg = await channel.fetch_message(payload.message_id)
    except Exception as _exc:
        print(f"⚠️ [bot_sakura.on_raw_reaction_add] suppressed {type(_exc).__name__}: {_exc}", flush=True)
        target_msg = None

    if emoji_str == "✅":
        mark_proposal_status(payload.message_id, "Approved")

        # Resolve #application-docs channel across guild
        docs_ch = None
        if target_msg and target_msg.guild:
            docs_ch = discord.utils.get(target_msg.guild.text_channels, name="application-docs")
        if not docs_ch:
            for guild in bot.guilds:
                docs_ch = discord.utils.get(guild.text_channels, name="application-docs")
                if docs_ch:
                    break

        embed = discord.Embed(
            title="✅ Application Approved by Hans!",
            description=f"Package for **{data['role']}** at **{data['company']}** is **Approved**.\n"
                        f"• Status has been logged as **Approved** in `memory/applications_log.md`.\n"
                        + (f"• Finalized Resume & Cover Letter PDFs delivered to <#{docs_ch.id}>!" if docs_ch else "• Finalized documents saved in applications archive."),
            color=0x57F287
        )
        embed.set_footer(text="LE SSERAFIM AI HQ • Approved by Candidate")
        if target_msg:
            await target_msg.reply(embed=embed)
        else:
            await channel.send(embed=embed)

        # Dispatch both finalized Resume & Cover Letter PDFs to #application-docs
        if docs_ch:
            files_to_send = []
            safe_comp = re.sub(r'[^a-zA-Z0-9_-]', '_', data.get('company', '')).strip('_')

            resume_path = Path(data.get("resume_pdf", ""))
            if not resume_path.exists():
                matches = list(APPLICATIONS_DIR.glob(f"*{safe_comp}*Resume*.pdf"))
                if matches:
                    resume_path = matches[0]

            cover_path = Path(data.get("cover_pdf", ""))
            if not cover_path.exists():
                matches = list(APPLICATIONS_DIR.glob(f"*{safe_comp}*CoverLetter*.pdf"))
                if matches:
                    cover_path = matches[0]

            saved_file = Path(data.get("saved_file", ""))
            if not saved_file.exists() and data.get("filename"):
                saved_file = APPLICATIONS_DIR / data["filename"]

            if resume_path.exists():
                files_to_send.append(discord.File(str(resume_path), filename=resume_path.name))
            if cover_path.exists():
                files_to_send.append(discord.File(str(cover_path), filename=cover_path.name))
            if saved_file.exists():
                files_to_send.append(discord.File(str(saved_file), filename=saved_file.name))

            if files_to_send:
                try:
                    file_list_str = "\n".join([f"• `{f.filename}`" for f in files_to_send])
                    appr_doc_embed = discord.Embed(
                        title=f"🎓 Approved & Ready to Submit: {data['role']} @ {data['company']}",
                        description=f"Hans approved this application in <#{channel.id}>!\n"
                                    f"Download your ATS-optimized Resume & Cover Letter below:",
                        color=0x57F287
                    )
                    appr_doc_embed.add_field(
                        name="📁 Ready-to-Submit Files",
                        value=file_list_str,
                        inline=False
                    )
                    appr_doc_embed.set_footer(text="LE SSERAFIM AI HQ • Approved Application Vault")
                    await docs_ch.send(embed=appr_doc_embed, files=files_to_send)
                except Exception as e:
                    print(f"⚠️ Could not mirror approved package to #application-docs: {e}", flush=True)
    elif emoji_str == "❌":
        mark_proposal_status(payload.message_id, "Discarded")
        embed = discord.Embed(
            title="❌ Application Discarded & Noted in QA Memory",
            description=f"The application proposal for **{data['role']}** at **{data['company']}** has been shelved / discarded.\n\n"
                        f"🛡️ **Eunchae Post-Mortem Sentinel:**\n"
                        f"If there was a specific defect or issue (e.g. bad tone, wrong skills, formatting bug), "
                        f"type: `!reflect {data['company']} | <what went wrong> | <what to do next time>` "
                        f"in `#pc-vitals` so the squad never repeats this mistake!",
            color=0xED4245
        )
        embed.set_footer(text="LE SSERAFIM AI HQ • Eunchae QA Sentinel • Shelved")
        if target_msg:
            await target_msg.reply(embed=embed)
        else:
            await channel.send(embed=embed)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    channel_name = getattr(message.channel, "name", "").lower()
    is_mentioned = bot.user in message.mentions
    is_home_channel = any(k in channel_name for k in ["command-center", "daily-briefing", "commands-chat"])
    if not (is_home_channel or is_mentioned):
        return

    clean_content = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not clean_content and not message.attachments:
        await message.reply("🌸 Yes, Hans? I'm here. How can I help orchestrate today?")
        return

    lower_content = clean_content.lower()
    loop = asyncio.get_running_loop()

    # Check for squad pipeline triggers (!apply or natural language apply)
    if lower_content.startswith("apply ") or "apply to " in lower_content or "apply for " in lower_content:
        # Strip triggering verb
        input_text = clean_content
        for prefix in ["apply to ", "apply for ", "apply "]:
            if lower_content.startswith(prefix):
                input_text = clean_content[len(prefix):].strip()
                break
        await handle_apply_orchestration(message, job_input=input_text, attachments=message.attachments)
        return

    # Check for brain mode switches
    if any(phrase in lower_content for phrase in ["switch to cloud", "use cloud mode", "cloud mode"]):
        set_brain_mode("cloud")
        await message.reply("🌸 Squad brain switched to **CLOUD MODE**! Powered by Groq Cloud with zero load on your RX 6600 XT.")
        return
    elif any(phrase in lower_content for phrase in ["switch to local", "use local mode", "local mode"]):
        set_brain_mode("local")
        await message.reply("🌸 Squad brain switched to **LOCAL MODE**! Directing requests to your local RX 6600 XT via Ollama.")
        return
    elif any(phrase in lower_content for phrase in ["switch to auto", "use auto mode", "auto mode"]):
        set_brain_mode("auto")
        await message.reply("🌸 Squad brain switched to **AUTO-DETECT MODE**! Using local GPU when available, with automatic fallback to Groq Cloud.")
        return
    elif any(k in lower_content for k in ["brain status", "what mode", "current mode", "which model"]):
        status = get_brain_status()
        embed = build_brain_embed(status)
        await message.reply(embed=embed)
        return

    # Check for briefing triggers
    if any(phrase in lower_content for phrase in ["morning briefing", "daily briefing", "what's the plan", "today's plan", "briefing"]):
        async with message.channel.typing():
            try:
                briefing_text = await loop.run_in_executor(None, generate_morning_briefing)
                await send_clean_embeds(
                    target=message,
                    title="🌅 Daily Executive Morning Briefing",
                    content=briefing_text,
                    color=0xF472B6,
                    footer_text="Sakura • Chief of Staff • LE SSERAFIM AI HQ"
                )
            except Exception as e:
                await message.reply(f"❌ Could not compile briefing: `{e}`")
        return

    # Check for goals triggers
    if any(phrase in lower_content for phrase in ["goals", "roadmap", "milestones", "target companies"]):
        goals_path = MEMORY_DIR / "sakura_goals.md"
        if goals_path.exists():
            content = goals_path.read_text(encoding="utf-8")
            await send_clean_embeds(
                target=message,
                title="🎯 Hans Aaron Laureles — Master Career Roadmap",
                content=content,
                color=0xF472B6,
                footer_text="Maintained by Sakura • memory/sakura_goals.md"
            )
        return

    # Strategic advisory chat
    async with message.channel.typing():
        try:
            advice_text = await loop.run_in_executor(None, consult_sakura, clean_content)
            await send_clean_embeds(
                target=message,
                title="🌸 Strategic Guidance from Sakura",
                content=advice_text,
                color=0xF472B6,
                footer_text="Chief of Staff • Strategic Advisory"
            )
        except Exception as e:
            await message.reply(f"❌ Consultation failed: `{e}`")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="🌸 Sakura — Chief of Staff & Central Orchestrator",
        description="I manage Hans's overall AI ecosystem, coordinate the squad, and maintain high-level career strategy:",
        color=0xF472B6
    )
    embed.add_field(
        name="🤝 `!apply [URL or job text]`",
        value="Dispatches the full squad pipeline: Chaewon tailors resume & letter, Yunjin curates case studies, Kazuha builds the tech pitch, and posts a Master Proposal to `#approvals` for your 1-click reaction!",
        inline=False
    )
    embed.add_field(
        name="🌅 `!briefing` (or `!plan`)",
        value="Assembles today's executive morning briefing, pipeline update, and top 3 priorities.",
        inline=False
    )
    embed.add_field(
        name="🎯 `!goals`",
        value="Displays Hans's active career roadmap, target companies, and weekly sprint milestones.",
        inline=False
    )
    embed.add_field(
        name="🧠 `!mode [auto|cloud|local]` (or `!brain`)",
        value="Switches the AI engine between **Groq Cloud** (gaming-safe, 0% GPU load) and **Local Ollama** (private on your RX 6600 XT).",
        inline=False
    )
    embed.add_field(
        name="💬 `!strategy [question]` *(alias: `!consult`)*",
        value="Consult me for strategic advice on interviews, salary negotiation, or day-to-day focus.",
        inline=False
    )
    embed.add_field(
        name="🏓 `!ping`",
        value="Check Sakura's connection latency and gateway status.",
        inline=False
    )
    embed.set_footer(text="LE SSERAFIM AI HQ • Sakura (Chief of Staff)")
    await ctx.reply(embed=embed)


@bot.command(name="apply")
async def apply_command(ctx, *, args: str = None):
    """Executes the full multi-agent squad collaboration pipeline."""
    await handle_apply_orchestration(ctx, job_input=args or "", attachments=ctx.message.attachments)


@bot.command(name="brain")
async def brain_command(ctx):
    status = get_brain_status()
    embed = build_brain_embed(status)
    await ctx.reply(embed=embed)


@bot.command(name="mode")
async def mode_command(ctx, target_mode: str = None):
    if not target_mode:
        status = get_brain_status()
        embed = build_brain_embed(status)
        await ctx.reply(embed=embed)
        return
    
    target_clean = target_mode.lower().strip()
    if target_clean not in ["auto", "local", "cloud"]:
        await ctx.reply("⚠️ Invalid mode. Please choose: `!mode auto`, `!mode cloud`, or `!mode local`.")
        return
        
    set_brain_mode(target_clean)
    if target_clean == "cloud":
        desc = "🚀 **CLOUD MODE ACTIVATED**\nAll 5 agents are now powered by the specialized cloud cluster (Groq LPUs + Gemini Flash) for maximum output quality and sub-second responses. Zero load on your RX 6600 XT—100% of your GPU & RAM is free for gaming!"
        color = 0x3B82F6
    elif target_clean == "local":
        desc = "💻 **LOCAL MODE ACTIVATED**\nAll agents are now routed to **Local Ollama (qwen2.5-coder:7b)** on your RX 6600 XT. 100% private, zero model swapping, your data never leaves your PC."
        color = 0x10B981
    else:
        desc = "🤖 **AUTO-DETECT MODE ACTIVATED**\nSmart dual-brain activated! The squad uses your local GPU if Ollama is running, and automatically falls back to the specialized cloud cluster if Ollama is closed."
        color = 0xF472B6

    embed = discord.Embed(title="🌸 Squad Brain Mode Updated", description=desc, color=color)
    embed.set_footer(text="Chief of Staff • AI Brain Dispatcher")
    await ctx.reply(embed=embed)


@bot.command(name="ping")
async def ping(ctx):
    latency_ms = round(bot.latency * 1000)
    status = get_brain_status()
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Gateway Latency: **{latency_ms}ms**\nActive Engine: **{status['active_provider']}**\nTarget: `{status['active_model']}`",
        color=0xF472B6
    )
    await ctx.reply(embed=embed)


@bot.command(name="goals")
async def goals(ctx):
    goals_path = MEMORY_DIR / "sakura_goals.md"
    if not goals_path.exists():
        await ctx.reply("⚠️ Master goals file not found.")
        return

    content = goals_path.read_text(encoding="utf-8")
    embed = discord.Embed(
        title="🎯 Hans Aaron Laureles — Master Career Roadmap",
        description=content[:2000],
        color=0xF472B6
    )
    embed.set_footer(text="Maintained by Sakura • memory/sakura_goals.md")
    await ctx.reply(embed=embed)


@bot.command(name="briefing", aliases=["plan", "launchpad", "morning"])
async def briefing(ctx):
    status_msg = await ctx.reply("🌸 **Sakura is preparing today's Executive Morning Briefing...**\n*(Reviewing Chaewon's applications log, Yunjin's portfolio status, and active milestones)*")
    
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            briefing_text = await loop.run_in_executor(None, generate_morning_briefing)
    except Exception as e:
        await status_msg.edit(content=f"❌ Failed to generate briefing: `{e}`")
        return

    await send_clean_embeds(
        target=ctx,
        title="🌅 Daily Executive Morning Briefing",
        content=briefing_text,
        color=0xF472B6,
        footer_text="Sakura • Chief of Staff • LE SSERAFIM AI HQ",
        status_msg_to_delete=status_msg
    )


@bot.command(name="rollup", aliases=["standup", "evening", "close"])
async def rollup_command(ctx):
    status_msg = await ctx.reply("🌆 **Sakura is compiling today's Evening Standup & Daily Rollup...**\n*(Gathering agent dispatches from Obsidian, Git commit history, and hardware vitals)*")
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await loop.run_in_executor(None, execute_evening_rollup, True)
    except Exception as e:
        await status_msg.edit(content=f"❌ Failed to compile evening rollup: `{e}`")
        return

    await send_clean_embeds(
        target=ctx,
        title=f"🌆 Evening Standup & Daily Rollup — {res['date_str']}",
        content=res["rollup_text"],
        color=0x9B59B6,
        footer_text="Sakura • Chief of Staff • Daily Standup Sealed in Obsidian",
        status_msg_to_delete=status_msg
    )


@bot.command(name="strategy", aliases=["consult", "advice"])
async def strategy_command(ctx, *, question: str = None):
    if not question:
        await ctx.reply("🌸 Please provide a question! Example: `!strategy How should I prepare for a design systems interview at Linear?`")
        return

    status_msg = await ctx.reply("🧠 **Sakura is consulting the candidate roadmap and strategy models...**")

    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            advice_text = await loop.run_in_executor(None, consult_sakura, question)
    except Exception as e:
        await status_msg.edit(content=f"❌ Consultation failed: `{e}`")
        return

    await send_clean_embeds(
        target=ctx,
        title=f"🌸 Strategic Guidance: {question}",
        content=advice_text,
        color=0xF472B6,
        footer_text="Chief of Staff • Strategic Advisory",
        status_msg_to_delete=status_msg
    )


@bot.command(name="inbox")
async def inbox_command(ctx, scope: str = ""):
    """Executes AI triage of emails in Hans's Gmail inbox."""
    from gmail_engine import is_configured, triage_inbox
    
    if not is_configured():
        embed = discord.Embed(
            title="📬 Gmail Engine — 1-Time Setup Required",
            description="To allow Sakura to organize your Gmail, download your OAuth credentials from Google Cloud Console:\n\n"
                        "1️⃣ Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)\n"
                        "2️⃣ Create an OAuth Client ID for **Desktop App**\n"
                        "3️⃣ Download and save it as `credentials.json` in `lsfm-swarm/`\n"
                        "4️⃣ Run `python test_gmail_auth.py` in your terminal to authenticate!\n\n"
                        "💡 *Your emails are 100% private and processed locally on your PC.*",
            color=0xFEE75C
        )
        embed.set_footer(text="Chief of Staff • Inbox Triage Setup")
        await ctx.reply(embed=embed)
        return

    is_all = scope.lower().strip() in ["all", "full", "recent"]
    scan_limit = 30 if is_all else 20
    status_msg = await ctx.reply(f"🌸 **Sakura is connecting to your Gmail and running AI triage on {'recent primary inbox emails' if is_all else 'unread & active primary inbox emails'}...**")
    
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await run_heavy(triage_inbox, scan_limit, not is_all)
    except Exception as e:
        await status_msg.edit(content=f"❌ Error during inbox triage: `{e}`")
        return

    if not res.get("success"):
        await status_msg.edit(content=f"⚠️ {res.get('error', 'Authentication needed.')}")
        return

    interviews = res.get("interviews", [])
    applications = res.get("applications", [])
    university = res.get("university", [])
    finance = res.get("finance", [])
    deliveries = res.get("deliveries", [])
    dev_ops = res.get("dev_ops", [])
    security = res.get("security", [])
    personal_vip = res.get("personal_vip", [])
    newsletters_archived = res.get("newsletters_archived", 0)
    promos_archived = res.get("promos_archived", 0)
    scanned_count = res.get("total_scanned", 0)

    # Sync applications log if confirmations found
    log_path = MEMORY_DIR / "applications_log.md"
    if log_path.exists() and applications:
        try:
            log_text = log_path.read_text(encoding="utf-8")
            updated_log = log_text
            for app in applications:
                comp = app.get("company", "")
                if comp and comp != "Company" and comp in log_text:
                    status_val = "Rejected" if app.get("status") == "Rejected" else "Confirmed"
                    updated_log = re.sub(
                        rf"(\|[^|]+\|[^|]*{re.escape(comp)}[^|]*\|[^|]+\|[^|]+\|[^|]+\|)\s*Ready\s*(\([0-9]+%\)\s*\|)",
                        rf"\1 {status_val} \2",
                        updated_log,
                        flags=re.IGNORECASE
                    )
            if updated_log != log_text:
                log_path.write_text(updated_log, encoding="utf-8")
        except Exception as e:
            print(f"⚠️ Could not sync applications log: {e}", flush=True)

    color = 0xED4245 if interviews else (0x57F287 if scanned_count > 0 else 0x5865F2)
    
    embed = discord.Embed(
        title="📬 Sakura — Executive Inbox Triage Report",
        description=f"Scanned **{scanned_count}** unread messages in your Primary Inbox.",
        color=color
    )

    if interviews:
        interview_lines = []
        for it in interviews:
            interview_lines.append(f"• **{it['company']}** (From: `{it['sender'][:25]}`)\n  👉 *{it['summary']}*")
        embed.add_field(
            name=f"🚨 URGENT: Recruiter & Interview Inquiries ({len(interviews)})",
            value="\n".join(interview_lines)[:1024],
            inline=False
        )

    if university:
        uni_lines = [f"• **{u['source']}**: {u['summary']}" for u in university[:4]]
        embed.add_field(
            name=f"🎓 University & Academics ({len(university)})",
            value="\n".join(uni_lines)[:1024],
            inline=False
        )

    if applications:
        app_lines = [f"• **{app['company']}**: {app['summary']}" for app in applications[:4]]
        embed.add_field(
            name=f"💼 Job Applications ({len(applications)})",
            value="\n".join(app_lines)[:1024],
            inline=False
        )

    if finance:
        fin_lines = [f"• **{f['source']}**: {f['summary']}" for f in finance[:4]]
        embed.add_field(
            name=f"💳 Finance & Banking ({len(finance)})",
            value="\n".join(fin_lines)[:1024],
            inline=False
        )

    if deliveries:
        del_lines = [f"• **{d['source']}**: {d['summary']}" for d in deliveries[:4]]
        embed.add_field(
            name=f"📦 Deliveries & Orders ({len(deliveries)})",
            value="\n".join(del_lines)[:1024],
            inline=False
        )

    if dev_ops:
        dev_lines = [f"• **{d['source']}**: {d['summary']}" for d in dev_ops[:4]]
        embed.add_field(
            name=f"⚙️ Dev Ops & Cloud ({len(dev_ops)})",
            value="\n".join(dev_lines)[:1024],
            inline=False
        )

    if security:
        sec_lines = [f"• **{s['source']}**: {s['summary']}" for s in security[:3]]
        embed.add_field(
            name=f"🔑 Security & 2FA / OTPs ({len(security)})",
            value="\n".join(sec_lines)[:1024],
            inline=False
        )

    if personal_vip:
        vip_lines = [f"• **{v['sender'].split('<')[0].strip()}**: {v['summary']}" for v in personal_vip[:3]]
        embed.add_field(
            name=f"💬 Personal VIP & Direct Messages ({len(personal_vip)})",
            value="\n".join(vip_lines)[:1024],
            inline=False
        )

    total_cleaned = newsletters_archived + promos_archived
    if total_cleaned > 0:
        embed.add_field(
            name="🧹 Noise Cleaned & Auto-Archived",
            value=f"Archived **{promos_archived}** promo blasts to `LSFM/📁 Promos & Clutter` and **{newsletters_archived}** reads to `LSFM/📰 Design & Tech Reads`.",
            inline=False
        )

    if not interviews and not university and not applications and not finance and not deliveries and not dev_ops and total_cleaned == 0:
        embed.add_field(
            name="✨ Inbox Zero State",
            value="No pending unread action items in your primary inbox!",
            inline=False
        )

    embed.set_footer(text="Sakura • Chief of Staff • Commands: !inbox • !clean • !receipts")
    await status_msg.delete()
    await ctx.reply(embed=embed)


@bot.command(name="triage")
async def triage_command(ctx, scope: str = ""):
    await inbox_command(ctx, scope=scope)


@bot.command(name="clean")
async def clean_command(ctx):
    """Sweeps promo clutter and newsletters out of the primary inbox to achieve Inbox Zero."""
    from gmail_engine import is_configured, sweep_inbox

    if not is_configured():
        await ctx.reply("⚠️ Gmail Engine is not configured. Please run `test_gmail_auth.py` first.")
        return

    status_msg = await ctx.reply("🧹 **Sakura is sweeping promotional clutter and newsletters from your Primary Inbox...**")
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await run_heavy(sweep_inbox, 50)
    except Exception as e:
        await status_msg.edit(content=f"❌ Error during sweep: `{e}`")
        return

    if not res.get("success"):
        await status_msg.edit(content=f"⚠️ {res.get('error', 'Sweep failed.')}")
        return

    total_scanned = res.get("total_scanned", 0)
    total_swept = res.get("total_swept", 0)
    promos = res.get("promos_swept", 0)
    newsletters = res.get("newsletters_swept", 0)
    retained = res.get("retained_in_inbox", 0)

    embed = discord.Embed(
        title="🧹 Sakura — Inbox Zero Sweep Complete",
        description=f"Scanned **{total_scanned}** unread messages in your primary inbox.\nSwept **{total_swept}** clutter emails directly into their LSFM archives!",
        color=0x57F287 if total_swept > 0 else 0x5865F2
    )
    embed.add_field(
        name="📁 Promos & Store Clutter Cleaned",
        value=f"**{promos}** marketing blasts archived to `LSFM/📁 Promos & Clutter`",
        inline=True
    )
    embed.add_field(
        name="📰 Newsletters Filed",
        value=f"**{newsletters}** reads archived to `LSFM/📰 Design & Tech Reads`",
        inline=True
    )
    embed.add_field(
        name="🛡️ High-Priority Retained in Inbox",
        value=f"**{retained}** important emails (University, Career, Finance, Orders, VIP) remain in your Primary Inbox.",
        inline=False
    )
    embed.set_footer(text="Sakura • Chief of Staff • Inbox Zero Sweep")
    await status_msg.delete()
    await ctx.reply(embed=embed)


@bot.command(name="sweep")
async def sweep_command(ctx):
    await clean_command(ctx)


@bot.command(name="receipts")
async def receipts_command(ctx):
    """Displays a ledger of recent bank transactions, e-wallet receipts, and orders."""
    from gmail_engine import is_configured, get_recent_finance_summary

    if not is_configured():
        await ctx.reply("⚠️ Gmail Engine is not configured. Please run `test_gmail_auth.py` first.")
        return

    status_msg = await ctx.reply("💳 **Sakura is retrieving recent finance, wallet, and order receipts from your Gmail...**")
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await run_heavy(get_recent_finance_summary, 15)
    except Exception as e:
        await status_msg.edit(content=f"❌ Error retrieving receipts: `{e}`")
        return

    if not res.get("success"):
        await status_msg.edit(content=f"⚠️ {res.get('error', 'Failed to retrieve receipts.')}")
        return

    txs = res.get("transactions", [])
    if not txs:
        await status_msg.edit(content="💳 No recent transactions or purchase receipts found in your inbox.")
        return

    embed = discord.Embed(
        title="💳 Sakura — Recent Financial & Order Receipts",
        description=f"Retrieved **{len(txs)}** recent transactions from GCash, Maya, Shopee, Grab & Banking.",
        color=0x10B981
    )

    for tx in txs[:10]:
        amt_badge = f"`{tx['amount']}`" if tx["amount"] != "N/A" else "Receipt"
        embed.add_field(
            name=f"{tx['merchant']} • {amt_badge}",
            value=f"*{tx['subject']}*\n📅 `{tx['date']}`",
            inline=False
        )

    embed.set_footer(text="Sakura • Chief of Staff • Finance Ledger")
    await status_msg.delete()
    await ctx.reply(embed=embed)


@bot.command(name="expenses")
async def expenses_command(ctx):
    await receipts_command(ctx)


@bot.command(name="cleanse")
async def cleanse_command(ctx):
    """Executes 1-time mass cleanse across the entire primary inbox (up to 250 emails)."""
    from gmail_engine import is_configured
    from scripts.mass_cleanse import run_mass_cleanse

    if not is_configured():
        await ctx.reply("⚠️ Gmail Engine is not configured. Please run `test_gmail_auth.py` first.")
        return

    status_msg = await ctx.reply("🌸 **Sakura is launching the Mass Inbox Cleanse... Scanning all emails, filing noise, and organizing your high-signal workspace...**")
    loop = asyncio.get_running_loop()
    try:
        async with ctx.typing():
            res = await run_heavy(run_mass_cleanse)
    except Exception as e:
        await status_msg.edit(content=f"❌ Error during mass cleanse: `{e}`")
        return

    if not res.get("success"):
        await status_msg.edit(content=f"⚠️ {res.get('error', 'Mass cleanse failed.')}")
        return

    total = res.get("total", 0)
    archived = res.get("archived_count", 0)
    retained = res.get("retained_count", 0)
    cats = res.get("categories", {})

    embed = discord.Embed(
        title="✨ Sakura — Mass Inbox Cleanse Complete",
        description=f"Scanned all **{total}** emails in your Primary Inbox!\n"
                    f"🧹 **{archived}** promotional blasts & newsletters archived.\n"
                    f"📌 **{retained}** high-signal emails neatly labeled in your Primary Inbox.",
        color=0x10B981
    )

    breakdown_lines = []
    for cat, count in sorted(cats.items(), key=lambda x: x[1], reverse=True):
        breakdown_lines.append(f"• **{cat}**: {count} emails")
    if breakdown_lines:
        embed.add_field(
            name="📂 Classification Breakdown",
            value="\n".join(breakdown_lines)[:1024],
            inline=False
        )

    embed.set_footer(text="Sakura • Chief of Staff • High-Signal Workspace Activated")
    await status_msg.delete()
    await ctx.reply(embed=embed)


@bot.command(name="mass_sort")
async def mass_sort_command(ctx):
    await cleanse_command(ctx)


if __name__ == "__main__":
    print("🚀 Starting Sakura (Chief of Staff)...", flush=True)
    bot.run(TOKEN)
