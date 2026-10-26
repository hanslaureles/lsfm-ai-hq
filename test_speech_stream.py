"""
Tests for speech_stream: sentences come out of a streaming synthesis reply in
speaking order, as soon as each is complete, however the reply is chunked.
Run: python -m pytest -q test_speech_stream.py
"""

import json
import unittest

from speech_stream import VoiceStream, split_sentences

REPLY = json.dumps({
    "japanese_voice": "「告。」解析を完了しました。温度は２５度です！",
    "english_voice": 'Notice: It is 25.5 degrees. Humidity is "high." Version v1.2 is live! Done',
    "display_text": "**Weather** \"report\"\nline two",
}, ensure_ascii=False)  # raw UTF-8, as the LLM sends it
EXPECTED = [
    ("ja", "「告。」"), ("ja", "解析を完了しました。"), ("ja", "温度は２５度です！"),
    ("en", "Notice: It is 25.5 degrees."), ("en", 'Humidity is "high."'),
    ("en", "Version v1.2 is live!"), ("en", "Done"),
]


def feed_in(pieces, fields=None):
    stream, out = (VoiceStream(fields) if fields else VoiceStream()), []
    for piece in pieces:
        out += stream.feed(piece)
    return out


class HeardNothingTest(unittest.TestCase):
    """Assistant B3: Whisper's words for silence never reach Ciel as a command."""

    def test_silence_phrases_and_prompt_echoes_are_blanked(self):
        from unittest import mock
        import voice_engine
        for heard in ("", "  ", ".", "Thank you.", "Thanks for watching!", "you", "ご視聴ありがとうございました。",
                      "Hans, Aaron, Laureles, Ciel."):
            with self.subTest(heard=heard), mock.patch.object(voice_engine, "_transcribe",
                                                              return_value={"text": heard, "engine": "x"}):
                self.assertEqual(voice_engine.transcribe_audio(b"x")["text"], "")
        for said in ("thank you, what's the weather?", "check my vitals", "Ciel, what time is it in Tokyo?",
                     "Git status.", "vitals", "Ciel"):  # short runs of prompt words are real commands
            with self.subTest(said=said), mock.patch.object(voice_engine, "_transcribe",
                                                            return_value={"text": said, "engine": "x"}):
                self.assertEqual(voice_engine.transcribe_audio(b"x"), {"text": said, "engine": "x"})


class FirstClipTest(unittest.TestCase):
    """Assistant B1: a long first sentence is cut at a clause mark, so the first clip is short."""

    EN = (("english_voice", "en"),)

    def test_a_long_first_sentence_is_cut_at_its_first_clause_mark(self):
        reply = json.dumps({"english_voice": "Report: A B-tree keeps every leaf at one depth, because a full "
                                             "node splits and pushes its middle key up. Lookups stay fast, too."})
        for size in (1, 3, len(reply)):  # token-sized pieces, small chunks, all at once
            with self.subTest(size=size):
                out = feed_in([reply[i:i + size] for i in range(0, len(reply), size)], self.EN)
                self.assertEqual(out, [("en", "Report: A B-tree keeps every leaf at one depth,"),
                                       ("en", "because a full node splits and pushes its middle key up."),
                                       ("en", "Lookups stay fast, too.")])  # only the first clip is cut

    def test_a_short_sentence_or_early_mark_is_not_cut(self):
        reply = json.dumps({"english_voice": "Notice: Done, all clear. Next, a long sentence follows here, yes."})
        self.assertEqual(feed_in([reply], self.EN)[:1], [("en", "Notice: Done, all clear.")])  # stop before 24
        self.assertEqual(feed_in([json.dumps({"english_voice": "Version 1,000 of it is live today for everyone."})],
                                 self.EN), [("en", "Version 1,000 of it is live today for everyone.")])


class TestVoiceStream(unittest.TestCase):

    def test_whole_reply(self):
        self.assertEqual(feed_in([REPLY]), EXPECTED)

    def test_any_chunking_gives_the_same_sentences(self):
        # One character at a time splits every escape, keyword and stop.
        self.assertEqual(feed_in(list(REPLY)), EXPECTED)
        for size in (2, 3, 7, 13):
            with self.subTest(size=size):
                self.assertEqual(feed_in([REPLY[i:i + size] for i in range(0, len(REPLY), size)]), EXPECTED)

    def test_first_sentence_is_out_before_the_reply_finishes(self):
        stream = VoiceStream()
        head = REPLY[:REPLY.index("解析") + 1]  # one character past 「告。」
        self.assertEqual(stream.feed(head), [("ja", "「告。」")])

    def test_escaped_and_ascii_escaped_text(self):
        reply = json.dumps({"japanese_voice": "告。テスト😀。", "english_voice": "Say \"hi\".\nNext line."},
                           ensure_ascii=True)  # \u escapes, including a surrogate pair
        self.assertEqual(feed_in(list(reply)), [("ja", "告。"), ("ja", "テスト😀。"),
                                                ("en", 'Say "hi".'), ("en", "Next line.")])

    def test_japanese_is_spoken_first_whatever_the_key_order(self):
        reply = json.dumps({"english_voice": "Report: first.", "japanese_voice": "告。"}, ensure_ascii=False)
        self.assertEqual(feed_in(list(reply)), [("ja", "告。"), ("en", "Report: first.")])

    def test_no_voice_fields(self):
        self.assertEqual(feed_in(["not json at all. Really."]), [])


class TestSplit(unittest.TestCase):

    def test_open_ending_is_held_until_final(self):
        self.assertEqual(split_sentences("One. Two.", final=False), (["One."], 4))
        self.assertEqual(split_sentences("One. Two.", final=True), (["One.", "Two."], 9))


if __name__ == "__main__":
    unittest.main()
