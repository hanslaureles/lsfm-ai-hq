import os
import sys
import json
import urllib.request
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # quiet: no console to reconfigure (pythonw, redirected stream)
        pass

load_dotenv()
TOKEN = os.getenv("SAKURA_BOT_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "YOUR_DISCORD_GUILD_ID")

headers = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "DiscordBot (https://github.com/discord/discord-api-docs, 1.0)"
}

def api_call(endpoint, data=None, method="GET"):
    url = f"https://discord.com/api/v10{endpoint}"
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    if not TOKEN:
        print("⚠️ SAKURA_BOT_TOKEN not found in environment or .env.", flush=True)
        return
    if not GUILD_ID or GUILD_ID == "YOUR_DISCORD_GUILD_ID":
        print("⚠️ Please set DISCORD_GUILD_ID in your .env file before running this script.", flush=True)
        return

    print("🔍 Fetching existing channels in Discord server...", flush=True)
    channels = api_call(f"/guilds/{GUILD_ID}/channels")
    
    all_cmd_ch = None
    for c in channels:
        if c.get("name", "").lower() == "all-commands":
            all_cmd_ch = c
            print(f"✅ Found #all-commands channel: {c['id']}", flush=True)
            break

    if not all_cmd_ch:
        print("⚠️ #all-commands channel not found yet! Please create it in Discord first.", flush=True)
        return

    all_cmd_id = all_cmd_ch["id"]
    print(f"📜 Posting Master Command Cheatsheets to #all-commands ({all_cmd_id})...", flush=True)

    embeds = [
        {
            "title": "📖 LE SSERAFIM AI HQ — Master Command Directory",
            "description": "Welcome to the universal command directory for Hans Aaron Laureles's personal AI squad.\n\n"
                            "• **`#all-commands`**: Your permanent quick-reference guide for every agent.\n"
                            "• **`#commands-chat`**: Your shared universal playground where you can invoke **any** agent or command at any time!",
            "color": 0xF472B6,
                "fields": [
                    {
                        "name": "⚡ Universal Shared Access",
                        "value": "Every member responds in `#commands-chat` to their commands and direct mentions.",
                        "inline": False
                    }
                ],
                "footer": {"text": "LE SSERAFIM AI HQ • Unified Command System"}
            },
            {
                "title": "🌸 01 · SAKURA — Chief of Staff & Central Orchestrator",
                "description": "Lead orchestrator, career strategist, multi-agent pipeline dispatch, and AI brain mode control.",
                "color": 0xF472B6,
                "fields": [
                    {
                        "name": "🤝 `!apply [URL or job text]`",
                        "value": "Dispatches the full squad pipeline: Chaewon tailors resume & letter, Yunjin curates portfolio case studies, Kazuha builds tech pitch, and Sakura posts the Master Proposal to `#approvals` for your 1-click reaction approval (`✅`/`❌`).",
                        "inline": False
                    },
                    {
                        "name": "🧠 `!mode [auto|cloud|local]` & `!brain`",
                        "value": "Switch AI inference engine:\n• `!mode cloud` — 100% Groq Cloud, zero GPU load for gaming\n• `!mode local` — 100% Local Ollama on RX 6600 XT\n• `!mode auto` — Smart dual-brain auto-detection\n• `!brain` — Real-time hardware & inference diagnostic",
                        "inline": False
                    },
                    {
                        "name": "📬 `!inbox` & 🧹 `!clean`",
                        "value": "Executive Gmail intelligence & triage:\n• `!inbox` — Scans unread recruiter outreach & critical job updates\n• `!clean` — Safe bulk-unsubscribe from promotional clutter\n• `!receipts` / `!expenses` — Aggregates financial receipts and invoices",
                        "inline": False
                    },
                    {
                        "name": "🌅 `!briefing` & 🎯 `!goals`",
                        "value": "• `!briefing` (or `!plan`) — Executive morning briefing & top 3 sprint priorities.\n• `!goals` — Displays active career roadmap and sprint targets.\n• `!strategy [topic]` — Deep coaching on negotiation, interviews, and focus.",
                        "inline": False
                    }
                ],
                "footer": {"text": "Sakura • Chief of Staff • Command Directory"}
            },
            {
                "title": "⭐ 02 · CHAEWON — Career & Application Agent",
                "description": "Talent agent, ATS keyword optimization, job scouting radar, and PDF generation.",
                "color": 0xF1C40F,
                "fields": [
                    {
                        "name": "🎯 `!tailor [URL or text]` *(alias: `!apply`)*",
                        "value": "Tailors your resume & cover letter to match any job posting:\n• **Link:** `!tailor https://boards.greenhouse.io/...`\n• **Text:** `!tailor Product Designer at Stripe ::: [paste text]`\n• **File:** Attach a `.txt` or `.md` job spec",
                        "inline": False
                    },
                    {
                        "name": "📡 `!scout [keywords]` *(aliases: `!radar`, `!jobs`, `!findjobs`)*",
                        "value": "Autonomous job radar scanning remote and Philippine tech boards for high-match Frontend, UI/UX, and AI engineering opportunities.",
                        "inline": False
                    },
                    {
                        "name": "📄 `!pdf [job_title]`",
                        "value": "Compiles tailored application assets into vector ATS-compliant 1-page PDF resumes & cover letters ready for submission.",
                        "inline": False
                    },
                    {
                        "name": "👤 `!profile` & 📜 `!history`",
                        "value": "• `!profile` — Inspects your master resume and active project memory.\n• `!history` — Lists recent tailored applications and match percentages.",
                        "inline": False
                    }
                ],
                "footer": {"text": "Chaewon • Career Copilot • Command Directory"}
            },
            {
                "title": "🎨 03 · YUNJIN — Portfolio Agent & Senior UX Critic",
                "description": "Design director heuristics, design system librarian, and portfolio craft guardian.",
                "color": 0xE91E63,
                "fields": [
                    {
                        "name": "🔍 `!audit`",
                        "value": "Full scan of `portfolio-site/`: verifies images on disk, HTML meta tags, and generates an executive UX craft score.",
                        "inline": False
                    },
                    {
                        "name": "🔬 `!critique [project]`",
                        "value": "Deep design director critique of your flagship case studies:\n• `!critique aura` (Aura Coffee & Kitchen — Artisan Cafe Storefront & Checkout)\n• `!critique fintrack` (FinTrack — High-Frequency Financial Pipeline)\n• `!critique vellum` (Mobile UX & Notification Triage)\n• `!critique lumina` (B2B SaaS Data Dashboard)",
                        "inline": False
                    },
                    {
                        "name": "📊 `!status`",
                        "value": "Displays live completion scorecard, assets, and deployment status across your portfolio.",
                        "inline": False
                    }
                ],
                "footer": {"text": "Yunjin • Portfolio Guardian • Command Directory"}
            },
            {
                "title": "💻 04 · KAZUHA — Frontend Agent & Cognitive Memory",
                "description": "Lead engineer, accessible component architect, and cognitive episodic memory bridge.",
                "color": 0x00B4D8,
                "fields": [
                    {
                        "name": "⚡ `!recall [query]` *(alias: `!heuristics`)*",
                        "value": "Searches cognitive episodic memory for past post-mortems and permanent rules before writing code.\n• Example: `!recall navigation flexbox`, `!recall coffee copy`",
                        "inline": False
                    },
                    {
                        "name": "📜 `!rules`",
                        "value": "Displays crystallized workspace rules compiled from real-world post-mortems in `.agents/rules/learned_rules.md`.",
                        "inline": False
                    },
                    {
                        "name": "🧠 `!reflect domain | trigger | symptom | root_cause | rule`",
                        "value": "Logs a new engineering post-mortem directly into episodic memory from Discord so the squad never repeats it.",
                        "inline": False
                    },
                    {
                        "name": "🔎 `!search [query]` & 🧠 `!ask [question]`",
                        "value": "• `!search [query]` — Hybrid semantic + BM25 search across your codebase & vector vault.\n• `!ask [question]` — Codebase Q&A grounded in your active source files with exact citations.",
                        "inline": False
                    },
                    {
                        "name": "🛠️ `!inspect [file]` & 🧩 `!component [description]`",
                        "value": "• `!inspect [file]` — Architectural token audit on files like `styles.css` or `index.html`.\n• `!component [desc]` — Generates production-grade HTML/CSS/React components.",
                        "inline": False
                    }
                ],
                "footer": {"text": "Kazuha • Lead Frontend Architect • Command Directory"}
            },
            {
                "title": "🛡️ 05 · EUNCHAE — System Guardian & PC Watchdog",
                "description": "Hardware health monitoring, gaming resource protection, and uptime watchdog.",
                "color": 0xE67E22,
                "fields": [
                    {
                        "name": "⚡ `!vitals` (or `!health`)",
                        "value": "Real-time CPU %, RAM usage (GB used/total), C: drive free space, and PC uptime.",
                        "inline": False
                    },
                    {
                        "name": "🛡️ `!checkin`",
                        "value": "Generates a full, energetic hardware and squad status checkup from the maknae.",
                        "inline": False
                    },
                    {
                        "name": "🏓 `!ping`",
                        "value": "Tests gateway latency and bot response times.",
                        "inline": False
                    }
                ],
                "footer": {"text": "Eunchae • System Guardian • Command Directory"}
            },
            {
                "title": "🧠 06 · SHARED COGNITIVE MEMORY FLYWHEEL",
                "description": "The workspace-wide self-improving engine that eliminates AI session amnesia.",
                "color": 0x9B59B6,
                "fields": [
                    {
                        "name": "🔄 The Continuous Improvement Loop",
                        "value": "1. **Pre-Flight Recall (`!recall`)**: Pulls past lessons before touching code.\n2. **Execution**: Builds solutions without repeating known failures.\n3. **Post-Mortem (`!reflect`)**: Records fixes to `experience_store.jsonl` with auto-deduplication.\n4. **Crystallization (`!rules`)**: Auto-compiles permanent heuristics into `.agents/rules/`.",
                        "inline": False
                    },
                    {
                        "name": "📁 Core Engine Locations",
                        "value": "• Store: `agent-memory/store/experience_store.jsonl`\n• Active Rules: `.agents/rules/learned_rules.md`\n• Skill Integration: `.agents/skills/self-improving-agent/`",
                        "inline": False
                    }
                ],
                "footer": {"text": "LE SSERAFIM AI HQ • Unified Episodic Memory Hub"}
            }
    ]

    for card in embeds:
        api_call(f"/channels/{all_cmd_id}/messages", data={"embeds": [card]}, method="POST")

    print("🎉 Successfully posted all cards to #all-commands via REST API!", flush=True)

if __name__ == "__main__":
    main()
