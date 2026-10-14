# Copyright (c) 2026 Efstratios Goudelis

"""Integration contract for the optional local text translation service."""

import asyncio
import logging
import queue
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from audio import translation
from audio.deepgramtranscriptionworker import DeepgramTranscriptionWorker
from audio.transcriptionworker import TranscriptionWorker
from common.arguments import arguments
from handlers.entities.appsettings import get_libretranslate_capabilities


@pytest.mark.unit
@pytest.mark.asyncio
async def test_libretranslate_contract_and_capabilities(monkeypatch):
    requests = []

    async def languages(_request):
        return web.json_response(
            [
                {"code": "en", "name": "English", "targets": ["el", "zh-Hans"]},
                {"code": "zh-Hans", "name": "Chinese (Simplified)", "targets": ["en"]},
            ]
        )

    async def translate_text(request):
        requests.append(await request.json())
        return web.json_response({"detectedLanguage": {"language": "en"}, "translatedText": "Γεια"})

    app = web.Application()
    app.router.add_get("/languages", languages)
    app.router.add_post("/translate", translate_text)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    service_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    monkeypatch.setattr(arguments, "libretranslate_url", service_url)
    try:
        capabilities = await get_libretranslate_capabilities(
            None, None, logging.getLogger(__name__), "sid"
        )
        assert capabilities["data"]["languages"][0]["targets"] == ["el", "zh-Hans"]
        assert (
            await translation.translate_text(
                "Hello",
                "auto",
                "el",
                None,
                logging.getLogger(__name__),
                translation_backend="libretranslate",
                libretranslate_url=service_url,
            )
            == "Γεια"
        )
        assert (
            await translation.translate_text(
                "Hello",
                "en",
                "zh",
                None,
                logging.getLogger(__name__),
                translation_backend="libretranslate",
                libretranslate_url=service_url,
            )
            == "Γεια"
        )
        assert requests == [
            {"q": "Hello", "source": "auto", "target": "el", "format": "text"},
            {"q": "Hello", "source": "en", "target": "zh-Hans", "format": "text"},
        ]
    finally:
        await runner.cleanup()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_libre_failure_keeps_original_and_does_not_call_google(monkeypatch):
    calls = []

    async def fail(_request):
        calls.append("libre")
        return web.json_response({"error": "unsupported pair"}, status=400)

    app = web.Application()
    app.router.add_post("/translate", fail)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    service_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    monkeypatch.setattr(translation, "GOOGLE_TRANSLATE_URL", "http://127.0.0.1:1/translate")
    try:
        assert (
            await translation.translate_text(
                "original",
                "en",
                "hr",
                "unused-google-key",
                logging.getLogger(__name__),
                translation_backend="libretranslate",
                libretranslate_url=service_url,
            )
            is None
        )
        assert calls == ["libre"]
        assert (
            await translation.translate_text(
                "original",
                "auto",
                "el",
                "unused-google-key",
                logging.getLogger(__name__),
                translation_backend="libretranslate",
                libretranslate_url="",
            )
            is None
        )

        worker = DeepgramTranscriptionWorker(
            transcription_queue=queue.Queue(),
            sio=None,
            loop=asyncio.get_running_loop(),
            api_key="deepgram-key",
            session_id="session",
            vfo_number=1,
            translate_to="hr",
            translation_backend="libretranslate",
            libretranslate_url=service_url,
        )
        emit = AsyncMock()
        report = AsyncMock()
        monkeypatch.setattr(TranscriptionWorker, "_emit_transcription", emit)
        monkeypatch.setattr(worker, "_report_translation_failure", report)
        await worker._emit_transcription("original", "en", True)
        emit.assert_awaited_once_with("original", "en", True, None)
        report.assert_awaited_once()
    finally:
        await runner.cleanup()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capabilities_without_url_are_unconfigured(monkeypatch):
    monkeypatch.setattr(arguments, "libretranslate_url", "")
    response = await get_libretranslate_capabilities(None, None, logging.getLogger(__name__), "sid")
    assert response == {"success": True, "data": {"configured": False, "languages": []}}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deepgram_no_translation_skips_translation_request(monkeypatch):
    worker = DeepgramTranscriptionWorker(
        transcription_queue=queue.Queue(),
        sio=None,
        loop=asyncio.get_running_loop(),
        api_key="deepgram-key",
        session_id="session",
        vfo_number=1,
        translate_to="el",  # A stale target must not enable translation.
        translation_backend="none",
    )
    translate = AsyncMock()
    emit = AsyncMock()
    monkeypatch.setattr(worker, "_translate_text", translate)
    monkeypatch.setattr(TranscriptionWorker, "_emit_transcription", emit)

    await worker._emit_transcription("original", "en", True)

    translate.assert_not_awaited()
    emit.assert_awaited_once_with("original", "en", True, None)
