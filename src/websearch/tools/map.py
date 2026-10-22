"""Ferramenta `map`: descobre as URLs de um site (sem devolver conteúdo)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from websearch.tools.common import PageFetcher, new_request_id
from websearch.tools.crawler import Crawler, CrawlParams


@dataclass
class MapResponse:
    base_url: str
    results: list[str]
    response_time: float
    request_id: str

    def to_dict(self) -> dict:
        return {"base_url": self.base_url, "results": self.results,
                "response_time": self.response_time, "request_id": self.request_id}


class MapTool:
    def __init__(self, fetcher: PageFetcher, *, budget_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic,
                 request_id: Callable[[], str] = new_request_id):
        self._crawler = Crawler(fetcher, budget_seconds=budget_seconds, clock=clock)
        self._clock = clock
        self._request_id = request_id

    async def map(self, params: CrawlParams) -> MapResponse:
        started = self._clock()
        walked = await self._crawler.walk(params)
        return MapResponse(
            base_url=walked.base_url, results=[v.url for v in walked.visited],
            response_time=round(self._clock() - started, 2), request_id=self._request_id(),
        )
