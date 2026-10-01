# ⚔️ Kazuha's Local Git & PR Sentinel — User Guide

An autonomous, local-first code review, conventional commit generator, and GitHub Pull Request sentinel designed for **Hans Aaron Laureles**.

Powered by local GPU inference (**`qwen2.5-coder:7b` on AMD Radeon RX 6600 XT via Ollama**) with automatic cloud failover to Groq Cloud / Gemini.

---

## 🚀 Dual Interface: CLI & Discord

You can access the Sentinel either **directly in your Windows terminal** while coding or **via Discord** in `#git-log` / `#frontend-lab`.

---

## 1. Terminal / CLI Usage (PowerShell or CMD)

Run Sentinel commands from `lsfm-swarm/` or from the repository root:

### Check Git Health & Status
```powershell
python git_sentinel.py status
```
*Outputs current branch, total commits, staged, unstaged, and untracked files.*

### Perform Autonomous Code & Security Review
```powershell
# Review all active changes (staged + unstaged)
python git_sentinel.py review

# Review only staged changes
python git_sentinel.py review --staged

# Review any other repository on your PC
python git_sentinel.py review --path "C:\path\to\other_repo"
```
* **Score (0–100%) & Verdict:** `🟢 READY TO COMMIT`, `🟡 CAUTION`, or `🔴 BLOCKED`.
* **Security Pre-Flight:** Alerts on accidental secret/token leaks or sensitive `.env` files.
* **Code Health:** Flags logic bugs, null safety, race conditions, leftover `console.log` / `print()` statements.
* **Design System Audit:** Verifies CSS variables and Swiss Editorial token conformance for web files.

### Synthesize Conventional Commit Message
```powershell
# Draft a conventional commit message from active diff
python git_sentinel.py commit

# Draft from staged files only
python git_sentinel.py commit --staged

# Review and automatically run `git commit -m` with confirmation
python git_sentinel.py commit --apply
```
*Follows strict Conventional Commits specification (`feat`, `fix`, `refactor`, `chore`, `docs`) with a 72-character headline and concise bulleted motivation.*

### Generate Production GitHub PR Description
```powershell
python git_sentinel.py pr
```
*Outputs a complete, Markdown PR description with Overview, Key Changes, Verification Steps, and Review Checklist.*

---

## 2. Discord Usage (`#git-log` & `#frontend-lab`)

Kazuha is directly listening in **LSFM HQ**:

| Discord Command | Description |
| :--- | :--- |
| `!git` | Displays workspace git status and active branch telemetry. |
| `!review` or `!git review` | Triggers architectural code review on current workspace changes. |
| `!review --staged` | Reviews only staged changes. |
| `!commit` or `!git commit` | Synthesizes a Conventional Commit message from the active diff. |
| `!pr` or `!git pr` | Generates a complete GitHub Pull Request description draft. |

### Conversational Triggers
You can also simply chat with Kazuha in `#git-log` or `#frontend-lab`:
* *"Kazuha, review my changes"*
* *"Kazuha, write a commit for this"*
* *"Kazuha, what is the git status?"*

---

## 3. Optional: Automatic Git Pre-Commit Hook

To automatically run Kazuha's Sentinel review before **every** git commit, create a pre-commit hook in your `.git/hooks/pre-commit` file:

```bash
#!/bin/sh
python lsfm-swarm/git_sentinel.py review --staged
```

Whenever you run `git commit`, Kazuha will inspect your staged code and provide an instant verdict before your commit is finalized!

---

## 🛡️ Inference Routing
1. **Local Silicon (Priority 1):** Routes to `localhost:11434` with model `qwen2.5-coder:7b` on your AMD RX 6600 XT. **$0.00 compute cost & 100% offline.**
2. **Cloud Failover (Priority 2):** If Ollama is offline or uninstalled, automatically falls back to Groq Cloud (`qwen/qwen3.8-27b` $\rightarrow$ `openai/gpt-oss-120b`) and Gemini.
