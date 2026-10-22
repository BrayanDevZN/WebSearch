"""Roteador de provedores: ordem de preferência, retentativa com backoff e disjuntor (circuit breaker)."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable, Sequence

from websearch.search.base import SearchHit, SearchProvider, SearchProviderError, SearchQuery
from websearch.search.filters import apply_filters


@dataclass
class SearchOutcome:
    hits: list[SearchHit]
    provider: str | None                    # quem respondeu (None se ninguém)
    errors: list[str] = field(default_factory=list)
    relaxed: bool = False                   # só veio depois de trocar `site:` por palavras na consulta
    cached: bool = False


RELAXED_OVERFETCH = 4
MAX_RELAXED_DOMAINS = 3


def _cache_key(query: SearchQuery) -> tuple:
    return (query.query.strip().lower(), query.max_results, query.topic, query.time_range, query.start_date,
            query.end_date, tuple(sorted(d.lower() for d in query.include_domains)),
            tuple(sorted(d.lower() for d in query.exclude_domains)), query.region)


class SearchRouter:
    def __init__(
        self,
        providers: Sequence[SearchProvider],
        *,
        retries: int = 1,
        backoff: float = 0.5,
        cooldown: float = 60.0,
        cache_ttl: float = 600.0,
        cache_size: int = 256,
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
        self._cache_ttl = cache_ttl
        self._cache_size = cache_size
        self._cache: OrderedDict[tuple, tuple[float, SearchOutcome]] = OrderedDict()

    async def search(self, query: SearchQuery) -> SearchOutcome:
        """Cache → consulta normal → (se vazia e com filtro de domínio) consulta relaxada."""
        cached = self._cache_get(query)
        if cached is not None:
            return cached
        outcome = await self._search_once(query, query)
        if not outcome.hits and (query.include_domains or query.exclude_domains):
            # O operador `site:` é a causa mais comum de resposta vazia (ex.: consulta em português).
            # Sem o operador, o domínio entra como palavra comum e o filtro é aplicado depois.
            domains = [d for d in query.include_domains if d.strip()][:MAX_RELAXED_DOMAINS]
            relaxed = replace(
                query, query=" ".join([query.query.strip(), *domains]),
                include_domains=(), exclude_domains=(), max_results=query.max_results * RELAXED_OVERFETCH,
            )
            again = await self._search_once(relaxed, query)
            if again.hits:
                again.relaxed = True
                again.errors = outcome.errors + again.errors
                outcome = again
            else:
                outcome.errors += again.errors
        self._cache_put(query, outcome)
        return outcome

    def _cache_get(self, query: SearchQuery) -> SearchOutcome | None:
        key = _cache_key(query)
        entry = self._cache.get(key) if self._cache_ttl > 0 else None
        if entry is None:
            return None
        stored_at, outcome = entry
        if self._clock() - stored_at > self._cache_ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return replace(outcome, hits=list(outcome.hits), errors=list(outcome.errors), cached=True)

    def _cache_put(self, query: SearchQuery, outcome: SearchOutcome) -> None:
        if self._cache_ttl <= 0 or not outcome.hits:        # vazio nunca é guardado
            return
        key = _cache_key(query)
        self._cache[key] = (self._clock(), outcome)
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    async def _search_once(self, query: SearchQuery, original: SearchQuery) -> SearchOutcome:
        """Primeiro provedor que devolver resultados vence; filtros e limite vêm da consulta `original`."""
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
            except Exception as exc:  # provedor com bug: não derruba a busca, tenta o próximo
                errors.append(f"{provider.name}: erro inesperado ({type(exc).__name__})")
                continue
            hits = apply_filters(hits, original)[: original.max_results]
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
