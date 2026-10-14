# Copyright (c) 2026 Efstratios Goudelis

"""Shared transcript translation for providers without built-in translation."""

import asyncio
import logging
from typing import Optional

import aiohttp

GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"


def libre_language_code(language: str) -> str:
    """Map the legacy Chinese selection to LibreTranslate's explicit script code."""
    return "zh-Hans" if language == "zh" else language


def google_language_code(language: str) -> str:
    """Translate LibreTranslate's script-specific codes to Google equivalents."""
    return {"zh-Hans": "zh-CN", "zh-Hant": "zh-TW"}.get(language, language)


async def get_libretranslate_languages(service_url: str) -> list[dict]:
    """Fetch source languages and supported targets for UI capability checks."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{service_url.strip().rstrip('/')}/languages",
            timeout=aiohttp.ClientTimeout(total=5.0),
        ) as response:
            response.raise_for_status()
            result = await response.json()
            if not isinstance(result, list):
                raise ValueError("LibreTranslate returned an invalid language list")
            return [
                {"code": item["code"], "name": item["name"], "targets": item["targets"]}
                for item in result
                if isinstance(item, dict)
                and isinstance(item.get("code"), str)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("targets"), list)
            ]


async def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    api_key: Optional[str],
    logger: logging.Logger,
    translation_backend: str = "google",
    libretranslate_url: str = "",
) -> Optional[str]:
    if translation_backend == "none" or target_lang == "none":
        return text

    if translation_backend == "libretranslate":
        if not libretranslate_url.strip():
            logger.error("LibreTranslate URL is not configured; keeping original transcript")
            return None
        payload = {
            "q": text,
            "source": libre_language_code(source_lang) if source_lang else "auto",
            "target": libre_language_code(target_lang),
            "format": "text",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{libretranslate_url.strip().rstrip('/')}/translate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10.0),
                ) as response:
                    if response.status != 200:
                        logger.error("LibreTranslate API error (status %s)", response.status)
                        return None
                    result = await response.json()
                    translated = result.get("translatedText")
                    if isinstance(translated, str) and translated:
                        return translated
                    logger.error("LibreTranslate returned no translated text")
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as exc:
            logger.error("LibreTranslate error: %s", exc)
        return None

    if translation_backend != "google":
        logger.error("Unknown translation backend: %s", translation_backend)
        return None

    if not api_key:
        logger.warning("Google Translate API key not configured, skipping translation")
        return None

    payload = {"q": text, "target": google_language_code(target_lang), "format": "text"}
    if source_lang and source_lang != "auto":
        payload["source"] = google_language_code(source_lang)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GOOGLE_TRANSLATE_URL,
                params={"key": api_key},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=5.0),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return str(result["data"]["translations"][0]["translatedText"])
                logger.error("Google Translate API error (status %s)", response.status)
                return None
    except asyncio.TimeoutError:
        logger.warning("Google Translate API timeout, skipping translation")
    except Exception as exc:
        logger.error("Translation error: %s", exc, exc_info=True)
    return None
