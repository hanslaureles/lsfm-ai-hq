# 🛡️ Eunchae QA Guardian & Failure Learning Guide

An overview of **Eunchae's** evolved dual role as **System Guardian & Quality Assurance (QA) Inspector** for Hans Aaron Laureles's LE SSERAFIM AI HQ.

---

## 🎯 Dual Responsibilities

```
                      🛡️ EUNCHAE
          ┌─────────────────┴─────────────────┐
          ▼                                   ▼
  1. System Guardian                 2. QA Gatekeeper & Heuristics
  • Real-time CPU/RAM/Disk vitals     • Audits squad application packages
  • AMD RX 6600 XT GPU monitor        • Pre-flight template placeholder scan
  • Ollama local daemon check         • Recalls past failure rules from memory
  • High resource load alert loops    • Captures post-mortems on rejection
```

---

## 🔍 Automated QA Gatekeeper Pipeline (During `!apply`)

Whenever Sakura dispatches a squad mission, Eunchae intercepts the deliverables before they reach `#approvals`:

1. **Deterministic Sanity Checks:**
   * **Placeholders:** Scans for `[Company]`, `[Client]`, `[Phone]`, `N/A`, `Lorem Ipsum`, or `TODO`.
   * **PDF Asset Verification:** Confirms both `Resume_<Company>.pdf` and `CoverLetter_<Company>.pdf` exist on disk and have healthy binary sizes.
   * **Integrity:** Blocks generic error fallbacks (e.g. `error.md`).
2. **Pre-Flight Heuristic Memory Check:**
   * Queries `agent-memory` for past failure patterns matching the company, role, or stack (e.g., `MEM-008` cover letter parser rules, `MEM-009` rate-limit rules).
3. **Eunchae QA Certification:**
   * Dispatches a structured **`🛡️ EUNCHAE — Quality Assurance Gatekeeper Audit`** card to `#agent-handoffs`.
   * Badges the Master Proposal Card in `#approvals` with her verdict (`🟢 QA CERTIFIED`, `🟡 QA PASS WITH POLISH`, or `🔴 QA DEFECT BLOCKED`).

---

## 🧠 Failure Learning Flywheel (Post-Mortem Engine)

Eunchae actively learns from rejections and corrections:

### 1. Rejection in `#approvals`
When you click **`❌`** on a proposal in `#approvals`:
* Sakura notes the status as `Discarded`.
* Eunchae triggers a post-mortem note with instructions on how to teach the squad what went wrong.

### 2. Teaching Eunchae Directly (`!reflect`)
You can teach the squad permanent rules directly in Discord:
```
!reflect <domain> | <symptom / what failed> | <permanent rule to follow>
```
*Example:*
```
!reflect cover_letter | body text was blank | Never terminate parsing on contact lines containing portfolio
```
Eunchae immediately logs the lesson to `agent-memory/store/experience_store.jsonl` and crystallizes it across all 5 agents.

### 3. Viewing Active Heuristics (`!lessons`)
In `#pc-vitals` or `#system-alerts`:
```
!lessons
```
*Displays all active failure lessons and permanent heuristics stored in memory.*

---

## 🛠️ Discord Commands Reference

| Command | Channel | Description |
| :--- | :--- | :--- |
| `!qa <filename>` | `#pc-vitals` / any | Audits an existing markdown application or file for QA defects. |
| `!lessons` (or `!failures`) | `#pc-vitals` | Views all learned failure post-mortems and permanent squad rules. |
| `!reflect <domain> \| <symptom> \| <rule>` | Any channel | Teaches Eunchae a new permanent rule on the spot. |
| `!vitals` (or `!health`) | `#pc-vitals` | Real-time PC CPU, RAM, disk, and uptime metrics. |
| `!checkin` | `#pc-vitals` | Full hardware and subsystem pulse report. |
