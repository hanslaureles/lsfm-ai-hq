"""
Assistant B7: the wake-word detector's framing, threshold and cooldown. Hermetic: a fake model, no
openWakeWord or ONNX needed. Run: python -m pytest -q test_wake_word.py
"""

import unittest

import wake_word


class FakeModel:
    """Scores frames from a script; records frame sizes and resets."""

    def __init__(self, scores):
        self.scores, self.frames, self.resets = list(scores), [], 0

    def predict(self, x):
        self.frames.append(len(x))
        return {"hey_jarvis": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.08  # each frame is 80 ms of audio
        return self.t


FRAME = b"\x00\x00" * wake_word.FRAME_SAMPLES


class WakeDetectorTest(unittest.TestCase):
    def test_frames_are_80_ms_and_partial_audio_waits(self):
        model = FakeModel([])
        d = wake_word.WakeDetector(model, clock=Clock())
        self.assertIsNone(d.feed(FRAME[:1000]))
        self.assertEqual(model.frames, [])
        d.feed(FRAME[1000:] + FRAME[:10])
        self.assertEqual(model.frames, [wake_word.FRAME_SAMPLES])
        self.assertEqual(len(d.buf), 10)

    def test_a_phrase_wakes_once_then_cooldown(self):
        model = FakeModel([0.1, 0.92, 0.95, 0.9] + [0.0] * 30 + [0.8])
        d = wake_word.WakeDetector(model, threshold=0.5, cooldown_s=2.0, clock=Clock())
        fired = [d.feed(FRAME) for _ in range(35)]
        self.assertEqual([f for f in fired if f], [0.92, 0.8])  # the 2nd and 3rd high frames fall in the cooldown
        self.assertEqual(model.resets, 2)

    def test_below_threshold_never_wakes(self):
        d = wake_word.WakeDetector(FakeModel([0.49] * 5), threshold=0.5, clock=Clock())
        self.assertEqual([d.feed(FRAME) for _ in range(5)], [None] * 5)


if __name__ == "__main__":
    unittest.main()
