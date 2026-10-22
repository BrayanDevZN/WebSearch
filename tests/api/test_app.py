import pytest
from fastapi.testclient import TestClient

from tests.tools.test_search import FakeFetcher, FakeProvider, page_html
from websearch.api.app import create_app
from websearch.config import load_settings
from websearch.search.base import SearchHit, SearchProviderError
from websearch.search.router import SearchRouter

KEY = "k" * 40
AUTH = {"Authorization": f"Bearer {KEY}"}
URL = "https://s0.com/a"
BODY = "Python é uma linguagem de programação. " * 12


def make_client(provider=None, rate=100, clock=None, **env):
    settings = load_settings({"API_SEARCH_KEY": KEY, "RATE_LIMIT_PER_MINUTE": str(rate), **env})
    provider = provider or FakeProvider([SearchHit("T", URL, "snippet python", "fake", 0)])
    fetcher = FakeFetcher({URL: page_html("Página", [BODY])})
    kwargs = {"clock": clock} if clock else {}
    app = create_app(settings, router=SearchRouter([provider]), fetcher=fetcher, **kwargs)
    return TestClient(app, raise_server_exceptions=False)


def test_health_is_open():
    r = make_client().get("/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer errada"}, {"X-API-Key": "x"},
                                     {"Authorization": f"Basic {KEY}"}])
def test_auth_required(headers):
    r = make_client().post("/v1/search", json={"query": "python"}, headers=headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert KEY not in r.text and "request_id" in r.json()


@pytest.mark.parametrize("headers", [AUTH, {"X-API-Key": KEY}])
def test_both_key_formats(headers):
    r = make_client().post("/v1/search", json={"query": "python"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["url"] == URL and body["request_id"]


def test_search_options_roundtrip():
    r = make_client().post("/v1/search", headers=AUTH, json={
        "query": "python", "search_depth": "advanced", "include_raw_content": "text", "chunks_per_source": 2})
    assert r.status_code == 200 and "Python" in r.json()["results"][0]["raw_content"]


def test_extract_map_crawl():
    c = make_client()
    ex = c.post("/v1/extract", headers=AUTH, json={"urls": [URL, "ftp://x.com"]}).json()
    assert ex["results"][0]["url"] == URL and ex["failed_results"][0]["error"] == "scheme"
    mp = c.post("/v1/map", headers=AUTH, json={"url": URL, "respect_robots": False})
    assert mp.status_code == 422                      # campo desconhecido é rejeitado
    mp = c.post("/v1/map", headers=AUTH, json={"url": URL})
    assert mp.status_code == 200 and mp.json()["results"] == [URL]
    cr = c.post("/v1/crawl", headers=AUTH, json={"url": URL, "format": "text"})
    assert cr.status_code == 200 and cr.json()["results"][0]["title"] == "Página"


@pytest.mark.parametrize("path,body,status,code", [
    ("/v1/search", {"query": ""}, 400, "invalid_request"),
    ("/v1/search", {"query": "x", "max_results": 99}, 400, "invalid_request"),
    ("/v1/search", {"query": "x", "include_answer": True}, 400, "unsupported"),
    ("/v1/search", {}, 422, "invalid_request"),
    ("/v1/search", {"query": "x", "search_depth": "slow"}, 422, "invalid_request"),
    ("/v1/search", {"query": "x", "bogus": 1}, 422, "invalid_request"),
    ("/v1/extract", {"urls": []}, 400, "invalid_request"),
    ("/v1/map", {"url": "x.com", "select_paths": ["("]}, 400, "invalid_request"),
    ("/v1/crawl", {"url": "ftp://x.com"}, 400, "invalid_request"),
])
def test_errors(path, body, status, code):
    r = make_client().post(path, headers=AUTH, json=body)
    assert r.status_code == status and r.json()["error"]["code"] == code and r.json()["request_id"]


def test_provider_outage_is_502_without_details():
    c = make_client(FakeProvider(error=SearchProviderError("fake", "senha=segredo")))
    r = c.post("/v1/search", headers=AUTH, json={"query": "x"})
    assert r.status_code == 502 and "segredo" not in r.text


def test_unexpected_error_is_500_and_generic(monkeypatch):
    async def boom(self, request):
        raise RuntimeError("detalhe interno")

    monkeypatch.setattr("websearch.tools.search.SearchTool.search", boom)
    r = make_client().post("/v1/search", headers=AUTH, json={"query": "x"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error" and "detalhe" not in r.text


def test_rate_limit_and_window():
    now = [0.0]
    c = make_client(rate=2, clock=lambda: now[0])
    codes = [c.post("/v1/search", headers=AUTH, json={"query": "x"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = c.post("/v1/search", headers=AUTH, json={"query": "x"})
    assert int(r.headers["Retry-After"]) >= 1
    assert c.get("/v1/health").status_code == 200       # health não conta
    now[0] = 61
    assert c.post("/v1/search", headers=AUTH, json={"query": "x"}).status_code == 200


def test_unauthenticated_requests_do_not_consume_rate_limit():
    c = make_client(rate=1)
    for _ in range(3):
        assert c.post("/v1/search", json={"query": "x"}).status_code == 401
    assert c.post("/v1/search", headers=AUTH, json={"query": "x"}).status_code == 200


def test_docs_are_not_exposed():
    c = make_client()
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


async def test_real_fetcher_blocks_internal_urls():
    from websearch.net.fetcher import Fetcher
    from websearch.tools.crawl import CrawlRequest, CrawlTool

    async with Fetcher() as fetcher:
        resp = await CrawlTool(fetcher).crawl(CrawlRequest("http://127.0.0.1/"))
    assert resp.results == [] and resp.failed_results[0]["error"] == "unsafe_url"
