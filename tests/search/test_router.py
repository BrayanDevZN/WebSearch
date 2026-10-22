import pytest

from websearch.config import load_settings
from websearch.search.base import SearchHit, SearchProviderError, SearchQuery
from websearch.search.factory import build_router
from websearch.search.router import SearchRouter


class Fake:
    def __init__(self, name, results=None, errors=None):
        self.name = name
        self.results = results if results is not None else []
        self.errors = list(errors or [])
        self.calls = 0

    async def search(self, query):
        self.calls += 1
        if self.errors:
            err = self.errors.pop(0)
            if err:
                raise err
        return list(self.results)


def hits(provider, *urls):
    return [SearchHit("t", u, "s", provider, i) for i, u in enumerate(urls)]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


async def no_sleep(_):
    return None


def router(*providers, clock=None, **kw):
    return SearchRouter(providers, sleep=no_sleep, clock=clock or Clock(), **kw)


async def test_primeiro_provedor_com_resultado_vence():
    a, b = Fake("a", hits("a", "https://x.com/1")), Fake("b", hits("b", "https://y.com/1"))
    out = await router(a, b).search(SearchQuery("q"))
    assert out.provider == "a" and len(out.hits) == 1 and b.calls == 0


async def test_cai_para_o_proximo_quando_vazio():
    a, b = Fake("a", []), Fake("b", hits("b", "https://y.com/1"))
    out = await router(a, b).search(SearchQuery("q"))
    assert out.provider == "b" and "a: sem resultados" in out.errors


async def test_cai_para_o_proximo_quando_filtros_removem_tudo():
    a = Fake("a", hits("a", "https://x.com/1"))
    b = Fake("b", hits("b", "https://y.com/1"))
    out = await router(a, b).search(SearchQuery("q", include_domains=["y.com"]))
    assert out.provider == "b"


async def test_aplica_filtros_dedupe_e_limite():
    a = Fake("a", hits("a", "https://x.com/1", "https://x.com/1?utm_source=z", "https://x.com/2", "https://x.com/3"))
    out = await router(a).search(SearchQuery("q", max_results=2))
    assert [h.url for h in out.hits] == ["https://x.com/1", "https://x.com/2"]


async def test_retentativa_com_backoff_em_erro_transitorio():
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    a = Fake("a", hits("a", "https://x.com/1"), errors=[SearchProviderError("a", "t", retryable=True), None])
    r = SearchRouter([a], retries=2, backoff=0.5, sleep=sleep, clock=Clock())
    out = await r.search(SearchQuery("q"))
    assert out.provider == "a" and a.calls == 2 and sleeps == [0.5]


async def test_backoff_exponencial_e_desiste_apos_retries():
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    err = lambda: SearchProviderError("a", "t", retryable=True)
    a = Fake("a", errors=[err(), err(), err()])
    r = SearchRouter([a], retries=2, backoff=1.0, sleep=sleep, clock=Clock())
    out = await r.search(SearchQuery("q"))
    assert out.provider is None and a.calls == 3 and sleeps == [1.0, 2.0]


async def test_erro_nao_retentavel_nao_repete_e_vai_ao_proximo():
    a = Fake("a", errors=[SearchProviderError("a", "fatal")])
    b = Fake("b", hits("b", "https://y.com/1"))
    out = await router(a, b).search(SearchQuery("q"))
    assert a.calls == 1 and out.provider == "b" and any("fatal" in e for e in out.errors)


async def test_rate_limit_troca_de_provedor_sem_retentar_e_entra_em_pausa():
    clock = Clock()
    a = Fake("a", hits("a", "https://x.com/1"), errors=[SearchProviderError("a", "rl", rate_limited=True)])
    b = Fake("b", hits("b", "https://y.com/1"))
    r = router(a, b, clock=clock, cooldown=60)

    out = await r.search(SearchQuery("q"))
    assert out.provider == "b" and a.calls == 1

    clock.now = 30                                  # ainda em pausa: nem tenta o "a"
    out = await r.search(SearchQuery("q"))
    assert out.provider == "b" and a.calls == 1 and any("em pausa" in e for e in out.errors)

    clock.now = 61                                  # pausa acabou: volta ao "a"
    out = await r.search(SearchQuery("q"))
    assert out.provider == "a" and a.calls == 2


async def test_todos_falham():
    a = Fake("a", errors=[SearchProviderError("a", "x")])
    b = Fake("b", [])
    out = await router(a, b).search(SearchQuery("q"))
    assert out.hits == [] and out.provider is None and len(out.errors) == 2


def test_precisa_de_provedor():
    with pytest.raises(ValueError):
        SearchRouter([])


def test_factory_segue_a_ordem_do_env():
    s = load_settings({"API_SEARCH_KEY": "k" * 40, "LLM_PROVIDER": "local",
                       "SEARCH_PROVIDERS": "searxng,ddgs", "SEARXNG_URL": "http://localhost:8888"})
    assert [p.name for p in build_router(s)._providers] == ["searxng", "ddgs"]
