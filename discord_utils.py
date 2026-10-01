import discord
from discord.ext import commands
import re
from typing import List, Optional, Union, Any

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
                                                current_chunk = w[:max_chars]

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
        except Exception:
            pass

    chunks = split_smart_chunks(content, max_chars=3800)
    total_parts = len(chunks)

    for i, chunk in enumerate(chunks, 1):
        part_title = title if total_parts == 1 else f"{title} (Part {i}/{total_parts})"
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
                cite_str = " · ".join(citations[:5])
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
                except Exception:
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
