"""LLM client abstraction (DECISIONS.md §7, §12).

The reasoning layer depends on this tiny Protocol, never on a specific provider.
``FakeLLM`` backs tests (scripted, no network); ``OpenRouterLLM`` (in
``openrouter.py``) is the real adapter. Keeping the surface minimal — one
``complete`` call — makes it cheap to cap calls and bound latency, which is the
main LLM cost lever.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, timeout: float = 30.0) -> str: ...


class FakeLLM:
    """Deterministic stand-in. Returns ``verdict`` for evaluator-style prompts
    (detected by a marker in the system text) and ``branch`` otherwise. Records
    every call so tests can assert the call count (the cost lever)."""

    def __init__(self, *, verdict: str = 'VETO', branch: str = 'a balanced thesis') -> None:
        self.verdict = verdict
        self.branch = branch
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *, timeout: float = 30.0) -> str:
        self.calls.append((system, user))
        if 'evaluator' in system.lower():
            return self.verdict
        return self.branch
