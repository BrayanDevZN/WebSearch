import pytest

from websearch.extract.chunk import split_chunks


def test_texto_vazio():
    assert split_chunks("") == [] and split_chunks("   \n\n  ") == []


def test_junta_paragrafos_curtos():
    text = "Primeiro paragrafo.\n\nSegundo paragrafo.\n\nTerceiro."
    assert split_chunks(text, target=500) == ["Primeiro paragrafo.\n\nSegundo paragrafo.\n\nTerceiro."]


def test_quebra_quando_passa_do_alvo():
    paras = [("palavra " * 40).strip() for _ in range(5)]   # ~320 chars cada
    chunks = split_chunks("\n\n".join(paras), target=500, max_chars=800)
    assert len(chunks) == 5 and all(len(c) <= 800 for c in chunks)


def test_paragrafo_gigante_quebra_por_frases():
    sentence = "Esta é uma frase de tamanho razoável para o teste."
    para = " ".join([sentence] * 40)
    chunks = split_chunks(para, target=300, max_chars=400)
    assert len(chunks) > 3
    assert all(len(c) <= 400 for c in chunks)
    assert all(c.endswith(".") for c in chunks)          # nenhuma frase cortada no meio
    assert " ".join(chunks).split() == para.split()      # nada se perde


def test_frase_gigante_sem_pontuacao_quebra_por_palavras():
    text = " ".join(f"w{i}" for i in range(2000))
    chunks = split_chunks(text, target=300, max_chars=400)
    assert all(len(c) <= 400 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_palavra_unica_enorme_e_cortada():
    chunks = split_chunks("x" * 2000, target=300, max_chars=400)
    assert all(len(c) <= 400 for c in chunks) and "".join(chunks) == "x" * 2000


def test_titulo_gruda_no_texto_seguinte():
    body = ("conteudo " * 55).strip()
    text = f"Intro curta.\n\n{body}\n\n## Secao importante\n\n{body}"
    chunks = split_chunks(text, target=500, max_chars=800)
    heading_chunk = [c for c in chunks if "## Secao importante" in c][0]
    assert heading_chunk.startswith("## Secao importante") and "conteudo" in heading_chunk


def test_parametros_invalidos():
    with pytest.raises(ValueError):
        split_chunks("a", target=0)
    with pytest.raises(ValueError):
        split_chunks("a", target=500, max_chars=100)


def test_preserva_conteudo_total():
    text = "\n\n".join(f"Paragrafo {i}. " + "texto " * 30 for i in range(30))
    assert " ".join(split_chunks(text).__iter__()).split() == text.split()
