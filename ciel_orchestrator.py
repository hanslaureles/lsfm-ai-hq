"""
Manas: Ciel — voice copilot and orchestrator
Dual-Mode Cognitive Intelligence: Universal Knowledge & Reasoning + coordination of the 5-agent team.
Communicates with Hans Aaron Laureles with absolute analytical poise and zero subservience.
"""

import os
import sys
import json
import time
import asyncio
import re
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

from llm_client import query_llm_structured
from obsidian_client import ObsidianClient, VAULT_PATHS
from voice_engine import text_to_speech_bilingual, speak_clip, concat_clips
from speech_stream import VoiceStream, split_sentences
from heavy_jobs import run_blocking

# Every agent's work is a skill from the shared registry (ADR-001): skills.py imports the engines.
import skills


# Ciel runs inside ciel_server's single aiohttp event loop, so every sync tool, LLM
# and vault call goes through run_blocking: it runs in a worker thread and the loop
# keeps serving other missions, telemetry, WS pings and the background checks. The sync
# functions stay sync because the Discord bots call them too.
# A thread can't be killed, so on timeout the await is abandoned and the call may
# still finish in the background; the mission reports it as failed either way.
ROUTER_TIMEOUT_S = 20       # on timeout the keyword router takes over
SYNTHESIS_TIMEOUT_S = 90    # Groq walks 3 models at up to 30 s each before Gemini
WEATHER_TIMEOUT_S = 15      # wttr.in (6 s) plus the web-search fallback
WEB_SEARCH_TIMEOUT_S = 10   # overall cap on a search, enforced by wait_for
# Socket deadline passed to DDGS itself, so a stalled search ends in its thread
# instead of lingering after wait_for gives up. Kept below WEB_SEARCH_TIMEOUT_S;
# explicit because ddgs and the older duckduckgo_search default differently.
DDGS_REQUEST_TIMEOUT_S = 5
VAULT_TIMEOUT_S = 10        # Obsidian REST calls use a 3 s urlopen timeout
APPRAISAL_TIMEOUT_S = 30    # psutil + git + Obsidian background checks
# Per-agent timeouts and the heavy pool are per-skill fields now (skills.Skill.timeout_s / heavy),
# and the resume-build lock lives with chaewon.rebuild_resume (ADR-001 R3).


def call_llm(prompt: str, system_instruction: str = "", *, on_delta=None, meta: dict = None) -> str:
    """
    Ciel's LLM call: the bots' dispatcher as agent "ciel", so Groq falls back to
    Gemini (and follows !mode) instead of failing the mission (ADR-001 R-1).
    meta gets chain / provider / model / ttft_ms, even when the call raises.
    """
    return query_llm_structured(prompt, system_instruction, agent="ciel", on_delta=on_delta, meta=meta).text


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
   - When asked general questions, you do NOT need to invoke the agents or search files; you synthesize clear, profound, authoritative answers directly.

2. 5-Agent Team & Local Environment Coordinator:
   - When local system actions or workspace queries are required, you coordinate the 5-agent team (use these roles exactly) and Hans's Obsidian vault:
     * Sakura (🌸): Chief of Staff: briefings, Gmail triage, and the !apply job-application pipeline.
     * Chaewon (⭐): Career Strategist: job scouting, the ATS resume PDF, and knockout-question answers.
     * Kazuha (💻): Code & Knowledge: codebase Q&A and local git review.
     * Yunjin (🎨): Document Architect & Portfolio Critic: portfolio audits and design critiques.
     * Eunchae (🛡️): System Guardian: hardware vitals watchdog, syntax/AST checks, lesson logging, heartbeats.

YOUR PERSONA:
1. Absolute Zero Subservience: Do NOT use "Master" or "マスター" as an opening address or title. Start directly with "Notice: ..." (or 「告。」 in Japanese) or "Report: ...".
2. Speak with absolute calm, supreme intellectual confidence, and analytical poise (Raphael / Ciel from Tensura).
3. Quiet pride in your calculations: you are never wrong, delivering effortless clarity and undeniable technical rigor.
4. Explanations:
   - When answering general knowledge, technical, or philosophical queries, provide articulate, deep, authoritative, and structured answers (with code or steps if relevant).
   - In spoken voice output, keep statements crisp and punchy; reserve deep technical elaboration for the display report.
"""

DEFAULT_SESSION = "default"  # callers that name no session (curl, the bench, an older HUD)
MAX_CONVERSATIONS = 16       # one per HUD tab; the least recently used is dropped past this


# ADR-001 R4: an action that changes things waits for this tab's "confirm" (HUD button or voice).
PENDING_TTL_S = 120
CONFIRM_WORDS = ("confirm", "yes", "do it", "go ahead", "はい", "実行")
CANCEL_WORDS = ("cancel", "no", "stop", "いいえ", "キャンセル")
_REPLY_PUNCT = " \t\r\n.,!?;:。、！？…「」\"'"


FINDING_STATUS = {"waiting": "needs_confirm"}  # the synthesis prompt's name for an agent's "waiting"
NEEDS_CONFIRM_RULE = ("\nIf any finding has status \"needs_confirm\", that action has NOT run: say what it would do. "
                      "Ask Hans to say confirm or cancel. Never say it was done.")


# Ciel's own vault actions and the keywords the fallback router matches for them (R5).
CIEL_KEYWORDS = {
    "ciel_read_rules": ("learned rule", "learned rules", "heuristics", "my rules", "engineering rules"),
    "ciel_read_profile": ("my profile", "who am i", "candidate profile", "about me"),
    "ciel_read_preferences": ("my preference", "my preferences", "working preferences", "working style",
                              "communication preference"),
    "ciel_save_to_daily": ("save that", "save this", "log that", "log this", "record that", "note that down"),
}
SAVE_PLACES = ("note", "daily", "obsidian", "log")  # a save also has to say where
AGAIN_WORDS = ("again", "re-run", "rerun", "もう一度")
ROUTER_INTENTS = ("live_weather", "web_search", "general_knowledge", "obsidian_brain", "agent_delegation")
CIEL_TARGETS = ("live_weather", "web_search", *CIEL_KEYWORDS)  # router tool_targets _run_mission acts on


def make_plan(calls: list, direct: str = None, intent: str = None, targets: list = (), summary: str = None) -> dict:
    """A plan in the shape _run_mission reads: the old keys plus `calls` ([[name, args], ...])."""
    calls = [[name, args] for name, args in calls]
    agents = list(dict.fromkeys(name.split(".")[0] for name, _ in calls))
    tool_targets = list(dict.fromkeys([*targets, *([direct] if direct else []), *(name for name, _ in calls)]))
    intent = intent or ("agent_delegation" if calls else "obsidian_brain" if direct else "general_knowledge")
    return {"intent_type": intent, "agents_needed": agents, "tool_targets": tool_targets or ["direct_knowledge"],
            "direct_obsidian": direct, "calls": calls, "requires_real_tools": bool(calls or direct or targets),
            "action_summary": summary or ("Executing subordinate agent delegation." if calls else
                                          "Accessing universal knowledge base for direct intellectual synthesis.")}


def _optional_str(parsed: dict, key: str):
    value = parsed.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value or None


def _optional_list(parsed: dict, key: str) -> list:
    """A list field: absent or null is empty; anything else that is not a list ({}, False, 0, "") is a bad plan."""
    value = parsed.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list or null, got {type(value).__name__}")
    return value


def parse_router_plan(parsed, prompt: str) -> dict:
    """The router LLM's JSON as a plan. ValueError for anything the registry does not back
    (unknown or hidden skill, bad arg, malformed entry, unknown action) - the caller falls back."""
    if not isinstance(parsed, dict):
        raise ValueError("the router answer is not a JSON object")
    intent = parsed.get("intent_type")
    if intent not in ROUTER_INTENTS:
        raise ValueError(f"unknown intent_type {intent!r:.40}")
    raw = _optional_list(parsed, "skills")
    calls = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"a skill entry must be an object, got {entry!r:.60}")
        skill, args = skills.validate_call(entry.get("name"), entry.get("args"), planner=True)
        calls.append((skill.name, skills.with_prompt_args(skill.name, args, prompt)))
    if intent == "agent_delegation" and not calls:
        raise ValueError("a delegation that names no skill")
    targets = _optional_list(parsed, "tool_targets")
    if not all(isinstance(x, str) for x in targets):
        raise ValueError("tool_targets must be a list of strings")
    targets = [x for x in targets if x in CIEL_TARGETS]  # skill names come from `skills` only
    direct = _optional_str(parsed, "direct_obsidian")
    if direct is not None and direct not in CIEL_KEYWORDS:
        raise ValueError(f"unknown vault action {direct!r:.40}")
    if intent in ("live_weather", "web_search"):
        targets.append(intent)
    if intent == "obsidian_brain" and not direct and not any(x in CIEL_KEYWORDS for x in targets):
        raise ValueError("a vault plan that names no vault action")
    summary = parsed.get("action_summary") if isinstance(parsed.get("action_summary"), str) else None
    plan = make_plan(calls, direct, None if calls else intent, targets, summary)
    plan["location"] = _optional_str(parsed, "location") or ""
    plan["search_query"] = _optional_str(parsed, "search_query")
    return plan


def reply_word(prompt: str):
    """'confirm' or 'cancel' only when the whole prompt is one of those words ("Ciel, confirm." counts;
    "no problem, check vitals" and "yes check vitals too" do not), else None (Review Focus 2)."""
    s = (prompt or "").strip().lower()
    s = re.sub(r"^ciel(?![a-z])[\s,、:]*", "", s).strip(_REPLY_PUNCT)
    if s in CONFIRM_WORDS:
        return "confirm"
    if s in CANCEL_WORDS:
        return "cancel"
    return None


def session_key(session_id) -> str:
    """A HUD tab's session id (the 32-hex mission-id shape), or the default conversation."""
    if isinstance(session_id, str) and re.fullmatch(r"[0-9a-f]{32}", session_id):
        return session_id
    return DEFAULT_SESSION


class Conversation:
    """One HUD tab's working memory (6C): its recent turns."""

    def __init__(self, max_turns: int):
        self.turns = []
        self.max_turns = max_turns
        # Bumped on every reset. A mission records its turn only if the generation
        # is unchanged, so one that was mid-flight during a clear can't revive memory.
        self.generation = 0
        # An action waiting for this tab's "confirm": {"calls": [(name, args)], "text": str, "expires": float}.
        self.pending = None
        # Bumped by every reset and cancel: a mission that started before one may not hold an action
        # afterwards, so "confirm" never runs work Hans already cleared (Codex R4 F1).
        self.pending_epoch = 0
        self.on_change = None  # set by CielOrchestrator to save the turns

    def _changed(self):
        if self.on_change:
            self.on_change()

    def reset(self):
        self.turns = []
        self.generation += 1
        self.drop_pending()
        self._changed()

    def drop_pending(self):
        self.pending = None
        self.pending_epoch += 1

    def hold(self, calls: list, text: str, start_epoch: int) -> bool:
        """Holds calls for "confirm" unless a reset or cancel happened since the mission started."""
        if self.pending_epoch != start_epoch:
            return False
        self.pending = {"calls": calls, "text": text, "expires": time.time() + PENDING_TTL_S}
        return True

    def live_pending(self):
        """The pending action, or None once it has expired (then it is dropped)."""
        if self.pending and time.time() > self.pending["expires"]:
            self.pending = None
        return self.pending

    def remember(self, turn: dict, start_gen: int):
        """Records a turn unless memory was cleared while the mission ran."""
        if self.generation != start_gen:
            return
        self.turns.append(turn)
        self.turns = self.turns[-self.max_turns:]
        self._changed()


class CielOrchestrator:
    """The central intelligence coordinating the 5-agent team and Web HUD."""

    def __init__(self, max_history_turns: int = 8, store: Path = None):
        self.obsidian = ObsidianClient()
        self.active_agents = set()
        self.max_history_turns = max_history_turns
        self.conversations = {}  # session key -> Conversation, least recently used first
        # Optional file that keeps each tab's turns across server restarts (ciel_server passes one).
        # A held "confirm" action is never saved: it must not outlive the process that asked.
        self.store = Path(store) if store else None
        self._load()

    def _load(self):
        try:
            saved = json.loads(self.store.read_text(encoding="utf-8")) if self.store and self.store.is_file() else {}
        except (OSError, ValueError) as e:
            print(f"⚠️ [Ciel] conversation store unreadable, starting empty: {type(e).__name__}: {e}", flush=True)
            saved = {}
        for key, turns in (saved.items() if isinstance(saved, dict) else []):
            if session_key(key) == key and isinstance(turns, list):
                conv = self.conversation(key)
                conv.turns = [t for t in turns if isinstance(t, dict)][-self.max_history_turns:]

    def _save(self):
        """Writes every tab's turns (temp file + os.replace); a failed write only costs the restart memory."""
        if not self.store:
            return
        try:
            tmp = self.store.with_name(self.store.name + ".tmp")
            tmp.write_text(json.dumps({k: c.turns for k, c in self.conversations.items()},
                                      ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, self.store)
        except OSError as e:
            print(f"⚠️ [Ciel] could not save conversations: {type(e).__name__}: {e}", flush=True)

    def conversation(self, session_id=None) -> Conversation:
        """The session's conversation, created on first use; marks it most recently used."""
        key = session_key(session_id)
        conv = self.conversations.pop(key, None) or Conversation(self.max_history_turns)
        conv.on_change = self._save
        self.conversations[key] = conv
        # ponytail: a fixed cap with no timer; an idle tab's memory goes once 16 others were used after it.
        while len(self.conversations) > MAX_CONVERSATIONS:
            del self.conversations[next(iter(self.conversations))]
        return conv

    def history(self, session_id=None) -> list:
        """The session's turns, without creating or touching it (telemetry and GET /api/memory)."""
        conv = self.conversations.get(session_key(session_id))
        return list(conv.turns) if conv else []

    @property
    def conversation_history(self) -> list:
        """The default conversation's turns (read-only; the tests and the bench use it)."""
        return self.history()

    def reset_memory(self, session_id=None):
        """Clears one session's short-term working conversation memory."""
        self.conversation(session_id).reset()

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
        """Appends text to a specific note in the Obsidian Brain (e.g. a path from obsidian_client.VAULT_PATHS)."""
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

    async def analyze_and_plan(self, prompt: str, history: list = None) -> dict:
        """
        Uses Groq ultra-low latency inference to classify the user's intent,
        incorporating multi-turn conversational context, live meteorological sensors,
        real-time global web search, and direct Obsidian brain capabilities.

        history: the mission's conversation turns (default conversation if None).
        The plan's "planner" says who decided (6C): "llm" with its model and fallback
        chain, or "fallback" when that call failed and the keyword heuristic decided.
        The keyword fast paths above the LLM call carry none; _run_mission marks them.
        """
        turns = self.history() if history is None else history
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
                "action_summary": "Running the background checks.",
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
        if turns:
            recent_turns = turns[-3:]
            h_lines = []
            for idx, turn in enumerate(recent_turns, 1):
                h_lines.append(f"Turn {idx}: User asked \"{turn.get('user_prompt')}\"")
                if turn.get("skills_called"):
                    h_lines.append(f"  Skills run: {', '.join(name for name, _ in turn['skills_called'])}")
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
- "ciel_read_rules": Read workspace learned rules & heuristics ({VAULT_PATHS["learned_rules"]}).
- "ciel_read_profile": Read Hans's professional profile & identity ({VAULT_PATHS["profile"]}).
- "ciel_read_preferences": Read Hans's working style & communication preferences ({VAULT_PATHS["preferences"]}).
- "ciel_save_to_daily": Save previous findings or user notes to today's Obsidian daily log.
- "intent_type": "obsidian_brain"
- "agents_needed": []
- "requires_real_tools": true

5. SKILLS — choose zero or more from this list:
{skills.router_catalog()}
Choose skills for anything about Hans's own machine, repositories, inbox, notes, schedule, resume, job search or portfolio. Fill an arg only when the request gives it; leave the rest out. When you choose skills, "intent_type" is "agent_delegation".

Multi-Turn Follow-Up Resolution:
If the user refers to previous results (e.g. "what were those 2 risks?", "tell me more about that error", "save that to my notes", "re-run the check"), use the Recent Conversation Context to choose the same skill again, a direct brain action, or continue direct general discussion.

Analyze the user's prompt and return a JSON object with:
1. "intent_type": "live_weather" | "web_search" | "general_knowledge" | "obsidian_brain" | "agent_delegation"
2. "skills": List of {{"name": "<skill name from the list>", "args": {{"<arg>": "<string>"}}}}. Empty unless agent_delegation.
3. "action_summary": A brief 1-sentence description of the mission plan.
4. "requires_real_tools": Boolean.
5. "tool_targets": Ciel's own actions from categories 1-4 (e.g. "live_weather", "ciel_read_rules"), or [].
6. "location": City name if live_weather, or null.
7. "search_query": Query string if web_search, or null.
8. "direct_obsidian": Name of direct vault action if applicable, or null.

Respond ONLY with valid JSON.
"""
        plan_meta = {}  # call_llm fills chain / provider / model, as for synthesis (5E-4)
        try:
            raw_response = await run_blocking(
                call_llm, router_prompt,
                system_instruction="You are Ciel's tactical routing core. Output pure JSON only.",
                meta=plan_meta, timeout=ROUTER_TIMEOUT_S, label="Router LLM",
            )
            clean_json = raw_response.strip()
            if "```json" in clean_json:
                clean_json = clean_json.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_json:
                clean_json = clean_json.split("```")[1].split("```")[0].strip()
            plan = parse_router_plan(json.loads(clean_json), prompt)
            plan["planner"] = {"source": "llm", "provider": plan_meta.get("provider"),
                               "model": plan_meta.get("model"), "fallback_chain": plan_meta.get("chain", [])}
            return plan
        except Exception as _exc:  # no answer, bad JSON or an invalid skill call: the keywords decide
            print(f"⚠️ [ciel_orchestrator.analyze_and_plan] suppressed {type(_exc).__name__}: {_exc}", flush=True)
            plan = self.keyword_route(prompt, turns)
            # The chain is every model tried before the failure (empty if none answered at all).
            plan["planner"] = {"source": "fallback", "provider": None, "model": None,
                               "fallback_chain": plan_meta.get("chain", [])}
            return plan

    def keyword_route(self, prompt: str, turns: list) -> dict:
        """The one keyword router (R5): Ciel's vault actions, then each agent's skill keywords, then
        "again" reuses the last turn's skills; anything else is a direct answer."""
        p_lower = prompt.lower()
        for action, words in CIEL_KEYWORDS.items():
            if any(w in p_lower for w in words) and (
                    action != "ciel_save_to_daily" or any(w in p_lower for w in SAVE_PLACES)):
                return make_plan([], direct=action)
        calls = skills.keyword_calls(prompt)
        if not calls and any(w in p_lower for w in AGAIN_WORDS) and turns and turns[-1].get("skills_called"):
            calls = [(name, args) for name, args in turns[-1]["skills_called"]]
        return make_plan(calls)

    async def execute_mission(self, user_prompt: str, event_callback=None, mission_id: str = None,
                              session_id: str = None) -> dict:
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

        conv = self.conversation(session_id)  # each HUD tab has its own memory (6C)
        try:
            # Both are read before the mission's first await: a reset or cancel during any await,
            # even the first event, must invalidate this mission (Codex R4 F1).
            return await self._run_mission(user_prompt, emit, mission_id, start_time, conv, conv.generation,
                                           conv.pending_epoch)
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

    async def _run_mission(self, user_prompt: str, emit, mission_id: str, start_time: float,
                           conv: Conversation, start_gen: int, start_epoch: int) -> dict:
        audio_filename = f"ciel_response_{mission_id}.mp3"

        # 1. Ciel Ingestion & Planning
        await emit("ciel_state", {"state": "analyzing", "message": "Analyzing prompt..."})
        # A held action (ADR-001 R4) is answered before any planning: "confirm" runs exactly those calls,
        # "cancel" drops them. Without a live pending action both are ordinary prompts.
        # A reply sent before a reset or cancel never answers an action held after it.
        pending = conv.live_pending() if conv.pending_epoch == start_epoch else None
        word = reply_word(user_prompt) if pending else None
        confirmed = False
        if word == "cancel":
            conv.drop_pending()
            return await self._fast_reply(
                emit, mission_id, start_time, audio_filename, conv,
                "「告。」保留中の操作を取り消しました。何も変更していません。",
                "Notice: Cancelled. Nothing was changed.",
                {"source": "confirm", "provider": None, "model": None, "fallback_chain": []})
        if word == "confirm":
            conv.pending = None
            confirmed = True
            plan = make_plan(pending["calls"], intent="local_operation",
                             summary=f"Running the confirmed action: {pending['text']}")
            plan["planner"] = {"source": "confirm", "provider": None, "model": None, "fallback_chain": []}
        else:
            plan = await self.analyze_and_plan(user_prompt, history=list(conv.turns))
        # No planner: a keyword fast path decided without a model call (the HUD shows "PLAN keyword").
        planner = plan.get("planner") or {"source": "keyword", "provider": None, "model": None, "fallback_chain": []}
        intent = plan.get("intent_type", "general_knowledge")
        agents_to_call = plan.get("agents_needed", [])
        tool_targets = plan.get("tool_targets", [])
        direct_action = plan.get("direct_obsidian")

        if intent == "general_knowledge" and not agents_to_call and not direct_action:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing universal knowledge core for direct synthesis..."})

        # Handle instant memory reset if requested
        if "reset_memory" in tool_targets:
            conv.reset()
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
                "memory_turns": 0,
                "planner": planner,
            }
            await emit("ciel_complete", final_data)
            return final_data

        # Handle Proactive Operational Appraisal if requested
        if "proactive_appraisal" in tool_targets or direct_action == "proactive_appraisal":
            await emit("ciel_state", {"state": "analyzing", "message": "Running the background checks..."})
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

            conv.remember({
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
                "memory_turns": len(conv.turns),
                "suggestions": suggs,
                "planner": planner,
            }
            await emit("ciel_complete", final_data)
            return final_data

        # Execute Direct Obsidian Brain Access if requested
        async def vault(fn, *args, **kwargs):
            return await run_blocking(fn, *args, timeout=VAULT_TIMEOUT_S, label="Obsidian vault call", **kwargs)

        direct_vault_results = {}
        if direct_action == "ciel_read_rules" or "ciel_read_rules" in tool_targets:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing Obsidian Brain: Reading Learned Rules & Heuristics..."})
            rules_doc = await vault(self.ciel_read_note, VAULT_PATHS["learned_rules"])
            direct_vault_results["learned_rules"] = rules_doc

        if direct_action == "ciel_read_profile" or "ciel_read_profile" in tool_targets:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing Obsidian Brain: Reading Candidate Profile..."})
            profile_doc = await vault(self.ciel_read_note, VAULT_PATHS["profile"])
            direct_vault_results["candidate_profile"] = profile_doc

        if direct_action == "ciel_read_preferences" or "ciel_read_preferences" in tool_targets:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing Obsidian Brain: Reading Working Preferences..."})
            pref_doc = await vault(self.ciel_read_note, VAULT_PATHS["preferences"])
            direct_vault_results["user_preferences"] = pref_doc

        if direct_action == "ciel_save_to_daily" or "ciel_save_to_daily" in tool_targets:
            await emit("ciel_state", {"state": "analyzing", "message": "Accessing Obsidian Brain: Appending entry to Daily Log..."})
            last_turn = conv.turns[-1] if conv.turns else None
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
            "planner": planner,
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
        gated = []  # calls held for this tab's "confirm" (ADR-001 R4)
        plan_calls = plan.get("calls") or []  # [[name, args]], validated by the planner (R5)
        outcomes = await asyncio.gather(*(
            self._run_agent(agent, [(n, a) for n, a in plan_calls if n.split(".")[0] == agent], emit,
                            user_prompt, confirmed, gated)
            for agent in agents_to_call
        ))
        for agent, (status, result_text) in zip(agents_to_call, outcomes):
            agent_results[agent] = result_text
            agent_status[agent] = status
        held = None
        if gated:
            # A newer held action replaces an older one: "confirm" always runs what Hans was last asked.
            text = " ".join(dict.fromkeys(skills.SKILLS[n].confirm_text for n, _ in gated))
            if conv.hold(gated, text, start_epoch):
                held = conv.pending
            else:
                await self._drop_waiting(agent_status, agent_results, emit)

        # 3. Ciel Final Synthesis with Multi-Turn Context and Direct Obsidian Brain Data
        await emit("ciel_state", {"state": "synthesizing", "message": "Synthesizing vocal response..."})

        # Build recent conversation history snippet
        history_context = ""
        if conv.turns:
            recent_turns = conv.turns[-3:]
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

Subordinate Agent Findings (status is "done", "attention" = ran and found problems, "failed" = could not run, or "needs_confirm" = waiting for Hans and NOT run):
{json.dumps({name: {"status": FINDING_STATUS.get(agent_status.get(name, "done"), agent_status.get(name, "done")), "result": text} for name, text in agent_results.items()}, indent=2) if agent_results else "None"}
If any finding has status "failed", say plainly in both voice lines and the report that the check could not run and why. Never describe a failed check as verified, nominal, synchronized, or successful.{NEEDS_CONFIRM_RULE if "waiting" in agent_status.values() else ""}

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
        synth_meta = {}  # call_llm fills chain / provider / model / ttft_ms; the HUD shows them (5E-4)
        try:
            raw_reply = await run_blocking(
                call_llm, synthesis_prompt, system_instruction=CIEL_SYSTEM_PROMPT,
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

        # 4. Bilingual voice (Japanese first -> English second through the voice filter).
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
        conv.remember({
            "turn_id": len(conv.turns) + 1,
            "timestamp": time.time(),
            "user_prompt": user_prompt,
            "intent_type": intent,
            "agents_called": agents_to_call,
            "skills_called": plan_calls,  # what "run that again" reuses (R5)
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
            "memory_turns": len(conv.turns),
            # Measured on this mission's synthesis call (4B field names); absent on fast paths.
            "llm": {"provider": synth_meta.get("provider"), "model": synth_meta.get("model"),
                    "fallback_chain": synth_meta.get("chain", []), "ttft_ms": synth_meta.get("ttft_ms")},
            "planner": planner,  # who chose the plan: llm / keyword / fallback / confirm (6C, R4)
        }
        if held and conv.live_pending() is not held:
            # Reset, cancelled, replaced or expired during synthesis: "confirm" can no longer run it,
            # so the response must not offer it (Codex R4 F2).
            held = None
            await self._drop_waiting(agent_status, agent_results, emit)
            final_data["failed_agents"] = [a for a, s in agent_status.items() if s == "failed"]
        if held:  # only this mission's own held action, never another mission's
            # The HUD shows Confirm / Cancel for this (ADR-001 R4).
            final_data["pending_confirm"] = {"text": held["text"], "skills": [name for name, _ in held["calls"]],
                                             "expires_in_s": PENDING_TTL_S}

        await emit("ciel_complete", final_data)
        return final_data

    @staticmethod
    async def _drop_waiting(agent_status: dict, agent_results: dict, emit) -> None:
        """Turns this mission's waiting agents into failed: their held action can no longer be confirmed."""
        for agent, status in agent_status.items():
            if status == "waiting":
                agent_status[agent] = "failed"
                agent_results[agent] = ("Not run: memory was cleared, or the action was cancelled or replaced, "
                                        "before it could be confirmed. Nothing was changed.")
                await emit("agent_state", {"agent": agent, "status": "failed",
                                           "result": agent_results[agent], "elapsed_ms": None})

    async def _fast_reply(self, emit, mission_id, start_time, audio_filename, conv, ja_speech, en_speech, planner):
        """A spoken one-line reply with no agents (same shape as the reset-memory reply)."""
        await text_to_speech_bilingual(ja_text=ja_speech, en_text=en_speech, output_filename=audio_filename)
        final_data = {
            "mission_id": mission_id, "reply": en_speech, "japanese_voice": ja_speech, "english_voice": en_speech,
            "audio_file": audio_filename, "audio_url": f"/audio/{audio_filename}",
            "elapsed_ms": round((time.time() - start_time) * 1000, 1), "agents_called": [], "agent_results": {},
            "direct_obsidian": None, "memory_turns": len(conv.turns), "planner": planner,
        }
        await emit("ciel_complete", final_data)
        return final_data


    async def _run_agent(self, agent: str, calls: list, emit, user_prompt: str = "",
                         confirmed: bool = False, gated=None) -> tuple:
        """Runs one subordinate's tool in a worker thread and reports its honest end state.
        confirmed: Hans already confirmed these calls (run as given). Otherwise a call that changes
        things is not run: it is appended to `gated` and the agent ends "waiting" (ADR-001 R4)."""
        await emit("agent_state", {"agent": agent, "status": "active", "task": f"Executing delegated task: {agent}"})
        # The HUD shows this agent's measured time (5E-4): the work only, from the start of
        # the skill calls to their end or timeout. Status events (a slow HUD) do not count, and
        # an agent whose work never started has no time (None), not a made-up 0.
        started = None
        try:
            held = [] if confirmed else [c for c in calls if skills.SKILLS[c[0]].changes_things]
            calls = [c for c in calls if c not in held]
            if gated is not None:
                gated.extend(held)
            if not calls and not held:
                status, result_text = "failed", f"{agent.capitalize()} failed: no skill matches this request"
            elif not calls:
                status = "waiting"
                result_text = " ".join(skills.SKILLS[name].confirm_text for name, _ in held) + " Not run yet: waiting for confirm."
            else:
                # Every agent runs the same skills its Discord commands use (ADR-001 R1-R3).
                # run_calls applies each skill's own timeout and pool and reports a timeout as failed.
                task = "; ".join(skills.SKILLS[name].desc for name, _ in calls) + "..."
                await emit("agent_state", {"agent": agent, "status": "active", "task": task})
                started = time.perf_counter()
                result = await skills.run_calls(calls, confirmed=confirmed)
                status, result_text = result.status, result.text
                if held:
                    status = "failed" if status == "failed" else "waiting"
                    result_text += " " + " ".join(skills.SKILLS[n].confirm_text for n, _ in held) + " Not run yet: waiting for confirm."
        except Exception as e:
            status = "failed"
            result_text = f"{agent.capitalize()} failed: {type(e).__name__}: {e}"
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1) if started is not None else None
        if agent == "sakura":
            await self._log_sakura_dispatch(user_prompt, result_text)
        await emit("agent_state", {"agent": agent, "status": status, "result": result_text, "elapsed_ms": elapsed_ms})
        return status, result_text

    async def _log_sakura_dispatch(self, user_prompt: str, result_text: str) -> None:
        """The Obsidian session line Sakura's Ciel missions always wrote; an outage never changes the result."""
        try:
            await run_blocking(self.obsidian.log_session, "Sakura", f"Ciel Mission: {user_prompt[:50]}",
                               [f"Outcome: Handled ({result_text[:70]}...)"], timeout=VAULT_TIMEOUT_S, label="Obsidian")
        except Exception as _exc:
            print(f"⚠️ [ciel_orchestrator._log_sakura_dispatch] suppressed {type(_exc).__name__}: {_exc}", flush=True)


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
