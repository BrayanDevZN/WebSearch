"""Provedor DuckDuckGo & cia via biblioteca `ddgs` (metabuscador gratuito, sem chave)."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any, Callable

from websearch.search.base import SearchHit, SearchProviderError, SearchQuery

_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}
# O texto dos resultados do ddgs costuma começar com a data: "Jun 15, 2026 · Linus Torvalds…"
_LEADING_DATE = re.compile(r"^\s*([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})\s*[·\-–—]\s*")

SearchFn = Callable[..., list[dict[str, Any]]]


def _default_search(kind: str, query: str, **kwargs: Any) -> list[dict[str, Any]]:
    from ddgs import DDGS

    client = DDGS(timeout=10)
    return getattr(client, kind)(query, **kwargs)


def _split_leading_date(body: str) -> tuple[str | None, str]:
    match = _LEADING_DATE.match(body or "")
    if not match:
        return None, (body or "").strip()
    raw = match.group(1).replace(".", "")
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat(), body[match.end():].strip()
        except ValueError:
            continue
    return None, (body or "").strip()


def build_query_text(query: SearchQuery) -> str:
    """Aplica include/exclude de domínio como operadores `site:` (e filtra de novo depois)."""
    text = query.query.strip()
    includes = [d for d in query.include_domains if d.strip()]
    if len(includes) == 1:
        text += f" site:{includes[0]}"
    elif includes:
        text += " (" + " OR ".join(f"site:{d}" for d in includes) + ")"
    for d in query.exclude_domains:
        if d.strip():
            text += f" -site:{d}"
    return text


class DDGSProvider:
    name = "ddgs"

    def __init__(self, search_fn: SearchFn | None = None, *, overfetch: int = 2):
        self._search = search_fn or _default_search
        self._overfetch = max(1, overfetch)

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        kind = "news" if query.topic == "news" else "text"
        kwargs: dict[str, Any] = {"max_results": max(1, query.max_results) * self._overfetch}
        if query.time_range:
            kwargs["timelimit"] = _TIMELIMIT[query.time_range]
        if query.region:
            kwargs["region"] = query.region
        text = build_query_text(query)
        try:
            rows = await asyncio.to_thread(self._search, kind, text, **kwargs)
        except Exception as exc:  # a biblioteca levanta tipos próprios; classificamos pelo nome
            error = self._classify(exc)
            if isinstance(error, _NoResults):
                return []
            raise error from None
        return [hit for i, row in enumerate(rows or []) if (hit := self._to_hit(row, i, kind))]

    def _classify(self, exc: Exception) -> Exception:
        name = type(exc).__name__
        message = str(exc)
        if name == "RatelimitException":
            return SearchProviderError(self.name, "limite de taxa", rate_limited=True)
        if name == "TimeoutException" or isinstance(exc, TimeoutError):
            return SearchProviderError(self.name, "tempo esgotado", retryable=True)
        if name == "DDGSException" and "no results" in message.lower():
            return _NoResults()
        return SearchProviderError(self.name, f"erro: {name}", retryable=name == "DDGSException")

    def _to_hit(self, row: dict[str, Any], index: int, kind: str) -> SearchHit | None:
        url = (row.get("href") or row.get("url") or "").strip()
        if not url:
            return None
        body = row.get("body") or ""
        published = row.get("date") if kind == "news" else None
        if kind == "text":
            published, body = _split_leading_date(body)
        return SearchHit(
            title=(row.get("title") or "").strip(),
            url=url,
            snippet=body.strip(),
            provider=self.name,
            position=index,
            published_date=published,
        )


class _NoResults(Exception):
    """Interno: o ddgs não achou nada (não é falha)."""
