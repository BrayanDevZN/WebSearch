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
    kw.setdefault("cache_ttl", 0)                 # os testes antigos repetem a mesma consulta de propósito
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


async def test_provedor_com_bug_nao_derruba_busca_e_cai_para_o_proximo():
    class Buggy:
        name = "buggy"

        async def search(self, q):
            raise RuntimeError("segredo interno")

    good = Fake("good", hits("good", "https://a.com/x"))
    out = await SearchRouter([Buggy(), good], sleep=no_sleep).search(SearchQuery("q"))
    assert out.provider == "good" and out.errors == ["buggy: erro inesperado (RuntimeError)"]
    assert "segredo" not in "".join(out.errors)


# ---------------------------------------------------------------- cache e relaxamento --
class Scripted:
    """Provedor que responde conforme o texto da consulta recebida."""

    def __init__(self, name, by_text):
        self.name, self.by_text, self.seen = name, by_text, []

    async def search(self, query):
        self.seen.append(query)
        return list(self.by_text.get(query.query, []))


async def test_cache_evita_nova_chamada_e_expira():
    clock = Clock()
    a = Fake("a", hits("a", "https://x.com/1"))
    r = router(a, clock=clock, cache_ttl=100)
    first = await r.search(SearchQuery("Python  "))
    second = await r.search(SearchQuery("python"))              # mesma consulta (normalizada)
    assert a.calls == 1 and not first.cached and second.cached and second.hits == first.hits
    second.hits.clear()                                          # a cópia não contamina o cache
    assert len((await r.search(SearchQuery("python"))).hits) == 1
    clock.now = 101
    assert not (await r.search(SearchQuery("python"))).cached and a.calls == 2


async def test_cache_nao_guarda_vazio_e_diferencia_parametros():
    a = Fake("a", [])
    r = router(a, cache_ttl=100)
    await r.search(SearchQuery("q")); await r.search(SearchQuery("q"))
    assert a.calls == 2                                          # vazio não é cacheado
    b = Fake("b", hits("b", "https://x.com/1"))
    r = router(b, cache_ttl=100)
    await r.search(SearchQuery("q", max_results=5)); await r.search(SearchQuery("q", max_results=3))
    await r.search(SearchQuery("q", include_domains=["x.com"])); await r.search(SearchQuery("q", include_domains=("x.com",)))
    assert b.calls == 3                                          # lista e tupla são a mesma chave


async def test_cache_tem_tamanho_maximo():
    a = Fake("a", hits("a", "https://x.com/1"))
    r = router(a, cache_ttl=100, cache_size=2)
    for text in ("um", "dois", "tres"):
        await r.search(SearchQuery(text))
    assert a.calls == 3
    await r.search(SearchQuery("um"))                            # foi expulso
    assert a.calls == 4


async def test_site_vazio_e_relaxado_para_palavra_e_filtrado_depois():
    prov = Scripted("p", {"fastapi middleware fastapi.tiangolo.com": [
        SearchHit("o", "https://fastapi.tiangolo.com/tutorial/middleware/", "s", "p", 0),
        SearchHit("x", "https://outro.com/fastapi", "s", "p", 1),
    ]})
    out = await router(prov).search(SearchQuery("fastapi middleware", max_results=2, include_domains=["fastapi.tiangolo.com"]))
    assert out.relaxed and [h.url for h in out.hits] == ["https://fastapi.tiangolo.com/tutorial/middleware/"]
    assert prov.seen[0].include_domains == ("fastapi.tiangolo.com",) or list(prov.seen[0].include_domains) == ["fastapi.tiangolo.com"]
    assert prov.seen[1].include_domains == () and prov.seen[1].max_results == 8      # sem operador, busca mais


async def test_relaxamento_nao_acontece_sem_filtro_nem_quando_normal_funciona():
    prov = Scripted("p", {"q": [], "ok": [SearchHit("o", "https://a.com/", "s", "p", 0)]})
    out = await router(prov).search(SearchQuery("q"))
    assert not out.hits and len(prov.seen) == 1
    out = await router(prov).search(SearchQuery("ok", include_domains=["a.com"]))
    assert out.hits and not out.relaxed and len(prov.seen) == 2


async def test_relaxado_tambem_vazio_devolve_vazio_com_erros_dos_dois_passos():
    prov = Scripted("p", {})
    out = await router(prov).search(SearchQuery("q", exclude_domains=["a.com"]))
    assert not out.hits and not out.relaxed and len(prov.seen) == 2 and len(out.errors) == 2
