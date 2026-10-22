"""Ferramenta `extract`: baixa e limpa várias URLs; falhas parciais vão para `failed_results`."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence

from websearch.extract.chunk import split_chunks
from websearch.net.urlsafe import UnsafeURLError, normalize_url
from websearch.rank.select import join_chunks, select_chunks
from websearch.tools.common import InvalidRequest, PageFetcher, PageOutcome, load_page, new_request_id

MAX_URLS = 20
MAX_ECHO_CHARS = 300


def _echo(url: str) -> str:
    """URL do cliente devolvida em `failed_results`, truncada para não inflar a resposta."""
    return url if len(url) <= MAX_ECHO_CHARS else url[:MAX_ECHO_CHARS] + "…"

MAX_IMAGES_PER_PAGE = 20
DEFAULT_BUDGET_SECONDS = 40.0


@dataclass
class ExtractRequest:
    urls: str | Sequence[str]
    query: str | None = None
    chunks_per_source: int = 3
    extract_depth: Literal["basic", "advanced"] = "basic"
    format: Literal["markdown", "text"] = "markdown"
    include_images: bool = False
    include_favicon: bool = False

    def url_list(self) -> list[str]:
        return [self.urls] if isinstance(self.urls, str) else list(self.urls)

    def validate(self) -> None:
        urls = self.url_list()
        if not urls or any(not isinstance(u, str) or not u.strip() for u in urls):
            raise InvalidRequest("urls deve conter ao menos uma URL não vazia.")
        if len(urls) > MAX_URLS:
            raise InvalidRequest(f"urls aceita no máximo {MAX_URLS} URLs por chamada.")
        if not 1 <= self.chunks_per_source <= 5:
            raise InvalidRequest("chunks_per_source deve estar entre 1 e 5.")
        if self.extract_depth not in ("basic", "advanced"):
            raise InvalidRequest("extract_depth deve ser 'basic' ou 'advanced'.")
        if self.format not in ("markdown", "text"):
            raise InvalidRequest("format deve ser 'markdown' ou 'text'.")
        if self.query is not None and len(self.query) > 400:
            raise InvalidRequest("query deve ter no máximo 400 caracteres.")


@dataclass
class ExtractResponse:
    results: list[dict]
    failed_results: list[dict]
    response_time: float
    request_id: str

    def to_dict(self) -> dict:
        return {"results": self.results, "failed_results": self.failed_results,
                "response_time": self.response_time, "request_id": self.request_id}


class ExtractTool:
    def __init__(
        self,
        fetcher: PageFetcher,
        *,
        budget_seconds: float = DEFAULT_BUDGET_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        request_id: Callable[[], str] = new_request_id,
    ):
        self._fetcher = fetcher
        self._budget = budget_seconds
        self._clock = clock
        self._request_id = request_id

    async def extract(self, request: ExtractRequest) -> ExtractResponse:
        request.validate()
        started = self._clock()
        results: list[dict | None] = []
        failed: list[dict] = []
        slots: list[tuple[int, str]] = []          # (posição em results, url normalizada)
        seen: set[str] = set()
        for raw in request.url_list():
            try:
                url = normalize_url(raw.strip())[0]
            except UnsafeURLError as exc:
                failed.append({"url": _echo(raw.strip()), "error": exc.reason})
                continue
            if url in seen:
                continue
            seen.add(url)
            slots.append((len(results), url))
            results.append(None)

        tasks = [asyncio.ensure_future(load_page(self._fetcher, url, request.format)) for _, url in slots]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self._budget)
            for task in pending:
                task.cancel()
        for (index, url), task in zip(slots, tasks):
            ok = task.done() and not task.cancelled() and task.exception() is None
            outcome = task.result() if ok else PageOutcome(error="timeout")
            if outcome.error or outcome.page is None:
                failed.append({"url": url, "error": outcome.error or "fetch_failed"})
            else:
                results[index] = self._item(request, url, outcome)
        return ExtractResponse(
            results=[r for r in results if r is not None], failed_results=failed,
            response_time=round(self._clock() - started, 2), request_id=self._request_id(),
        )

    @staticmethod
    def _item(request: ExtractRequest, url: str, outcome: PageOutcome) -> dict:
        page = outcome.page
        text = page.text
        if request.query and request.query.strip():
            chunks = split_chunks(text)
            text = join_chunks(select_chunks(request.query, chunks, request.chunks_per_source)) or text
        item: dict = {"url": page.url or url, "title": page.title, "raw_content": text}
        if page.published_date:
            item["published_date"] = page.published_date
        if request.extract_depth == "advanced":
            item["links"] = [{"url": l.url, "text": l.text} for l in page.links]
        if request.include_images:
            item["images"] = [i.url for i in page.images[:MAX_IMAGES_PER_PAGE]]
        if request.include_favicon:
            item["favicon"] = page.favicon
        return item
