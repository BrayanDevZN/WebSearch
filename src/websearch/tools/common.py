"""Erros e utilitários compartilhados pelas ferramentas (search, extract, map, crawl)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Protocol

from websearch.extract.html import ExtractedPage, Format, extract_from_fetch
from websearch.net.fetcher import FetchError, FetchResult
from websearch.net.urlsafe import UnsafeURLError


class InvalidRequest(ValueError):
    """Parâmetros inválidos (vira HTTP 400/422 na API)."""


class SearchUnavailable(Exception):
    """Todos os provedores de busca falharam (vira HTTP 502/503 na API)."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Nenhum provedor de busca disponível: " + "; ".join(errors))


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> FetchResult: ...


@dataclass
class PageOutcome:
    page: ExtractedPage | None = None
    error: str | None = None          # código estável: timeout, unsafe_url, http_404, empty...


def new_request_id() -> str:
    return uuid.uuid4().hex


async def load_page(fetcher: PageFetcher, url: str, fmt: Format = "markdown") -> PageOutcome:
    """Baixa e extrai uma página; nunca levanta (a falha vira `error`)."""
    try:
        result = await fetcher.fetch(url)
    except FetchError as exc:
        return PageOutcome(error=exc.reason)
    except UnsafeURLError as exc:
        return PageOutcome(error=exc.reason)
    except Exception:
        return PageOutcome(error="fetch_failed")
    try:
        page = await asyncio.to_thread(extract_from_fetch, result, fmt)
    except Exception:
        return PageOutcome(error="extract_failed")
    if not page.text.strip():
        return PageOutcome(page=page, error="empty")
    return PageOutcome(page=page)
