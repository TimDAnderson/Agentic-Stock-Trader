"""OpenRouter LLM adapter (DECISIONS.md §7, §12).

OpenRouter is an OpenAI-compatible HTTP API, so this is a thin client. httpx is
imported lazily (optional ``reasoning`` extra). Use a cheap model for the
research branches and reserve a smarter model for the evaluator if desired —
the main cost lever is keeping calls few and context lean.
"""

from __future__ import annotations

import os

_DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1'


class OpenRouterError(RuntimeError):
    """OpenRouter is misconfigured or the request failed."""


class OpenRouterLLM:
    """LLMClient backed by OpenRouter's chat-completions endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        default_timeout: float = 30.0,
    ) -> None:
        key = api_key or os.environ.get('OPENROUTER_API_KEY')
        if not key:
            raise OpenRouterError(
                'Missing OpenRouter API key; set OPENROUTER_API_KEY or pass api_key.'
            )
        self._model = model
        self._key = key
        self._base_url = base_url.rstrip('/')
        self._default_timeout = default_timeout

    def complete(self, system: str, user: str, *, timeout: float = 30.0) -> str:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - only without the extra
            raise OpenRouterError(
                'httpx is not installed. Install the reasoning extra: `uv sync --extra reasoning`.'
            ) from exc

        response = httpx.post(
            f'{self._base_url}/chat/completions',
            headers={'Authorization': f'Bearer {self._key}', 'Content-Type': 'application/json'},
            json={
                'model': self._model,
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user},
                ],
            },
            timeout=timeout or self._default_timeout,
        )
        response.raise_for_status()
        data = response.json()
        try:
            return str(data['choices'][0]['message']['content'])
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError(f'Unexpected OpenRouter response shape: {data!r}') from exc
