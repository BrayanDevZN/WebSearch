import asyncio

import pytest

pytest.importorskip("numpy")

from tests.fakes import FakeEmbedder  # noqa: E402
from websearch.rank.hybrid import Ranker, _shortlist  # noqa: E402
from websearch.rank.semantic import SemanticUnavailable  # noqa: E402

PAGES = [["automovel veiculo barato", "bolo receita doce"], ["carro novo", "texto qualquer"]]


async def test_sem_embedder_usa_bm25_sem_aviso():
    r = Ranker()
    out = await r.score_pages("carro", PAGES)
    assert out.mode == "bm25" and out.warning is None and r.default_mode == "bm25"
    assert out.scores[1][0] == 1.0 and out.scores[0][0] == 0.0       # só "carro novo" tem a palavra


async def test_bm25_e_comparavel_entre_paginas():
    out = await Ranker().score_pages("carro", [["carro rapido"], ["carro carro carro carro"], ["nada"]])
    assert max(max(p) for p in out.scores) == 1.0 and out.scores[2] == [0.0]


async def test_hibrido_acha_sinonimo_que_o_bm25_nao_acha():
    out = await Ranker(FakeEmbedder()).score_pages("carro", PAGES)
    assert out.mode == "hybrid"
    syn, cake, exact, other = out.scores[0][0], out.scores[0][1], out.scores[1][0], out.scores[1][1]
    assert syn > 0.4 and syn > cake and exact >= syn and other < syn  # sinônimo "automovel veiculo" sobe
    assert all(0.0 <= x <= 1.0 for page in out.scores for x in page)


async def test_modo_semantico_ignora_palavra_exata_sem_sentido():
    out = await Ranker(FakeEmbedder()).score_pages("carro", [["automovel veiculo"], ["bolo receita"]], "semantic")
    assert out.mode == "semantic" and out.scores[0][0] > 0.9 and out.scores[1][0] < 0.2


async def test_modo_bm25_explicito_nao_chama_o_modelo():
    emb = FakeEmbedder()
    out = await Ranker(emb).score_pages("carro", PAGES, "bm25")
    assert out.mode == "bm25" and out.warning is None and emb.calls == []


async def test_pedido_semantico_sem_modelo_avisa():
    out = await Ranker().score_pages("carro", PAGES, "hybrid")
    assert out.mode == "bm25" and "indisponível" in out.warning


async def test_modo_invalido():
    with pytest.raises(ValueError):
        await Ranker().score_pages("q", PAGES, "magico")


async def test_modelo_indisponivel_em_tempo_de_execucao_cai_para_bm25_com_aviso():
    class Down(FakeEmbedder):
        async def embed(self, texts):
            raise SemanticUnavailable("fora")

    out = await Ranker(Down()).score_pages("carro", PAGES)
    assert out.mode == "bm25" and "indisponível" in out.warning and out.scores[1][0] == 1.0


async def test_modelo_lento_estoura_timeout_e_cai_para_bm25():
    class Slow(FakeEmbedder):
        async def embed(self, texts):
            await asyncio.sleep(5)

    out = await Ranker(Slow(), timeout=0.05).score_pages("carro", PAGES)
    assert out.mode == "bm25" and "demorou" in out.warning


async def test_paginas_vazias_e_consulta_sem_termos():
    r = Ranker(FakeEmbedder())
    assert (await r.score_pages("carro", [[], []])).scores == [[], []]
    assert (await r.score_pages("carro", [])).scores == []
    out = await r.score_pages("the of a", [["automovel", "bolo"]])           # só stopwords: BM25 zero
    assert out.mode == "hybrid" and all(x >= 0 for x in out.scores[0])


async def test_score_texts():
    out = await Ranker(FakeEmbedder()).score_texts("carro", ["automovel veiculo", "bolo receita"])
    assert out.scores[0][0] > out.scores[0][1]


def test_peso_invalido():
    with pytest.raises(ValueError):
        Ranker(bm25_weight=1.5)


async def test_teto_de_embeddings_limita_trabalho_e_mantem_melhores_do_bm25():
    emb = FakeEmbedder()
    pages = [[f"texto {i}" for i in range(30)] + ["carro carro"] for _ in range(3)]       # 93 trechos
    out = await Ranker(emb, max_embed=12).score_pages("carro", pages)
    assert len(emb.calls[0]) - 1 <= 12                                                    # -1 = a consulta
    assert all(out.scores[p][30] > 0.9 for p in range(3))                                 # o relevante entrou


def test_shortlist_inclui_primeiro_e_respeita_o_teto():
    pages = [["a", "b", "c", "d"], ["e", "f", "g", "h"]]
    bm25 = [[0.0, 0.1, 0.9, 0.2], [0.0, 0.0, 0.0, 0.8]]
    picks = _shortlist(pages, bm25, 4)
    assert (0, 0) in picks and (1, 0) in picks and (0, 2) in picks and (1, 3) in picks and len(picks) == 4
    assert len(_shortlist(pages, bm25, 100)) == 8


async def test_excecao_inesperada_do_modelo_tambem_cai_para_bm25():
    class Weird(FakeEmbedder):
        async def embed(self, texts):
            raise ValueError("forma inesperada /caminho/interno")

    out = await Ranker(Weird()).score_pages("carro", PAGES)
    assert out.mode == "bm25" and "falhou" in out.warning and "interno" not in out.warning
    assert out.scores[1][0] == 1.0
