from __future__ import annotations

import json
import logging

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.llm.base import BaseLLMProvider, LLMError, RuleVerdict
from app.rules import Rule

logger = logging.getLogger(__name__)

# Appended to the prompt on the retry attempt to nudge the model toward
# producing a valid JSON object.
_JSON_RETRY_SUFFIX = (
    "\n\nIMPORTANT: Your previous response could not be parsed as JSON. "
    "Respond ONLY with a valid JSON object in this exact format, with no "
    'markdown fences, no extra text, and no trailing characters: '
    '{"adheres": true, "reason": "your explanation here"}'
)


class OllamaProvider(BaseLLMProvider):
    """LLM provider backed by a local Ollama server.

    Uses the ``POST /api/generate`` endpoint with ``format="json"`` to request
    structured output. If the model response cannot be parsed into a
    RuleVerdict, one retry is performed with an explicit JSON reminder appended
    to the prompt. A second failure raises LLMError.
    """

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.llm_base_url.rstrip("/")
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._timeout = settings.llm_timeout

    async def evaluate_rule(self, code: str, rule: Rule) -> RuleVerdict:
        prompt = rule.prompt_template.replace("{code}", code)

        verdict = await self._call(prompt)
        if verdict is not None:
            return verdict

        logger.warning(
            "Rule '%s': JSON parse failed on first attempt, retrying with hint.",
            rule.id,
        )
        verdict = await self._call(prompt + _JSON_RETRY_SUFFIX)
        if verdict is not None:
            return verdict

        raise LLMError(
            f"Rule '{rule.id}': Ollama returned an unparseable response after "
            "1 retry. Ensure the model supports JSON output."
        )

    async def _call(self, prompt: str) -> RuleVerdict | None:
        """Send one request to Ollama and return a parsed RuleVerdict or None."""
        payload = {
            "model": self._model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {
                "temperature": self._temperature,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama request timed out after {self._timeout}s.") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMError(f"Could not reach Ollama at '{self._base_url}': {exc}") from exc

        try:
            body = response.json()
        except Exception as exc:
            raise LLMError(
                f"Ollama response body is not valid JSON: {exc!r}"
            ) from exc

        raw_text = body.get("response")
        if not isinstance(raw_text, str):
            raise LLMError(
                f"Ollama response is missing the 'response' field or it is not a string. "
                f"Got: {raw_text!r}"
            )

        try:
            data = json.loads(raw_text)
            return RuleVerdict.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.debug("Could not parse Ollama response as RuleVerdict: %s | raw: %r", exc, raw_text)
            return None
