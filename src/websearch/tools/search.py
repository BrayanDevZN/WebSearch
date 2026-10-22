"""Pipeline `search`: provedores → filtros → (leitura das páginas) → trechos ranqueados → formato Tavily.

Profundidades:
- `ultra-fast` / `fast`: só o snippet do provedor (nenhuma página é baixada);
- `basic`: baixa as melhores páginas (até `BASIC_FETCH`) e usa o melhor trecho de cada uma;
- `advanced`: baixa todas e usa os `chunks_per_source` melhores trechos.
`include_raw_content`/`include_images`/`include_favicon` forçam o download das páginas em qualquer profundidade.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Literal, Sequence

from websearch.extract.chunk import split_chunks
from websearch.extract.html import ExtractedPage
from websearch.rank.bm25 import BM25, normalize_scores
from websearch.rank.select import ScoredChunk, join_chunks, score_pages, select_chunks
from websearch.search.base import SearchHit, SearchQuery, TimeRange, Topic
from websearch.search.filters import parse_date
from websearch.search.router import SearchRouter
from websearch.tools.common import (
    InvalidRequest, PageFetcher, PageOutcome, SearchUnavailable, load_page, new_request_id,
)

Depth = Literal["ultra-fast", "fast", "basic", "advanced"]
DEPTHS = ("ultra-fast", "fast", "basic", "advanced")
BASIC_FETCH = 3
MAX_RESULTS = 20
MAX_QUERY_CHARS = 400
MAX_IMAGES = 10
DEFAULT_BUDGET_SECONDS = 25.0


@dataclass
class SearchRequest:
    query: str
    search_depth: Depth = "basic"
    max_results: int = 5
    topic: Topic = "general"
    time_range: TimeRange | None = None
    start_date: str | None = None
    end_date: str | None = None
    include_domains: Sequence[str] = ()
    exclude_domains: Sequence[str] = ()
    chunks_per_source: int = 3
    include_raw_content: bool | Literal["markdown", "text"] = False
    include_images: bool = False
    include_favicon: bool = False
    region: str | None = None

    def validate(self) -> None:
        query = (self.query or "").strip()
        if not query:
            raise InvalidRequest("query não pode ser vazia.")
        if len(query) > MAX_QUERY_CHARS:
            raise InvalidRequest(f"query deve ter no máximo {MAX_QUERY_CHARS} caracteres.")
        if self.search_depth not in DEPTHS:
            raise InvalidRequest(f"search_depth deve ser um de: {', '.join(DEPTHS)}.")
        if not 1 <= self.max_results <= MAX_RESULTS:
            raise InvalidRequest(f"max_results deve estar entre 1 e {MAX_RESULTS}.")
        if not 1 <= self.chunks_per_source <= 5:
            raise InvalidRequest("chunks_per_source deve estar entre 1 e 5.")
        if self.topic not in ("general", "news"):
            raise InvalidRequest("topic deve ser 'general' ou 'news'.")
        if self.time_range not in (None, "day", "week", "month", "year"):
            raise InvalidRequest("time_range deve ser day, week, month ou year.")
        if self.time_range and (self.start_date or self.end_date):
            raise InvalidRequest("time_range não pode ser usado junto com start_date/end_date.")
        for name in ("start_date", "end_date"):
            value = getattr(self, name)
            if value is not None:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise InvalidRequest(f"{name} deve estar no formato YYYY-MM-DD.") from None
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise InvalidRequest("start_date não pode ser depois de end_date.")
        if self.include_raw_content not in (True, False, "markdown", "text"):
            raise InvalidRequest("include_raw_content deve ser booleano, 'markdown' ou 'text'.")

    @property
    def raw_format(self) -> Literal["markdown", "text"]:
        return "text" if self.include_raw_content == "text" else "markdown"

    @property
    def wants_raw(self) -> bool:
        return bool(self.include_raw_content)

    def pages_to_fetch(self, total: int) -> int:
        if self.wants_raw or self.include_images or self.include_favicon or self.search_depth == "advanced":
            return total
        if self.search_depth == "basic":
            return min(BASIC_FETCH, total)
        return 0

    def chunk_count(self) -> int:
        return self.chunks_per_source if self.search_depth == "advanced" else 1


@dataclass
class SearchResponse:
    query: str
    results: list[dict]
    response_time: float
    request_id: str
    images: list[dict] = field(default_factory=list)
    provider: str | None = None

    def to_dict(self) -> dict:
        out = {"query": self.query, "results": self.results, "response_time": self.response_time,
               "request_id": self.request_id}
        if self.images:
            out["images"] = self.images
        return out


class SearchTool:
    def __init__(
        self,
        router: SearchRouter,
        fetcher: PageFetcher,
        *,
        budget_seconds: float = DEFAULT_BUDGET_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        request_id: Callable[[], str] = new_request_id,
    ):
        self._router = router
        self._fetcher = fetcher
        self._budget = budget_seconds
        self._clock = clock
        self._request_id = request_id

    async def search(self, request: SearchRequest) -> SearchResponse:
        request.validate()
        started = self._clock()
        query = request.query.strip()
        outcome = await self._router.search(SearchQuery(
            query=query, max_results=request.max_results, topic=request.topic,
            time_range=request.time_range, start_date=request.start_date, end_date=request.end_date,
            include_domains=tuple(request.include_domains), exclude_domains=tuple(request.exclude_domains),
            region=request.region,
        ))
        if not outcome.hits and outcome.errors and not any(e.endswith("sem resultados") for e in outcome.errors):
            raise SearchUnavailable(outcome.errors)

        hits = outcome.hits
        pages = await self._fetch_pages(request, hits)
        results, images = self._build(request, query, hits, pages)
        return SearchResponse(
            query=query, results=results, images=images, provider=outcome.provider,
            response_time=round(self._clock() - started, 2), request_id=self._request_id(),
        )

    # ------------------------------------------------------------------ pages --
    async def _fetch_pages(self, request: SearchRequest, hits: list[SearchHit]) -> list[PageOutcome | None]:
        count = request.pages_to_fetch(len(hits))
        if count == 0:
            return [None] * len(hits)
        fmt = request.raw_format
        tasks = [asyncio.ensure_future(load_page(self._fetcher, h.url, fmt)) for h in hits[:count]]
        done, pending = await asyncio.wait(tasks, timeout=self._budget)
        for task in pending:
            task.cancel()
        outcomes: list[PageOutcome | None] = []
        for task in tasks:
            if task in done and not task.cancelled() and task.exception() is None:
                outcomes.append(task.result())
            else:
                outcomes.append(PageOutcome(error="timeout"))
        return outcomes + [None] * (len(hits) - count)

    # ---------------------------------------------------------------- results --
    def _build(self, request, query, hits, outcomes):
        chunk_lists: list[list[str]] = []
        for outcome in outcomes:
            ok = outcome is not None and outcome.page is not None and outcome.error is None
            chunk_lists.append(split_chunks(outcome.page.text) if ok else [])
        all_scores = score_pages(query, chunk_lists)

        results: list[dict] = []
        raw_scores: list[float] = []
        for hit, outcome, chunks, scores in zip(hits, outcomes, chunk_lists, all_scores):
            page: ExtractedPage | None = outcome.page if outcome and not outcome.error else None
            selected: list[ScoredChunk] = []
            if chunks:
                selected = select_chunks(query, chunks, request.chunk_count(), scores)
                content = join_chunks(selected)
                raw_scores.append(max(scores, default=0.0))
            else:
                content = hit.snippet
                raw_scores.append(-1.0)             # marcador: pontuar pelo snippet depois
            item: dict = {"title": (page.title if page and page.title else hit.title) or hit.url,
                          "url": hit.url, "content": content, "score": 0.0}
            published = (page.published_date if page else None) or hit.published_date
            if published:
                item["published_date"] = published
            if request.wants_raw:
                item["raw_content"] = page.text if page else None
            if request.include_favicon:
                item["favicon"] = page.favicon if page else None
            results.append(item)

        self._assign_scores(query, hits, results, raw_scores)
        order = sorted(range(len(results)), key=lambda i: (-results[i]["score"], hits[i].position, i))
        images = self._images(request, outcomes) if request.include_images else []
        return [results[i] for i in order], images

    @staticmethod
    def _assign_scores(query, hits, results, raw_scores) -> None:
        """Páginas lidas pontuam pelo melhor trecho; as demais pelo snippet; tudo em [0, 1]."""
        page_scores = normalize_scores([max(s, 0.0) for s in raw_scores])
        snippet_texts = [f"{h.title} {h.snippet}" for h in hits]
        snippet_scores = normalize_scores(BM25(snippet_texts).scores(query)) if hits else []
        for i, result in enumerate(results):
            if raw_scores[i] >= 0 and page_scores[i] > 0:
                value = page_scores[i]
            elif snippet_scores and snippet_scores[i] > 0:
                value = snippet_scores[i] * 0.9        # lê-se menos do que a página inteira
            else:
                value = 1.0 / (2 + hits[i].position)   # sem termos em comum: ordem do provedor
            result["score"] = round(min(value, 1.0), 4)

    @staticmethod
    def _images(request, outcomes) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for outcome in outcomes:
            if not outcome or not outcome.page:
                continue
            for image in outcome.page.images:
                if image.url in seen:
                    continue
                seen.add(image.url)
                out.append({"url": image.url, "description": image.alt} if image.alt else {"url": image.url})
                if len(out) >= MAX_IMAGES:
                    return out
        return out
