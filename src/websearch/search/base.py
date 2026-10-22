"""Tipos e interface comuns dos provedores de busca."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, Sequence

Topic = Literal["general", "news"]
TimeRange = Literal["day", "week", "month", "year"]


@dataclass(frozen=True)
class SearchQuery:
    query: str
    max_results: int = 10
    topic: Topic = "general"
    time_range: TimeRange | None = None
    start_date: str | None = None          # YYYY-MM-DD
    end_date: str | None = None            # YYYY-MM-DD
    include_domains: Sequence[str] = field(default_factory=tuple)
    exclude_domains: Sequence[str] = field(default_factory=tuple)
    region: str | None = None              # ex.: "br-pt", "us-en" (formato do DuckDuckGo)


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    provider: str
    position: int = 0                      # posição original no provedor (0 = primeiro)
    published_date: str | None = None      # ISO (YYYY-MM-DD ou datetime), se conhecida


class SearchProviderError(Exception):
    """Falha de um provedor. `rate_limited`/`retryable` guiam o roteador."""

    def __init__(self, provider: str, message: str, *, rate_limited: bool = False, retryable: bool = False):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.rate_limited = rate_limited
        self.retryable = retryable or rate_limited


class SearchProvider(Protocol):
    name: str

    async def search(self, query: SearchQuery) -> list[SearchHit]: ...
