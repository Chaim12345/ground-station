# Copyright (c) 2026 Efstratios Goudelis

import asyncio
import io
import logging
import queue
import wave
from unittest.mock import AsyncMock

import aiohttp
import numpy as np
import pytest
from aiohttp import web

from audio import translation as translationmodule
from audio.parakeettranscriptionworker import ParakeetTranscriptionWorker
from common import appsettings
from pipeline.managers import transcriptionmanager as managermodule


def make_worker(loop, service_url="http://127.0.0.1:1"):
    return ParakeetTranscriptionWorker(
        transcription_queue=queue.Queue(),
        sio=None,
        loop=loop,
        session_id="test-session",
        vfo_number=1,
        service_url=service_url,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_speech_windows_make_wav_clips_with_boundary_overlap():
    worker = make_worker(asyncio.get_running_loop())
    worker.provider_loop = asyncio.get_running_loop()
    submitted = []

    async def collect(payload):
        submitted.append(payload)

    worker._stream_audio = collect
    speech = np.full(44100, 0.1, dtype=np.float32)
    for _ in range(10):
        worker._handle_audio_window(speech, 0.1)
    worker._handle_audio_window(np.zeros(44100, dtype=np.float32), 0)
    await asyncio.sleep(0.02)

    assert len(submitted) == 2
    assert [(start, stable) for _, start, stable in submitted] == [
        (0, 9 * 44100),
        (9 * 44100, 10 * 44100),
    ]
    with wave.open(io.BytesIO(submitted[0][0]), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.getnframes() == 10 * 16000


@pytest.mark.unit
def test_overlapping_word_timestamps_emit_each_word_once():
    worker = make_worker(None)
    first = {
        "words": [
            {"word": "hello", "end": 8.5},
            {"word": "world", "end": 9.5},
        ]
    }
    second = {
        "words": [
            {"word": "world", "end": 0.5},
            {"word": "again", "end": 1.2},
        ]
    }

    assert worker._extract_new_text(first, 0, 9 * 44100) == "hello"
    assert worker._extract_new_text(second, 9 * 44100, 11 * 44100) == "world again"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parakeet_http_contract_and_stop_discards_late_result(monkeypatch):
    request_seen = asyncio.Event()
    release_response = asyncio.Event()
    uploads = []

    async def health(_request):
        return web.json_response({"status": "ok"})

    async def transcribe(request):
        form = await request.post()
        upload = form["file"]
        uploads.append(
            (upload.filename, upload.content_type, upload.file.read(), form["response_format"])
        )
        request_seen.set()
        await release_response.wait()
        return web.json_response({"text": "hello", "words": [{"word": "hello", "end": 0.5}]})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/v1/audio/transcriptions", transcribe)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    worker = make_worker(asyncio.get_running_loop(), f"http://127.0.0.1:{port}/")
    assert worker.service_url == f"http://127.0.0.1:{port}"
    worker._emit_transcription = AsyncMock()
    payload = worker._prepare_audio_payload(np.full(44100, 0.1, dtype=np.float32))
    try:
        await worker._connect()
        request = asyncio.create_task(worker._send_audio_to_provider((payload, 0, 44100)))
        await asyncio.wait_for(request_seen.wait(), timeout=3)
        assert uploads[0][:2] == ("audio.wav", "audio/wav")
        assert uploads[0][3] == "verbose_json"
        release_response.set()
        await asyncio.wait_for(request, timeout=3)
        worker._emit_transcription.assert_awaited_once_with(
            text="hello", language="auto", is_final=True
        )

        worker._emit_transcription.reset_mock()
        request_seen.clear()
        release_response.clear()
        request = asyncio.create_task(worker._send_audio_to_provider((payload, 44100, 2 * 44100)))
        await asyncio.wait_for(request_seen.wait(), timeout=3)
        worker.stop()
        release_response.set()
        await asyncio.wait_for(request, timeout=3)
        worker._emit_transcription.assert_not_awaited()
    finally:
        release_response.set()
        await worker._disconnect()
        await runner.cleanup()


@pytest.mark.unit
def test_manager_starts_keyless_parakeet_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        managermodule.arguments,
        "parakeet_url",
        appsettings.DEFAULT_APP_CONFIG["parakeet_url"],
    )
    settings_service = appsettings.AppSettingsService()
    settings_service.config_path = tmp_path / "app_config.json"
    assert settings_service.update({"parakeet_url": "https://example.test/parakeet"})["success"]

    class Broadcaster:
        def subscribe(self, _key, maxsize):
            assert maxsize > 0
            return queue.Queue()

        def unsubscribe(self, _key):
            return None

    class Worker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

    monkeypatch.setattr(managermodule, "ParakeetTranscriptionWorker", Worker)
    broadcaster = Broadcaster()
    processes = {"sdr": {"broadcasters": {"audio_session_vfo1": broadcaster}}}
    manager = managermodule.TranscriptionManager(processes, None, None)
    manager.set_google_translate_api_key("translation-key")

    assert manager.start_transcription("sdr", "session", 1, provider="parakeet")
    worker = processes["sdr"]["transcription_consumers"]["session"][1]["instance"]
    assert worker.started
    assert worker.kwargs["google_translate_api_key"] == "translation-key"
    assert worker.kwargs["service_url"] == "https://example.test/parakeet"


@pytest.mark.unit
def test_manager_rejects_parakeet_without_configured_url(monkeypatch):
    monkeypatch.setattr(managermodule.arguments, "parakeet_url", "")
    manager = managermodule.TranscriptionManager({"sdr": {}}, None, None)

    assert not manager.start_transcription("sdr", "session", 1, provider="parakeet")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shared_translation_auto_detects_source(monkeypatch):
    received = []

    async def translate(request):
        received.append(await request.json())
        return web.json_response({"data": {"translations": [{"translatedText": "Γεια"}]}})

    app = web.Application()
    app.router.add_post("/translate", translate)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        translationmodule, "GOOGLE_TRANSLATE_URL", f"http://127.0.0.1:{port}/translate"
    )
    try:
        result = await translationmodule.translate_text(
            "hello", "auto", "el", "test-key", logging.getLogger("test")
        )
        assert result == "Γεια"
        assert received == [{"q": "hello", "target": "el", "format": "text"}]
    finally:
        await runner.cleanup()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_multiple_vfos_submit_one_inference_at_a_time(monkeypatch):
    active = 0
    highest_active = 0

    async def transcribe(_request):
        nonlocal active, highest_active
        active += 1
        highest_active = max(highest_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return web.json_response({"text": "hello", "words": [{"word": "hello", "end": 0.5}]})

    app = web.Application()
    app.router.add_post("/v1/audio/transcriptions", transcribe)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    workers = [
        make_worker(asyncio.get_running_loop(), f"http://127.0.0.1:{port}") for _ in range(2)
    ]
    for worker in workers:
        worker._emit_transcription = AsyncMock()
        worker._session = aiohttp.ClientSession()
    payload = workers[0]._prepare_audio_payload(np.full(44100, 0.1, dtype=np.float32))
    try:
        await asyncio.gather(
            *(worker._send_audio_to_provider((payload, 0, 44100)) for worker in workers)
        )
        assert highest_active == 1
        assert all(worker._emit_transcription.await_count == 1 for worker in workers)
    finally:
        await asyncio.gather(*(worker._disconnect() for worker in workers))
        await runner.cleanup()
