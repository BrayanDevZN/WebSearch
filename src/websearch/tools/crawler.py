"""Rastreador em largura (BFS) compartilhado por `map` e `crawl`.

Profundidade = saltos de link a partir da raiz (raiz = 0); páginas de profundidade 0..`max_depth` são
baixadas. `limit` é o total de páginas baixadas. Respeita robots.txt (falha ao ler = permitido).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from websearch.extract.html import ExtractedPage
from websearch.net.urlsafe import UnsafeURLError, normalize_url
from websearch.search.filters import canonical_url, clean_domain, matches_domain, url_domain
from websearch.tools.common import InvalidRequest, PageFetcher, load_page

MAX_PATTERNS = 20
MAX_PATTERN_CHARS = 200
SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".mp3", ".mp4", ".avi", ".mov", ".webm",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".exe", ".dmg", ".iso", ".css", ".js", ".json", ".xml", ".woff", ".woff2",
    ".ttf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)
ROBOTS_AGENT = "WebSearchBot"


@dataclass
class CrawlParams:
    url: str
    max_depth: int = 1
    max_breadth: int = 20
    limit: int = 50
    select_paths: Sequence[str] = ()
    exclude_paths: Sequence[str] = ()
    select_domains: Sequence[str] = ()
    exclude_domains: Sequence[str] = ()
    allow_external: bool = False
    respect_robots: bool = True
    format: Literal["markdown", "text"] = "markdown"

    def validate(self) -> None:
        if not (self.url or "").strip():
            raise InvalidRequest("url não pode ser vazia.")
        if not 1 <= self.max_depth <= 5:
            raise InvalidRequest("max_depth deve estar entre 1 e 5.")
        if not 1 <= self.max_breadth <= 500:
            raise InvalidRequest("max_breadth deve estar entre 1 e 500.")
        if not 1 <= self.limit <= 500:
            raise InvalidRequest("limit deve estar entre 1 e 500.")
        if self.format not in ("markdown", "text"):
            raise InvalidRequest("format deve ser 'markdown' ou 'text'.")


@dataclass
class Visit:
    url: str
    depth: int
    page: ExtractedPage


@dataclass
class WalkResult:
    base_url: str
    visited: list[Visit] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


def compile_patterns(patterns: Sequence[str], name: str) -> list[re.Pattern]:
    if len(patterns) > MAX_PATTERNS:
        raise InvalidRequest(f"{name} aceita no máximo {MAX_PATTERNS} padrões.")
    out = []
    for pattern in patterns:
        if len(pattern) > MAX_PATTERN_CHARS:
            raise InvalidRequest(f"{name}: padrão maior que {MAX_PATTERN_CHARS} caracteres.")
        try:
            out.append(re.compile(pattern, re.I))
        except re.error:
            raise InvalidRequest(f"{name}: regex inválida: {pattern!r}.") from None
    return out


class Crawler:
    def __init__(self, fetcher: PageFetcher, *, budget_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self._fetcher = fetcher
        self._budget = budget_seconds
        self._clock = clock

    async def walk(self, params: CrawlParams) -> WalkResult:
        params.validate()
        select_paths = compile_patterns(params.select_paths, "select_paths")
        exclude_paths = compile_patterns(params.exclude_paths, "exclude_paths")
        select_domains = compile_patterns(params.select_domains, "select_domains")
        exclude_domains = compile_patterns(params.exclude_domains, "exclude_domains")
        try:
            root = normalize_url(params.url.strip())[0]
        except UnsafeURLError as exc:
            raise InvalidRequest(f"url inválida: {exc}") from None

        def allowed(url: str) -> bool:
            parts = urlsplit(url)
            host = url_domain(url)
            if parts.path.lower().endswith(SKIP_EXTENSIONS):
                return False
            if not params.allow_external and not matches_domain(url, clean_domain(root)):
                return False
            if select_domains and not any(p.search(host) for p in select_domains):
                return False
            if any(p.search(host) for p in exclude_domains):
                return False
            path = parts.path or "/"
            if select_paths and not any(p.search(path) for p in select_paths):
                return False
            return not any(p.search(path) for p in exclude_paths)

        robots: dict[str, RobotFileParser | None] = {}

        async def robots_allow(url: str) -> bool:
            if not params.respect_robots:
                return True
            parts = urlsplit(url)
            origin = f"{parts.scheme}://{parts.netloc}"
            if origin not in robots:
                robots[origin] = await self._load_robots(origin)
            parser = robots[origin]
            return parser is None or parser.can_fetch(ROBOTS_AGENT, url)

        result = WalkResult(base_url=root)
        deadline = self._clock() + self._budget
        seen = {canonical_url(root)}
        frontier = [root]
        for depth in range(params.max_depth + 1):
            if not frontier or len(result.visited) + len(result.failed) >= params.limit:
                break
            room = params.limit - len(result.visited) - len(result.failed)
            batch: list[str] = []
            for url in frontier:
                if len(batch) >= room:
                    break
                if await robots_allow(url):
                    batch.append(url)
                else:
                    result.failed.append({"url": url, "error": "robots_disallowed"})
            remaining = deadline - self._clock()
            if not batch or remaining <= 0:
                break
            tasks = [asyncio.ensure_future(load_page(self._fetcher, u, params.format)) for u in batch]
            _, pending = await asyncio.wait(tasks, timeout=remaining)
            for task in pending:
                task.cancel()
            next_frontier: list[str] = []
            for url, task in zip(batch, tasks):
                ok = task.done() and not task.cancelled() and task.exception() is None
                outcome = task.result() if ok else None
                if outcome is None or outcome.page is None or (outcome.error and outcome.error != "empty"):
                    result.failed.append({"url": url, "error": outcome.error if outcome else "timeout"})
                    continue
                result.visited.append(Visit(url=outcome.page.url or url, depth=depth, page=outcome.page))
                taken = 0
                for link in outcome.page.links:
                    if taken >= params.max_breadth:
                        break
                    key = canonical_url(link.url)
                    if key in seen or not allowed(link.url):
                        continue
                    seen.add(key)
                    next_frontier.append(link.url)
                    taken += 1
            frontier = next_frontier
        return result

    async def _load_robots(self, origin: str) -> RobotFileParser | None:
        try:
            fetched = await self._fetcher.fetch(origin + "/robots.txt")
        except Exception:
            return None
        if fetched.status != 200 or not fetched.text or "html" in fetched.content_type:
            return None
        parser = RobotFileParser()
        parser.parse(fetched.text.splitlines())
        return parser
