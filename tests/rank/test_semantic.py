import asyncio
import sys
import types

import pytest

np = pytest.importorskip("numpy")

from websearch.rank.semantic import (  # noqa: E402
    DEFAULT_MODEL, FastEmbedder, SemanticUnavailable, semantic_scores,
)


class FakeEmbedder:
    """Vetores determinísticos: um eixo por 'conceito' (sinônimos compartilham o eixo)."""

    name = "fake"
    CONCEPTS = [("carro", "automovel", "veiculo", "car"), ("bolo", "receita", "cake"), ("dor", "cabeca", "headache")]

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        rows = []
        for t in texts:
            words = t.lower().split()
            v = np.array([sum(w in group for w in words) for group in self.CONCEPTS] + [0.1], dtype="float32")
            rows.append(v / np.linalg.norm(v))
        return np.stack(rows)


async def test_scores_pegam_sinonimos_que_o_texto_nao_compartilha():
    scores = await semantic_scores(FakeEmbedder(), "carro", ["automovel veiculo", "bolo receita", "carro"])
    assert scores[2] > scores[0] > scores[1]
    assert scores[0] > 0.9 and scores[1] < 0.2


async def test_scores_vazio_e_um_unico_embed_por_chamada():
    emb = FakeEmbedder()
    assert await semantic_scores(emb, "q", []) == []
    assert emb.calls == []                                           # sem trechos: nem chama o modelo
    await semantic_scores(emb, "carro", ["a", "b", "c"])
    assert len(emb.calls) == 1 and emb.calls[0][0] == "carro" and len(emb.calls[0]) == 4


async def test_numero_inesperado_de_vetores_e_erro_claro():
    class Broken(FakeEmbedder):
        async def embed(self, texts):
            return (await super().embed(texts))[:-1]

    with pytest.raises(SemanticUnavailable):
        await semantic_scores(Broken(), "q", ["a", "b"])


# ------------------------------------------------------------------ FastEmbedder (com fastembed falso) --
class FakeTextEmbedding:
    instances = []

    def __init__(self, name, cache_dir=None, threads=None):
        self.name, self.cache_dir, self.seen = name, cache_dir, []
        FakeTextEmbedding.instances.append(self)

    def embed(self, texts):
        self.seen.append(list(texts))
        for t in texts:
            yield np.array([len(t), 1.0, 0.0], dtype="float32")


@pytest.fixture
def fake_fastembed(monkeypatch, tmp_path):
    FakeTextEmbedding.instances.clear()
    module = types.ModuleType("fastembed")
    module.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)
    return tmp_path


async def test_embedder_normaliza_carrega_uma_vez_e_cria_pasta(fake_fastembed):
    emb = FastEmbedder("m", cache_dir=str(fake_fastembed / "novo"))
    out = await emb.embed(["abc", "defgh"])
    assert out.shape == (2, 3) and np.allclose(np.linalg.norm(out, axis=1), 1.0)
    await emb.embed(["x"])
    assert len(FakeTextEmbedding.instances) == 1 and (fake_fastembed / "novo").is_dir()


async def test_embedder_cache_evita_recalcular_e_deduplica(fake_fastembed):
    emb = FastEmbedder("m", cache_dir=str(fake_fastembed))
    await emb.embed(["a", "b", "a"])
    await emb.embed(["a", "b", "c"])
    seen = FakeTextEmbedding.instances[0].seen
    assert seen == [["a", "b"], ["c"]]                       # duplicado só 1x; já vistos não voltam


async def test_embedder_cache_tem_limite_lru(fake_fastembed):
    emb = FastEmbedder("m", cache_dir=str(fake_fastembed), cache_size=2)
    await emb.embed(["a", "b"]); await emb.embed(["a"]); await emb.embed(["c"])   # "b" é o menos recente
    seen_before = len(FakeTextEmbedding.instances[0].seen)
    await emb.embed(["a"])
    assert len(FakeTextEmbedding.instances[0].seen) == seen_before                # "a" ficou
    await emb.embed(["b"])
    assert FakeTextEmbedding.instances[0].seen[-1] == ["b"]                       # "b" foi expulso


async def test_embedder_trunca_texto_gigante_e_aceita_vazio(fake_fastembed):
    emb = FastEmbedder("m", cache_dir=str(fake_fastembed))
    await emb.embed(["x" * 50_000, ""])
    sent = FakeTextEmbedding.instances[0].seen[0]
    assert len(sent[0]) == 2000 and sent[1] == " "
    assert (await emb.embed([])).shape[0] == 0


async def test_embedder_vetor_zero_nao_vira_nan(fake_fastembed, monkeypatch):
    monkeypatch.setattr(FakeTextEmbedding, "embed", lambda self, texts: iter([np.zeros(3, dtype="float32") for _ in texts]))
    out = await FastEmbedder("m", cache_dir=str(fake_fastembed)).embed(["a"])
    assert not np.isnan(out).any()


async def test_embedder_concorrente_carrega_modelo_uma_unica_vez(fake_fastembed):
    emb = FastEmbedder("m", cache_dir=str(fake_fastembed))
    await asyncio.gather(*[emb.embed([f"t{i}"]) for i in range(8)])
    assert len(FakeTextEmbedding.instances) == 1


async def test_sem_fastembed_instalado_vira_semantic_unavailable(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "fastembed", None)             # import levanta ImportError
    with pytest.raises(SemanticUnavailable, match="fastembed"):
        await FastEmbedder("m", cache_dir=str(tmp_path)).embed(["a"])


async def test_modelo_que_nao_carrega_nao_vaza_detalhes(monkeypatch, tmp_path):
    class Boom:
        def __init__(self, *a, **k):
            raise OSError("/caminho/secreto não encontrado")

    module = types.ModuleType("fastembed")
    module.TextEmbedding = Boom
    monkeypatch.setitem(sys.modules, "fastembed", module)
    with pytest.raises(SemanticUnavailable) as e:
        await FastEmbedder("m", cache_dir=str(tmp_path)).embed(["a"])
    assert "secreto" not in str(e.value) and "OSError" in str(e.value)


async def test_falha_ao_embeddar_vira_semantic_unavailable(fake_fastembed, monkeypatch):
    def boom(self, texts):
        raise RuntimeError("detalhe interno")
        yield  # pragma: no cover

    monkeypatch.setattr(FakeTextEmbedding, "embed", boom)
    with pytest.raises(SemanticUnavailable) as e:
        await FastEmbedder("m", cache_dir=str(fake_fastembed)).embed(["a"])
    assert "detalhe" not in str(e.value)


def test_modelo_padrao_e_multilingue():
    assert "multilingual" in DEFAULT_MODEL
