"""Deterministic routing shared by the five Discord clients; no inference calls."""

import copy
import re


HOME_CHANNELS = {
    "sakura": ("command-center", "daily-briefing"),
    "chaewon": ("job-tailoring", "application-docs"),
    "kazuha": ("frontend-lab", "git-log"),
    "yunjin": ("portfolio-audits", "project-status"),
    "eunchae": ("pc-vitals", "system-alerts"),
}
# Keep aliases here too: command ownership must survive a client's outage/startup.
COMMANDS = {
    "sakura": "help ping hunt apply brain mode goals briefing plan launchpad morning rollup standup evening close strategy consult advice inbox triage clean sweep receipts expenses cleanse mass_sort".split(),
    "chaewon": "help ping profile history scout radar jobs findjobs tailor pdf screen qa question screening rebuild_resume resume compile_resume master_resume".split(),
    "kazuha": "help ping ask search rag find reindex ragstats git review commit pr inspect component recall heuristics lessons rules guidelines kreflect kazuha_reflect scout_research digest paper research ai_scout".split(),
    "yunjin": "help ping status audit critique".split(),
    "eunchae": "help ping vitals health checkin qa audit lessons failures heuristics reflect test testcode pytest runtest heartbeat obsidian brain_ping vault_status telemetry".split(),
}
DEFAULTS = {"audit": "yunjin", "qa": "eunchae", "lessons": "eunchae", "heuristics": "eunchae"}
_identities = {}
_mention = re.compile(r"<@!?(\d+)>")


def register(agent, user):
    """Remember actual IDs on ready/reconnect; names are only an offline fallback."""
    _identities[user.id] = agent


def agent_for(user_id):
    return _identities.get(user_id)


def content_for(message, agent, bot):
    """Return normalized text for the selected client, or None for every other one.

    The caller must run owner_guard first. Explicit targets never fall through to
    another bot, even when the requested command belongs to that other bot.
    """
    content = message.content.strip()
    mentions = {user.id: user for user in getattr(message, "mentions", ())}
    addressed = None
    bot_mentions = set()
    for match in _mention.finditer(content):
        user_id = int(match.group(1))
        user = mentions.get(user_id)
        if user_id in _identities or (user is not None and getattr(user, "bot", False)):
            bot_mentions.add(user_id)
            if addressed is None:
                if user_id == getattr(getattr(bot, "user", None), "id", None):
                    addressed = agent
                else:
                    addressed = _identities.get(user_id, "unavailable")

    # Strip bot addressing only; user mentions inside command arguments survive.
    content = _mention.sub(lambda m: "" if int(m.group(1)) in bot_mentions else m.group(0), content).strip()
    if addressed is not None:
        target = addressed
    elif getattr(message, "guild", None) is None:
        target = agent  # a DM is delivered only to its recipient client
    else:
        channel = getattr(message, "channel", None)
        channel = getattr(channel, "parent", None) or channel
        name = (getattr(channel, "name", "") or "").lower()
        home = next((a for a, names in HOME_CHANNELS.items() if any(n in name for n in names)), None)
        if content.startswith("!"):
            command = content[1:].split(maxsplit=1)[0] if content[1:].strip() else ""
            owners = [a for a, commands in COMMANDS.items() if command in commands]
            target = home if home in owners else DEFAULTS.get(command)
            target = target or (owners[0] if owners else home or "sakura")
        else:
            target = home or ("sakura" if "commands-chat" in name else None)
    return content if target == agent else None


async def process_commands(bot, message, content):
    """Let discord.py parse '@Agent !command' without mutating the incoming event."""
    command_message = copy.copy(message)
    command_message.content = content
    await bot.process_commands(command_message)
