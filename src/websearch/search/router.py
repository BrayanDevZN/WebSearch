"""Roteador de provedores: ordem de preferência, retentativa com backoff e disjuntor (circuit breaker)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Sequence

from websearch.search.base import SearchHit, SearchProvider, SearchProviderError, SearchQuery
from websearch.search.filters import apply_filters


@dataclass
class SearchOutcome:
    hits: list[SearchHit]
    provider: str | None                    # quem respondeu (None se ninguém)
    errors: list[str] = field(default_factory=list)


class SearchRouter:
    def __init__(
        self,
        providers: Sequence[SearchProvider],
        *,
        retries: int = 1,
        backoff: float = 0.5,
        cooldown: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not providers:
            raise ValueError("É preciso ao menos um provedor de busca.")
        self._providers = list(providers)
        self._retries = max(0, retries)
        self._backoff = backoff
        self._cooldown = cooldown
        self._sleep = sleep
        self._clock = clock
        self._blocked_until: dict[str, float] = {}

    async def search(self, query: SearchQuery) -> SearchOutcome:
        """Primeiro provedor que devolver resultados (já filtrados) vence."""
        errors: list[str] = []
        for provider in self._providers:
            if self._clock() < self._blocked_until.get(provider.name, 0.0):
                errors.append(f"{provider.name}: em pausa após limite de taxa")
                continue
            try:
                hits = await self._try(provider, query)
            except SearchProviderError as exc:
                errors.append(str(exc))
                if exc.rate_limited:
                    self._blocked_until[provider.name] = self._clock() + self._cooldown
                continue
            hits = apply_filters(hits, query)[: query.max_results]
            if hits:
                return SearchOutcome(hits, provider.name, errors)
            errors.append(f"{provider.name}: sem resultados")
        return SearchOutcome([], None, errors)

    async def _try(self, provider: SearchProvider, query: SearchQuery) -> list[SearchHit]:
        attempt = 0
        while True:
            try:
                return await provider.search(query)
            except SearchProviderError as exc:
                # Limite de taxa: trocar de provedor é melhor que insistir no mesmo.
                if exc.rate_limited or not exc.retryable or attempt >= self._retries:
                    raise
                await self._sleep(self._backoff * (2 ** attempt))
                attempt += 1
