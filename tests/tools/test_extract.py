import pytest

from tests.tools.test_search import FakeFetcher, page_html
from websearch.tools.common import InvalidRequest
from websearch.tools.extract import ExtractRequest, ExtractTool

A, B = "https://a.com/x", "https://b.com/y"
BODY = [f"Assunto {i} totalmente diverso e longo. " * 10 for i in range(6)] + ["Foguetes espaciais e órbita. " * 10]


def tool(fetcher, **kw):
    return ExtractTool(fetcher, request_id=lambda: "rid", **kw)


async def test_partial_failure_and_order():
    f = FakeFetcher({A: page_html("A", BODY)}, errors={B: "timeout"})
    resp = await tool(f).extract(ExtractRequest([B, A, "ftp://x.com/a", A]))
    assert [r["url"] for r in resp.results] == [A]
    assert {x["url"]: x["error"] for x in resp.failed_results} == {B: "timeout", "ftp://x.com/a": "scheme"}
    assert resp.to_dict()["request_id"] == "rid"


async def test_single_string_and_full_text():
    f = FakeFetcher({A: page_html("A", BODY)})
    resp = await tool(f).extract(ExtractRequest(A, format="text"))
    assert "Assunto 0" in resp.results[0]["raw_content"] and resp.results[0]["title"] == "A"


async def test_query_selects_chunks():
    f = FakeFetcher({A: page_html("A", BODY)})
    resp = await tool(f).extract(ExtractRequest(A, query="foguetes órbita", chunks_per_source=1))
    content = resp.results[0]["raw_content"]
    assert "Foguetes" in content and "Assunto 0" not in content


async def test_options():
    html = page_html("A", BODY, '<img src="https://a.com/i.png"><a href="https://a.com/z">z</a>')
    f = FakeFetcher({A: html})
    r = (await tool(f).extract(ExtractRequest(A, extract_depth="advanced", include_images=True,
                                              include_favicon=True))).results[0]
    assert r["images"] == ["https://a.com/i.png"] and "favicon" in r and r["links"]
    basic = (await tool(f).extract(ExtractRequest(A))).results[0]
    assert "links" not in basic and "images" not in basic


async def test_timeout_budget():
    f = FakeFetcher({A: page_html("A", BODY)}, delay=5)
    resp = await tool(f, budget_seconds=0.05).extract(ExtractRequest(A))
    assert resp.results == [] and resp.failed_results[0]["error"] == "timeout"


@pytest.mark.parametrize("kw", [
    {"urls": []}, {"urls": [" "]}, {"urls": ["https://a.com"] * 21},
    {"urls": A, "chunks_per_source": 0}, {"urls": A, "extract_depth": "x"}, {"urls": A, "format": "html"},
])
async def test_validation(kw):
    with pytest.raises(InvalidRequest):
        await tool(FakeFetcher()).extract(ExtractRequest(**kw))


async def test_url_gigante_e_truncada_na_resposta():
    resp = await tool(FakeFetcher()).extract(ExtractRequest(["https://a.com/" + "x" * 3000]))
    assert resp.results == [] and resp.failed_results[0]["error"] == "too_long"
    assert len(resp.failed_results[0]["url"]) <= 301


async def test_urls_internas_vao_para_failed_com_fetcher_real():
    from websearch.net.fetcher import Fetcher

    async with Fetcher() as real:
        resp = await ExtractTool(real).extract(
            ExtractRequest(["http://127.0.0.1/", "http://169.254.169.254/latest", "http://[::1]/", "http://localhost/"]))
    assert resp.results == []
    assert {f["error"] for f in resp.failed_results} <= {"unsafe_url", "private_ip", "blocked_host"}
    assert len(resp.failed_results) == 4


# ------------------------------------------------------------------ query semântica --
np = pytest.importorskip("numpy")
from tests.fakes import FakeEmbedder  # noqa: E402
from websearch.rank.hybrid import Ranker  # noqa: E402
from websearch.rank.semantic import SemanticUnavailable  # noqa: E402

SEM_BODY = [f"Assunto {i} totalmente diverso e longo. " * 10 for i in range(8)] + ["O automovel veiculo mais economico. " * 10]


async def test_query_semantica_acha_sinonimo_sem_palavra_em_comum():
    f = FakeFetcher({A: page_html("A", SEM_BODY)})
    sem = await ExtractTool(f, ranker=Ranker(FakeEmbedder())).extract(ExtractRequest(A, query="carro", chunks_per_source=1))
    assert "automovel" in sem.results[0]["raw_content"] and "warnings" not in sem.to_dict()
    bm = await ExtractTool(f, ranker=Ranker(FakeEmbedder())).extract(
        ExtractRequest(A, query="carro", chunks_per_source=1, ranking="bm25"))
    assert "automovel" not in bm.results[0]["raw_content"]             # BM25 não enxerga o sinônimo


async def test_query_semantica_modelo_fora_cai_para_bm25_com_aviso():
    class Down(FakeEmbedder):
        async def embed(self, texts):
            raise SemanticUnavailable("fora")

    f = FakeFetcher({A: page_html("A", BODY)})
    resp = await ExtractTool(f, ranker=Ranker(Down())).extract(ExtractRequest(A, query="foguetes", chunks_per_source=1))
    assert "Foguetes" in resp.results[0]["raw_content"] and "indisponível" in resp.warnings[0]


async def test_ranking_sem_query_nao_chama_modelo_e_invalido_e_rejeitado():
    emb = FakeEmbedder()
    f = FakeFetcher({A: page_html("A", BODY)})
    await ExtractTool(f, ranker=Ranker(emb)).extract(ExtractRequest(A))
    assert emb.calls == []
    with pytest.raises(InvalidRequest):
        await ExtractTool(f).extract(ExtractRequest(A, ranking="magico"))


async def test_varias_urls_pontuadas_num_unico_lote_semantico():
    emb = FakeEmbedder()
    f = FakeFetcher({A: page_html("A", BODY), B: page_html("B", BODY)})
    await ExtractTool(f, ranker=Ranker(emb)).extract(ExtractRequest([A, B], query="foguetes"))
    assert len(emb.calls) == 1
