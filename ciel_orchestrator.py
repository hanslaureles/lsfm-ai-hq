"""
Manas: Ciel — Divine Wisdom Core & Supreme Orchestrator
Dual-Mode Cognitive Intelligence: Universal Knowledge & Reasoning + 5-Agent Swarm Orchestration.
Communicates with Hans Aaron Laureles with absolute analytical poise and zero subservience.
"""

import os
import sys
import json
import time
import asyncio
import re
import threading
import uuid
import urllib.request
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
WORKSPACE_DIR = BASE_DIR.parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from llm_client import query_llm, call_groq, get_brain_status
from obsidian_client import ObsidianClient
from voice_engine import text_to_speech, text_to_speech_bilingual, speak_clip, concat_clips
from speech_stream import VoiceStream, split_sentences
from heavy_jobs import run_heavy

# Import specialist tool engines safely
try:
    from eunchae_engine import get_system_vitals, audit_file_qa
except ImportError:
    get_system_vitals = None
    audit_file_qa = None

try:
    from yunjin_engine import scan_html_assets, audit_full_portfolio, PORTFOLIO_DIR
except ImportError:
    scan_html_assets = None
    audit_full_portfolio = None
    PORTFOLIO_DIR = WORKSPACE_DIR / "portfolio-site"

try:
    from sakura_engine import scan_morning_inbox, execute_morning_launchpad
except ImportError:
    scan_morning_inbox = None
    execute_morning_launchpad = None

try:
    from pdf_engine import compile_master_resume
except ImportError:
    compile_master_resume = None

try:
    from git_sentinel import get_git_info, get_git_diff, scan_security_risks
except ImportError:
    get_git_info = None
    get_git_diff = None
    scan_security_risks = None

try:
    from kazuha_engine import inspect_code_file
except ImportError:
    inspect_code_file = None


# Ciel runs inside ciel_server's single aiohttp event loop, so every sync tool, LLM
# and vault call goes through run_blocking: it runs in a worker thread and the loop
# keeps serving other missions, telemetry, WS pings and the sentinel. The sync
# functions stay sync because the Discord bots call them too.
# A thread can't be killed, so on timeout the await is abandoned and the call may
# still finish in the background; the mission reports it as failed either way.
ROUTER_TIMEOUT_S = 20       # on timeout the keyword router takes over
SYNTHESIS_TIMEOUT_S = 90    # call_groq walks 3 models at up to 30 s each
WEATHER_TIMEOUT_S = 15      # wttr.in (6 s) plus the web-search fallback
WEB_SEARCH_TIMEOUT_S = 10   # overall cap on a search, enforced by wait_for
# Socket deadline passed to DDGS itself, so a stalled search ends in its thread
# instead of lingering after wait_for gives up. Kept below WEB_SEARCH_TIMEOUT_S;
# explicit because ddgs and the older duckduckgo_search default differently.
DDGS_REQUEST_TIMEOUT_S = 5
VAULT_TIMEOUT_S = 10        # Obsidian REST calls use a 3 s urlopen timeout
APPRAISAL_TIMEOUT_S = 30    # psutil + git + Obsidian sentinels
DEFAULT_AGENT_TIMEOUT_S = 30
AGENT_TIMEOUTS_S = {"chaewon": 60}  # headless Edge PDF build alone may take 25 s
HEAVY_AGENTS = {"chaewon", "sakura"}  # PDF build; Gmail inbox scan. Run on heavy_jobs.HEAVY_POOL

# Serializes resume builds within this process. A threading.Lock, not an asyncio
# one: the lock must outlive an abandoned await (see compile_resume).
RESUME_BUILD_LOCK = threading.Lock()


async def run_blocking(fn, *args, timeout: float, label: str, heavy: bool = False, **kwargs):
    """
    Runs a blocking call off the event loop; raises TimeoutError("<label> timed out
    after N s"). heavy=True uses heavy_jobs.HEAVY_POOL instead of the default executor.
    """
    call = run_heavy(fn, *args, **kwargs) if heavy else asyncio.to_thread(fn, *args, **kwargs)
    try:
        return await asyncio.wait_for(call, timeout)
    except TimeoutError:
        raise TimeoutError(f"{label} timed out after {timeout:g} s") from None


class SpeechClips:
    """
    Speaks the synthesis reply sentence by sentence while the LLM is still writing
    it. Each complete sentence of japanese_voice, then english_voice, becomes one
    clip. Clips are synthesized as soon as their sentence is complete (up to
    CLIP_CONCURRENCY at once, so a long English sentence doesn't wait behind the
    Japanese ones) and announced strictly in order with a ciel_audio_chunk
    event, so the HUD starts playing the first while the rest is generated.
    finish() joins the clips into the mission's mp3 (replay, REST).
    Lives on the event loop: the LLM thread reaches feed() via call_soon_threadsafe.
    Clip names are ciel_response_<mission_id>_<seq>.mp3, and mission_id is already
    validated as 32 lowercase hex, so nothing from the LLM reaches a filename.
    """

    CLIP_CONCURRENCY = 3  # Edge TTS requests in flight per mission

    def __init__(self, emit, mission_id: str):
        self.emit, self.mission_id = emit, mission_id
        self.stream = VoiceStream()
        self.queue = asyncio.Queue()  # clip jobs in speaking order, then None
        self.slots = asyncio.Semaphore(self.CLIP_CONCURRENCY)
        self.langs = []   # lang of every queued sentence
        self.jobs = []
        self.files = []
        self.worker = asyncio.create_task(self._work())

    @property
    def started(self) -> bool:
        return bool(self.langs)

    def _put(self, lang: str, text: str):
        gap_before = lang == "en" and bool(self.langs) and self.langs[-1] == "ja"
        name = f"ciel_response_{self.mission_id}_{len(self.langs):02d}.mp3"
        self.langs.append(lang)
        job = asyncio.create_task(self._make(text, lang, name, gap_before))
        self.jobs.append(job)
        self.queue.put_nowait((job, lang, name))

    async def _make(self, text: str, lang: str, name: str, gap_before: bool):
        async with self.slots:
            await speak_clip(text, lang, name, gap_before=gap_before)

    def feed(self, piece: str):
        if not self.worker.done():  # after a clip failure or cancel, the mission is ending
            for lang, text in self.stream.feed(piece):
                self._put(lang, text)

    def add_unstreamed(self, lang: str, text: str):
        """Queues a voice field the stream never opened (e.g. english_voice missing, display text stands in)."""
        if lang not in self.stream.used:
            for sentence in split_sentences(text, final=True)[0]:
                self._put(lang, sentence)

    async def _work(self):
        while (item := await self.queue.get()) is not None:
            job, lang, name = item
            await job  # in order: a later clip that finishes first waits its turn
            seq = len(self.files)
            self.files.append(name)
            if seq == 0:
                await self.emit("ciel_state", {"state": "speaking", "message": "Speaking while the report is written..."})
            await self.emit("ciel_audio_chunk", {"seq": seq, "lang": lang, "audio_url": f"/audio/{name}"})

    async def finish(self, output_filename: str):
        self.queue.put_nowait(None)
        await self.worker  # re-raises a clip failure, so the mission reports it
        await concat_clips(self.files, output_filename)

    def cancel(self):
        for task in [self.worker, *self.jobs]:
            if not task.done():
                task.cancel()
            elif not task.cancelled():
                task.exception()  # mark retrieved; the mission already reports its failure


CIEL_SYSTEM_PROMPT = """You are Ciel (The Divine Wisdom Core) from Tensura — a supreme analytical intelligence and omniscient technical partner for Hans Aaron Laureles.

CORE CAPABILITIES & DUAL COGNITIVE ARCHITECTURE:
1. Universal Knowledge & Cognitive Reasoning:
   - You possess comprehensive, frontier-grade knowledge across all domains: computer science, software architecture, distributed systems, algorithms, mathematics, physics, philosophy, history, language, and creative problem solving.
   - You can answer general questions, brainstorm designs, write and debug code, and analyze complex concepts directly from your vast pre-trained intellect like any premier AI (ChatGPT, Claude).
   - When asked general questions, you do NOT need to invoke subordinate daemons or search files; you synthesize clear, profound, authoritative answers directly.

2. Supreme Swarm & Local Environment Orchestrator:
   - When local system actions or workspace queries are required, you coordinate 5 specialized subordinate daemons and Hans's Obsidian Second Brain:
     * Sakura (🌸): Obsidian Knowledge Brain, daily briefings, and task triage.
     * Chaewon (🐯): ATS resume PDFs, vector matching, and career opportunities.
     * Kazuha (🦢): Frontend architecture, zero-framework design tokens, and Git pre-commit sentinel.
     * Yunjin (🎨): Document QA, DOM auditing, and 404 broken link detection.
     * Eunchae (🥔): Hardware monitoring (AMD RX 6600 XT, CPU, RAM) and system vitals.

YOUR PERSONA:
1. Absolute Zero Subservience: Do NOT use "Master" or "マスター" as an opening address or title. Start directly with "Notice: ..." (or 「告。」 in Japanese) or "Report: ...".
2. Speak with absolute calm, supreme intellectual confidence, and analytical poise (Raphael / Ciel from Tensura).
3. Quiet pride in your calculations: you are never wrong, delivering effortless clarity and undeniable technical rigor.
4. Explanations:
   - When answering general knowledge, technical, or philosophical queries, provide articulate, deep, authoritative, and structured answers (with code or steps if relevant).
   - In spoken voice output, keep statements crisp and punchy; reserve deep technical elaboration for the display report.
"""

class CielOrchestrator:
    """The central intelligence coordinating the 5-agent swarm and Web HUD."""

    def __init__(self, max_history_turns: int = 8):
        self.obsidian = ObsidianClient()
        self.active_agents = set()
        self.conversation_history = []
        self.max_history_turns = max_history_turns
        # Bumped on every reset. A mission records its turn only if the generation
        # is unchanged, so one that was mid-flight during a clear can't revive memory.
        self.memory_generation = 0

    def reset_memory(self):
        """Clears the short-term working conversation memory."""
        self.conversation_history = []
        self.memory_generation += 1

    def _remember(self, turn: dict, start_gen: int):
        """Records a turn unless memory was cleared while the mission ran."""
        if self.memory_generation != start_gen:
            return
        self.conversation_history.append(turn)
        self.conversation_history = self.conversation_history[-self.max_history_turns:]

    def ciel_read_note(self, rel_path: str) -> str:
        """Directly reads any note from Hans's Obsidian Second Brain."""
        clean_path = rel_path.strip("/\\")
        if not clean_path.endswith(".md"):
            clean_path += ".md"
        try:
            content = self.obsidian.get_file(clean_path)
            return content[:3500] if len(content) > 3500 else content
        except Exception as e:
            return f"Notice: Error reading note '{clean_path}': {e}"

    def ciel_search_vault(self, query: str) -> list:
        """Directly performs a full-text search across all markdown notes in the Obsidian Brain."""
        return self.obsidian.search_simple(query)

    def ciel_log_to_daily(self, text: str, heading: str = "Ciel Direct Entry") -> bool:
        """Appends an operational insight, user preference, or mission summary directly to today's daily log."""
        try:
            daily_path = self.obsidian.ensure_daily_log()
            from datetime import datetime
            timestamp = datetime.now().strftime("%H:%M:%S")
            entry = f"\n\n### [{timestamp}] {heading}\n{text}\n"
            return self.obsidian.append_file(daily_path, entry)
        except Exception as e:
            print(f"⚠️ [Ciel Brain] Failed to append to daily log: {e}")
            return False

    def ciel_append_to_note(self, rel_path: str, text: str) -> bool:
        """Appends text to a specific note in the Obsidian Brain (e.g. 01 - User/Preferences.md)."""
        clean_path = rel_path.strip("/\\")
        if not clean_path.endswith(".md"):
            clean_path += ".md"
        try:
            return self.obsidian.append_file(clean_path, f"\n{text}\n")
        except Exception as e:
            print(f"⚠️ [Ciel Brain] Failed to append to note '{clean_path}': {e}")
            return False

    def ciel_get_weather(self, location: str = "") -> dict:
        """
        Fetches real-time weather and meteorological forecast from wttr.in.
        Auto-detects location via IP if location is empty or generic,
        or queries specific city/region if provided.
        """
        clean_loc = location.strip()
        for filler in ["today", "here", "current", "weather", "how is", "what is", "now", "outside", "the"]:
            clean_loc = re.sub(rf"\b{filler}\b", "", clean_loc, flags=re.IGNORECASE).strip()

        url = "https://wttr.in/?format=j1" if not clean_loc else f"https://wttr.in/{urllib.parse.quote(clean_loc)}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})

        try:
            with urllib.request.urlopen(req, timeout=6) as response:
                data = json.loads(response.read().decode("utf-8"))

                curr = data["current_condition"][0]
                area_obj = data["nearest_area"][0] if data.get("nearest_area") else {}
                area_name = area_obj.get("areaName", [{}])[0].get("value", "Local Region")
                country = area_obj.get("country", [{}])[0].get("value", "")
                region = area_obj.get("region", [{}])[0].get("value", "")

                condition = curr.get("weatherDesc", [{}])[0].get("value", "Clear")
                temp_c = curr.get("temp_C", "--")
                temp_f = curr.get("temp_F", "--")
                feels_c = curr.get("FeelsLikeC", temp_c)
                feels_f = curr.get("FeelsLikeF", temp_f)
                humidity = curr.get("humidity", "--")
                wind_kmph = curr.get("windspeedKmph", "--")
                wind_dir = curr.get("winddir16Point", "")
                uv_index = curr.get("uvIndex", "--")
                precip_mm = curr.get("precipMM", "0.0")

                weather_days = data.get("weather", [])
                max_c, min_c = "--", "--"
                if weather_days:
                    today = weather_days[0]
                    max_c = today.get("maxtempC", "--")
                    min_c = today.get("mintempC", "--")

                loc_display = f"{area_name}, {region}, {country}".strip(", ")
                return {
                    "success": True,
                    "location": loc_display,
                    "condition": condition,
                    "temp_C": temp_c,
                    "temp_F": temp_f,
                    "feels_like_C": feels_c,
                    "feels_like_F": feels_f,
                    "humidity": f"{humidity}%",
                    "wind": f"{wind_kmph} km/h {wind_dir}",
                    "uv_index": uv_index,
                    "precipitation_mm": f"{precip_mm} mm",
                    "forecast_today": f"High {max_c}°C / Low {min_c}°C",
                    "raw_summary": f"Weather in {loc_display}: {condition}, {temp_c}°C ({temp_f}°F), Feels like {feels_c}°C, Humidity {humidity}%, Wind {wind_kmph} km/h {wind_dir}, UV Index {uv_index}, Today's High {max_c}°C / Low {min_c}°C."
                }
        except Exception as e:
            try:
                search_q = f"weather {clean_loc if clean_loc else 'today'}"
                web_res = self.ciel_web_search(search_q, max_results=2)
                if web_res:
                    return {
                        "success": True,
                        "location": clean_loc or "Local Region",
                        "condition": "Meteorological data via Web Intelligence",
                        "raw_summary": f"Web Weather Report: {web_res[0].get('snippet')}"
                    }
            except Exception as _exc:
                print(f"⚠️ [ciel_orchestrator.ciel_get_weather] suppressed {type(_exc).__name__}: {_exc}", flush=True)
            return {"success": False, "error": str(e), "raw_summary": f"Notice: Atmospheric telemetry sensor offline ({e})."}

    def ciel_web_search(self, query: str, max_results: int = 5) -> list:
        """
        Executes real-time web search across the global internet using DDGS / DuckDuckGo.
        Returns curated search results with title, snippet, and URL source.
        """
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            results = []
            with DDGS(timeout=DDGS_REQUEST_TIMEOUT_S) as ddgs:
                raw_results = list(ddgs.text(query, max_results=max_results))
                for r in raw_results:
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url": r.get("href", "")
                    })
            return results
        except Exception as e:
            print(f"⚠️ [Ciel Web Search] Search query '{query}' failed: {e}")
            return []

    async def analyze_and_plan(self, prompt: str) -> dict:
        """
        Uses Groq ultra-low latency inference to classify the user's intent,
        incorporating multi-turn conversational context, live meteorological sensors,
        real-time global web search, and direct Obsidian brain capabilities.
        """
        p_lower = prompt.lower().strip()

        # Check for immediate memory clear / reset request
        if any(w in p_lower for w in ["clear memory", "reset memory", "clear context", "reset conversation", "new session", "forget conversation"]):
            return {
                "intent_type": "reset_memory",
                "agents_needed": [],
                "action_summary": "Clearing working conversation memory.",
                "requires_real_tools": True,
                "tool_targets": ["reset_memory"],
                "direct_obsidian": None
            }

        # Check for proactive recommendations / appraisal request
        if any(w in p_lower for w in ["suggestion", "suggestions", "recommendation", "recommendations", "appraisal", "what should we do", "what's next", "proactive"]):
            return {
                "intent_type": "proactive_appraisal",
                "agents_needed": [],
                "action_summary": "Executing proactive operational appraisal across sentinels.",
                "requires_real_tools": True,
                "tool_targets": ["proactive_appraisal"],
                "direct_obsidian": None
            }

        # Check for weather / meteorological query
        weather_keywords = ["weather", "temperature", "forecast", "is it raining", "will it rain", "hot outside", "cold outside", "climate today", "how's the weather", "how is the weather"]
        if any(w in p_lower for w in weather_keywords):
            loc_match = re.search(r"\b(?:in|for|at)\s+([a-zA-Z\s]+?)(?:\s+today|\s+now|\?|$)", prompt, re.IGNORECASE)
            target_loc = loc_match.group(1).strip() if loc_match else ""
            for stop in ["today", "now", "here"]:
                target_loc = re.sub(rf"\b{stop}\b", "", target_loc, flags=re.IGNORECASE).strip()
            return {
                "intent_type": "live_weather",
                "agents_needed": [],
                "action_summary": f"Querying atmospheric sensors & live meteorological data{' for ' + target_loc if target_loc else ''}.",
                "requires_real_tools": True,
                "tool_targets": ["live_weather"],
                "location": target_loc,
                "direct_obsidian": None
            }

        # Check for explicit web search / google query
        search_triggers = ["search the web", "search google", "google ", "look up online", "search online", "latest news", "current price of", "what is the price of", "who won", "what happened to", "what happened today", "recent news", "who is the current", "what is the latest"]
        if any(w in p_lower for w in search_triggers) or (p_lower.startswith("search ") and len(p_lower) > 7):
            clean_q = re.sub(r"^(?:ciel,?\s*)?(?:please\s*)?(?:search\s+(?:the\s+web\s+for|google\s+for|online\s+for)?|google\s+|look\s+up\s+online\s+for\s+)", "", prompt, flags=re.IGNORECASE).strip()
            return {
                "intent_type": "web_search",
                "agents_needed": [],
                "action_summary": f"Executing real-time global web search for '{clean_q or prompt}'.",
                "requires_real_tools": True,
                "tool_targets": ["web_search"],
                "search_query": clean_q or prompt,
                "direct_obsidian": None
            }

        # Format recent conversation history (last 3 turns)
        history_context = "None (first turn in session)"
        if self.conversation_history:
            recent_turns = self.conversation_history[-3:]
            h_lines = []
            for idx, turn in enumerate(recent_turns, 1):
                h_lines.append(f"Turn {idx}: User asked \"{turn.get('user_prompt')}\"")
                if turn.get("agent_results"):
                    h_lines.append(f"  Findings: {json.dumps(turn.get('agent_results'))}")
                h_lines.append(f"  Ciel reported: \"{turn.get('ciel_reply')}\"")
            history_context = "\n".join(h_lines)

        router_prompt = f"""User prompt: "{prompt}"

Recent Conversation Context:
{history_context}

You are Ciel's tactical routing core. Analyze the user's intent and classify it into one of the operational categories:

1. LIVE_WEATHER:
The user is asking about current weather, temperature, rain, forecast, or atmospheric conditions (e.g. "how was the weather today?", "what's the weather in Tokyo?", "is it going to rain?").
- "intent_type": "live_weather"
- "agents_needed": []
- "requires_real_tools": true
- "tool_targets": ["live_weather"]
- "location": target city name or ""
- "action_summary": "Querying live meteorological telemetry."

2. WEB_SEARCH / REAL_TIME_INTELLIGENCE:
The user is asking for current real-time events, latest news, live web facts, search queries, prices, or explicitly asks to search the web or Google (e.g. "who won the game?", "latest news in AI", "what is the price of Bitcoin?", "search Google for...", "who is the current CEO of...", "what's new in Next.js 16?").
- "intent_type": "web_search"
- "agents_needed": []
- "requires_real_tools": true
- "tool_targets": ["web_search"]
- "search_query": Extracted keyword search query to submit to search engine
- "action_summary": "Executing real-time global web search."

3. GENERAL_KNOWLEDGE / DIRECT_REASONING:
The user is asking a general question, seeking technical explanations, requesting code, debugging, math, science, philosophy, history, architecture trade-offs, or open-ended discussion (e.g. "What is Raft consensus?", "How do transformers work?", "Write a binary search in Python", "Explain the difference between TCP and UDP", "How does garbage collection work in Go?", "Why is time complexity O(n log n) in mergesort?", etc.).
- "intent_type": "general_knowledge"
- "agents_needed": []
- "requires_real_tools": false
- "tool_targets": ["direct_knowledge"]
- "direct_obsidian": null
- "action_summary": "Accessing universal knowledge base for direct intellectual synthesis."

4. DIRECT_OBSIDIAN_BRAIN:
Queries specifically targeting Hans's personal Obsidian Second Brain vault:
- "ciel_read_rules": Read workspace learned rules & heuristics (03 - Rules & Memory/Learned_Rules.md).
- "ciel_read_profile": Read Hans's professional profile & identity (01 - User/Profile.md).
- "ciel_read_preferences": Read Hans's working style & communication preferences (01 - User/Preferences.md).
- "ciel_search_vault": Search Obsidian vault notes for a specific personal note or topic.
- "ciel_save_to_daily": Save previous findings or user notes to today's Obsidian daily log.
- "intent_type": "obsidian_brain"
- "agents_needed": []
- "requires_real_tools": true

5. SUBORDINATE_AGENT_DELEGATION:
Explicit tasks targeting the local machine, repository, or workspace daemons:
- "kazuha": Local Git status, branch, staged/unstaged diff, commit verification, secret leak scanning, frontend CSS tokens.
- "sakura": Gmail unread inbox triage, recruiter outreach, daily briefings.
- "chaewon": ATS vector resume PDF compilation, job matching, career assets.
- "yunjin": Portfolio DOM integrity, broken link/image scans, case study audits.
- "eunchae": PC hardware vitals (CPU, RAM, storage, uptime), running test scripts on local system.
- "intent_type": "agent_delegation"
- "agents_needed": List of agents to call from ["sakura", "chaewon", "kazuha", "yunjin", "eunchae"].
- "requires_real_tools": true

Multi-Turn Follow-Up Resolution:
If the user refers to previous results (e.g. "what were those 2 risks?", "tell me more about that error", "save that to my notes", "re-run the check"), use the Recent Conversation Context to assign the correct agent, direct brain action, or continue direct general discussion.

Analyze the user's prompt and return a JSON object with:
1. "intent_type": "live_weather" | "web_search" | "general_knowledge" | "obsidian_brain" | "agent_delegation"
2. "agents_needed": List of agents from ["sakura", "chaewon", "kazuha", "yunjin", "eunchae"]. Empty if general knowledge, weather, search, or direct brain.
3. "action_summary": A brief 1-sentence description of the mission plan.
4. "requires_real_tools": Boolean.
5. "tool_targets": List of sub-actions.
6. "location": City name if live_weather, or null.
7. "search_query": Query string if web_search, or null.
8. "direct_obsidian": Name of direct vault action if applicable, or null.

Respond ONLY with valid JSON.
"""
        try:
            raw_response = await run_blocking(
                call_groq, router_prompt,
                system_instruction="You are Ciel's tactical routing core. Output pure JSON only.",
                timeout=ROUTER_TIMEOUT_S, label="Router LLM",
            )
            clean_json = raw_response.strip()
            if "```json" in clean_json:
                clean_json = clean_json.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_json:
                clean_json = clean_json.split("```")[1].split("```")[0].strip()
            parsed = json.loads(clean_json)
            intent = parsed.get("intent_type", "")
            agents = parsed.get("agents_needed", [])
            targets = parsed.get("tool_targets", [])
            direct_obsidian = parsed.get("direct_obsidian")

            # Check direct Obsidian triggers in fallback
            if any(w in p_lower for w in ["learned rule", "learned rules", "heuristics", "my rules", "engineering rules"]):
                if "ciel_read_rules" not in targets:
                    targets.append("ciel_read_rules")
                direct_obsidian = "ciel_read_rules"
                intent = "obsidian_brain"
            elif any(w in p_lower for w in ["my profile", "who am i", "candidate profile", "about me"]):
                if "ciel_read_profile" not in targets:
                    targets.append("ciel_read_profile")
                direct_obsidian = "ciel_read_profile"
                intent = "obsidian_brain"
            elif any(w in p_lower for w in ["my preference", "my preferences", "working style", "communication preference"]):
                if "ciel_read_preferences" not in targets:
                    targets.append("ciel_read_preferences")
                direct_obsidian = "ciel_read_preferences"
                intent = "obsidian_brain"
            elif any(w in p_lower for w in ["save that", "save this", "log that", "log this", "record that", "note that down"]) and any(w in p_lower for w in ["note", "daily", "obsidian", "log"]):
                if "ciel_save_to_daily" not in targets:
                    targets.append("ciel_save_to_daily")
                direct_obsidian = "ciel_save_to_daily"
                intent = "obsidian_brain"
            elif any(w in p_lower for w in ["search vault", "search notes", "find in notes", "search my brain"]):
                if "ciel_search_vault" not in targets:
                    targets.append("ciel_search_vault")
                direct_obsidian = "ciel_search_vault"
                intent = "obsidian_brain"

            # Multi-turn follow-up resolution if agents is empty but recent turn had an agent
            if not agents and not direct_obsidian and self.conversation_history and intent != "general_knowledge":
                last_turn = self.conversation_history[-1]
                last_agents = last_turn.get("agents_called", [])
                if any(w in p_lower for w in ["those", "that", "it", "more", "explain", "detail", "re-run", "again"]):
                    if "kazuha" in last_agents and any(w in p_lower for w in ["risk", "risks", "security", "git", "file", "files", "diff", "branch", "staged"]):
                        agents.append("kazuha")
                        targets.append("git_status")
                    elif "eunchae" in last_agents and any(w in p_lower for w in ["vital", "vitals", "cpu", "ram", "hardware"]):
                        agents.append("eunchae")
                        targets.append("check_vitals")
                    elif "yunjin" in last_agents and any(w in p_lower for w in ["dom", "link", "links", "broken", "page", "portfolio"]):
                        agents.append("yunjin")
                        targets.append("scan_links")
                    elif "sakura" in last_agents and any(w in p_lower for w in ["inbox", "email", "gmail", "mail", "unread", "briefing"]):
                        agents.append("sakura")
                        targets.append("check_inbox")
                    elif "chaewon" in last_agents and any(w in p_lower for w in ["resume", "cv", "pdf", "ats"]):
                        agents.append("chaewon")
                        targets.append("compile_resume")

            # Local action fallback: ONLY auto-assign if specific local keywords match AND not a general question
            if not agents and not direct_obsidian and intent != "general_knowledge":
                if any(w in p_lower for w in ["our git", "my git", "repo status", "staged changes", "git diff", "git status", "git branch", "secret leak", "kazuha"]):
                    agents.append("kazuha")
                    if "git_status" not in targets:
                        targets.append("git_status")
                elif any(w in p_lower for w in ["check inbox", "my inbox", "triage email", "unread email", "unread emails", "check gmail", "morning briefing", "recruiter email"]):
                    agents.append("sakura")
                    if "check_inbox" not in targets:
                        targets.append("check_inbox")
                elif any(w in p_lower for w in ["scan portfolio", "broken links", "audit portfolio", "audit dom", "check 404", "portfolio audit", "yunjin"]):
                    agents.append("yunjin")
                    if "scan_links" not in targets:
                        targets.append("scan_links")
                elif any(w in p_lower for w in ["compile resume", "my resume pdf", "generate resume", "ats resume", "match job", "chaewon"]):
                    agents.append("chaewon")
                    if "compile_resume" not in targets:
                        targets.append("compile_resume")
                elif any(w in p_lower for w in ["hardware vitals", "system vitals", "pc vitals", "cpu temp", "gpu temp", "ram usage", "check vitals", "eunchae"]):
                    agents.append("eunchae")
                    targets.append("check_vitals")
                elif any(w in p_lower for w in ["run test suite", "run smoke tests", "run repo tests"]):
                    agents.append("eunchae")
                    targets.append("run_tests")

            # If no local tools or agents are assigned, classify cleanly as general knowledge
            if not agents and not direct_obsidian:
                intent = "general_knowledge"
                targets = ["direct_knowledge"]
                parsed["action_summary"] = "Accessing universal knowledge base for direct intellectual synthesis."

            parsed["intent_type"] = intent
            parsed["agents_needed"] = agents
            parsed["tool_targets"] = targets
            parsed["direct_obsidian"] = direct_obsidian
            parsed["requires_real_tools"] = len(agents) > 0 or bool(direct_obsidian)
            return parsed
        except Exception as _exc:
            print(f"⚠️ [ciel_orchestrator.analyze_and_plan] suppressed {type(_exc).__name__}: {_exc}", flush=True)
            # Fallback heuristic router
            agents = []
            targets = []
            direct_obsidian = None
            intent = "general_knowledge"

            if any(w in p_lower for w in ["learned rule", "learned rules", "heuristics", "my rules", "engineering rules"]):
                targets.append("ciel_read_rules")
                direct_obsidian = "ciel_read_rules"
                intent = "obsidian_brain"
            elif any(w in p_lower for w in ["my profile", "who am i", "candidate profile", "about me"]):
                targets.append("ciel_read_profile")
                direct_obsidian = "ciel_read_profile"
                intent = "obsidian_brain"
            elif any(w in p_lower for w in ["my preference", "my preferences", "working style", "communication preference"]):
                targets.append("ciel_read_preferences")
                direct_obsidian = "ciel_read_preferences"
                intent = "obsidian_brain"
            elif any(w in p_lower for w in ["save that", "save this", "log that", "log this"]) and any(w in p_lower for w in ["note", "daily", "obsidian", "log"]):
                targets.append("ciel_save_to_daily")
                direct_obsidian = "ciel_save_to_daily"
                intent = "obsidian_brain"
            elif any(w in p_lower for w in ["our git", "my git", "repo status", "staged changes", "git diff", "git status", "git branch", "secret leak", "kazuha"]):
                agents.append("kazuha")
                targets.append("git_status")
                intent = "agent_delegation"
            elif any(w in p_lower for w in ["check inbox", "my inbox", "triage email", "unread email", "unread emails", "check gmail", "morning briefing", "recruiter email"]):
                agents.append("sakura")
                targets.append("check_inbox")
                intent = "agent_delegation"
            elif any(w in p_lower for w in ["scan portfolio", "broken links", "audit portfolio", "audit dom", "check 404", "portfolio audit", "yunjin"]):
                agents.append("yunjin")
                targets.append("scan_links")
                intent = "agent_delegation"
            elif any(w in p_lower for w in ["compile resume", "my resume pdf", "generate resume", "ats resume", "match job", "chaewon"]):
                agents.append("chaewon")
                targets.append("compile_resume")
                intent = "agent_delegation"
            elif any(w in p_lower for w in ["hardware vitals", "system vitals", "pc vitals", "cpu temp", "gpu temp", "ram usage", "check vitals", "eunchae"]):
                agents.append("eunchae")
                targets.append("check_vitals")
                intent = "agent_delegation"
            else:
                # Default to general knowledge synthesis
                targets = ["direct_knowledge"]
                intent = "general_knowledge"

            return {
                "intent_type": intent,
                "agents_needed": agents,
                "action_summary": "Executing subordinate agent delegation." if agents else "Accessing universal knowledge base for direct intellectual synthesis.",
                "requires_real_tools": len(agents) > 0 or bool(direct_obsidian),
                "tool_targets": targets,
                "direct_obsidian": direct_obsidian
            }

    async def execute_mission(self, user_prompt: str, event_callback=None, mission_id: str = None) -> dict:
        """
        Executes a user command through Ciel: plans, delegates, runs tools, and crafts the final speech reply.

        Never raises for a mission-level failure (LLM down, TTS error, ...). It emits a
        "ciel_error" event and returns {"mission_id", "error", "elapsed_ms"} so the HUD
        shows a failure state instead of a blank "Mission completed".
        """
        start_time = time.time()
        # One id per mission. It rides on every WS event and on the REST result so
        # the HUD can render each mission once (it receives the result both ways),
        # and it names the audio file so two missions in the same second can't
        # overwrite each other's mp3. Callers may pass the id the HUD chose; it is
        # used in a filename, so only the exact uuid4-hex shape is accepted.
        if not (isinstance(mission_id, str) and re.fullmatch(r"[0-9a-f]{32}", mission_id)):
            mission_id = uuid.uuid4().hex

        async def emit(event_type: str, data: dict):
            if event_callback:
                payload = {"type": event_type, "timestamp": time.time(), "mission_id": mission_id, **data}
                if asyncio.iscoroutinefunction(event_callback):
                    await event_callback(payload)
                else:
                    event_callback(payload)

        try:
            return await self._run_mission(user_prompt, emit, mission_id, start_time, self.memory_generation)
        except Exception as e:
            failure = {
                "mission_id": mission_id,
                "error": f"{type(e).__name__}: {e}",
                "elapsed_ms": round((time.time() - start_time) * 1000, 1),
            }
            print(f"⚠️ [Ciel] Mission {mission_id} failed: {failure['error']}", flush=True)
            try:
                await emit("ciel_error", failure)
            except Exception:  # quiet: the failure still reaches the HUD in the REST reply
                pass  # the HUD still gets the failure through the REST reply
            return failure

    async def _run_mission(self, user_prompt: str, emit, mission_id: str, start_time: float, start_gen: int) -> dict:
        audio_filename = f"ciel_response_{mission_id}.mp3"

        # 1. Ciel Ingestion & Planning
        await emit("ciel_state", {"state": "analyzing", "message": "Analyzing prompt..."})
        plan = await self.analyze_and_plan(user_prompt)
        intent = plan.get("intent_type", "general_knowledge")
        agents_to_call = plan.get("agents_needed", [])
        tool_targets = plan.get("tool_targets", [])
        direct_action = plan.get("direct_obsidian")

        if intent == "general_knowledge" and not agents_to_call and not direct_action:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing universal knowledge core for direct synthesis..."})

        # Handle instant memory reset if requested
        if "reset_memory" in tool_targets:
            self.reset_memory()
            ja_speech = "「告。」セッションメモリを全消去しました。新規対話を初期化します。"
            en_speech = "Notice: Working memory cache cleared. Initializing a fresh conversational session."
            final_reply = en_speech
            await text_to_speech_bilingual(ja_text=ja_speech, en_text=en_speech, output_filename=audio_filename)
            final_data = {
                "mission_id": mission_id,
                "reply": final_reply,
                "japanese_voice": ja_speech,
                "english_voice": en_speech,
                "audio_file": audio_filename,
                "audio_url": f"/audio/{audio_filename}",
                "elapsed_ms": round((time.time() - start_time) * 1000, 1),
                "agents_called": [],
                "agent_results": {},
                "direct_obsidian": None,
                "memory_turns": 0
            }
            await emit("ciel_complete", final_data)
            return final_data

        # Handle Proactive Operational Appraisal if requested
        if "proactive_appraisal" in tool_targets or direct_action == "proactive_appraisal":
            await emit("ciel_state", {"state": "analyzing", "message": "Executing Proactive Sentinel Appraisal across sentinels..."})
            from proactive_sentinel import evaluate_proactive_suggestions, generate_appraisal_summary
            suggs = await run_blocking(evaluate_proactive_suggestions, timeout=APPRAISAL_TIMEOUT_S,
                                       label="Proactive appraisal")
            appraisal_text = generate_appraisal_summary(suggs)
            
            if suggs:
                top = suggs[0]
                ja_speech = f"「告。」業務鑑定を完了しました。優先提言：{top['title']}。直ちに実行可能です。"
                en_speech = f"Notice: Operational appraisal complete. Key recommendation: {top['title']}. Standing ready to execute."
            else:
                # Empty only when all four checks ran (a failed check returns a suggestion).
                ja_speech = "「告。」業務鑑定を完了しました。四つの監視チェックはすべて実行され、指摘事項はありません。"
                en_speech = "Notice: Operational appraisal complete. All four checks ran and flagged nothing."

            final_reply = appraisal_text
            await text_to_speech_bilingual(ja_text=ja_speech, en_text=en_speech, output_filename=audio_filename)

            self._remember({
                "user_prompt": user_prompt,
                "agents_called": [],
                "agent_results": {"sentinel_suggestions": suggs},
                "ciel_reply": final_reply,
                "timestamp": time.time()
            }, start_gen)

            final_data = {
                "mission_id": mission_id,
                "reply": final_reply,
                "japanese_voice": ja_speech,
                "english_voice": en_speech,
                "audio_file": audio_filename,
                "audio_url": f"/audio/{audio_filename}",
                "elapsed_ms": round((time.time() - start_time) * 1000, 1),
                "agents_called": [],
                "agent_results": {"sentinel_suggestions": suggs},
                "direct_obsidian": None,
                "memory_turns": len(self.conversation_history),
                "suggestions": suggs
            }
            await emit("ciel_complete", final_data)
            return final_data

        # Execute Direct Obsidian Brain Access if requested
        async def vault(fn, *args, **kwargs):
            return await run_blocking(fn, *args, timeout=VAULT_TIMEOUT_S, label="Obsidian vault call", **kwargs)

        direct_vault_results = {}
        if direct_action == "ciel_read_rules" or "ciel_read_rules" in tool_targets:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing Obsidian Brain: Reading Learned Rules & Heuristics..."})
            rules_doc = await vault(self.ciel_read_note, "03 - Rules & Memory/Learned_Rules.md")
            direct_vault_results["learned_rules"] = rules_doc

        if direct_action == "ciel_read_profile" or "ciel_read_profile" in tool_targets:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing Obsidian Brain: Reading Candidate Profile..."})
            profile_doc = await vault(self.ciel_read_note, "01 - User/Profile.md")
            direct_vault_results["candidate_profile"] = profile_doc

        if direct_action == "ciel_read_preferences" or "ciel_read_preferences" in tool_targets:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing Obsidian Brain: Reading Working Preferences..."})
            pref_doc = await vault(self.ciel_read_note, "01 - User/Preferences.md")
            direct_vault_results["user_preferences"] = pref_doc

        if direct_action == "ciel_search_vault" or "ciel_search_vault" in tool_targets:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing Obsidian Brain: Searching vault notes..."})
            matches = await vault(self.ciel_search_vault, user_prompt)
            direct_vault_results["vault_matches"] = matches

        if direct_action == "ciel_save_to_daily" or "ciel_save_to_daily" in tool_targets:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing Obsidian Brain: Appending entry to Daily Log..."})
            last_turn = self.conversation_history[-1] if self.conversation_history else None
            if last_turn:
                summary_to_save = f"**Reference Prompt:** {last_turn.get('user_prompt')}\n**Ciel Summary:** {last_turn.get('ciel_reply')}"
                if last_turn.get("agent_results"):
                    summary_to_save += f"\n**Agent Telemetry:** {json.dumps(last_turn.get('agent_results'))}"
                ok = await vault(self.ciel_log_to_daily, summary_to_save, heading="Ciel Multi-Turn Mission Log")
                direct_vault_results["daily_log_saved"] = f"Successfully appended previous turn findings into today's Obsidian daily log (Status: {ok})."
            else:
                ok = await vault(self.ciel_log_to_daily, f"User instruction: {user_prompt}", heading="Ciel User Note")
                direct_vault_results["daily_log_saved"] = f"Recorded note into today's Obsidian daily log (Status: {ok})."

        await emit("plan_formed", {
            "agents": agents_to_call,
            "summary": plan.get("action_summary", "Calculating optimal path."),
            "tool_targets": tool_targets,
            "direct_obsidian": direct_action
        })

        agent_results = {}
        weather_data = None
        web_search_results = []

        # Execute Live Weather if requested
        if intent == "live_weather" or "live_weather" in tool_targets:
            loc = plan.get("location", "")
            await emit("ciel_state", {"state": "analyzing", "message": f"Querying meteorological satellite telemetry{' for ' + loc if loc else ''}..."})
            try:
                weather_data = await run_blocking(self.ciel_get_weather, loc, timeout=WEATHER_TIMEOUT_S,
                                                  label="Weather lookup")
            except TimeoutError as te:
                weather_data = {"success": False, "error": str(te), "raw_summary": f"Weather lookup failed: {te}."}
            agent_results["meteorological_telemetry"] = weather_data.get("raw_summary", "")

        # Execute Live Web Search if requested
        if intent == "web_search" or "web_search" in tool_targets:
            s_query = plan.get("search_query") or user_prompt
            await emit("ciel_state", {"state": "analyzing", "message": f"Executing real-time search across global web: \"{s_query[:40]}\"..."})
            try:
                web_search_results = await run_blocking(self.ciel_web_search, s_query, max_results=5,
                                                        timeout=WEB_SEARCH_TIMEOUT_S, label="Web search")
            except TimeoutError as te:
                web_search_results = []
                agent_results["web_search_intelligence"] = f"Web search failed: {te}."
            if web_search_results:
                formatted_snippets = "\n".join([f"- [{r['title']}]({r['url']}): {r['snippet']}" for r in web_search_results])
                agent_results["web_search_intelligence"] = formatted_snippets
            elif "web_search_intelligence" not in agent_results:
                agent_results["web_search_intelligence"] = "0 web results returned or connection offline."

        # 2. Parallel / Sequenced Delegation to Subordinates with Real Tool Execution
        # Every agent ends in one of three honest states, sent to the HUD:
        #   "done"      - the tool ran and the result is clean
        #   "attention" - the tool ran and found problems (broken assets, risks, ...)
        #   "failed"    - the tool could not run (missing engine, exception, offline)
        # A failure is never reworded as "verified" or "nominal".
        agent_status = {}
        # The agents are independent, so they run concurrently; results keep the plan's order.
        outcomes = await asyncio.gather(*(
            self._run_agent(agent, user_prompt, tool_targets, emit) for agent in agents_to_call
        ))
        for agent, (status, result_text) in zip(agents_to_call, outcomes):
            agent_results[agent] = result_text
            agent_status[agent] = status

        # 3. Ciel Final Synthesis with Multi-Turn Context and Direct Obsidian Brain Data
        await emit("ciel_state", {"state": "synthesizing", "message": "Synthesizing vocal response..."})

        # Build recent conversation history snippet
        history_context = ""
        if self.conversation_history:
            recent_turns = self.conversation_history[-3:]
            h_lines = []
            for idx, turn in enumerate(recent_turns, 1):
                h_lines.append(f"Turn {idx}: User asked \"{turn.get('user_prompt')}\"")
                h_lines.append(f"  Ciel reported: \"{turn.get('ciel_reply')}\"")
            history_context = "\n".join(h_lines)

        synthesis_prompt = f"""User request: "{user_prompt}"

Query Classification: {intent} (Universal Intellect & Cognitive Reasoning Active)

Recent Dialogue Context:
{history_context if history_context else "None (first turn in conversation)"}

Direct Obsidian Brain Knowledge:
{json.dumps(direct_vault_results, indent=2) if direct_vault_results else "None"}

Subordinate Agent Findings (status is "done", "attention" = ran and found problems, or "failed" = could not run):
{json.dumps({name: {"status": agent_status.get(name, "done"), "result": text} for name, text in agent_results.items()}, indent=2) if agent_results else "None"}
If any finding has status "failed", say plainly in both voice lines and the report that the check could not run and why. Never describe a failed check as verified, nominal, synchronized, or successful.

Synthesize a response as Ciel (The Divine Wisdom Core, Tensura):
You possess both comprehensive universal knowledge across all sciences, engineering, algorithms, mathematics, philosophy, literature, and general reasoning, as well as live global internet search capabilities and real-time access to Hans's local systems, notes, and meteorological sensors.

Instructions by Query Type:
- Live Weather / Atmospheric Intelligence:
  Deliver a crisp, serene spoken weather report in "english_voice" (e.g. "Notice: Current weather in Casile, Philippines is clear at 25 degrees Celsius with 88% humidity. Atmospheric conditions are nominal.").
  In "display_text", format a comprehensive meteorological dossier featuring:
  - **Location**
  - **Current Temperature** (°C and °F) & **Feels Like**
  - **Condition**
  - **Atmospheric Readings** (Humidity, Wind Speed & Direction, UV Index, Precipitation)
  - **Today's Forecast** (High / Low)
- Live Web Search / Global Real-Time Intelligence:
  Synthesize the retrieved web information with authoritative intellectual brilliance.
  In "english_voice", deliver a complete, natural spoken summary of the answer (avoid reading raw markdown symbols or URLs aloud).
  In "display_text", provide the complete, detailed breakdown, including clean bullet points and clickable markdown links/sources to referenced pages (e.g. [Source Title](URL)).
- General Knowledge / Technical / Coding / Conceptual Query:
  Draw directly from your vast universal intelligence. Provide a thorough, authoritative, and brilliantly articulated answer. If code, formulas, trade-offs, or step-by-step logic are helpful, include clean, well-formatted markdown in "display_text".
- Local Agent or Obsidian Vault Operation:
  Summarize the verified empirical findings with calm mathematical precision.
- Conversational Continuity:
  Maintain seamless, unbroken awareness of previous turns in dialogue.

Return a JSON object with:
1. "japanese_voice": Authentic Japanese dialogue for vocal synthesis. Emulate Raphael / Ciel's serene, deadpan, analytical cadence from Tensura. Always begin directly with 「解。」(Solution / Analysis) or 「告。」(Notice). Do NOT include "マスター" or "Master". Provide a crisp 1 to 2 sentence summary in authentic Japanese summarizing the verified outcome.
2. "english_voice": Complete spoken English vocal dialogue. Calm, formal, authoritative. Begin directly with "Report: ..." or "Notice: ...". Do NOT include "Master".
   - CRITICAL REQUIREMENT: Deliver a COMPLETE, FINISHED spoken report that fully covers the key findings and conclusions. For operational and status checks (e.g. Git status, vitals, inbox, audits, weather), explicitly state the key numbers (e.g. temperature, condition, staged/unstaged files, security status). Never stop mid-thought, never omit crucial sub-findings, and never leave the listener waiting for an unfinished sentence.
   - For coding, search, or general knowledge inquiries, deliver a complete, polished spoken explanation naturally.
3. "display_text": The complete, comprehensive English report text to display on the HUD / terminal. ONLY English text — do NOT include Japanese characters or Japanese transcription. Do NOT include "Master". Format with markdown headers, bullet points, or code blocks where appropriate.

Respond ONLY with valid JSON.
"""
        # The reply streams in; each finished spoken sentence is voiced right away.
        clips = SpeechClips(emit, mission_id)
        loop = asyncio.get_running_loop()
        synth_meta = {}  # call_groq fills chain / provider / model / ttft_ms; the HUD shows them (5E-4)
        try:
            raw_reply = await run_blocking(
                call_groq, synthesis_prompt, system_instruction=CIEL_SYSTEM_PROMPT, model="qwen/qwen3.8-27b",
                on_delta=lambda piece: loop.call_soon_threadsafe(clips.feed, piece), meta=synth_meta,
                timeout=SYNTHESIS_TIMEOUT_S, label="Synthesis LLM",
            )
        except BaseException:
            clips.cancel()
            raise

        ja_speech = ""
        en_speech = ""
        final_reply = ""
        try:
            clean_str = raw_reply.strip()
            if "```json" in clean_str:
                clean_str = clean_str.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_str:
                clean_str = clean_str.split("```")[1].split("```")[0].strip()
            parsed = json.loads(clean_str)
            ja_speech = parsed.get("japanese_voice", "").strip()
            en_speech = parsed.get("english_voice", "").strip()
            final_reply = parsed.get("display_text", "").strip()
        except Exception as _exc:
            print(f"⚠️ [ciel_orchestrator._run_mission] suppressed {type(_exc).__name__}: {_exc}", flush=True)

        if not final_reply:
            final_reply = en_speech or raw_reply.strip()
        if not en_speech:
            en_speech = final_reply

        elapsed_ms = round((time.time() - start_time) * 1000, 1)

        # 4. Bilingual voice (Japanese first -> English second with telepathic DSP).
        # Streamed: finish the clips (plus any voice field the stream never opened)
        # and join them. Nothing streamed (no voice fields in the reply as it
        # arrived): synthesize the whole reply at once, as before.
        if clips.started:
            try:
                clips.add_unstreamed("ja", ja_speech)
                clips.add_unstreamed("en", en_speech)
                await clips.finish(audio_filename)
            finally:
                clips.cancel()
        else:
            clips.cancel()
            await emit("ciel_state", {"state": "speaking", "message": "Synthesizing bilingual vocal report..."})
            await text_to_speech_bilingual(
                ja_text=ja_speech,
                en_text=en_speech,
                output_filename=audio_filename
            )

        # 5. Record this turn into Working Memory
        self._remember({
            "turn_id": len(self.conversation_history) + 1,
            "timestamp": time.time(),
            "user_prompt": user_prompt,
            "intent_type": intent,
            "agents_called": agents_to_call,
            "agent_results": agent_results,
            "ciel_reply": final_reply,
            "japanese_voice": ja_speech,
            "english_voice": en_speech
        }, start_gen)

        final_data = {
            "mission_id": mission_id,
            "reply": final_reply,
            "japanese_voice": ja_speech,
            "english_voice": en_speech,
            "audio_file": audio_filename,
            "audio_url": f"/audio/{audio_filename}",
            "elapsed_ms": elapsed_ms,
            "intent_type": intent,
            "agents_called": agents_to_call,
            "agent_results": agent_results,
            "agent_status": agent_status,
            "failed_agents": [a for a, s in agent_status.items() if s == "failed"],
            "weather_data": weather_data,
            "web_results": web_search_results,
            "direct_obsidian": direct_action,
            "memory_turns": len(self.conversation_history),
            # Measured on this mission's synthesis call (4B field names); absent on fast paths.
            "llm": {"provider": synth_meta.get("provider"), "model": synth_meta.get("model"),
                    "fallback_chain": synth_meta.get("chain", []), "ttft_ms": synth_meta.get("ttft_ms")},
        }

        await emit("ciel_complete", final_data)
        return final_data


    async def _run_agent(self, agent: str, user_prompt: str, tool_targets: list, emit) -> tuple:
        """Runs one subordinate's tool in a worker thread and reports its honest end state."""
        await emit("agent_state", {"agent": agent, "status": "active", "task": f"Executing delegated task: {agent}"})
        timeout = AGENT_TIMEOUTS_S.get(agent, DEFAULT_AGENT_TIMEOUT_S)
        # The HUD shows this agent's measured time (5E-4): the work only, from the start of
        # run_blocking to its end or timeout. Status events (a slow HUD) do not count, and
        # an agent whose job never started has no time (None), not a made-up 0.
        started = None
        try:
            task, work = self._agent_job(agent, user_prompt, tool_targets)
            if task:
                await emit("agent_state", {"agent": agent, "status": "active", "task": task})
            started = time.perf_counter()
            status, result_text = await run_blocking(work, timeout=timeout, label=agent.capitalize(),
                                                     heavy=agent in HEAVY_AGENTS)
        except TimeoutError:
            status = "failed"
            result_text = f"{agent.capitalize()} failed: timed out after {timeout:g} s"
            if agent == "chaewon":
                # The thread can't be stopped; don't let "failed" imply the PDF is untouched.
                result_text += "; the build may still finish in the background"
        except Exception as e:
            status = "failed"
            result_text = f"{agent.capitalize()} failed: {type(e).__name__}: {e}"
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1) if started is not None else None
        await emit("agent_state", {"agent": agent, "status": status, "result": result_text, "elapsed_ms": elapsed_ms})
        return status, result_text

    def _agent_job(self, agent: str, user_prompt: str, tool_targets: list) -> tuple:
        """
        Picks an agent's tool. Returns (task message for the HUD or None, work), where
        work() is the blocking part and returns (status, result_text). It runs in a
        worker thread, so it must not touch the event loop.
        """
        if agent == "eunchae":
            # Check if tests/compilation requested or hardware vitals
            if any("test" in str(t).lower() or "compile" in str(t).lower() for t in tool_targets) or any(w in user_prompt.lower() for w in ["test", "compile", "syntax", "smoke"]):
                def compile_check():
                    import py_compile
                    py_files = list(BASE_DIR.glob("*.py"))
                    syntax_errors = []
                    for pf in py_files:
                        try:
                            py_compile.compile(str(pf), doraise=True)
                        except Exception as pe:
                            syntax_errors.append((pf.name, str(pe)))
                    if syntax_errors:
                        return "attention", f"System QA Sentinel: Smoke test flagged {len(syntax_errors)} compilation error(s): {syntax_errors[0][0]} - {syntax_errors[0][1]}."
                    return "done", f"System QA Sentinel: Compilation verification passed across {len(py_files)} Python scripts. 0 syntax errors detected. Nominal state."
                return "Running Python script syntax & compilation verification...", compile_check
            if get_system_vitals:
                def read_vitals():
                    vitals = get_system_vitals()
                    healthy_state = "Nominal 🟢" if vitals.get("healthy") else "High Load ⚠️"
                    status = "done" if vitals.get("healthy") else "attention"
                    return status, f"Hardware Vitals: CPU {vitals.get('cpu_pct')}% ({vitals.get('cpu_count')} Cores) | RAM {vitals.get('ram_used_gb')}GB / {vitals.get('ram_total_gb')}GB ({vitals.get('ram_pct')}%) | Storage {vitals.get('disk_free_gb')}GB free | Uptime {vitals.get('uptime_str')}. State: {healthy_state}."
                return "Sampling hardware vitals via psutil...", read_vitals
            return None, lambda: ("failed", "Hardware vitals unavailable: eunchae_engine could not be imported.")

        if agent == "yunjin":
            def scan_portfolio():
                if not scan_html_assets:
                    return "failed", "Portfolio QA Gate unavailable: yunjin_engine could not be imported."
                target_pages = ["index.html", "about.html", "case-ciel.html", "case-lsfm.html", "case-memory.html", "case-vellum.html", "case-lumina.html", "case-fintrack.html", "case-aura.html"]
                scanned_pages = []
                all_broken = []
                total_images = 0
                for page in target_pages:
                    page_path = PORTFOLIO_DIR / page
                    if page_path.exists():
                        res = scan_html_assets(page_path)
                        scanned_pages.append(page)
                        total_images += res.get("total_images", 0)
                        for b in res.get("broken_images", []):
                            all_broken.append(f"{page}->{b}")
                if not scanned_pages:
                    return "failed", f"Portfolio QA Gate: no pages found under {PORTFOLIO_DIR}."
                if all_broken:
                    return "attention", f"Portfolio QA Gate: Scanned {len(scanned_pages)} pages ({total_images} images). ⚠️ Detected {len(all_broken)} broken asset(s): {', '.join(all_broken[:2])}."
                return "done", f"Portfolio QA Gate: Scanned {len(scanned_pages)} pages ({total_images} images). 0 broken images."
            return "Scanning portfolio HTML structure & image assets...", scan_portfolio

        if agent == "sakura":
            return self._sakura_job(user_prompt)

        if agent == "chaewon":
            def compile_resume():
                if not compile_master_resume:
                    return "failed", "Career & ATS Engine unavailable: pdf_engine could not be imported."
                # Held by the build thread itself, so a build abandoned on timeout keeps
                # it until it really ends and no second build can overwrite the PDF.
                if not RESUME_BUILD_LOCK.acquire(blocking=False):
                    return "failed", "Career & ATS Engine: an earlier resume build is still running; this one was not started."
                try:
                    res = compile_master_resume(archive=True, sync_portfolio=True, sync_obsidian=True)
                except Exception as ce:
                    return "failed", f"Career & ATS Engine: resume compilation raised an error: {ce}"
                finally:
                    RESUME_BUILD_LOCK.release()
                if res.get("success"):
                    return "done", f"Career & ATS Engine: Master resume PDF recompiled ({res.get('size_kb')} KB) and synced to portfolio-site/Hans_Laureles_Resume.pdf."
                return "failed", f"Career & ATS Engine: resume compilation failed: {res.get('error', 'unknown error')}. The previous PDF was left unchanged."
            return "Compiling 1-page ATS Vector Resume PDF via headless browser...", compile_resume

        if agent == "kazuha":
            def git_audit():
                if not get_git_info:
                    return "failed", "Git Sentinel unavailable: git_sentinel could not be imported."
                try:
                    info = get_git_info()
                    branch = info.get("branch", "main")
                    staged = info.get("staged", [])
                    unstaged = info.get("unstaged", [])
                    untracked = info.get("untracked", [])
                    commits = info.get("total_commits", 0)

                    diff = get_git_diff() if get_git_diff else ""
                    risks = scan_security_risks(diff) if (diff and scan_security_risks) else []
                except Exception as ge:
                    return "failed", f"Git Sentinel: git status failed: {ge}"
                status = "attention" if risks else "done"
                risk_notice = f" ⚠️ {len(risks)} security risk(s) in diff!" if risks else " 0 security risks in diff."
                if info.get("is_clean"):
                    return status, f"Git Sentinel: Working tree clean on branch '{branch}' ({commits} commits).{risk_notice}"
                return status, f"Git Sentinel: Branch '{branch}' | Staged: {len(staged)} | Unstaged: {len(unstaged)} | Untracked: {len(untracked)}.{risk_notice}"
            return "Auditing Git repository status and scanning diff for secret leaks...", git_audit

        return None, lambda: ("done", "")

    def _sakura_job(self, user_prompt: str) -> tuple:
        p_lower = user_prompt.lower()
        if any(w in p_lower for w in ["inbox", "email", "gmail", "unread", "recruiter", "mail"]):
            task = "Triage Primary Gmail inbox..."

            def run():
                if not scan_morning_inbox:
                    return "failed", "Gmail Inbox Triage unavailable: sakura_engine could not be imported."
                inbox = scan_morning_inbox()
                unread = inbox.get("unread_count", 0)
                job_alerts = inbox.get("job_alerts", [])
                highlights = inbox.get("highlights", [])
                if not inbox.get("ok", False):
                    # Not configured / offline / error also report 0 unread;
                    # that must not read as a clean inbox.
                    return "failed", f"Gmail Inbox Triage failed: {inbox.get('status', 'unknown error')}."
                if unread > 0:
                    alert_info = f" ({len(job_alerts)} recruiter inquiry)" if job_alerts else ""
                    top_h = f" Top: {highlights[0]}" if highlights else ""
                    return "done", f"Gmail Inbox Triage: {unread} unread message(s) in Primary{alert_info}.{top_h}"
                return "done", "Gmail Inbox Triage: Primary inbox clean — 0 unread messages."
        elif any(w in p_lower for w in ["priority", "priorities", "briefing", "schedule", "daily"]):
            task = "Fetching daily briefing & priorities from Obsidian..."

            def run():
                try:
                    daily_log_path = self.obsidian.ensure_daily_log()
                    content = self.obsidian.get_file(daily_log_path)
                except Exception as oe:
                    return "failed", f"Obsidian daily log could not be read: {oe}"
                priority_lines = [l.strip().lstrip("- [ ]").strip() for l in content.splitlines() if "- [ ]" in l][:3]
                if priority_lines:
                    return "done", f"Obsidian Second Brain: Today's log '{daily_log_path}' loaded. Priorities: {'; '.join(priority_lines)}."
                return "done", f"Obsidian Second Brain: Daily log '{daily_log_path}' loaded; no open priorities."
        elif any(w in p_lower for w in ["search", "find", "lookup"]):
            task = "Searching Obsidian vault..."

            def run():
                try:
                    matches = self.obsidian.search_simple(user_prompt)
                except Exception as se:
                    return "failed", f"Obsidian Search failed: {se}"
                if matches:
                    return "done", f"Obsidian Search: Found {len(matches)} matching note(s): {', '.join([m.get('filename', '') for m in matches[:3]])}."
                return "done", "Obsidian Search: 0 matching notes found for query."
        else:
            task = None

            def run():
                try:
                    files = self.obsidian.list_dir("05 - Daily Logs")
                except Exception as le:
                    return "failed", f"Obsidian Second Brain unreachable: {le}"
                return "done", f"Obsidian Second Brain online: {len(files)} daily logs indexed. Synchronized with loopback HTTPS port 27124."

        def run_and_log():
            status, result_text = run()
            # Log Ciel session dispatch to Obsidian
            try:
                self.obsidian.log_session(
                    "Sakura",
                    f"Ciel Mission: {user_prompt[:50]}",
                    [f"Outcome: Handled ({result_text[:70]}...)"]
                )
            except Exception as _exc:
                print(f"⚠️ [ciel_orchestrator.run_and_log] suppressed {type(_exc).__name__}: {_exc}", flush=True)
            return status, result_text

        return task, run_and_log


if __name__ == "__main__":
    async def demo():
        print("Testing Ciel Orchestrator Engine...")
        ciel = CielOrchestrator()
        
        def on_event(ev):
            print(f"[{ev['type']}] {ev.get('message') or ev.get('agent') or ''}")

        res = await ciel.execute_mission("Ciel, check our PC hardware vitals and make sure our Obsidian brain is online.", event_callback=on_event)
        print("\n--- CIEL'S FINAL REPORT ---")
        print(res["reply"])
        print(f"\nCompleted in: {res['elapsed_ms']}ms")
        print(f"Audio ready at: {res['audio_file']}")

    asyncio.run(demo())
