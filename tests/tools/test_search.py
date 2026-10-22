import asyncio

import pytest

from websearch.net.fetcher import FetchError, FetchResult
from websearch.search.base import SearchHit, SearchProviderError
from websearch.search.router import SearchRouter
from websearch.tools.common import InvalidRequest, SearchUnavailable
from websearch.tools.search import SearchRequest, SearchTool


def page_html(title, body, extra=""):
    paras = "".join(f"<p>{p}</p>" for p in body)
    return f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1>{paras}{extra}</article></body></html>"


class FakeProvider:
    name = "fake"

    def __init__(self, hits=None, error=None):
        self.hits, self.error = hits or [], error

    async def search(self, query):
        if self.error:
            raise self.error
        return list(self.hits)


class FakeFetcher:
    def __init__(self, pages=None, errors=None, delay=0.0):
        self.pages, self.errors, self.delay = pages or {}, errors or {}, delay
        self.calls = []

    async def fetch(self, url):
        self.calls.append(url)
        if self.delay:
            await asyncio.sleep(self.delay)
        if url in self.errors:
            raise FetchError(self.errors[url], "x", url=url)
        html = self.pages[url]
        return FetchResult(url=url, requested_url=url, status=200, content_type="text/html",
                           body=html.encode(), text=html, truncated=False)


LONG = "Python é uma linguagem de programação. " * 12


def hit(i, snippet="resumo"):
    return SearchHit(f"Título {i}", f"https://s{i}.com/a", snippet, "fake", i)


def make(hits, fetcher=None, **kw):
    router = SearchRouter([FakeProvider(hits)])
    return SearchTool(router, fetcher or FakeFetcher(), request_id=lambda: "rid", **kw)


async def test_fast_uses_snippets_and_downloads_nothing():
    fetcher = FakeFetcher()
    tool = make([hit(0, "gatos pretos"), hit(1, "python rápido")], fetcher)
    resp = await tool.search(SearchRequest("python", search_depth="fast"))
    assert fetcher.calls == []
    assert resp.results[0]["url"] == "https://s1.com/a"      # snippet com o termo sobe
    assert resp.results[0]["content"] == "python rápido"
    assert resp.request_id == "rid" and resp.to_dict()["query"] == "python"
    assert all(0 <= r["score"] <= 1 for r in resp.results)


async def test_basic_fetches_only_top_three():
    hits = [hit(i) for i in range(5)]
    fetcher = FakeFetcher({h.url: page_html(f"P{h.position}", [LONG]) for h in hits})
    resp = await make(hits, fetcher).search(SearchRequest("python", max_results=5))
    assert len(fetcher.calls) == 3 and len(resp.results) == 5


async def test_advanced_returns_requested_chunks_and_raw():
    body = [f"Parágrafo {i} sobre assuntos variados. " * 8 for i in range(6)] + ["Python python python é o tema. " * 8]
    h = hit(0)
    fetcher = FakeFetcher({h.url: page_html("Guia", body)})
    resp = await make([h], fetcher).search(
        SearchRequest("python", search_depth="advanced", chunks_per_source=2, include_raw_content="text"))
    r = resp.results[0]
    assert "python python" in r["content"].lower() and r["content"].count("[...]") + r["content"].count("\n\n") >= 0
    assert "Parágrafo 0" in r["raw_content"] and r["title"] == "Guia"
    assert r["score"] == 1.0


async def test_failed_page_falls_back_to_snippet():
    hits = [hit(0, "snippet zero"), hit(1)]
    fetcher = FakeFetcher({hits[1].url: page_html("Ok", [LONG])}, errors={hits[0].url: "timeout"})
    resp = await make(hits, fetcher).search(SearchRequest("python", include_raw_content=True))
    by_url = {r["url"]: r for r in resp.results}
    assert by_url[hits[0].url]["content"] == "snippet zero" and by_url[hits[0].url]["raw_content"] is None
    assert by_url[hits[1].url]["raw_content"]


async def test_slow_pages_hit_budget():
    h = hit(0, "só snippet")
    fetcher = FakeFetcher({h.url: page_html("x", [LONG])}, delay=5)
    resp = await make([h], fetcher, budget_seconds=0.05).search(SearchRequest("python"))
    assert resp.results[0]["content"] == "só snippet"


async def test_images_and_favicon():
    h = hit(0)
    extra = '<img src="https://s0.com/i.png" alt="Foto">'
    fetcher = FakeFetcher({h.url: page_html("T", [LONG], extra)})
    resp = await make([h], fetcher).search(SearchRequest("python", include_images=True, include_favicon=True))
    assert resp.to_dict()["images"] == [{"url": "https://s0.com/i.png", "description": "Foto"}]
    assert "favicon" in resp.results[0]


async def test_no_results_vs_provider_failure():
    empty = await make([]).search(SearchRequest("x"))
    assert empty.results == []
    router = SearchRouter([FakeProvider(error=SearchProviderError("fake", "boom"))])
    with pytest.raises(SearchUnavailable):
        await SearchTool(router, FakeFetcher()).search(SearchRequest("x"))


@pytest.mark.parametrize("kw", [
    {"query": "  "}, {"query": "x" * 401}, {"query": "x", "search_depth": "slow"},
    {"query": "x", "max_results": 0}, {"query": "x", "max_results": 21}, {"query": "x", "chunks_per_source": 6},
    {"query": "x", "time_range": "week", "start_date": "2024-01-01"},
    {"query": "x", "start_date": "2024-13-01"}, {"query": "x", "start_date": "2024-05-02", "end_date": "2024-05-01"},
    {"query": "x", "topic": "sports"}, {"query": "x", "include_raw_content": "html"},
])
async def test_validation(kw):
    with pytest.raises(InvalidRequest):
        await make([hit(0)]).search(SearchRequest(**kw))


async def test_aviso_quando_vazio_e_nada_quando_normal():
    empty = await make([]).search(SearchRequest("x"))
    assert empty.results == [] and "warnings" in empty.to_dict() and "Nenhum resultado" in empty.warnings[0]
    normal = await make([hit(0)]).search(SearchRequest("x", search_depth="fast"))
    assert "warnings" not in normal.to_dict()


async def test_aviso_de_consulta_relaxada():
    class OnlyRelaxed:
        name = "fake"

        async def search(self, q):
            return [] if q.include_domains else [SearchHit("t", "https://s0.com/a", "python", "fake", 0)]

    tool = SearchTool(SearchRouter([OnlyRelaxed()]), FakeFetcher())
    resp = await tool.search(SearchRequest("python", search_depth="fast", include_domains=["s0.com"]))
    assert [r["url"] for r in resp.results] == ["https://s0.com/a"] and "refeita" in resp.warnings[0]


async def test_busca_lenta_estoura_o_teto_e_vira_indisponivel():
    class Slow:
        name = "slow"

        async def search(self, q):
            await asyncio.sleep(5)
            return []

    tool = SearchTool(SearchRouter([Slow()]), FakeFetcher(), search_timeout=0.05)
    with pytest.raises(SearchUnavailable) as e:
        await tool.search(SearchRequest("x"))
    assert "tempo limite" in str(e.value)
