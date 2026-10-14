# Copyright (c) 2026 Efstratios Goudelis

import asyncio
import io
import logging
import queue
import wave
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import numpy as np
import pytest
from aiohttp import web

from audio import translation as translationmodule
from audio.parakeettranscriptionworker import ParakeetTranscriptionWorker
from common import appsettings
from pipeline.managers import transcriptionmanager as managermodule
from vfos.state import VFOManager


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
async def test_replacement_workers_emit_distinct_status_ids():
    loop = asyncio.get_running_loop()
    old_worker = make_worker(loop)
    new_worker = make_worker(loop)
    old_worker.sio = MagicMock(emit=AsyncMock())
    new_worker.sio = MagicMock(emit=AsyncMock())

    old_worker._send_status_to_ui("closed")
    new_worker._send_status_to_ui("starting")
    await asyncio.sleep(0.01)

    old_status = old_worker.sio.emit.await_args.args[1]
    new_status = new_worker.sio.emit.await_args.args[1]
    assert old_status["decoder_id"] != new_status["decoder_id"]
    assert old_status["worker_started_at"] <= new_status["worker_started_at"]
    assert new_status["status"] == "starting"


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
            text="hello", language="auto", is_final=True, detected_language=None
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
def test_vfo_translation_can_be_disabled_and_legacy_target_keeps_google():
    manager = VFOManager()
    session_id = "test-vfo-optional-translation"
    try:
        manager.update_vfo_state(
            session_id, 1, transcription_provider="parakeet", transcription_translate_to="el"
        )
        state = manager.get_vfo_state(session_id, 1)
        assert state.transcription_translation_backend == "google"
        assert state.transcription_translate_to == "el"

        manager.update_vfo_state(session_id, 1, transcription_translation_backend="none")
        assert state.transcription_translation_backend == "none"
        assert state.transcription_translate_to == "none"
    finally:
        manager.clear_session_vfos(session_id)


@pytest.mark.unit
def test_manager_switches_translation_backend_and_restarts_worker(monkeypatch):
    monkeypatch.setattr(managermodule.arguments, "parakeet_url", "https://parakeet.example.org")
    monkeypatch.setattr(managermodule.arguments, "libretranslate_url", "")

    class Broadcaster:
        def subscribe(self, _key, maxsize):
            return queue.Queue()

        def unsubscribe(self, _key):
            pass

    class Worker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

        def is_alive(self):
            return self.started

    monkeypatch.setattr(managermodule, "ParakeetTranscriptionWorker", Worker)
    processes = {"sdr": {"broadcasters": {"audio_session_vfo1": Broadcaster()}}}
    manager = managermodule.TranscriptionManager(processes, None, None)
    assert not manager.start_transcription(
        "sdr",
        "session",
        1,
        provider="parakeet",
        translate_to="el",
        translation_backend="libretranslate",
    )
    assert manager.start_transcription(
        "sdr",
        "session",
        1,
        provider="parakeet",
        translate_to="el",
    )
    first = processes["sdr"]["transcription_consumers"]["session"][1]["instance"]
    assert first.kwargs["translation_backend"] == "google"

    monkeypatch.setattr(
        managermodule.arguments, "libretranslate_url", "https://translate.example.org"
    )
    manager._last_start_ts.clear()
    assert manager.start_transcription(
        "sdr",
        "session",
        1,
        provider="parakeet",
        translate_to="el",
        translation_backend="libretranslate",
    )
    second = processes["sdr"]["transcription_consumers"]["session"][1]["instance"]
    assert second is not first
    assert not first.is_alive()
    assert second.kwargs["libretranslate_url"] == "https://translate.example.org"
    assert second.kwargs["translation_backend"] == "libretranslate"

    monkeypatch.setattr(managermodule.arguments, "libretranslate_url", "")
    assert not manager.start_transcription(
        "sdr",
        "session",
        1,
        provider="parakeet",
        translate_to="el",
        translation_backend="libretranslate",
    )
    assert not second.is_alive()

    manager._last_start_ts.clear()
    assert manager.start_transcription(
        "sdr",
        "session",
        1,
        provider="parakeet",
        translate_to="el",  # A stale target must not enable translation.
        translation_backend="none",
    )
    third = processes["sdr"]["transcription_consumers"]["session"][1]
    assert third["translate_to"] == "none"
    assert third["translation_backend"] == "none"
    assert third["instance"].kwargs["translate_to"] == "none"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parakeet_no_translation_skips_translation_request(monkeypatch):
    worker = make_worker(asyncio.get_running_loop())
    worker.translation_backend = "none"
    worker.translate_to = "el"  # Defensive guard for old or inconsistent settings.
    worker._session = MagicMock()
    response = worker._session.post.return_value.__aenter__.return_value
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "text": "hello",
        "words": [{"word": "hello", "end": 0.5}],
    }
    worker._emit_transcription = AsyncMock()
    translate = AsyncMock()
    monkeypatch.setattr("audio.parakeettranscriptionworker.translate_text", translate)

    await worker._send_audio_to_provider((b"wav", 0, 44100))

    translate.assert_not_awaited()
    worker._emit_transcription.assert_awaited_once_with(
        text="hello", language="auto", is_final=True, detected_language=None
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parakeet_detects_original_language_before_translation(monkeypatch):
    original = "Это длинное русское предложение о погоде и работе радиостанции сегодня."
    worker = make_worker(asyncio.get_running_loop())
    worker.translate_to = "en"
    worker._session = MagicMock()
    response = worker._session.post.return_value.__aenter__.return_value
    response.raise_for_status = MagicMock()
    response.json.return_value = {"text": original, "language": "en"}
    worker._emit_transcription = AsyncMock()
    translate = AsyncMock(return_value="This is an English translation.")
    monkeypatch.setattr("audio.parakeettranscriptionworker.translate_text", translate)

    await worker._send_audio_to_provider((b"wav", 0, 44100))

    assert translate.await_args.args[0] == original
    worker._emit_transcription.assert_awaited_once_with(
        text="This is an English translation.",
        language="auto",
        is_final=True,
        detected_language="ru",
    )
    assert worker._detect_source_language("да") == "ru"


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
