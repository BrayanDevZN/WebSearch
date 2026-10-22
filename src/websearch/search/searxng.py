"""Provedor SearXNG (metabuscador auto-hospedado com API JSON).

A URL é configurada pelo operador (pode ser local/privada), então não passa pela checagem SSRF;
a resposta, porém, é tratada como dado não confiável.
"""

from __future__ import annotations

import httpx

from websearch.search.base import SearchHit, SearchProviderError, SearchQuery
from websearch.search.ddg import build_query_text

_TIME_RANGE = {"day": "day", "week": "week", "month": "month", "year": "year"}


class SearXNGProvider:
    name = "searxng"

    def __init__(self, base_url: str, *, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=False,
                                         headers={"Accept": "application/json", "User-Agent": "WebSearchBot/0.1"})

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        params = {
            "q": build_query_text(query),
            "format": "json",
            "categories": "news" if query.topic == "news" else "general",
            "safesearch": "0",
        }
        if query.time_range:
            params["time_range"] = _TIME_RANGE[query.time_range]
        if query.region and "-" in query.region:
            params["language"] = query.region.split("-", 1)[1]
        try:
            response = await self._client.get(f"{self._base}/search", params=params)
        except httpx.TimeoutException:
            raise SearchProviderError(self.name, "tempo esgotado", retryable=True) from None
        except httpx.HTTPError as exc:
            raise SearchProviderError(self.name, f"erro de rede: {type(exc).__name__}", retryable=True) from None
        if response.status_code == 429:
            raise SearchProviderError(self.name, "limite de taxa", rate_limited=True)
        if response.status_code == 403:
            raise SearchProviderError(self.name, "formato json desativado no servidor (HTTP 403)")
        if response.status_code >= 400:
            raise SearchProviderError(self.name, f"HTTP {response.status_code}", retryable=response.status_code >= 500)
        try:
            payload = response.json()
        except ValueError:
            raise SearchProviderError(self.name, "resposta não é JSON") from None
        rows = payload.get("results") if isinstance(payload, dict) else None
        hits: list[SearchHit] = []
        for i, row in enumerate(rows or []):
            url = (row.get("url") or "").strip() if isinstance(row, dict) else ""
            if not url:
                continue
            hits.append(SearchHit(
                title=(row.get("title") or "").strip(),
                url=url,
                snippet=(row.get("content") or "").strip(),
                provider=self.name,
                position=i,
                published_date=row.get("publishedDate") or None,
            ))
        return hits
