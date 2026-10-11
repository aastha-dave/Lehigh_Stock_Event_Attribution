from __future__ import annotations

from functools import lru_cache

from app.config.settings import get_settings

_PROVIDER_CONFIG = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_attr": "gemini_api_key",
        "default_model": "gemini-2.0-flash",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_attr": "groq_api_key",
        "default_model": "llama-3.1-8b-instant",
    },
    "openai": {
        "base_url": None,
        "key_attr": "openai_api_key",
        "default_model": "gpt-4.1-2025-04-14",
    },
}

@lru_cache(maxsize=1)
def get_default_model():
    settings = get_settings()
    if settings.ai_model:
        return settings.ai_model
    provider = settings.ai_provider or "gemini"
    config = _PROVIDER_CONFIG.get(provider, _PROVIDER_CONFIG["gemini"])
    return config["default_model"]


@lru_cache(maxsize=1)
def get_openai_client():
    from openai import OpenAI

    settings = get_settings()
    provider = settings.ai_provider or "gemini"
    config = _PROVIDER_CONFIG.get(provider, _PROVIDER_CONFIG["gemini"])

    api_key = getattr(settings, config["key_attr"])
    base_url = config["base_url"]

    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)

@lru_cache(maxsize=1)
def get_gemini_client():
    from openai import OpenAI

    settings = get_settings()
    return OpenAI(
        api_key=settings.gemini_api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )