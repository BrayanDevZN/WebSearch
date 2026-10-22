import pytest

from websearch.rank.bm25 import BM25, normalize_scores, tokenize
from websearch.rank.select import (
    SEPARATOR, join_chunks, score_pages, select_chunks,
)


def test_tokenize_basico():
    assert tokenize("O Kernel Linux 7.1, lançado!") == ["kernel", "linux", "7", "1", "lancado"]


def test_tokenize_remove_acentos_e_stopwords():
    assert tokenize("a configuração do sistema") == ["configuracao", "sistema"]
    assert tokenize("the and of") == []
    assert tokenize("the and", keep_stopwords=True) == ["the", "and"]


def test_tokenize_cjk_por_caractere():
    assert tokenize("机器学习") == ["机", "器", "学", "习"]


def test_tokenize_vazio_e_nulo():
    assert tokenize("") == [] and tokenize(None) == []  # type: ignore[arg-type]


def test_bm25_ordena_por_relevancia():
    docs = [
        "receita de bolo de chocolate com cobertura",
        "o kernel linux foi lançado com novo driver ntfs",
        "futebol brasileiro resultados da rodada",
    ]
    s = BM25(docs).scores("novo driver ntfs linux")
    assert s.index(max(s)) == 1 and s[0] == 0 and s[2] == 0


def test_bm25_termo_raro_pesa_mais_que_comum():
    docs = ["python python python linguagem", "python rustacean", "python java"]
    # "rustacean" aparece só no doc 1, "python" em todos
    s = BM25(docs).scores("python rustacean")
    assert s[1] > s[0]


def test_bm25_saturacao_de_frequencia():
    docs = ["gato " + "gato " * 2 + "casa", "gato " * 50 + "casa", "cachorro"]
    s = BM25(docs).scores("gato")
    assert s[1] < 3 * s[0]            # repetir 50x não dá 50x de score


def test_bm25_normaliza_tamanho_do_documento():
    short = "linux kernel"
    long_ = "linux kernel " + "enchimento irrelevante " * 100
    s = BM25([short, long_, "outra coisa"]).scores("linux kernel")
    assert s[0] > s[1]


def test_bm25_casos_vazios():
    assert BM25([]).scores("x") == []
    assert BM25(["a b c"]).scores("") == [0.0]
    assert BM25(["texto"]).scores("inexistente") == [0.0]


def test_bm25_consulta_com_termos_repetidos_conta_uma_vez():
    m = BM25(["linux kernel", "outro"])
    assert m.scores("linux linux linux") == m.scores("linux")


def test_normalize_scores():
    assert normalize_scores([2, 4, 1]) == [0.5, 1.0, 0.25]
    assert normalize_scores([0, 0]) == [0.0, 0.0] and normalize_scores([]) == []


# ---------- seleção ------------------------------------------------------------------
CHUNKS = [
    "Introdução geral sobre sistemas operacionais.",
    "O kernel Linux 7.1 traz um novo driver NTFS reescrito.",
    "Receita de pão caseiro com fermento natural.",
    "O desempenho do driver NTFS melhorou em leitura sequencial.",
    "Conclusão e agradecimentos.",
]


def test_select_devolve_melhores_na_ordem_da_pagina():
    sel = select_chunks("driver NTFS linux", CHUNKS, 2)
    assert [c.index for c in sel] == [1, 3]
    assert all(c.score > 0 for c in sel)


def test_select_sem_correspondencia_devolve_inicio():
    sel = select_chunks("xyzzy quux", CHUNKS, 2)
    assert [c.index for c in sel] == [0, 1]


def test_select_n_maior_que_total_e_n_zero():
    assert len(select_chunks("ntfs", CHUNKS, 99)) == len(CHUNKS)
    assert select_chunks("ntfs", CHUNKS, 0) == [] and select_chunks("ntfs", [], 3) == []


def test_select_valida_tamanho_dos_scores():
    with pytest.raises(ValueError):
        select_chunks("a", ["x", "y"], 1, scores=[1.0])


def test_score_pages_usa_corpus_unico_e_mantem_formato():
    pages = [CHUNKS[:3], CHUNKS[3:], []]
    scores = score_pages("driver ntfs", pages)
    assert [len(s) for s in scores] == [3, 2, 0]
    assert scores[0][1] > 0 and scores[1][0] > 0 and scores[0][2] == 0


def test_score_pages_vazio():
    assert score_pages("x", []) == [] and score_pages("x", [[], []]) == [[], []]


def test_select_com_scores_externos():
    scores = score_pages("driver ntfs", [CHUNKS])[0]
    assert select_chunks("driver ntfs", CHUNKS, 1, scores=scores)[0].index in (1, 3)


def test_join_chunks_vizinhos_e_nao_vizinhos():
    sel = select_chunks("driver ntfs", CHUNKS, 2)           # índices 1 e 3 (não vizinhos)
    assert join_chunks(sel) == f"{CHUNKS[1]}{SEPARATOR}{CHUNKS[3]}"
    pagina = CHUNKS[:2] + CHUNKS[3:4]                      # trechos 1 e 2 da página são vizinhos
    adj = select_chunks("kernel linux driver ntfs", pagina, 2)
    assert [c.index for c in adj] == [1, 2]
    assert join_chunks(adj) == f"{CHUNKS[1]}\n\n{CHUNKS[3]}"


def test_join_chunks_vazio():
    assert join_chunks([]) == ""
