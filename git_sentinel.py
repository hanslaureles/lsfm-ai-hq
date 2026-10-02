import os
import sys
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # quiet: no console to reconfigure (pythonw, redirected stream)
        pass

BASE_DIR = Path(__file__).parent
WORKSPACE_DIR = BASE_DIR.parent
MEMORY_DIR = BASE_DIR / "memory"
# D6: the default repo is HQ itself. WORKSPACE_DIR (Projects/) is not a git repo, so
# every default check there failed ("Git Check Failed"). Pass repo_path for others.
GIT_REPO_DIR = BASE_DIR

from llm_client import query_llm, check_ollama_status, call_local_ollama

# Regex patterns for high-confidence credential / secret leak detection
SECRET_PATTERNS = [
    (r"(?i)api[_-]?key\s*[:=]\s*['\"]([a-zA-Z0-9_\-]{20,})['\"]", "Generic API Key"),
    (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{25,}", "Bearer Token"),
    (r"(?i)ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"(?i)sk-[a-zA-Z0-9]{32,}", "OpenAI / Claude Secret Key"),
    (r"(?i)gsk_[a-zA-Z0-9]{30,}", "Groq API Key"),
    (r"(?i)ai[a-zA-Z0-9_\-]{30,}", "Gemini API Key"),
    (r"(?i)AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "Private RSA/SSH Key"),
    (r"(?i)(password|secret|passwd)\s*[:=]\s*['\"][^'\"]{6,}['\"]", "Hardcoded Password/Secret"),
]
COMPILED_SECRET_PATTERNS = [(re.compile(p), n) for p, n in SECRET_PATTERNS]

def load_design_tokens() -> str:
    tokens_file = MEMORY_DIR / "design_system.md"
    if tokens_file.exists():
        return tokens_file.read_text(encoding="utf-8")
    return ""

def find_git_binary() -> str:
    import shutil
    git_in_path = shutil.which("git")
    if git_in_path:
        return git_in_path

    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd" / "git.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "git.exe",
        Path("C:/Program Files/Git/cmd/git.exe"),
        Path("C:/Program Files/Git/bin/git.exe"),
        Path("C:/Program Files (x86)/Git/cmd/git.exe")
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "git"

def run_git_command(args: List[str], cwd: Path) -> Tuple[int, str, str]:
    """Safely executes a git command in the target directory."""
    git_bin = find_git_binary()
    try:
        proc = subprocess.run(
            [git_bin] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def get_git_info(repo_path: Optional[Path] = None) -> Dict[str, Any]:
    """Extracts status, current branch, staged and unstaged file lists."""
    cwd = repo_path or GIT_REPO_DIR
    code, branch, _ = run_git_command(["branch", "--show-current"], cwd)
    if code != 0 or not branch:
        branch = "HEAD (detached)"

    code, status_out, _ = run_git_command(["status", "--porcelain"], cwd)
    
    staged = []
    unstaged = []
    untracked = []

    for line in status_out.splitlines():
        if len(line) < 3:
            continue
        index_status = line[0]
        worktree_status = line[1]
        filename = line[3:].strip()

        if index_status in ["M", "A", "R", "D"]:
            staged.append(filename)
        if worktree_status in ["M", "D"]:
            unstaged.append(filename)
        if index_status == "?" and worktree_status == "?":
            untracked.append(filename)

    code, commit_count, _ = run_git_command(["rev-list", "--count", "HEAD"], cwd)
    try:
        total_commits = int(commit_count) if code == 0 else 0
    except (ValueError, TypeError):
        total_commits = 0

    return {
        "repo_path": str(cwd),
        "branch": branch,
        "is_clean": len(staged) == 0 and len(unstaged) == 0 and len(untracked) == 0,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "total_commits": total_commits
    }

def get_git_diff(repo_path: Optional[Path] = None, staged: bool = False) -> str:
    """Retrieves the unified git diff. If staged=True, gets staged changes; otherwise unstaged (or both)."""
    cwd = repo_path or GIT_REPO_DIR
    
    if staged:
        code, diff_out, _ = run_git_command(["diff", "--cached"], cwd)
    else:
        # Check staged first
        code_s, diff_s, _ = run_git_command(["diff", "--cached"], cwd)
        # Check unstaged
        code_u, diff_u, _ = run_git_command(["diff"], cwd)
        
        parts = []
        if diff_s.strip():
            parts.append("### STAGED CHANGES:\n" + diff_s.strip())
        if diff_u.strip():
            parts.append("### UNSTAGED CHANGES:\n" + diff_u.strip())
            
        diff_out = "\n\n".join(parts)

    return diff_out.strip()

def scan_security_risks(diff_text: str) -> List[str]:
    """Performs deterministic regex scanning for secrets or accidental credential staging."""
    risks = []
    lines = diff_text.splitlines()

    # Check for .env or credential file patterns in diff headers
    for line in lines:
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            fname = line.split(" ", 1)[-1].strip()
            if any(s in fname.lower() for s in [".env", "id_rsa", "secret", "credentials.json", ".pem"]):
                risks.append(f"🚨 **Sensitive file modified/staged:** `{fname}`")

    # Check additions (+ lines) for secrets
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:].strip()
            for pattern, name in COMPILED_SECRET_PATTERNS:
                if pattern.search(content):
                    masked = content[:20] + "..." if len(content) > 20 else content
                    risks.append(f"⚠️ **Possible {name} leak:** `{masked}`")
                    break

    return list(dict.fromkeys(risks))  # Deduplicate

def execute_llm_query(prompt: str, system_instruction: str = "") -> str:
    """Routes to local Ollama if online (RX 6600 XT 0-cost), otherwise falls back to query_llm."""
    ollama_up, models = check_ollama_status()
    if ollama_up and any("qwen2.5-coder" in m.lower() for m in models):
        try:
            return call_local_ollama(prompt, system_instruction=system_instruction, model="qwen2.5-coder:7b")
        except Exception as _exc:
            print(f"⚠️ [git_sentinel.execute_llm_query] suppressed {type(_exc).__name__}: {_exc}", flush=True)
    return query_llm(prompt, system_instruction=system_instruction, temperature=0.2, agent="kazuha")

def review_code_diff(repo_path: Optional[Path] = None, staged_only: bool = False) -> Dict[str, Any]:
    """
    Performs Kazuha's Autonomous Code & PR Review on active workspace changes.
    """
    cwd = repo_path or GIT_REPO_DIR
    info = get_git_info(cwd)
    diff = get_git_diff(cwd, staged=staged_only)

    if not diff:
        return {
            "success": True,
            "status": "clean",
            "score": 100,
            "verdict": "🟢 CLEAN WORKING TREE",
            "security_alerts": [],
            "review": "✅ Working tree is completely clean! No modified, staged, or uncommitted code to review.",
            "info": info
        }

    # 1. Deterministic Security Pre-Flight
    security_alerts = scan_security_risks(diff)

    # 2. Design system context
    tokens = load_design_tokens()

    # Truncate diff if massive (>14,000 chars) to prevent context blowup
    truncated = False
    if len(diff) > 14000:
        diff_for_llm = diff[:14000] + "\n\n... [DIFF TRUNCATED FOR CONTEXT LENGTH] ..."
        truncated = True
    else:
        diff_for_llm = diff

    prompt = f"""
You are "Kazuha", Lead Frontend Architect and Autonomous Git & PR Sentinel for Hans Aaron Laureles.
Your duty is to perform a rigorous, disciplined pre-commit / PR code review of the following Git diff in the repository `{cwd.name}` (Branch: `{info['branch']}`).

### ACTIVE DESIGN SYSTEM STANDARDS:
{tokens}

### GIT DIFF TO REVIEW:
```diff
{diff_for_llm}
```

---

### INSTRUCTIONS:
Analyze the diff with surgical engineering precision and produce a structured markdown review containing:

1. **READINESS VERDICT & SCORE**:
   - Assign an engineering quality score (0 to 100%).
   - Choose one verdict:
     - `🟢 READY TO COMMIT` (Score >= 90%)
     - `🟡 CAUTION: MINOR POLISH RECOMMENDED` (Score 70-89%)
     - `🔴 BLOCKED: FIX CRITICAL ISSUES` (Score < 70%)

2. **CODE HEALTH & LOGIC AUDIT**:
   - Check for runtime bugs, null/undefined safety, race conditions, edge-case mishandling, and proper error boundaries.
   - Flag any leftover debug statements (e.g., `console.log`, `print()`, `debugger`, commented-out dead code).

3. **DESIGN SYSTEM & TOKEN CONFORMANCE**:
   - If web/UI code (HTML/CSS/JS/TS/React): check for hardcoded colors (should use CSS variables/tokens), responsive layout considerations, and accessibility (WCAG AA). If purely backend/Python, verify clean structure and typing.

4. **SECURITY & RISK SCAN**:
   - Flag any credentials, API keys, or unchecked user inputs.

5. **ACTIONABLE REFACTOR RECOMMENDATIONS**:
   - 2-3 concise bullet points showing exactly how to improve or clean up the code.

Tone: Calm, disciplined, precise, and authoritative. Format cleanly in Discord/GitHub Markdown.
"""
    system_instruction = "You are Kazuha, a master software architect and code reviewer. Be concise, objective, and technical."
    review_text = execute_llm_query(prompt, system_instruction=system_instruction)

    # Extract score if mentioned
    score = 85
    score_match = re.search(r"(?:Score|score)\s*[:=]?\s*(\d{1,3})%", review_text)
    if score_match:
        try:
            score = int(score_match.group(1))
        except Exception:  # quiet: unparsable score keeps the default
            pass

    verdict = "🟢 READY TO COMMIT" if score >= 90 else ("🟡 CAUTION" if score >= 70 else "🔴 BLOCKED")
    if security_alerts:
        verdict = "🔴 SECURITY ALERT / BLOCKED"
        score = min(score, 50)

    return {
        "success": True,
        "status": "changes_detected",
        "score": score,
        "verdict": verdict,
        "security_alerts": security_alerts,
        "review": review_text,
        "truncated": truncated,
        "info": info
    }

def generate_conventional_commit(repo_path: Optional[Path] = None, staged_only: bool = False) -> Dict[str, Any]:
    """
    Generates a high-precision Conventional Commit message based on the active diff.
    """
    cwd = repo_path or GIT_REPO_DIR
    diff = get_git_diff(cwd, staged=staged_only)

    if not diff:
        return {
            "success": False,
            "error": "Working tree is clean. Stage or modify files before generating a commit message."
        }

    # Truncate diff if massive
    diff_for_llm = diff[:12000] if len(diff) > 12000 else diff

    prompt = f"""
You are "Kazuha", Autonomous Git Sentinel for Hans Aaron Laureles.
Analyze the following Git diff and generate an exemplary, high-precision Conventional Commit message.

### GIT DIFF:
```diff
{diff_for_llm}
```

---

### REQUIREMENTS:
1. Follow the Conventional Commits specification: `<type>(<scope>): <short imperative summary>`
   - Types: `feat`, `fix`, `refactor`, `style`, `docs`, `test`, `chore`, `perf`.
   - The headline MUST be 72 characters or fewer, lowercase after prefix, no period at the end.
2. Under the headline, provide a blank line followed by 2 to 4 concise bullet points explaining the motivation and key changes.
3. If there are breaking changes, note them with `BREAKING CHANGE:`.
4. Output ONLY the commit message in a clean markdown code block. Do not include conversational filler.
"""
    commit_msg = execute_llm_query(prompt).strip()
    
    # Clean any surrounding markdown code fences if LLM wrapped it
    clean_msg = re.sub(r"^```(?:git|markdown|text)?\n?", "", commit_msg)
    clean_msg = re.sub(r"\n?```$", "", clean_msg).strip()

    first_line = clean_msg.splitlines()[0] if clean_msg else "chore: workspace updates"

    return {
        "success": True,
        "headline": first_line,
        "full_message": clean_msg,
        "staged_only": staged_only
    }

def generate_pr_description(repo_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Generates a production-ready GitHub Pull Request description.
    """
    cwd = repo_path or GIT_REPO_DIR
    info = get_git_info(cwd)
    diff = get_git_diff(cwd, staged=False)

    if not diff:
        # Try getting diff against main or HEAD~1
        code, diff, _ = run_git_command(["diff", "main...HEAD"], cwd)
        if not diff:
            code, diff, _ = run_git_command(["diff", "HEAD~1"], cwd)

    if not diff:
        return {
            "success": False,
            "error": "No commits or diff found to generate a Pull Request description."
        }

    diff_for_llm = diff[:14000] if len(diff) > 14000 else diff

    prompt = f"""
You are "Kazuha", Lead Architect and Git Sentinel for Hans Aaron Laureles.
Generate a comprehensive, executive-grade GitHub Pull Request (PR) description based on the active changes for branch `{info['branch']}`.

### GIT DIFF:
```diff
{diff_for_llm}
```

---

### INSTRUCTIONS:
Produce a complete Pull Request template in clean GitHub Markdown with:
1. **Title**: An informative, conventional PR title (e.g. `feat(sentinel): implement autonomous local code review & git sentinel`).
2. **📌 Overview & Motivation**: 2-3 sentences explaining why this change was made and the architectural value.
3. **🛠️ Key Changes**: Bulleted breakdown of major additions, modifications, or bug fixes grouped by component.
4. **🧪 Verification & Testing Plan**: How these changes were verified (manual tests, CLI commands, bot interaction).
5. **⚠️ Breaking Changes & Risk Assessment**: None or detailed.
6. **📋 Checklist**:
   - [x] Code passes architectural review
   - [x] No secrets or credentials exposed
   - [x] Design tokens / coding conventions respected
"""
    pr_text = execute_llm_query(prompt).strip()

    title_match = re.search(r"(?:Title|title)\s*[:=]?\s*([^\n]+)", pr_text)
    pr_title = title_match.group(1).strip("`*\" ") if title_match else f"feat({info['branch']}): workspace updates"

    return {
        "success": True,
        "title": pr_title,
        "body": pr_text,
        "branch": info["branch"]
    }

def apply_git_commit(commit_message: str, repo_path: Optional[Path] = None) -> Tuple[bool, str]:
    """Executes git commit -m with the generated message."""
    cwd = repo_path or GIT_REPO_DIR
    code, out, err = run_git_command(["commit", "-m", commit_message], cwd)
    if code == 0:
        return True, out or "Committed successfully."
    return False, err or out or "Git commit failed."

# ==============================================================================
# CLI ENTRYPOINT
# ==============================================================================
def cli_main():
    import argparse
    parser = argparse.ArgumentParser(
        description="⚔️ Kazuha's Local Git & PR Sentinel — Autonomous Code Reviewer & Commit Copilot"
    )
    subparsers = parser.add_subparsers(dest="command", help="Sentinel Command")

    # Command: status
    subparsers.add_parser("status", help="Inspect workspace git status & telemetry")

    # Command: review
    rev_p = subparsers.add_parser("review", help="Perform autonomous architectural code review")
    rev_p.add_argument("--staged", action="store_true", help="Review staged changes only")
    rev_p.add_argument("--path", type=str, default=None, help="Target git repository path")

    # Command: commit
    com_p = subparsers.add_parser("commit", help="Generate Conventional Commit message")
    com_p.add_argument("--apply", action="store_true", help="Automatically commit changes with generated message")
    com_p.add_argument("--staged", action="store_true", help="Generate from staged changes only")
    com_p.add_argument("--path", type=str, default=None, help="Target git repository path")

    # Command: pr
    pr_p = subparsers.add_parser("pr", help="Generate a production-ready GitHub PR description")
    pr_p.add_argument("--path", type=str, default=None, help="Target git repository path")

    args = parser.parse_args()
    target_path = Path(args.path).resolve() if getattr(args, "path", None) else GIT_REPO_DIR

    print("=" * 65)
    print("⚔️  KAZUHA'S LOCAL GIT & PR SENTINEL — LE SSERAFIM AI HQ")
    print(f"📁 Repository: {target_path}")
    print("=" * 65)

    if args.command in [None, "status"]:
        info = get_git_info(target_path)
        print(f"🌿 Current Branch:   {info['branch']}")
        print(f"📦 Total Commits:    {info['total_commits']}")
        print(f"✨ Working Tree:     {'CLEAN ✅' if info['is_clean'] else 'MODIFIED ⚠️'}")
        if info['staged']:
            print(f"📥 Staged Files ({len(info['staged'])}):")
            for f in info['staged']:
                print(f"   + {f}")
        if info['unstaged']:
            print(f"📝 Unstaged Files ({len(info['unstaged'])}):")
            for f in info['unstaged']:
                print(f"   * {f}")
        if info['untracked']:
            print(f"❓ Untracked Files ({len(info['untracked'])}):")
            for f in info['untracked'][:5]:
                print(f"   ? {f}")
            if len(info['untracked']) > 5:
                print(f"   ... and {len(info['untracked']) - 5} more")

    elif args.command == "review":
        print("🔍 Performing autonomous code & security review via local/cloud engine...")
        res = review_code_diff(target_path, staged_only=args.staged)
        print("\n" + "-" * 65)
        print(f"VERDICT: {res['verdict']} | SCORE: {res['score']}%")
        print("-" * 65)
        if res.get("security_alerts"):
            print("\n🚨 SECURITY ALERTS DETECTED:")
            for alert in res["security_alerts"]:
                print(f"  {alert}")
        print("\n" + res["review"])
        print("-" * 65)

    elif args.command == "commit":
        print("✍️ Synthesizing Conventional Commit message from active diff...")
        res = generate_conventional_commit(target_path, staged_only=args.staged)
        if not res.get("success"):
            print(f"⚠️ {res.get('error')}")
            return

        print("\n" + "-" * 65)
        print("PROPOSED CONVENTIONAL COMMIT:")
        print("-" * 65)
        print(res["full_message"])
        print("-" * 65)

        if args.apply:
            confirm = input("\nDo you want to apply this commit now? (y/N): ").strip().lower()
            if confirm == "y":
                ok, out = apply_git_commit(res["full_message"], target_path)
                if ok:
                    print("✅ Commit applied successfully!")
                else:
                    print(f"❌ Error applying commit: {out}")
            else:
                print("Commit aborted.")

    elif args.command == "pr":
        print("📄 Generating GitHub PR description from active diff...")
        res = generate_pr_description(target_path)
        if not res.get("success"):
            print(f"⚠️ {res.get('error')}")
            return
        print("\n" + "=" * 65)
        print(f"PR TITLE: {res['title']}")
        print("=" * 65)
        print(res["body"])
        print("=" * 65)

if __name__ == "__main__":
    cli_main()
