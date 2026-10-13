# Copyright (c) 2026 Efstratios Goudelis

"""Shared transcript translation for providers without built-in translation."""

import asyncio
import logging
from typing import Optional

import aiohttp

GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"


async def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    api_key: Optional[str],
    logger: logging.Logger,
) -> Optional[str]:
    if not api_key:
        logger.warning("Google Translate API key not configured, skipping translation")
        return None

    payload = {"q": text, "target": target_lang, "format": "text"}
    if source_lang and source_lang != "auto":
        payload["source"] = source_lang

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
