"""Modelo real (baixa ~0,5 GB na primeira vez). Rodar com: pytest -m network tests/rank/test_semantic_network.py"""
import pytest

pytest.importorskip("fastembed")
from websearch.rank.semantic import FastEmbedder, semantic_scores  # noqa: E402

pytestmark = pytest.mark.network


async def test_modelo_real_entende_sinonimos_e_troca_de_idioma():
    emb = FastEmbedder()
    docs = [
        "O automóvel com menor consumo de combustível é ideal para economizar.",   # sinônimo, 0 palavras em comum
        "Receita de bolo de chocolate com cobertura de brigadeiro.",
        "The best fuel-efficient cars of the year",                                 # outro idioma
        "Carro de brinquedo para crianças de 3 anos",                               # palavra igual, sentido errado
    ]
    s = await semantic_scores(emb, "qual o melhor carro econômico", docs)
    assert s[0] > s[3] and s[2] > s[3] and s[1] == min(s)


async def test_modelo_real_e_rapido_em_lote():
    import time

    emb = FastEmbedder()
    await emb.warmup()
    chunks = [f"Trecho número {i} sobre assuntos variados de tecnologia e ciência. " * 6 for i in range(200)]
    t = time.monotonic()
    out = await emb.embed(chunks)
    assert out.shape[0] == 200 and time.monotonic() - t < 5
