"""Local speech-to-text service for Ground Station (OpenAI-compatible subset).

Serves POST /v1/audio/transcriptions the way the app's Parakeet worker calls
it (multipart file=WAV, response_format=verbose_json, word timestamps) using
faster-whisper on CPU. Point the app at it with parakeet_url
(data/configs/app_config.json, default http://127.0.0.1:5092).

Run:  /opt/ground-station/stt/venv/bin/python backend/tools/sttserver.py
Env:  STT_MODEL (default small), STT_PORT (5092), STT_MODEL_DIR, STT_THREADS
"""

from __future__ import annotations

import io
import os
import threading
import time
import wave

import numpy as np

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from faster_whisper import WhisperModel

MODEL_NAME = os.environ.get("STT_MODEL", "small")
MODEL_DIR = os.environ.get("STT_MODEL_DIR", "/opt/ground-station/stt/models")
PORT = int(os.environ.get("STT_PORT", "5092"))
THREADS = int(os.environ.get("STT_THREADS", str(max(1, (os.cpu_count() or 2) // 2))))

app = FastAPI(title="Ground Station STT")
_model: WhisperModel | None = None
_model_lock = threading.Lock()
_infer_lock = threading.Lock()  # one inference at a time on CPU


def model() -> WhisperModel:
    global _model
    with _model_lock:
        if _model is None:
            _model = WhisperModel(
                MODEL_NAME,
                device="cpu",
                compute_type="int8",
                cpu_threads=THREADS,
                download_root=MODEL_DIR,
            )
    return _model


WHISPER_RATE = 16_000


def load_audio(data: bytes) -> np.ndarray:
    """Decode PCM WAV (what the app sends) to 16 kHz mono float32.

    Avoids PyAV for the common case: its API drifts between releases and
    faster-whisper's call into it breaks (seen: metadata_errors TypeError).
    Non-WAV input still goes through faster-whisper's decoder.
    """
    try:
        with wave.open(io.BytesIO(data)) as w:
            rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except wave.Error:
        from faster_whisper.audio import decode_audio

        return decode_audio(io.BytesIO(data), sampling_rate=WHISPER_RATE)
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    pcm = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        pcm = (pcm - 128.0) / 128.0
    else:
        pcm /= float(np.iinfo(dtype).max)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    if rate != WHISPER_RATE:
        from scipy.signal import resample_poly

        g = np.gcd(rate, WHISPER_RATE)
        pcm = resample_poly(pcm, WHISPER_RATE // g, rate // g).astype(np.float32)
    return pcm


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "loaded": _model is not None}


@app.post("/v1/audio/transcriptions")
def transcribe(
    file: UploadFile = File(...),
    model_name: str = Form("whisper", alias="model"),
    language: str | None = Form(None),
    response_format: str = Form("json"),
):
    audio = load_audio(file.file.read())
    started = time.monotonic()
    with _infer_lock:
        segments, info = model().transcribe(
            audio,
            language=None if language in (None, "", "auto") else language,
            vad_filter=True,  # skip squelch noise / silence between overs
            word_timestamps=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        segs = list(segments)
    text = " ".join(s.text.strip() for s in segs).strip()
    if response_format != "verbose_json":
        return {"text": text}
    return {
        "text": text,
        "language": info.language,
        "duration": info.duration,
        "processing_seconds": round(time.monotonic() - started, 2),
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text.strip(), "avg_logprob": s.avg_logprob,
             "no_speech_prob": s.no_speech_prob}
            for s in segs
        ],
        "words": [
            {"word": w.word.strip(), "start": w.start, "end": w.end, "probability": w.probability}
            for s in segs
            for w in (s.words or [])
        ],
    }


if __name__ == "__main__":
    model()  # load before accepting requests
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
