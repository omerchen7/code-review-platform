from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.rules import Rule


class RuleVerdict(BaseModel):
    """The structured result of evaluating a single rule against a piece of code."""

    adheres: bool
    reason: str | None = None


class BaseLLMProvider(ABC):
    """Abstract interface for an LLM backend.

    The rest of the system depends only on this interface. Swapping providers
    (e.g. Ollama -> LM Studio) requires only a new subclass and a one-line
    change in factory.py.
    """

    @abstractmethod
    async def evaluate_rule(self, code: str, rule: Rule) -> RuleVerdict:
        """Evaluate whether the given code adheres to the rule.

        Args:
            code: The full source code to review.
            rule: The rule to evaluate, including its prompt_template.

        Returns:
            A RuleVerdict with ``adheres`` set to True or False and an
            optional ``reason`` string explaining the verdict.

        Raises:
            LLMError: if the provider is unreachable or returns an
                unparseable response after the allowed number of retries.
        """


class LLMError(Exception):
    """Raised when the LLM provider fails or returns an unusable response."""
