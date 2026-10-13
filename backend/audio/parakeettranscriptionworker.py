# Copyright (c) 2026 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Clip-based transcription through the configured Parakeet service."""

import asyncio
import io
import logging
import threading
import wave
from typing import Any, Dict, Optional

import aiohttp
import numpy as np

from audio.transcriptionworker import TranscriptionWorker
from audio.translation import translate_text

logger = logging.getLogger("transcription.parakeet")

_INFERENCE_LOCK = threading.Lock()


class ParakeetTranscriptionWorker(TranscriptionWorker):
    """Group speech into WAV clips and submit them to the single-model HTTP service."""

    def __init__(
        self,
        transcription_queue,
        sio,
        loop,
        session_id: str,
        vfo_number: int,
        service_url: str,
        language: str = "auto",
        translate_to: str = "none",
        google_translate_api_key: Optional[str] = None,
        satellite: Optional[Dict[str, Any]] = None,
        transmitter: Optional[Dict[str, Any]] = None,
        output_dir: Optional[str] = None,
        translation_backend: str = "google",
        libretranslate_url: str = "",
    ):
        super().__init__(
            transcription_queue=transcription_queue,
            sio=sio,
            loop=loop,
            api_key="",
            session_id=session_id,
            vfo_number=vfo_number,
            language=language,
            translate_to=translate_to,
            provider_name="parakeet",
            satellite=satellite,
            transmitter=transmitter,
            output_dir=output_dir,
            translation_backend=translation_backend,
        )
        self.requires_api_key = False
        self.uses_receiver = False
        self.chunk_duration = 1.0
        self.connection_backoff_seconds = 5
        self.google_translate_api_key = google_translate_api_key
        self.libretranslate_url = libretranslate_url
        self.service_url = service_url.strip().rstrip("/")
        if not self.service_url:
            raise ValueError("Parakeet service URL is not configured")

        # A pause finishes a clip; continuous speech is cut with one second of
        # overlap so words at a boundary can be recovered by the next request.
        self.max_clip_samples = 10 * self.input_sample_rate
        self.overlap_samples = self.input_sample_rate
        self._speech_chunks: list[np.ndarray] = []
        self._speech_samples = 0
        self._sample_cursor = 0
        self._clip_start_sample = 0
        self._last_emitted_end = -1.0
        self._recent_words: list[str] = []

        self._session: Optional[aiohttp.ClientSession] = None
        self._request_queue: Optional[asyncio.Queue] = None
        self._drain_task: Optional[asyncio.Task] = None

    def _handle_audio_window(self, audio_data: np.ndarray, rms: float):
        """Build clips on the worker thread, including pause and overlap state."""
        start_sample = self._sample_cursor
        self._sample_cursor += len(audio_data)

        if rms < self.silence_threshold:
            self._submit_clip(final=True)
            return

        if not self._speech_chunks:
            self._clip_start_sample = start_sample
        self._speech_chunks.append(audio_data)
        self._speech_samples += len(audio_data)
        if self._speech_samples >= self.max_clip_samples:
            self._submit_clip(final=False)

    def _submit_clip(self, final: bool):
        if not self._speech_chunks or not self.provider_loop or not self.running:
            self._speech_chunks = []
            self._speech_samples = 0
            return

        audio_data = np.concatenate(self._speech_chunks)
        clip_end = self._clip_start_sample + len(audio_data)
        overlap = min(self.overlap_samples, len(audio_data)) if not final else 0
        stable_end = clip_end - overlap
        wav_bytes = self._prepare_audio_payload(audio_data)
        payload = (wav_bytes, self._clip_start_sample, stable_end)
        asyncio.run_coroutine_threadsafe(self._stream_audio(payload), self.provider_loop)

        if overlap:
            self._speech_chunks = [audio_data[-overlap:].copy()]
            self._speech_samples = overlap
            self._clip_start_sample = clip_end - overlap
        else:
            self._speech_chunks = []
            self._speech_samples = 0

    def _prepare_audio_payload(self, audio_data: np.ndarray) -> bytes:
        normalized = self._normalize_audio(audio_data, target_level=0.7)
        resampled = self._resample_audio(normalized, target_rate=16000)
        pcm = np.clip(resampled * 32767, -32768, 32767).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm.tobytes())
        return buffer.getvalue()

    async def _stream_audio(self, audio_payload: Any):
        """Keep at most two pending clips while the service processes another."""
        if not self.running:
            return
        if self._request_queue is None:
            self._request_queue = asyncio.Queue(maxsize=2)
        if self._request_queue.full():
            with self.stats_lock:
                self.stats["errors"] += 1
            logger.warning(
                "Parakeet request backlog full for VFO %s; dropping clip", self.vfo_number
            )
            return
        self._request_queue.put_nowait(audio_payload)
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_requests())

    async def _drain_requests(self):
        while self.running and self._request_queue and not self._request_queue.empty():
            payload = self._request_queue.get_nowait()
            try:
                await super()._stream_audio(payload)
            finally:
                self._request_queue.task_done()

    async def _connect(self):
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=5)
            self._session = aiohttp.ClientSession(timeout=timeout)
        async with self._session.get(f"{self.service_url}/health") as response:
            response.raise_for_status()
        self.connected = True
        self.last_connection_attempt = 0

    async def _disconnect(self):
        self.connected = False
        session, self._session = self._session, None
        if session and not session.closed:
            await session.close()

    async def _receive_loop(self):
        # HTTP responses are handled in _send_audio_to_provider().
        return

    async def _send_audio_to_provider(self, audio_payload: Any):
        wav_bytes, clip_start_sample, stable_end_sample = audio_payload
        if self._session is None:
            raise RuntimeError("Parakeet HTTP session is unavailable")

        # The deployed service has one inference context. Polling this shared
        # lock keeps cancellation safe across the separate VFO event loops.
        acquired = _INFERENCE_LOCK.acquire(blocking=False)
        while self.running and not acquired:
            await asyncio.sleep(0.05)
            acquired = _INFERENCE_LOCK.acquire(blocking=False)
        if not self.running:
            if acquired:
                _INFERENCE_LOCK.release()
            return
        try:
            form = aiohttp.FormData()
            form.add_field("file", wav_bytes, filename="audio.wav", content_type="audio/wav")
            form.add_field("model", "parakeet")
            form.add_field("response_format", "verbose_json")
            form.add_field("timestamp_granularities[]", "word")
            async with self._session.post(
                f"{self.service_url}/v1/audio/transcriptions", data=form
            ) as response:
                response.raise_for_status()
                result = await response.json()
        finally:
            _INFERENCE_LOCK.release()

        if not self.running:
            return
        text = self._extract_new_text(result, clip_start_sample, stable_end_sample)
        if not text:
            return
        if self.translate_to and self.translate_to != "none":
            translated = await translate_text(
                text,
                self.language,
                self.translate_to,
                self.google_translate_api_key,
                logger,
                translation_backend=self.translation_backend,
                libretranslate_url=self.libretranslate_url,
            )
            if translated:
                text = translated
            else:
                await self._report_translation_failure()
        if self.running:
            # The service's reported language is a fixed placeholder, even for
            # this multilingual model. Keep the UI from implying detection.
            await self._emit_transcription(text=text, language="auto", is_final=True)

    def _extract_new_text(
        self, result: dict, clip_start_sample: int, stable_end_sample: int
    ) -> str:
        """Emit words only once; the next overlapping clip owns boundary words."""
        words = result.get("words") or []
        clip_start_seconds = clip_start_sample / self.input_sample_rate
        stable_end_seconds = stable_end_sample / self.input_sample_rate
        if words:
            fresh = []
            for word in words:
                end = clip_start_seconds + float(word.get("end", 0))
                if self._last_emitted_end + 0.02 < end <= stable_end_seconds + 0.02:
                    token = str(word.get("word", "")).strip()
                    if token:
                        fresh.append(token)
                        self._last_emitted_end = end
            return " ".join(fresh)

        # Older compatible servers may omit word timings. Remove the matching
        # prefix from the last emitted suffix when clips overlap.
        tokens = str(result.get("text", "")).strip().split()
        previous = [token.casefold() for token in self._recent_words]
        current = [token.casefold() for token in tokens]
        overlap = 0
        for count in range(min(len(previous), len(current)), 0, -1):
            if previous[-count:] == current[:count]:
                overlap = count
                break
        self._recent_words = (self._recent_words + tokens[overlap:])[-30:]
        return " ".join(tokens[overlap:])

    async def _cleanup_provider_resources(self):
        # A stopped or replaced VFO must not emit a late HTTP result.
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
        self._drain_task = None
        self._request_queue = None
        await super()._cleanup_provider_resources()
