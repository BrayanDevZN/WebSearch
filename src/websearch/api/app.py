"""API HTTP (FastAPI): /v1/search|extract|map|crawl protegidos por `API_SEARCH_KEY`, /v1/health aberto."""

from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Callable, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, ConfigDict, Field

from websearch import __version__
from websearch.config import Settings
from websearch.net.fetcher import Fetcher
from websearch.search.factory import build_router
from websearch.search.router import SearchRouter
from websearch.tools.common import InvalidRequest, PageFetcher, SearchUnavailable, new_request_id
from websearch.tools.crawl import CrawlRequest, CrawlTool
from websearch.tools.crawler import CrawlParams
from websearch.tools.extract import ExtractRequest, ExtractTool
from websearch.tools.map import MapTool
from websearch.tools.search import SearchRequest, SearchTool

log = logging.getLogger("websearch.api")
MAX_BODY_BYTES = 64 * 1024


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchBody(Body):
    query: str
    search_depth: Literal["ultra-fast", "fast", "basic", "advanced"] = "basic"
    max_results: int = 5
    topic: Literal["general", "news"] = "general"
    time_range: Literal["day", "week", "month", "year"] | None = None
    start_date: str | None = None
    end_date: str | None = None
    include_domains: list[str] = Field(default_factory=list, max_length=300)
    exclude_domains: list[str] = Field(default_factory=list, max_length=150)
    chunks_per_source: int = 3
    include_raw_content: bool | Literal["markdown", "text"] = False
    include_images: bool = False
    include_favicon: bool = False
    include_answer: bool = False
    region: str | None = None


class ExtractBody(Body):
    urls: str | list[str]
    query: str | None = None
    chunks_per_source: int = 3
    extract_depth: Literal["basic", "advanced"] = "basic"
    format: Literal["markdown", "text"] = "markdown"
    include_images: bool = False
    include_favicon: bool = False


class MapBody(Body):
    url: str
    max_depth: int = 1
    max_breadth: int = 20
    limit: int = 50
    select_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    select_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)
    allow_external: bool = False


class CrawlBody(MapBody):
    format: Literal["markdown", "text"] = "markdown"
    include_images: bool = False
    include_favicon: bool = False


class RateLimiter:
    """Janela deslizante de 60 s por chave de cliente (em memória, um processo)."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self._limit, self._clock = per_minute, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, client: str) -> int | None:
        """None se liberado; senão, segundos até poder tentar de novo."""
        now = self._clock()
        hits = self._hits[client]
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= self._limit:
            return max(1, int(60 - (now - hits[0])) + 1)
        hits.append(now)
        return None


def _key_from(headers: dict[str, str]) -> str:
    header = headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return headers.get("x-api-key", "").strip()


def create_app(
    settings: Settings,
    *,
    router: SearchRouter | None = None,
    fetcher: PageFetcher | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    own_fetcher = fetcher is None
    page_fetcher: PageFetcher = fetcher or Fetcher(settings.fetch)
    search_tool = SearchTool(router or build_router(settings), page_fetcher)
    extract_tool = ExtractTool(page_fetcher)
    map_tool = MapTool(page_fetcher)
    crawl_tool = CrawlTool(page_fetcher)
    limiter = RateLimiter(settings.rate_limit_per_minute, clock)
    expected = settings.api_search_key.encode()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if own_fetcher:
            await page_fetcher.aclose()  # type: ignore[attr-defined]

    app = FastAPI(title="WebSearch", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def error_response(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}, "request_id": new_request_id()},
                            status_code=status, headers=headers)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return error_response(422, "invalid_request", f"{where or 'corpo'}: {first.get('msg', 'inválido')}")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        codes = {404: "not_found", 405: "method_not_allowed"}
        return error_response(exc.status_code, codes.get(exc.status_code, "http_error"), str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.error("erro interno: %s", type(exc).__name__)   # sem mensagem: pode conter dados do cliente
        return error_response(500, "internal_error", "Erro interno.")

    @app.get("/v1/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.post("/v1/search")
    async def search(body: SearchBody):
        data = body.model_dump()
        if data.pop("include_answer"):
            raise ApiError(400, "unsupported", "include_answer não é suportado (sem LLM neste serviço).")
        return (await _run(search_tool.search(SearchRequest(**data)))).to_dict()

    @app.post("/v1/extract")
    async def extract(body: ExtractBody):
        return (await _run(extract_tool.extract(ExtractRequest(**body.model_dump())))).to_dict()

    @app.post("/v1/map")
    async def map_(body: MapBody):
        return (await _run(map_tool.map(CrawlParams(**body.model_dump())))).to_dict()

    @app.post("/v1/crawl")
    async def crawl(body: CrawlBody):
        return (await _run(crawl_tool.crawl(CrawlRequest(**body.model_dump())))).to_dict()

    app.add_middleware(
        Gate, expected=expected, limiter=limiter,
        reject=lambda status, code, message, headers=None: error_response(status, code, message, headers),
    )
    return app


class Gate:
    """Middleware ASGI: segurança de /v1/* **antes** de qualquer leitura/validação do corpo.

    Ordem: chave (401) → limite de taxa (429) → tamanho do corpo (413, também sem Content-Length).
    Só então a requisição chega ao FastAPI com o corpo já em memória.
    """

    OPEN_PATHS = {"/v1/health"}

    def __init__(self, app, *, expected: bytes, limiter: RateLimiter, reject):
        self.app, self.expected, self.limiter, self.reject = app, expected, limiter, reject

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]
                message = {**message, "headers": headers}
            await send(message)

        if not scope["path"].startswith("/v1/") or scope["path"] in self.OPEN_PATHS:
            return await self.app(scope, receive, secured_send)

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        supplied = _key_from(headers).encode("utf-8", "ignore")
        if not supplied or not hmac.compare_digest(supplied, self.expected):
            response = self.reject(401, "unauthorized", "Chave de API ausente ou inválida.", {"WWW-Authenticate": "Bearer"})
            return await response(scope, receive, secured_send)
        wait = self.limiter.check("key")
        if wait is not None:
            response = self.reject(429, "rate_limited", "Limite de requisições excedido.", {"Retry-After": str(wait)})
            return await response(scope, receive, secured_send)

        declared = headers.get("content-length", "")
        too_large = self.reject(413, "too_large", "Corpo da requisição grande demais.")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return await too_large(scope, receive, secured_send)
        body, more = b"", True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > MAX_BODY_BYTES:
                return await too_large(scope, receive, secured_send)
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, secured_send)


async def _run(awaitable):
    try:
        return await awaitable
    except InvalidRequest as exc:
        raise ApiError(400, "invalid_request", str(exc)) from None
    except SearchUnavailable:
        raise ApiError(502, "search_unavailable", "Nenhum provedor de busca respondeu.") from None
