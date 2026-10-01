# LE SSERAFIM AI HQ — Complete Operational Guide

> **Ecosystem:** Zero-Cost Multi-Agent Personal AI Squad for Hans Aaron Laureles  
> **Workspace:** the repository root (all paths below are relative to it)  
> **Target Hardware:** Intel Core i5-12400F · AMD Radeon RX 6600 XT (8GB VRAM) · 16GB RAM  
> **Interface:** Private Discord Server (**LSFM HQ**)

---

## 🌸 1. Squad Architecture & Agent Pillars

The system distributes specialized cognitive domains across 5 distinct AI agents, each with dedicated Discord bot tokens, custom avatars, and channel boundaries:

| Agent / Pillar | Dedicated Channels | Core Capabilities |
|---|---|---|
| 📖 **00 · COMMAND DIRECTORY** | `#all-commands`<br>`#commands-chat` | Universal command reference cheatsheets & shared playground where any agent can be commanded |
| 🌸 **01 · SAKURA — COMMAND** | `#command-center`<br>`#daily-briefing` | Master roadmap alignment, daily morning briefings, cross-squad pipeline dispatch (`!apply`), brain mode control (`!mode`), and 1-click reaction approvals |
| ⭐ **03 · CHAEWON — CAREER & JOBS** | `#job-tailoring`<br>`#application-docs` | URL scraping, ATS keyword alignment, fit scoring (1–100%), tailored resume bullets, and human cover letters (`!tailor`) |
| 🎨 **04 · YUNJIN — PORTFOLIO & UX** | `#portfolio-audits`<br>`#project-status` | Automated portfolio health audits (`!audit`), case study critiques (`!critique`), and strategic portfolio project curation |
| 💻 **05 · KAZUHA — CODE & BUILD** | `#frontend-lab`<br>`#git-log` | Code health & token adherence inspection (`!inspect`), accessible UI component generation (`!component`), git monitoring |
| 🛡️ **06 · EUNCHAE — GUARDIAN** | `#pc-vitals`<br>`#system-alerts` | Real-time PC vitals (`!vitals`: CPU, RAM, Disk, Uptime), high-load alerts, squad gateway watchdog |

---

## 🧠 2. Hybrid Dual-Brain Architecture (Zero-Cost & Gaming-Safe)

The squad is powered by a dynamic inference router ([`llm_client.py`](llm_client.py)) supporting three operational modes:

### Primary Engines
1. **Groq Cloud Engine (`qwen/qwen3.8-27b`):**
   - Rate limits depend on model and tier. On this account `qwen/qwen3.8-27b` allows 1,000 requests and 8,000 tokens per minute (Groq's rate-limit headers, 2026-10-01).
   - **0% GPU load on RX 6600 XT**, ensuring 100% of your GPU VRAM and system RAM remain available for gaming.
2. **Local Ollama Engine (`qwen2.5-coder:7b`):**
   - Runs locally through Ollama. On the RX 6600 XT the Q4_K_M build loads fully into VRAM (4.74 GB) and decodes at ~28 tokens/s (`bench/results/2026-10-01.md`, N=10).
   - Your resume and proprietary code never leave your machine.

### Brain Modes (`!mode [auto|cloud|local]`)
- **`AUTO` (Default):** Smart dual-brain! Prioritizes local GPU when Ollama is running; seamlessly falls back to Groq Cloud if Ollama is closed or during heavy tasks.
- **`CLOUD`:** Forces all queries to Groq Cloud. Guarantees zero local resource consumption.
- **`LOCAL`:** Forces all queries to local Ollama.

---

## 🎮 3. 1-Click Hardware & Gaming Protection

Hans's PC does not need to run 24/7. When you want to use the squad or when you want to game, use these two scripts:

### 🚀 Starting the Squad ([`start_squad.bat`](start_squad.bat))
Double-click `start_squad.bat` to:
1. Automatically verify and launch the local Ollama background server on your RX 6600 XT (if not already running).
2. Connect all 5 LE SSERAFIM Discord bots to `LSFM HQ`.

### 🛑 Gaming / Shutdown Script ([`stop_squad.bat`](stop_squad.bat))
Double-click `stop_squad.bat` before launching a game to:
1. Instantly terminate all running Discord bot processes.
2. Terminate `ollama.exe` and flush the model from VRAM.
3. **Restores 100% of your RX 6600 XT GPU and RAM for maximum gaming FPS.**

---

## 🤝 4. Multi-Agent Squad Collaboration Pipeline (`!apply`)

When you discover an exciting job posting, run:
```text
!apply <Job URL or description>
```

### Collaboration Workflow:
1. **Sakura (Dispatch):** Scrapes the URL (or parses text), identifies the role and company, and posts the mission dispatch card into **`#agent-handoffs`**.
2. **Chaewon (Career):** Evaluates ATS fit score (**%**), crafts tailored resume bullets matching your CS degree and ROC internship, generates a tailored cover letter, saves the package to `applications/`, and posts to **`#agent-handoffs`**.
3. **Yunjin (Portfolio):** Selects the top 2 case studies from your portfolio (e.g. Vellum OS, Lumina Analytics, FinTrack, Aura) and provides interview walkthrough heuristics in **`#agent-handoffs`**.
4. **Kazuha (Frontend):** Builds the frontend engineering talking points, highlighting your CS degree and design-token component systems in **`#agent-handoffs`**.
5. **Sakura (Master Proposal):** Compiles the full package and posts the Master Proposal Card into **`#approvals`** with the markdown file attached and interactive **`✅`** / **`❌`** reaction buttons.
6. **1-Click Approval:**
   - Click **`✅`**: Sakura marks the application as **Approved** and logs it into [`memory/applications_log.md`](memory/applications_log.md).
   - Click **`❌`**: Sakura shelves the application as **Discarded**.

---

## ⏰ 5. Automated Daily Rituals & Background Cron

Native background loops (`discord.ext.tasks`) automatically handle squad routines:

1. **🌅 Daily Executive Morning Briefing (`#daily-briefing`)**:
   - Managed by **Sakura**. Dispatches once per calendar day at or after 08:00 AM (or immediately upon starting your PC).
   - Highlights active sprint priorities, total applications in the archive, and squad readiness.
2. **🎨 Weekly Portfolio Health Audit (`#portfolio-audits`)**:
   - Managed by **Yunjin**. Automatically scans `portfolio-site/` every 7 days, checking image links, CSS tokens, and recruiter heuristics.
3. **🛡️ PC Vitals Watchdog & High Load Alerts (`#system-alerts` & `#pc-vitals`)**:
   - Managed by **Eunchae**. Probes hardware every 5 minutes.
   - **High-Load Alert:** Warns you in `#system-alerts` if RAM > 92% or CPU > 95%, suggesting you switch to `!mode cloud` or run `stop_squad.bat` before gaming.
   - **Daily Diagnostic Pulse:** Posts a full hardware vitals report to `#pc-vitals` once per calendar day.

---

## 🕹️ 6. Discord Command Quick Reference

### 🌸 Sakura (Chief of Staff) — `#command-center`
- `!apply [URL or text]` — Dispatches full multi-agent squad collaboration pipeline.
- `!inbox` (or `!triage`) — Smart Executive Inbox Triage (use `!inbox all` for deep scan).
- `!clean` (or `!sweep`) — Quick Inbox Zero sweep of up to 50 unread promo blasts.
- `!cleanse` (or `!mass_sort`) — 1-time mass cleanse across the entire primary inbox (up to 250 emails).
- `!receipts` (or `!expenses`) — Scans recent banking alerts, e-wallets (GCash/Maya), and order receipts with amounts.
- `!briefing` (or `!plan`) — Today's executive morning briefing & sprint priorities.
- `!goals` — Displays active career roadmap and target companies.
- `!mode [auto|cloud|local]` — Switches the squad's active AI inference engine.
- `!brain` — Shows real-time AI brain diagnostic and hardware utilization.
- `!ask [question]` — Strategic executive coaching and interview guidance.

### ⭐ Chaewon (Career Agent) — `#job-tailoring`
- `!tailor [URL or text]` — Tailors resume bullets, cover letter, ATS fit score, and compiles PDF resume.
- `!pdf [target/company]` — Instantly compiles & uploads an ATS-compliant 1-page PDF Resume & Cover Letter for Hans.
- `!profile` — Displays active candidate profile and skills memory.
- `!history` — Lists recent tailored applications and match percentages.

### 🎨 Yunjin (Portfolio Agent) — `#portfolio-audits`
- `!audit` — Runs comprehensive code, image, and UX health audit of `portfolio-site/`.
- `!critique [vellum|lumina|fintrack|aura]` — Senior UX heuristic critique of a specific case study.
- `!status` — View the live project completion scorecard.

### 💻 Kazuha (Frontend Agent) — `#frontend-lab`
- `!inspect [file]` — Audits HTML/CSS/JS files for token adherence and clean code.
- `!component [description]` — Generates production-ready, accessible UI components.
- `!git` — Checks uncommitted git changes in the workspace.

### 🛡️ Eunchae (Guardian Agent) — `#pc-vitals`
- `!vitals` (or `!health`) — Real-time CPU, RAM, and Disk utilization stats.
- `!checkin` — Energetic diagnostic checkup from the maknae guardian.
