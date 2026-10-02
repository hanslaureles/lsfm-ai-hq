"""
speech_stream.py - Pulls Ciel's spoken lines out of a synthesis reply while it streams.

The synthesis LLM answers with one JSON object that starts with "japanese_voice"
and "english_voice". VoiceStream.feed() takes the reply piece by piece and
returns each sentence of those two fields as soon as it is complete, so speech
can start before the rest of the reply exists. Pure stdlib.
"""

import re

FIELDS = (("japanese_voice", "ja"), ("english_voice", "en"))
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_FULL_STOPS = "。！？"      # end a sentence by themselves
_ASCII_STOPS = ".!?"       # end one only before whitespace, so "2.5" and "v1.2" stay whole
_CLOSERS = "」』）)\"'”’"   # stay with the sentence they close


def decode_partial(s: str, i: int) -> tuple:
    """Decodes a JSON string body that starts at s[i]. Returns (text so far, closed)."""
    out = []
    while i < len(s):
        c = s[i]
        if c == '"':
            return "".join(out), True
        if c == "\\":
            if i + 1 >= len(s):
                break  # the escape is split across pieces; wait for the rest
            e = s[i + 1]
            if e == "u":
                if i + 6 > len(s):
                    break
                out.append(chr(int(s[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(_ESCAPES.get(e, e))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out), False


def _whole(text: str) -> str:
    # 😀-style escapes decode to two surrogate halves; join them.
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def split_sentences(text: str, final: bool) -> tuple:
    """
    Returns (complete sentences, chars consumed). Unless final, a sentence that
    may still grow (a stop at the very end, which a closer or space could follow)
    is left unconsumed.
    """
    out, start, i, n = [], 0, 0, len(text)
    while i < n:
        c = text[i]
        end = None
        if c in _FULL_STOPS or c in _ASCII_STOPS:
            j = i + 1
            while j < n and text[j] in _CLOSERS:
                j += 1
            if j == n:
                if not final:
                    break
                end = j
            elif c in _FULL_STOPS or text[j].isspace():
                end = j
        if end is None:
            i += 1
            continue
        sentence = text[start:end].strip()
        if sentence:
            out.append(_whole(sentence))
        start = i = end
    if final:
        rest = text[start:].strip()
        if rest:
            out.append(_whole(rest))
        start = n
    return out, start


class VoiceStream:
    """Feed it the reply as it streams; it hands back (lang, sentence) pairs in speaking order."""

    def __init__(self):
        self.buf = ""
        self.used = {}       # lang -> chars of that field already handed out
        self.closed = set()  # langs whose field has ended

    def feed(self, piece: str) -> list:
        self.buf += piece
        out = []
        for field, lang in FIELDS:
            if lang in self.closed:
                continue
            m = re.search(r'"%s"\s*:\s*"' % field, self.buf)
            if not m:
                break  # fields are spoken in order: no English before the Japanese is done
            text, closed = decode_partial(self.buf, m.end())
            pos = self.used.get(lang, 0)
            sentences, used = split_sentences(text[pos:], final=closed)
            self.used[lang] = pos + used
            out += [(lang, s) for s in sentences]
            if not closed:
                break
            self.closed.add(lang)
        return out
