"""Preview or upsert the server guide in one explicitly selected Discord guild.

Default: REST reads and a printed preview. --apply creates/edits six managed
messages and pins the starter when permitted; otherwise asks for a manual pin.
No gateway, announcement, deletion or unpinning.
Run one updater at a time; discord.py handles bounded HTTP/rate-limit retries.
"""

import argparse
import asyncio
import os
from pathlib import Path
import sys

import discord
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
MARKER = "LSFM HQ guide · "
LEGACY_MARKER = "lsfm-server-guide:v1:"
CHANNEL_NAMES = (
    "all-commands", "command-center", "daily-briefing", "approvals",
    "job-tailoring", "application-docs", "portfolio-audits", "project-status",
    "frontend-lab", "git-log", "pc-vitals", "system-alerts",
)


def resolve_channels(channels, guild_id):
    """Fail closed if a required text-channel name is absent or ambiguous."""
    result = {}
    for name in CHANNEL_NAMES:
        matches = [channel for channel in channels
                   if channel.guild.id == guild_id and channel.name == name
                   and channel.type == discord.ChannelType.text]
        if len(matches) != 1:
            raise ValueError(f"Expected one #{name} text channel in guild {guild_id}.")
        result[name] = matches[0]
    return result


def guide_cards(channels):
    """A concise task menu plus commands verified in the five bot modules."""
    def link(name):
        return channels[name].mention

    cards = [
        ("start", "Start here · LSFM AI HQ", (
            f"**Plan today** → {link('command-center')} · briefings in {link('daily-briefing')}\n"
            f"**Find a role / tailor an application** → {link('job-tailoring')}\n"
            f"**Review application packages** → {link('approvals')} · PDFs in {link('application-docs')}\n"
            f"**Review the portfolio** → {link('portfolio-audits')} · progress in {link('project-status')}\n"
            f"**Discuss code** → {link('frontend-lab')} · Git reviews in {link('git-log')}\n"
            f"**Check the PC / vault** → {link('pc-vitals')} · alerts in {link('system-alerts')}\n\n"
            "Start in the matching agent's home channel below and describe what you need. "
            "Use the specific commands on each card for repeatable tasks.\n\n"
            "**Your approval matters:** review facts and attachments before acting. "
            "Approval choices record your decision; they do not submit job applications. "
            "Open a PDF attachment in Discord to view or download it on your phone."
        )),
        ("sakura", "Sakura · planning & coordination", (
            f"Home: {link('command-center')} / {link('daily-briefing')}\n"
            "`!briefing` — morning briefing; `!rollup` — day review.\n"
            "`!strategy <question>` — strategic advice; `!goals` — current goals.\n"
            "`!hunt` — look for roles; `!apply <URL or job text>` — prepare a team proposal for review.\n"
            "`!brain` — inference status; `!mode auto|cloud|local` — change inference mode.\n"
            "`!inbox` — Gmail triage **also archives** newsletters/promotions. "
            "`!clean` — archive promotional clutter after your confirmation.\n"
            "`!receipts` — recent finance / order summary."
        )),
        ("chaewon", "Chaewon · jobs & application documents", (
            f"Home: {link('job-tailoring')} / {link('application-docs')}\n"
            "`!scout <query>` — search roles; `!tailor <URL or job text>` — tailor a package "
            "(or attach a `.txt` / `.md` job description).\n"
            "`!profile` — resume context; `!history` — recent tailored applications.\n"
            "`!screen <questions>` — draft screening answers; verify every claim yourself.\n"
            "`!pdf <role>` — generate resume / letter PDFs. "
            "`!rebuild_resume` — rebuild the master resume after confirmation; updates shared copies.\n"
            f"Look for PDF attachments in the reply or {link('application-docs')}; "
            "download and review them before submitting manually."
        )),
        ("yunjin", "Yunjin · portfolio & design", (
            f"Home: {link('portfolio-audits')} / {link('project-status')}\n"
            "`!status` — portfolio progress.\n"
            "In the home channel, ask for a portfolio audit or a critique of a named project.\n"
            "Use feedback as a review aid; inspect the live page before calling a change complete."
        )),
        ("kazuha", "Kazuha · frontend & Git", (
            f"Home: {link('frontend-lab')} / {link('git-log')}\n"
            "`!git` — workspace Git status; `!git review` — review changes "
            "(`!git review --staged` for staged changes).\n"
            "`!git commit` — draft a commit message; `!git pr` — draft a PR description.\n"
            "`!inspect <filename>` — inspect a file; `!component <description>` — draft a component.\n"
            "`!scout_research` — research digest. Drafts and reviews still need your verification."
        )),
        ("eunchae", "Eunchae · PC health & quality", (
            f"Home: {link('pc-vitals')} / {link('system-alerts')}\n"
            "`!vitals` — CPU, RAM, disk and uptime; `!checkin` — system check-in.\n"
            "`!heartbeat` — vault connectivity check.\n"
            "In the home channel, describe the file or behavior you want checked. "
            "A check covers its reported scope; it does not guarantee zero regressions."
        )),
    ]
    return {key: discord.Embed(title=title, description=body, color=0xF472B6)
            .set_footer(text=MARKER + ("Start here" if key == "start" else key.title()))
            for key, title, body in cards}


async def update_guide(channel, bot_id, cards, *, apply=False, pin_start=True):
    """Read the full history before writing, preserving every unmanaged message."""
    managed = {}
    async for message in channel.history(limit=None):
        if message.author.id != bot_id:
            continue
        for embed in message.embeds:
            marker = embed.footer.text or ""
            prefix = next((p for p in (MARKER, LEGACY_MARKER) if marker.startswith(p)), None)
            label = marker[len(prefix):].casefold() if prefix else ""
            key = "start" if label == "start here" else label
            if key in cards:
                if key in managed:
                    raise ValueError(f"Duplicate managed {key} cards; resolve manually before applying.")
                managed[key] = message
    actions = []
    for key, embed in cards.items():
        message = managed.get(key)
        changed = message is None or [e.to_dict() for e in message.embeds] != [embed.to_dict()]
        action = "create" if message is None else "edit" if changed else "keep"
        actions.append((key, action))
        if apply:
            if message is None:
                message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            elif changed:
                await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            if key == "start" and not message.pinned and pin_start:
                await message.pin(reason="LSFM AI HQ starter guide")
    return actions


async def run(guild_id, *, apply=False):
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("SAKURA_BOT_TOKEN")
    if not token:
        raise ValueError("SAKURA_BOT_TOKEN is required in the environment or project .env.")
    async with discord.Client(intents=discord.Intents.none(), max_ratelimit_timeout=30) as client:
        await client.login(token)
        guild = await client.fetch_guild(guild_id)
        if guild.id != guild_id:
            raise ValueError("Discord returned a different guild; refusing to proceed.")
        channels = resolve_channels(await guild.fetch_channels(), guild_id)
        target = channels["all-commands"]
        member = await guild.fetch_member(client.user.id)
        permissions = target.permissions_for(member)
        required = ["view_channel", "read_message_history"]
        if apply:
            required += ["send_messages", "embed_links"]
        if any(not getattr(permissions, permission) for permission in required):
            raise ValueError("Missing channel permissions; no guide messages changed.")
        can_pin = permissions.manage_messages or getattr(permissions, "pin_messages", False)
        cards = guide_cards(channels)
        for key, action in await update_guide(target, client.user.id, cards, apply=apply, pin_start=can_pin):
            print(f"{key}: {action}")
        if apply and not can_pin:
            print("Guide published. This bot cannot pin; use Discord to pin the starter if needed.")
        if not apply:
            print("DRY RUN: no messages or pins changed. Preview:")
            for embed in cards.values():
                print(f"\n{embed.title}\n{embed.description}")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guild-id", required=True, type=int, help="Exact target guild ID (no fallback).")
    parser.add_argument("--apply", action="store_true", help="Publish managed cards; pin starter if allowed, otherwise request a manual pin.")
    args = parser.parse_args(argv)
    if args.guild_id <= 0:
        parser.error("--guild-id must be a positive Discord guild ID")
    try:
        asyncio.run(run(args.guild_id, apply=args.apply))
    except ValueError as exc:
        print(f"Guide update stopped: {exc}")
        return 1
    except (discord.DiscordException, OSError, asyncio.TimeoutError) as exc:
        # HTTP response bodies can contain private data; only report the failure class/status.
        print(f"Guide update stopped: {type(exc).__name__} (status {getattr(exc, 'status', 'n/a')}). "
              "Some managed cards may have changed; rerun dry-run before retrying.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
