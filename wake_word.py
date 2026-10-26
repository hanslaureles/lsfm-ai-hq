"""
wake_word.py - Wake word for Ciel (assistant B7) with openWakeWord, run on the Ciel server.

The HUD streams its microphone as 16 kHz mono 16-bit PCM over /ws/wake; WakeDetector.feed() takes those
bytes, scores each 80 ms frame, and returns the score when the wake word is heard (then stays quiet for
COOLDOWN_S, so one phrase is one wake). The model runs locally (ONNX); no audio leaves the machine.

openWakeWord ships pretrained phrases ("hey jarvis", "alexa", "hey mycroft", "hey rhasspy"); CIEL_WAKE_MODEL
picks one (default hey_jarvis). A "hey Ciel" model needs its own training run.
"""

import os
import time

FRAME_SAMPLES = 1280  # 80 ms at 16 kHz: openWakeWord's step
FRAME_BYTES = FRAME_SAMPLES * 2
THRESHOLD = float(os.getenv("CIEL_WAKE_THRESHOLD", "0.5"))
COOLDOWN_S = 2.0


def model_name() -> str:
    return (os.getenv("CIEL_WAKE_MODEL") or "hey_jarvis").strip()


def load_model(name: str = None):
    """openWakeWord's model for `name` on ONNX (its tflite runtime has no Windows build). Raises if missing."""
    from openwakeword.model import Model
    return Model(wakeword_models=[name or model_name()], inference_framework="onnx")


def available() -> tuple:
    """(True, model name) when openWakeWord and its model load, else (False, the reason)."""
    try:
        load_model()
        return True, model_name()
    except Exception as e:  # not installed, or the model was never downloaded
        return False, f"{type(e).__name__}: {e}"


class WakeDetector:
    """One per audio stream: openWakeWord keeps per-stream state between frames."""

    def __init__(self, model=None, threshold: float = THRESHOLD, cooldown_s: float = COOLDOWN_S, clock=time.monotonic):
        self.model = model if model is not None else load_model()
        self.threshold, self.cooldown_s, self.clock = threshold, cooldown_s, clock
        self.buf = b""
        self.last_wake = None

    def feed(self, pcm: bytes):
        """The score (0-1) when this audio completes the wake word, else None. Partial frames wait."""
        import numpy as np
        self.buf += pcm
        fired = None
        while len(self.buf) >= FRAME_BYTES:
            frame, self.buf = self.buf[:FRAME_BYTES], self.buf[FRAME_BYTES:]
            scores = self.model.predict(np.frombuffer(frame, dtype=np.int16))
            score = max(scores.values()) if scores else 0.0
            now = self.clock()
            if score >= self.threshold and (self.last_wake is None or now - self.last_wake >= self.cooldown_s):
                self.last_wake = now
                self.model.reset()  # drop the phrase's frames, so it does not fire again next frame
                fired = round(float(score), 3)
        return fired
