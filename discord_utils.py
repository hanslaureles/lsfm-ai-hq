import discord
from discord.ext import commands
import re
from typing import List, Optional, Union, Any

# Discord rejects the whole message (HTTP 400) if any embed exceeds these.
EMBED_TITLE_MAX = 256
EMBED_FIELD_VALUE_MAX = 1024
EMBED_DESCRIPTION_MAX = 4096


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"

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
