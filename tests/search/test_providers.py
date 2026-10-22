import httpx
import pytest

from websearch.search.base import SearchProviderError, SearchQuery
from websearch.search.ddg import DDGSProvider, build_query_text
from websearch.search.searxng import SearXNGProvider


# ---------------------------- DDGS -----------------------------------------------------
class RatelimitException(Exception): ...
class TimeoutException(Exception): ...
class DDGSException(Exception): ...


def fake(rows=None, exc=None):
    calls = []

    def fn(kind, query, **kwargs):
        calls.append((kind, query, kwargs))
        if exc:
            raise exc
        return rows
    fn.calls = calls
    return fn


TEXT_ROWS = [
    {"title": "Linux 7.1", "href": "https://a.com/1", "body": "Jun 15, 2026 · Linus Torvalds lançou o 7.1"},
    {"title": "Sem data", "href": "https://b.com/2", "body": "Texto qualquer"},
    {"title": "Sem url", "href": "", "body": "x"},
]


async def test_ddgs_mapeia_resultados_e_extrai_data_do_texto():
    fn = fake(TEXT_ROWS)
    hits = await DDGSProvider(fn).search(SearchQuery("linux 7.1", max_results=5))
    assert [h.url for h in hits] == ["https://a.com/1", "https://b.com/2"]
    assert hits[0].published_date == "2026-06-15" and hits[0].snippet == "Linus Torvalds lançou o 7.1"
    assert hits[1].published_date is None and hits[1].provider == "ddgs"
    kind, text, kwargs = fn.calls[0]
    assert kind == "text" and text == "linux 7.1" and kwargs["max_results"] == 10  # overfetch x2


async def test_ddgs_noticias_usam_news_e_data_do_campo():
    rows = [{"title": "N", "url": "https://n.com/1", "body": "corpo", "date": "2026-10-16T10:30:00+00:00"}]
    fn = fake(rows)
    hits = await DDGSProvider(fn).search(SearchQuery("kernel", topic="news", time_range="week"))
    assert fn.calls[0][0] == "news" and fn.calls[0][2]["timelimit"] == "w"
    assert hits[0].published_date == "2026-10-16T10:30:00+00:00"


@pytest.mark.parametrize("rng,code", [("day", "d"), ("week", "w"), ("month", "m"), ("year", "y")])
async def test_ddgs_time_range(rng, code):
    fn = fake([])
    await DDGSProvider(fn).search(SearchQuery("q", time_range=rng))
    assert fn.calls[0][2]["timelimit"] == code


async def test_ddgs_regiao_e_sem_timelimit_por_padrao():
    fn = fake([])
    await DDGSProvider(fn).search(SearchQuery("q", region="br-pt"))
    assert fn.calls[0][2]["region"] == "br-pt" and "timelimit" not in fn.calls[0][2]


def test_operadores_de_dominio_na_consulta():
    assert build_query_text(SearchQuery("q", include_domains=["a.com"])) == "q site:a.com"
    assert build_query_text(SearchQuery("q", include_domains=["a.com", "b.org"])) == "q (site:a.com OR site:b.org)"
    assert build_query_text(SearchQuery("q", exclude_domains=["x.com"])) == "q -site:x.com"
    assert build_query_text(SearchQuery("  q  ", include_domains=[" "])) == "q"


@pytest.mark.parametrize("exc,rate,retry", [
    (RatelimitException("202"), True, True),
    (TimeoutException("t"), False, True),
    (DDGSException("boom"), False, True),
    (ValueError("x"), False, False),
])
async def test_ddgs_classifica_erros(exc, rate, retry):
    with pytest.raises(SearchProviderError) as e:
        await DDGSProvider(fake(exc=exc)).search(SearchQuery("q"))
    assert e.value.rate_limited is rate and e.value.retryable is retry and e.value.provider == "ddgs"


async def test_ddgs_no_results_nao_e_erro():
    assert await DDGSProvider(fake(exc=DDGSException("No results found."))).search(SearchQuery("q")) == []


async def test_ddgs_resposta_vazia_ou_none():
    assert await DDGSProvider(fake([])).search(SearchQuery("q")) == []
    assert await DDGSProvider(fake(None)).search(SearchQuery("q")) == []


# ---------------------------- SearXNG --------------------------------------------------
def sx(handler):
    return SearXNGProvider("http://searx.local:8888/", transport=httpx.MockTransport(handler))


async def test_searxng_ok():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return httpx.Response(200, json={"results": [
            {"title": "A", "url": "https://a.com/1", "content": "resumo", "publishedDate": "2026-06-01T00:00:00"},
            {"title": "sem url", "content": "x"},
            {"title": "B", "url": "https://b.com/2", "content": ""},
        ]})

    hits = await sx(handler).search(SearchQuery("linux", topic="news", time_range="month", region="br-pt",
                                                include_domains=["a.com"]))
    p = seen["url"].params
    assert seen["url"].path == "/search" and p["format"] == "json" and p["categories"] == "news"
    assert p["time_range"] == "month" and p["language"] == "pt" and p["q"] == "linux site:a.com"
    assert [h.url for h in hits] == ["https://a.com/1", "https://b.com/2"]
    assert hits[0].published_date == "2026-06-01T00:00:00" and hits[0].provider == "searxng"


@pytest.mark.parametrize("status,rate,retry", [(429, True, True), (403, False, False), (500, False, True), (404, False, False)])
async def test_searxng_status(status, rate, retry):
    with pytest.raises(SearchProviderError) as e:
        await sx(lambda r: httpx.Response(status)).search(SearchQuery("q"))
    assert e.value.rate_limited is rate and e.value.retryable is retry


async def test_searxng_resposta_invalida_e_rede():
    with pytest.raises(SearchProviderError):
        await sx(lambda r: httpx.Response(200, text="<html>")).search(SearchQuery("q"))

    def boom(request):
        raise httpx.ConnectError("x")
    with pytest.raises(SearchProviderError) as e:
        await sx(boom).search(SearchQuery("q"))
    assert e.value.retryable

    def slow(request):
        raise httpx.ReadTimeout("t")
    with pytest.raises(SearchProviderError) as e:
        await sx(slow).search(SearchQuery("q"))
    assert e.value.retryable


async def test_searxng_json_sem_results():
    assert await sx(lambda r: httpx.Response(200, json={"foo": 1})).search(SearchQuery("q")) == []
    assert await sx(lambda r: httpx.Response(200, json=[1, 2])).search(SearchQuery("q")) == []
