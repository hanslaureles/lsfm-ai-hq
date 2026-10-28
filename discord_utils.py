import asyncio
import discord
from discord.ext import commands
import re
import json
from pathlib import Path
from typing import List, Optional, Union, Any

# Discord rejects the whole message (HTTP 400) if any embed exceeds these.
EMBED_TITLE_MAX = 256
EMBED_FIELD_VALUE_MAX = 1024
EMBED_DESCRIPTION_MAX = 4096


CONFIRM_EMOJI, CANCEL_EMOJI = "✅", "❌"


async def confirm_action(bot, ctx, text: str, timeout: float = 60.0) -> bool:
    """
    Asks the command's author to approve an action that changes things (ADR-001 R4): posts
    `text`, adds ✅ and ❌, and waits for the author's reaction on that message only.
    True only for the author's ✅; ❌ or no answer leaves the action unrun and says so.
    """
    msg = await ctx.reply(f"{text}\nReact {CONFIRM_EMOJI} to go ahead or {CANCEL_EMOJI} to cancel "
                          f"(waiting {timeout:g} s).")
    await msg.add_reaction(CONFIRM_EMOJI)
    await msg.add_reaction(CANCEL_EMOJI)

    def check(reaction, user):
        return (reaction.message.id == msg.id and user.id == ctx.author.id
                and str(reaction.emoji) in (CONFIRM_EMOJI, CANCEL_EMOJI))

    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=timeout, check=check)
    except asyncio.TimeoutError:
        await msg.edit(content=f"No answer in {timeout:g} s. Nothing was changed.")
        return False
    if str(reaction.emoji) == CONFIRM_EMOJI:
        return True
    await msg.edit(content="Cancelled. Nothing was changed.")
    return False


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


class ThreadTarget:
    """Keep existing command/message handlers while sending their replies into the task thread."""

    def __init__(self, original, thread):
        self.original, self.channel = original, thread

    def __getattr__(self, name):
        return getattr(self.original, name)

    async def reply(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)

    async def send(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)

    def typing(self):
        return self.channel.typing()


async def _details_thread(message, name):
    create = getattr(message, "create_thread", None)
    if create is None:
        return None
    try:
        return await create(name=clip(name, 100), auto_archive_duration=1440,
                            reason="Keep one LSFM task together")
    except discord.HTTPException as error:
        print(f"[Discord] Thread unavailable: {type(error).__name__}; keeping results in channel", flush=True)
        return None


async def start_task_thread(target, name):
    if not getattr(target, "guild", None) or isinstance(target.channel, discord.Thread):
        return target
    message = getattr(target, "message", target)
    thread = getattr(message, "thread", None) or await _details_thread(message, name)
    return ThreadTarget(target, thread) if thread is not None else target


async def send_summary(channel, title, content, color=0xF472B6, footer_text="LSFM AI HQ",
                       status_msg_to_delete=None):
    """A short overview; the complete result stays available even if threads are unavailable."""
    if status_msg_to_delete is not None:
        try:
            await status_msg_to_delete.delete()
        except Exception:  # quiet: transient cleanup must never discard the completed report
            pass
    if len(content) <= 1000 or isinstance(channel, discord.Thread):
        await send_clean_embeds(channel, title, content, color=color, footer_text=footer_text)
        return
    overview = content.split("\n\n**Details**", 1)[0]
    embed = discord.Embed(title=clip(title, 256), description=clip(overview, 900), color=color)
    embed.set_footer(text=footer_text + " · Full report in the thread")
    message = await channel.send(embed=embed)
    thread = await _details_thread(message, title + " · details")
    if thread is None:
        embed.set_footer(text=footer_text + " · Full report follows below")
        if hasattr(message, "edit"):
            await message.edit(embed=embed)
    await send_clean_embeds(thread or channel, title + " · full report", content,
                            color=color, footer_text=footer_text)


async def update_status_card(channel, embed, state_file):
    """Reuse the bot's saved routine status message, including after process restart."""
    state_file = Path(state_file)
    try:
        saved = json.loads(state_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        saved = {}
    if not isinstance(saved, dict):
        raise ValueError("Status message state must be an object")
    message_id = saved.get(str(channel.id))
    if message_id is not None:
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            message = None
        if message is not None:
            if message.author.id != channel.guild.me.id:
                raise ValueError("Saved status message belongs to another author")
            await message.edit(embed=embed)
            return message
    message = await channel.send(embed=embed)
    saved[str(channel.id)] = message.id
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    temporary.replace(state_file)
    return message

def split_smart_chunks(text: str, max_chars: int = 3800) -> List[str]:
    """
    Intelligently splits long text into chunks along markdown headers, paragraphs,
    sentences, or words, guaranteeing that every chunk strictly stays under max_chars
    and no sentences or words are sliced awkwardly in half.
    Properly balances and maintains markdown code blocks (```) across chunk boundaries.
    """
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    # Re-balancing code fences below can add "```lang\n" in front of a chunk and
    # "\n```" after it, so split to a budget that leaves room for both.
    langs = re.findall(r"```(\w*)", text)
    fence_room = (3 + max(map(len, langs)) + 1 + 4) if langs else 0
    max_chars = max(1, max_chars - fence_room)

    # Split primarily into paragraphs
    paragraphs = text.split("\n\n")
    raw_chunks = []
    current_chunk = ""

    for para in paragraphs:
        candidate = f"{current_chunk}\n\n{para}" if current_chunk else para
        if len(candidate) <= max_chars:
            current_chunk = candidate
        else:
            if current_chunk:
                raw_chunks.append(current_chunk)
                current_chunk = ""

            if len(para) <= max_chars:
                current_chunk = para
            else:
                # Paragraph exceeds limit, break down by line
                lines = para.split("\n")
                for line in lines:
                    candidate_line = f"{current_chunk}\n{line}" if current_chunk else line
                    if len(candidate_line) <= max_chars:
                        current_chunk = candidate_line
                    else:
                        if current_chunk:
                            raw_chunks.append(current_chunk)
                            current_chunk = ""

                        if len(line) <= max_chars:
                            current_chunk = line
                        else:
                            # Line exceeds limit, break down by sentence
                            sentences = re.split(r'(?<=\. )', line)
                            for s in sentences:
                                candidate_s = f"{current_chunk} {s}" if current_chunk else s
                                if len(candidate_s) <= max_chars:
                                    current_chunk = candidate_s
                                else:
                                    if current_chunk:
                                        raw_chunks.append(current_chunk)
                                        current_chunk = ""

                                    if len(s) <= max_chars:
                                        current_chunk = s
                                    else:
                                        # Sentence exceeds limit, break down by word
                                        words = s.split(" ")
                                        for w in words:
                                            candidate_w = f"{current_chunk} {w}" if current_chunk else w
                                            if len(candidate_w) <= max_chars:
                                                current_chunk = candidate_w
                                            else:
                                                if current_chunk:
                                                    raw_chunks.append(current_chunk)
                                                    current_chunk = ""
                                                # A single word longer than a chunk (a URL, a
                                                # base64 blob) is split, not truncated.
                                                while len(w) > max_chars:
                                                    raw_chunks.append(w[:max_chars])
                                                    w = w[max_chars:]
                                                current_chunk = w

    if current_chunk:
        raw_chunks.append(current_chunk)

    if not raw_chunks:
        raw_chunks = [text]

    # Post-process: balance markdown code blocks (```) across chunks
    processed_chunks = []
    active_code_fence = None  # e.g., 'python', 'bash', or ''

    for chunk in raw_chunks:
        chunk_content = chunk
        if active_code_fence is not None:
            chunk_content = f"```{active_code_fence}\n{chunk_content}"
            active_code_fence = None

        # Check if an open code fence exists in this chunk
        fences = re.findall(r"```(\w*)", chunk_content)
        if len(fences) % 2 != 0:
            active_code_fence = fences[-1] or ""
            chunk_content = f"{chunk_content}\n```"

        processed_chunks.append(chunk_content)

    return processed_chunks


async def send_clean_embeds(
    target: Union[commands.Context, discord.Message, discord.abc.Messageable, Any],
    title: str,
    content: str,
    color: int = 0x00B4D8,
    footer_text: str = "LE SSERAFIM AI HQ",
    citations: Optional[List[str]] = None,
    status_msg_to_delete: Optional[discord.Message] = None,
    thumbnail_url: Optional[str] = None
):
    """
    Sends long text responses strictly inside styled Discord Embeds without clipping or raw text spills.
    Handles responses of any length by paginating into clean consecutive embeds.
    Works seamlessly with Context, Message, or TextChannel targets.
    """
    # If a status message was provided, clean it up before sending final embeds
    if status_msg_to_delete:
        try:
            await status_msg_to_delete.delete()
        except Exception:  # quiet: best-effort delete of a status message
            pass

    chunks = split_smart_chunks(content, max_chars=3800)
    total_parts = len(chunks)

    for i, chunk in enumerate(chunks, 1):
        suffix = "" if total_parts == 1 else f" (Part {i}/{total_parts})"
        part_title = clip(title, EMBED_TITLE_MAX - len(suffix)) + suffix
        embed = discord.Embed(
            title=part_title,
            description=chunk,
            color=color
        )
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        # Attach citations and final footer on the last part
        if i == total_parts:
            if citations:
                cite_str = clip(" · ".join(citations[:5]), EMBED_FIELD_VALUE_MAX)
                embed.add_field(name="📚 Verified Citations", value=cite_str, inline=False)
            embed.set_footer(text=footer_text)
        else:
            embed.set_footer(text=f"{footer_text} • Continued in Part {i+1}/{total_parts} below...")

        # Dispatch
        if i == 1:
            replied = False
            if hasattr(target, "reply"):
                try:
                    await target.reply(embed=embed)
                    replied = True
                except Exception as _exc:
                    print(f"⚠️ [discord_utils.send_clean_embeds] suppressed {type(_exc).__name__}: {_exc}", flush=True)
                    replied = False
            if not replied:
                if hasattr(target, "channel") and hasattr(target.channel, "send"):
                    await target.channel.send(embed=embed)
                elif hasattr(target, "send"):
                    await target.send(embed=embed)
        else:
            if hasattr(target, "channel") and hasattr(target.channel, "send"):
                await target.channel.send(embed=embed)
            elif hasattr(target, "send"):
                await target.send(embed=embed)
