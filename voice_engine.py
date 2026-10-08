"""
Voice Engine for Manas: Ciel
Provides ultra-fast, zero-cost Speech-to-Text (Groq Whisper) and Neural Text-to-Speech (Edge-TTS).
"""

import os
import io
import asyncio
import urllib.request
import urllib.parse
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import edge_tts

BASE_DIR = Path(__file__).parent
AUDIO_DIR = BASE_DIR / "audio_cache"
AUDIO_DIR.mkdir(exist_ok=True)

# Explicitly load .env from BASE_DIR
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

# Domain Lexicon to prevent phonetic mangling by Whisper
DOMAIN_WHISPER_PROMPT = (
    "Hans, Aaron, Laureles, Ciel, Tensura, Raphael, Sakura, Chaewon, Yunjin, Kazuha, Eunchae, "
    "Obsidian, Brain, daily log, Git, status, vitals, hardware, AMD, Ollama, Vercel, portfolio, ATS, resume."
)

CIEL_JA_VOICE = os.getenv("CIEL_JA_VOICE", "ja-JP-NanamiNeural")
CIEL_JA_PITCH = os.getenv("CIEL_JA_PITCH", "-3Hz")
CIEL_JA_RATE = os.getenv("CIEL_JA_RATE", "-2%")

CIEL_EN_VOICE = os.getenv("CIEL_EN_VOICE", "en-US-AvaNeural")
CIEL_EN_PITCH = os.getenv("CIEL_EN_PITCH", "-2Hz")
CIEL_EN_RATE = os.getenv("CIEL_EN_RATE", "-1%")

# Defaults
CIEL_DEFAULT_VOICE = CIEL_JA_VOICE
CIEL_DEFAULT_PITCH = CIEL_JA_PITCH
CIEL_DEFAULT_RATE = CIEL_JA_RATE


def prune_audio_cache(max_files: int = 35) -> int:
    """
    Cleans up old audio files from audio_cache/ to prevent disk accumulation.
    Keeps only the most recent `max_files`.
    """
    try:
        audio_files = sorted(
            [f for f in AUDIO_DIR.glob("*.*") if f.suffix.lower() in (".mp3", ".wav")],
            key=lambda x: x.stat().st_mtime
        )
        removed = 0
        if len(audio_files) > max_files:
            to_delete = audio_files[:len(audio_files) - max_files]
            for f in to_delete:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
    except Exception as e:
        print(f"⚠️ [VoiceEngine] Audio cache prune error: {e}", flush=True)
        return 0


def normalize_audio_for_transcription(audio_bytes: bytes) -> bytes:
    """
    Uses FFmpeg to convert incoming audio (WebM/Opus, Ogg, raw WAV) into 16 kHz
    mono WAV with a fixed gain (volume=1.8) and an 80 Hz-7.5 kHz band-pass (no
    loudness normalisation). 32 ms p50, N=10 (bench/results/2026-10-01.md, ffmpeg_inbound).
    """
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-ar", "16000",
            "-ac", "1",
            "-af", "volume=1.8,highpass=f=80,lowpass=f=7500",
            "-f", "wav",
            "pipe:1"
        ]
        proc = subprocess.run(
            cmd,
            input=audio_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True
        )
        if proc.stdout and len(proc.stdout) > 44:
            return proc.stdout
    except Exception as _exc:
        print(f"⚠️ [voice_engine.normalize_audio_for_transcription] suppressed {type(_exc).__name__}: {_exc}", flush=True)
    return audio_bytes


def apply_telepathy_dsp(raw_path: Path, output_path: Path, lead_ms: int = 0) -> bool:
    """
    Applies Tensura Divine Wisdom telepathic resonance:
    - Highpass 120Hz (removes muddy room boom)
    - 3.5kHz crystalline EQ boost (crisp clarity)
    - Dual 25ms/45ms subtle comb reflection (speaks inside the soul corridor)
    lead_ms prepends silence (the 350 ms gap before the first English clip).
    Takes ~40ms on local CPU via FFmpeg.
    """
    lead = f"adelay={int(lead_ms)}:all=1," if lead_ms else ""
    try:
        cmd = [
            "ffmpeg", "-y", "-i", str(raw_path),
            "-af", lead + "highpass=f=120,equalizer=f=3500:t=q:w=1.2:g=3.0,aecho=0.85:0.8:25|45:0.18|0.09,volume=0.30",
            "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as _exc:
        print(f"⚠️ [voice_engine.apply_telepathy_dsp] suppressed {type(_exc).__name__}: {_exc}", flush=True)
        return False


async def text_to_speech(
    text: str,
    voice: str = CIEL_DEFAULT_VOICE,
    pitch: str = CIEL_DEFAULT_PITCH,
    rate: str = CIEL_DEFAULT_RATE,
    output_filename: str = "ciel_reply.mp3",
    apply_dsp: bool = True
) -> Path:
    """
    Converts single text to speech using calibrated Microsoft Edge neural TTS
    and enhances it with Tensura Thought Acceleration acoustic modeling.
    """
    clean_text = text.strip()
    if not clean_text:
        clean_text = "告。ご命令を。"

    final_output = AUDIO_DIR / output_filename
    temp_raw = AUDIO_DIR / f"raw_{output_filename}"

    communicate = edge_tts.Communicate(clean_text, voice=voice, pitch=pitch, rate=rate)
    target_path = temp_raw if apply_dsp else final_output
    await communicate.save(str(target_path))

    if apply_dsp:
        # FFmpeg runs in a worker thread so it doesn't stall the server's event loop.
        success = await asyncio.to_thread(apply_telepathy_dsp, temp_raw, final_output)
        if not success:
            if temp_raw.exists():
                temp_raw.replace(final_output)
        else:
            if temp_raw.exists():
                try:
                    temp_raw.unlink()
                except OSError:
                    pass

    return final_output


async def text_to_speech_bilingual(
    ja_text: str,
    en_text: str,
    output_filename: str = "ciel_reply.mp3",
    apply_dsp: bool = True
) -> Path:
    """
    Synthesizes Ciel's dual-language report:
    1. Spoken Japanese voice first (Megumi Toyoguchi / Raphael anime tone)
    2. Subtle 350ms pause
    3. Spoken English voice second (AvaNeural calibrated)
    4. Tensura telepathic resonance filter applied across both via FFmpeg
    """
    ja_clean = (ja_text or "").strip()
    en_clean = (en_text or "").strip()

    final_output = AUDIO_DIR / output_filename

    # If only one language is present, fallback to single speech
    if ja_clean and not en_clean:
        return await text_to_speech(ja_clean, voice=CIEL_JA_VOICE, pitch=CIEL_JA_PITCH, rate=CIEL_JA_RATE, output_filename=output_filename, apply_dsp=apply_dsp)
    if en_clean and not ja_clean:
        return await text_to_speech(en_clean, voice=CIEL_EN_VOICE, pitch=CIEL_EN_PITCH, rate=CIEL_EN_RATE, output_filename=output_filename, apply_dsp=apply_dsp)
    if not ja_clean and not en_clean:
        ja_clean = "告。ご命令を。"
        en_clean = "Notice: All systems stand ready for your instruction."

    p_ja_temp = AUDIO_DIR / f"temp_ja_{output_filename}"
    p_en_temp = AUDIO_DIR / f"temp_en_{output_filename}"

    # Generate both neural audios concurrently
    comm_ja = edge_tts.Communicate(ja_clean, voice=CIEL_JA_VOICE, pitch=CIEL_JA_PITCH, rate=CIEL_JA_RATE)
    comm_en = edge_tts.Communicate(en_clean, voice=CIEL_EN_VOICE, pitch=CIEL_EN_PITCH, rate=CIEL_EN_RATE)
    await asyncio.gather(
        comm_ja.save(str(p_ja_temp)),
        comm_en.save(str(p_en_temp))
    )

    try:
        # Concatenate JA -> 350ms pad -> EN with Thought Acceleration telepathic filter
        if apply_dsp:
            filter_graph = (
                "[0:a]apad=pad_dur=0.35[a0];"
                "[a0][1:a]concat=n=2:v=0:a=1,"
                "highpass=f=120,equalizer=f=3500:t=q:w=1.2:g=3.0,aecho=0.85:0.8:25|45:0.18|0.09,volume=0.30[out]"
            )
        else:
            filter_graph = "[0:a]apad=pad_dur=0.35[a0];[a0][1:a]concat=n=2:v=0:a=1,volume=0.30[out]"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(p_ja_temp),
            "-i", str(p_en_temp),
            "-filter_complex", filter_graph,
            "-map", "[out]",
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(final_output)
        ]
        await asyncio.to_thread(
            subprocess.run, cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as _exc:
        print(f"⚠️ [voice_engine.text_to_speech_bilingual] suppressed {type(_exc).__name__}: {_exc}", flush=True)
        # Fallback: if ffmpeg fails, use Japanese audio
        if p_ja_temp.exists():
            p_ja_temp.replace(final_output)
    finally:
        for p in (p_ja_temp, p_en_temp):
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    return final_output


CLIP_VOICES = {
    "ja": (CIEL_JA_VOICE, CIEL_JA_PITCH, CIEL_JA_RATE),
    "en": (CIEL_EN_VOICE, CIEL_EN_PITCH, CIEL_EN_RATE),
}
JA_EN_GAP_MS = 350  # the same pause text_to_speech_bilingual puts between the languages


async def speak_clip(text: str, lang: str, output_filename: str, gap_before: bool = False) -> Path:
    """
    One streamed sentence: Edge TTS in Ciel's voice for `lang`, then the same
    Thought Acceleration filter as the full reply. gap_before adds the JA->EN pause.
    Raises if Edge TTS fails.
    """
    voice, pitch, rate = CLIP_VOICES[lang]
    final_output = AUDIO_DIR / output_filename
    temp_raw = AUDIO_DIR / f"raw_{output_filename}"
    await edge_tts.Communicate(text, voice=voice, pitch=pitch, rate=rate).save(str(temp_raw))
    if await asyncio.to_thread(apply_telepathy_dsp, temp_raw, final_output,
                                 JA_EN_GAP_MS if gap_before else 0):
        temp_raw.unlink(missing_ok=True)
    else:
        temp_raw.replace(final_output)  # unfiltered beats silent
    return final_output


async def concat_clips(clip_filenames: list, output_filename: str) -> Path:
    """Joins already-filtered clips into one mp3 (stream copy, no re-encode) for replay and REST clients."""
    final_output = AUDIO_DIR / output_filename
    listing = AUDIO_DIR / f"concat_{output_filename}.txt"
    # Bare names: the concat demuxer resolves them next to the list file.
    listing.write_text("".join(f"file '{name}'\n" for name in clip_filenames), encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
           "-c", "copy", str(final_output)]
    try:
        await asyncio.to_thread(subprocess.run, cmd, check=True, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg could not join the audio clips: {e.stderr.decode(errors='replace')[-300:]}") from e
    finally:
        listing.unlink(missing_ok=True)
    return final_output


def _transcribe_groq(audio_bytes: bytes, filename: str = "input.wav", prompt: str = None) -> str:
    """Transcription via Groq Whisper Large v3 Turbo with domain vocabulary prompting."""
    groq_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not groq_key:
        raise ValueError("GROQ_API_KEY is not configured in .env")

    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = io.BytesIO()

    # Model parameter
    body.write(f"--{boundary}\r\n".encode("utf-8"))
    body.write(b'Content-Disposition: form-data; name="model"\r\n\r\n')
    body.write(b"whisper-large-v3-turbo\r\n")

    # Prompt parameter (Domain vocabulary guidance)
    context_prompt = prompt or DOMAIN_WHISPER_PROMPT
    if context_prompt:
        body.write(f"--{boundary}\r\n".encode("utf-8"))
        body.write(b'Content-Disposition: form-data; name="prompt"\r\n\r\n')
        body.write(context_prompt.encode("utf-8"))
        body.write(b"\r\n")

    # Audio file parameter
    body.write(f"--{boundary}\r\n".encode("utf-8"))
    body.write(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"))
    body.write(b"Content-Type: audio/wav\r\n\r\n")
    body.write(audio_bytes)
    body.write(b"\r\n")

    # Response format parameter
    body.write(f"--{boundary}\r\n".encode("utf-8"))
    body.write(b'Content-Disposition: form-data; name="response_format"\r\n\r\n')
    body.write(b"json\r\n")

    body.write(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        data=body.getvalue(),
        headers={
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "CielVoiceEngine/1.0"
        },
        method="POST"
    )

    import json
    with urllib.request.urlopen(req, timeout=12) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res.get("text", "").strip()


def _transcribe_openai(audio_bytes: bytes, filename: str = "input.wav", prompt: str = None) -> str:
    """Secondary cloud fallback via OpenAI Whisper-1."""
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not openai_key:
        raise ValueError("OPENAI_API_KEY is not configured in .env")

    boundary = "----WebKitFormBoundaryOpenAITranscribe"
    body = io.BytesIO()

    # Model parameter
    body.write(f"--{boundary}\r\n".encode("utf-8"))
    body.write(b'Content-Disposition: form-data; name="model"\r\n\r\n')
    body.write(b"whisper-1\r\n")

    # Prompt parameter
    context_prompt = prompt or DOMAIN_WHISPER_PROMPT
    if context_prompt:
        body.write(f"--{boundary}\r\n".encode("utf-8"))
        body.write(b'Content-Disposition: form-data; name="prompt"\r\n\r\n')
        body.write(context_prompt.encode("utf-8"))
        body.write(b"\r\n")

    # Audio file parameter
    body.write(f"--{boundary}\r\n".encode("utf-8"))
    body.write(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"))
    body.write(b"Content-Type: audio/wav\r\n\r\n")
    body.write(audio_bytes)
    body.write(b"\r\n")

    # Response format parameter
    body.write(f"--{boundary}\r\n".encode("utf-8"))
    body.write(b'Content-Disposition: form-data; name="response_format"\r\n\r\n')
    body.write(b"json\r\n")

    body.write(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body.getvalue(),
        headers={
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "CielVoiceEngine/1.0"
        },
        method="POST"
    )

    import json
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res.get("text", "").strip()


def transcribe_audio(audio_bytes: bytes, filename: str = "input.wav", prompt: str = None) -> dict:
    """
    Main transcription pipeline:
    1. Converts the audio with FFmpeg to 16 kHz mono WAV (fixed gain, 80 Hz-7.5 kHz band-pass; no loudness normalisation).
    2. Primary tier: Groq Whisper Large v3 Turbo (cloud LPU transcription).
    3. Fallback tier: OpenAI Whisper-1 (enterprise reliability fallback).
    """
    import time
    t0 = time.time()

    # FFmpeg conversion to 16 kHz mono
    normalized_bytes = normalize_audio_for_transcription(audio_bytes)
    norm_filename = "normalized.wav"

    # Tier 1: Groq Whisper Large v3 Turbo
    groq_error = None
    try:
        text = _transcribe_groq(normalized_bytes, filename=norm_filename, prompt=prompt)
        dt = round((time.time() - t0) * 1000, 1)
        return {"text": text, "engine": "groq-whisper-turbo", "elapsed_ms": dt}
    except Exception as e:
        groq_error = str(e)
        print(f"⚠️ [VoiceEngine] Groq Whisper failed ({groq_error}), switching to OpenAI Whisper fallback...", flush=True)

    # Tier 2: OpenAI Whisper Fallback
    try:
        text = _transcribe_openai(normalized_bytes, filename=norm_filename, prompt=prompt)
        dt = round((time.time() - t0) * 1000, 1)
        return {"text": text, "engine": "openai-whisper-1", "elapsed_ms": dt}
    except Exception as e:
        openai_error = str(e)
        print(f"⚠️ [VoiceEngine] OpenAI Whisper failed: {openai_error}", flush=True)
        raise RuntimeError(f"All transcription engines failed. Groq: {groq_error} | OpenAI: {openai_error}")


def transcribe_audio_groq(audio_bytes: bytes, filename: str = "input.wav") -> str:
    """Backwards-compatible wrapper returning pure text string."""
    res = transcribe_audio(audio_bytes, filename=filename)
    return res.get("text", "")


if __name__ == "__main__":
    async def demo():
        print("Testing Ciel Voice Engine...")
        p = await text_to_speech("Notice: Ciel voice engine is operational.")
        print(f"Generated speech at: {p}")

    asyncio.run(demo())
