"""Клиент языковой модели через OpenAI-совместимый API.

Подходит любой провайдер с таким API: Groq (по умолчанию, бесплатно),
OpenRouter, локальный Ollama или LM Studio. Меняется тремя строками в .env:
LLM_BASE_URL, LLM_API_KEY, LLM_MODEL.
"""

import logging
import re
from functools import lru_cache
from time import sleep

from django.conf import settings
from openai import APIConnectionError, APIStatusError, OpenAI

logger = logging.getLogger(__name__)

RETRYABLE_CODES = {429, 500, 502, 503, 504}
KEY_ERROR_CODES = {401, 403}
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(Exception):
    """Сбой модели. Текст ошибки безопасно показывать пользователю."""


def is_configured() -> bool:
    return bool(settings.LLM_API_KEY and settings.LLM_MODEL)


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    if not is_configured():
        raise LLMError("ИИ не подключён: впишите LLM_API_KEY в файл .env и перезапустите сервер.")
    return OpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        max_retries=0,
    )


def model_chain() -> list[str]:
    return list(dict.fromkeys(m for m in [settings.LLM_MODEL, *settings.LLM_FALLBACK_MODELS] if m))


def clean_text(text: str | None) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _error_text(code) -> str:
    if code in KEY_ERROR_CODES:
        return "Ключ ИИ не подошёл. Проверьте LLM_API_KEY в файле .env."
    if code == "network":
        return "Не удалось связаться с ИИ. Проверьте интернет."
    if code == 429:
        return "Лимит бесплатных запросов к ИИ исчерпан. Подождите минуту."
    return "ИИ сейчас не отвечает. Попробуйте через минуту."


def complete(messages: list[dict], *, tools: list[dict] | None = None, json_mode: bool = False,
             max_tokens: int = 1024):
    """Один ответ модели (объект message из OpenAI SDK).

    Перегрузку (5xx) и лимит (429) переживает повтором с паузой, затем
    переходит к следующей модели из LLM_FALLBACK_MODELS.
    """
    client = get_client()
    last_code = None
    for model in model_chain():
        for attempt in range(settings.LLM_RETRIES + 1):
            kwargs = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
            if tools:
                kwargs["tools"] = tools
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                return client.chat.completions.create(**kwargs).choices[0].message
            except APIStatusError as exc:
                last_code = exc.status_code
                logger.warning("LLM %s, попытка %s: %s %s", model, attempt + 1, exc.status_code, exc.message)
                if exc.status_code in KEY_ERROR_CODES:
                    raise LLMError(_error_text(exc.status_code)) from exc
                if exc.status_code in RETRYABLE_CODES and attempt < settings.LLM_RETRIES:
                    sleep(settings.LLM_RETRY_DELAY * (attempt + 1))
                    continue
                break
            except APIConnectionError as exc:
                logger.warning("LLM недоступен: %s", exc)
                raise LLMError(_error_text("network")) from exc
    raise LLMError(_error_text(last_code))
