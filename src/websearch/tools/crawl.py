"""Ferramenta `crawl`: percorre um site e devolve o conteúdo de cada página."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from websearch.tools.common import PageFetcher, new_request_id
from websearch.tools.crawler import Crawler, CrawlParams


@dataclass
class CrawlRequest(CrawlParams):
    include_images: bool = False
    include_favicon: bool = False


@dataclass
class CrawlResponse:
    base_url: str
    results: list[dict]
    failed_results: list[dict]
    response_time: float
    request_id: str

    def to_dict(self) -> dict:
        return {"base_url": self.base_url, "results": self.results, "failed_results": self.failed_results,
                "response_time": self.response_time, "request_id": self.request_id}


class CrawlTool:
    def __init__(self, fetcher: PageFetcher, *, budget_seconds: float = 90.0,
                 clock: Callable[[], float] = time.monotonic,
                 request_id: Callable[[], str] = new_request_id):
        self._crawler = Crawler(fetcher, budget_seconds=budget_seconds, clock=clock)
        self._clock = clock
        self._request_id = request_id

    async def crawl(self, request: CrawlRequest) -> CrawlResponse:
        started = self._clock()
        walked = await self._crawler.walk(request)
        results = []
        for visit in walked.visited:
            item = {"url": visit.url, "title": visit.page.title, "raw_content": visit.page.text}
            if visit.page.published_date:
                item["published_date"] = visit.page.published_date
            if request.include_images:
                item["images"] = [i.url for i in visit.page.images[:20]]
            if request.include_favicon:
                item["favicon"] = visit.page.favicon
            results.append(item)
        return CrawlResponse(
            base_url=walked.base_url, results=results, failed_results=walked.failed,
            response_time=round(self._clock() - started, 2), request_id=self._request_id(),
        )
