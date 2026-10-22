import pytest

from websearch.extract.html import (
    clean_text, extract_from_fetch, extract_html, extract_pdf,
)
from websearch.net.fetcher import FetchResult

ARTICLE = """<!doctype html>
<html lang="pt-BR"><head>
<title>Linux 7.1 chega com novo driver NTFS | Blog Exemplo</title>
<meta name="description" content="Resumo das novidades do kernel.">
<meta property="og:title" content="Linux 7.1 chega com novo driver NTFS">
<meta property="article:published_time" content="2026-06-15T10:00:00Z">
<link rel="icon" href="/static/fav.png">
<script>window.tracking = "NAO_DEVE_APARECER_SCRIPT";</script>
<style>.x{color:red} /* NAO_DEVE_APARECER_CSS */</style>
</head><body>
<nav><a href="/home">Início</a> <a href="/menu">MENU_DE_NAVEGACAO</a></nav>
<header><div>CABECALHO_DO_SITE</div></header>
<main><article>
<h1>Linux 7.1 chega com novo driver NTFS</h1>
<p>Linus Torvalds anunciou oficialmente o lançamento do kernel Linux 7.1 neste domingo, trazendo um
driver NTFS totalmente reescrito, melhorias no escalonador e suporte a novos processadores.</p>
<p>O novo driver NTFS promete desempenho muito melhor em leitura e escrita sequencial, além de
suporte completo a atributos estendidos. Os desenvolvedores testaram a mudança por vários meses.</p>
<h2>Outras mudanças</h2>
<ul><li>Escalonador mais justo para cargas interativas</li><li>Suporte a novas GPUs</li></ul>
<p>Leia também a <a href="https://kernel.org/doc/changes?utm=1#topo">lista completa</a> e
<a href="/relacionado/2">outra matéria</a>.</p>
<img src="/img/kernel.png" alt="Pinguim do Linux"><img src="https://cdn.exemplo.com/b.jpg">
</article></main>
<aside>PUBLICIDADE_LATERAL compre agora</aside>
<footer>RODAPE_COPYRIGHT todos os direitos</footer>
<a href="javascript:void(0)">js</a><a href="mailto:a@b.com">mail</a><a href="#topo">ancora</a>
</body></html>"""

URL = "https://blog.exemplo.com/2026/linux-71"


def test_extrai_texto_principal_sem_ruido():
    p = extract_html(ARTICLE, URL)
    assert p.method == "trafilatura"
    assert "driver NTFS totalmente reescrito" in p.text
    assert "Escalonador mais justo" in p.text
    for ruido in ("NAO_DEVE_APARECER_SCRIPT", "NAO_DEVE_APARECER_CSS", "MENU_DE_NAVEGACAO",
                  "CABECALHO_DO_SITE", "PUBLICIDADE_LATERAL", "RODAPE_COPYRIGHT"):
        assert ruido not in p.text


def test_markdown_tem_titulos_e_listas_e_texto_nao():
    md = extract_html(ARTICLE, URL, "markdown").text
    assert "## Outras mudanças" in md and "- Escalonador" in md
    txt = extract_html(ARTICLE, URL, "text").text
    assert "##" not in txt and "Outras mudanças" in txt


def test_metadados():
    p = extract_html(ARTICLE, URL)
    assert p.title.startswith("Linux 7.1 chega com novo driver NTFS")
    assert p.published_date == "2026-06-15"
    assert p.language == "pt"
    assert "kernel" in p.description.lower()
    assert p.favicon == "https://blog.exemplo.com/static/fav.png"


def test_links_absolutos_sem_lixo_e_sem_fragmento():
    urls = [l.url for l in extract_html(ARTICLE, URL).links]
    assert "https://kernel.org/doc/changes?utm=1" in urls       # fragmento removido
    assert "https://blog.exemplo.com/relacionado/2" in urls     # relativo → absoluto
    assert not any(u.startswith(("javascript", "mailto")) for u in urls)
    assert not any(u.endswith("#topo") for u in urls)
    assert len(urls) == len(set(urls))


def test_texto_do_link():
    links = {l.url: l.text for l in extract_html(ARTICLE, URL).links}
    assert links["https://kernel.org/doc/changes?utm=1"] == "lista completa"


def test_imagens():
    imgs = {i.url: i.alt for i in extract_html(ARTICLE, URL).images}
    assert imgs["https://blog.exemplo.com/img/kernel.png"] == "Pinguim do Linux"
    assert "https://cdn.exemplo.com/b.jpg" in imgs


def test_favicon_padrao_quando_nao_declarado():
    p = extract_html("<html><body><p>" + "texto " * 40 + "</p></body></html>", "https://a.com/x")
    assert p.favicon == "https://a.com/favicon.ico"


def test_fallback_quando_trafilatura_nao_acha_nada():
    html = "<html><head><title>Curta</title></head><body><h1>Oi</h1><p>Pouco texto aqui.</p></body></html>"
    p = extract_html(html, "https://a.com/")
    assert "Pouco texto aqui." in p.text
    assert p.method in ("fallback", "trafilatura")
    assert p.title == "Curta"


def test_fallback_remove_scripts_e_menus():
    html = ("<html><body><nav>MENU</nav><script>var x='SCRIPT'</script>"
            "<p>Conteudo real da pagina.</p></body></html>")
    p = extract_html(html, "https://a.com/")
    assert "Conteudo real" in p.text and "SCRIPT" not in p.text and "MENU" not in p.text


@pytest.mark.parametrize("html", ["", "   ", None])
def test_html_vazio(html):
    p = extract_html(html, "https://a.com/")
    assert p.text == "" and p.method == "none"


def test_html_quebrado_nao_levanta():
    p = extract_html("<div><p>texto <b>sem fechar " + "palavra " * 30 + "<<<>>>", "https://a.com/")
    assert "palavra" in p.text


def test_caracteres_de_controle_removidos():
    assert clean_text("a\x1b[31mvermelho\x07\x00 b") == "a[31mvermelho b"
    p = extract_html("<html><body><p>" + "ola\x1b]0;titulo\x07 mundo " * 20 + "</p></body></html>", "https://a.com/")
    assert "\x1b" not in p.text and "\x07" not in p.text


def test_clean_text_normaliza_espacos():
    assert clean_text("a  \r\n\r\n\r\n\r\nb   \n") == "a\n\nb"


# ---------- tipos de conteúdo ---------------------------------------------------------
def fr(content_type, text=None, body=b"", url="https://a.com/x"):
    return FetchResult(url=url, requested_url=url, status=200, content_type=content_type,
                       body=body, text=text, truncated=False)


def test_texto_puro():
    p = extract_from_fetch(fr("text/plain", "Titulo da nota\n\n\n\nCorpo."))
    assert p.method == "plain" and p.title == "Titulo da nota" and p.text == "Titulo da nota\n\nCorpo."


def test_despacha_html():
    assert extract_from_fetch(fr("text/html", ARTICLE, url=URL)).method == "trafilatura"


def minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_pdf_com_texto():
    p = extract_pdf(minimal_pdf("Relatorio anual de resultados"), "https://a.com/docs/relatorio.pdf")
    assert p.method == "pdf" and "Relatorio anual de resultados" in p.text
    assert p.title == "relatorio.pdf"


def test_pdf_corrompido_nao_levanta():
    p = extract_pdf(b"isto nao e um pdf", "https://a.com/x.pdf")
    assert p.text == "" and p.method == "none"


def test_despacha_pdf():
    p = extract_from_fetch(fr("application/pdf", None, body=minimal_pdf("Ola PDF"), url="https://a.com/a.pdf"))
    assert "Ola PDF" in p.text


def test_base_href_e_respeitado_em_links_e_imagens():
    p = extract_html("<base href='https://cdn.com/x/'><body><article><a href='z'>z</a><img src='i.png'>"
                     "<p>" + "Texto principal do artigo com bastante conteúdo. " * 10 + "</p></article></body>",
                     "https://ex.com/a/b")
    assert [l.url for l in p.links] == ["https://cdn.com/x/z"]
    assert [i.url for i in p.images] == ["https://cdn.com/x/i.png"]


def test_base_href_invalido_cai_para_url_da_pagina():
    p = extract_html("<base href='javascript:x'><body><article><a href='z'>z</a><p>"
                     + "Texto principal do artigo com bastante conteúdo. " * 10 + "</p></article></body>",
                     "https://ex.com/a/b")
    assert [l.url for l in p.links] == ["https://ex.com/a/z"]
