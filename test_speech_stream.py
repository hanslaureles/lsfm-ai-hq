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


def feed_in(pieces):
    stream, out = VoiceStream(), []
    for piece in pieces:
        out += stream.feed(piece)
    return out


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
