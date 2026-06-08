from __future__ import annotations

from app.config import Settings
from app.llm.base import BaseLLMProvider
from app.llm.ollama_provider import OllamaProvider


def build_provider(settings: Settings) -> BaseLLMProvider:
    """Instantiate and return the configured LLM provider.

    The provider is selected by ``settings.llm_provider``. Adding a new
    backend (e.g. LM Studio) requires only a new ``elif`` branch here and a
    corresponding provider class — nothing else in the system needs to change.

    Raises:
        ValueError: if ``settings.llm_provider`` names an unsupported backend.
    """
    if settings.llm_provider == "ollama":
        return OllamaProvider(settings)

    raise ValueError(
        f"Unsupported LLM provider: '{settings.llm_provider}'. "
        "Supported values: 'ollama'."
    )
