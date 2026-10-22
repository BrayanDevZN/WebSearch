import asyncio
import gzip

import httpx
import pytest

from websearch.config import FetchSettings
from websearch.net.fetcher import FetchError, Fetcher, decode_body

PUB = "93.184.216.34"


def dns(mapping=None):
    table = {"example.com": [PUB], "other.com": ["8.8.4.4"], "www.example.com": [PUB]}
    table.update(mapping or {})

    async def resolver(host, port):
        if host not in table:
            raise OSError("nxdomain")
        return table[host]
    return resolver


def make(handler, *, settings=None, resolver=None, pin_ip=True):
    return Fetcher(
        settings or FetchSettings(timeout_seconds=2),
        resolver=resolver or dns(),
        transport=httpx.MockTransport(handler),
        pin_ip=pin_ip,
    )


def html(body="<html><body>oi</body></html>", status=200, ctype="text/html; charset=utf-8", **headers):
    return httpx.Response(status, content=body.encode() if isinstance(body, str) else body,
                          headers={"content-type": ctype, **headers})


async def test_fetch_ok_e_pin_de_ip():
    seen = {}

    def handler(request):
        seen["host_url"] = request.url.host
        seen["host_header"] = request.headers["host"]
        seen["ua"] = request.headers["user-agent"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return html()

    async with make(handler) as f:
        r = await f.fetch("https://example.com/p?q=1")
    assert r.status == 200 and r.text == "<html><body>oi</body></html>"
    assert r.url == "https://example.com/p?q=1" and r.content_type == "text/html"
    assert seen["host_url"] == PUB            # conectou no IP validado
    assert seen["host_header"] == "example.com"  # mas com o Host original
    assert seen["sni"] == "example.com"
    assert "WebSearchBot" in seen["ua"]


async def test_sem_pin_usa_url_original():
    def handler(request):
        assert request.url.host == "example.com"
        return html()
    async with make(handler, pin_ip=False) as f:
        assert (await f.fetch("https://example.com/")).status == 200


async def test_pin_ipv6_e_porta_nao_padrao():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        return html()

    r6 = dns({"v6.com": ["2606:4700:4700::1111"]})
    async with make(handler, resolver=r6) as f:
        await f.fetch("https://v6.com/x")
    assert seen["url"].startswith("https://[2606:4700:4700::1111]/x")

    f2 = Fetcher(FetchSettings(timeout_seconds=2), resolver=dns(), allowed_ports=(80, 443, 8080),
                 transport=httpx.MockTransport(handler))
    async with f2:
        await f2.fetch("http://example.com:8080/y")
    assert seen["host"] == "example.com:8080"
    assert seen["url"] == f"http://{PUB}:8080/y"


async def test_tenta_proximo_ip_quando_conexao_falha():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "1.1.1.1":
            raise httpx.ConnectError("recusado")
        return html()

    async with make(handler, resolver=dns({"multi.com": ["1.1.1.1", "8.8.8.8"]})) as f:
        r = await f.fetch("https://multi.com/")
    assert r.status == 200 and calls == ["1.1.1.1", "8.8.8.8"]


async def test_todos_os_ips_falham():
    def handler(request):
        raise httpx.ConnectError("x")
    async with make(handler) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/")
    assert e.value.reason == "connect"


# ---------- SSRF / redirecionamentos ----------------------------------------------
async def test_url_inicial_privada_bloqueada_sem_requisicao():
    def handler(request):
        raise AssertionError("não deveria conectar")
    async with make(handler) as f:
        for url in ("http://127.0.0.1/", "http://169.254.169.254/latest", "http://localhost/",
                    "ftp://example.com/", "https://example.com:9999/"):
            with pytest.raises(FetchError) as e:
                await f.fetch(url)
            assert e.value.reason == "unsafe_url", url


async def test_dns_privado_bloqueado():
    def handler(request):
        raise AssertionError("não deveria conectar")
    async with make(handler, resolver=dns({"evil.com": ["192.168.0.10"]})) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://evil.com/")
    assert e.value.reason == "unsafe_url"


async def test_redirect_para_interno_e_bloqueado():
    def handler(request):
        if request.headers["host"] == "example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
        raise AssertionError("seguiu para o IP interno")
    async with make(handler) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/")
    assert e.value.reason == "unsafe_url"


async def test_redirect_para_host_que_resolve_para_privado():
    def handler(request):
        if request.headers["host"] == "example.com":
            return httpx.Response(301, headers={"location": "https://rebind.com/"})
        raise AssertionError("conectou no host privado")
    async with make(handler, resolver=dns({"rebind.com": ["10.0.0.1"]})) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/")
    assert e.value.reason == "unsafe_url"


async def test_segue_redirects_relativos_e_absolutos():
    def handler(request):
        host, path = request.headers["host"], request.url.path
        if (host, path) == ("example.com", "/a"):
            return httpx.Response(301, headers={"location": "/b"})
        if (host, path) == ("example.com", "/b"):
            return httpx.Response(302, headers={"location": "https://other.com/c#frag"})
        assert (host, path) == ("other.com", "/c")
        return html("final")

    async with make(handler) as f:
        r = await f.fetch("https://example.com/a")
    assert r.text == "final" and r.url == "https://other.com/c"
    assert r.redirects == ["https://example.com/a", "https://example.com/b"]
    assert r.requested_url == "https://example.com/a"


async def test_limite_de_redirects():
    def handler(request):
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"location": f"/{n + 1}"})
    async with make(handler, settings=FetchSettings(max_redirects=3, timeout_seconds=2)) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/0")
    assert e.value.reason == "too_many_redirects"


async def test_laco_de_redirect():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://example.com/x"})
    async with make(handler) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/x")
    assert e.value.reason == "redirect_loop"


async def test_redirect_sem_location():
    async with make(lambda r: httpx.Response(302)) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/")
    assert e.value.reason == "bad_redirect"


# ---------- tamanho, tipo, status ---------------------------------------------------
async def test_trunca_no_limite_de_bytes():
    big = "a" * 5000
    async with make(lambda r: html(big), settings=FetchSettings(max_bytes=1024, timeout_seconds=2)) as f:
        r = await f.fetch("https://example.com/")
    assert r.truncated and len(r.body) == 1024


async def test_limite_vale_depois_de_descomprimir():
    bomb = gzip.compress(b"a" * 5_000_000)  # ~5KB comprimido, 5MB real
    assert len(bomb) < 10_000
    handler = lambda r: httpx.Response(200, content=bomb, headers={"content-type": "text/html", "content-encoding": "gzip"})
    async with make(handler, settings=FetchSettings(max_bytes=2048, timeout_seconds=2)) as f:
        r = await f.fetch("https://example.com/")
    assert r.truncated and len(r.body) == 2048


async def test_nao_trunca_quando_cabe():
    async with make(lambda r: html("curto")) as f:
        assert not (await f.fetch("https://example.com/")).truncated


@pytest.mark.parametrize("ctype", ["image/png", "application/octet-stream", "video/mp4", "application/zip"])
async def test_tipos_nao_suportados(ctype):
    async with make(lambda r: httpx.Response(200, content=b"\x00", headers={"content-type": ctype})) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/")
    assert e.value.reason == "unsupported_content_type"


async def test_pdf_vem_como_bytes_sem_texto():
    async with make(lambda r: httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})) as f:
        r = await f.fetch("https://example.com/a.pdf")
    assert r.content_type == "application/pdf" and r.text is None and r.body.startswith(b"%PDF")


async def test_texto_puro_aceito():
    async with make(lambda r: httpx.Response(200, content=b"ola", headers={"content-type": "text/plain"})) as f:
        assert (await f.fetch("https://example.com/")).text == "ola"


@pytest.mark.parametrize("status", [400, 403, 404, 410, 429, 500, 503])
async def test_status_de_erro(status):
    async with make(lambda r: html("x", status=status)) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/")
    assert e.value.reason == "http_status" and e.value.status == status


async def test_timeout_total():
    async def slow(request):
        await asyncio.sleep(1)
        return html()
    async with make(slow, settings=FetchSettings(timeout_seconds=0.1)) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/")
    assert e.value.reason == "timeout"


async def test_erro_de_rede_generico():
    def handler(request):
        raise httpx.ReadError("conexão caiu")
    async with make(handler) as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("https://example.com/")
    assert e.value.reason == "network"


# ---------- estado -----------------------------------------------------------------
async def test_cookies_nao_persistem_entre_requisicoes():
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        return html(**{"set-cookie": "sid=abc; Path=/"})

    async with make(handler) as f:
        await f.fetch("https://example.com/1")
        await f.fetch("https://example.com/2")
    assert seen == [None, None]


async def test_concorrencia_respeita_o_limite():
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return html()

    async with make(handler, settings=FetchSettings(concurrency=2, timeout_seconds=5)) as f:
        await asyncio.gather(*(f.fetch(f"https://example.com/{i}") for i in range(8)))
    assert peak == 2


# ---------- decode ------------------------------------------------------------------
def test_decode_header_charset():
    assert decode_body("ação".encode("iso-8859-1"), "text/html; charset=iso-8859-1") == "ação"


def test_decode_meta_charset():
    body = '<meta charset="windows-1252"><p>café</p>'.encode("cp1252")
    assert "café" in decode_body(body, "text/html")


def test_decode_utf8_por_padrao_e_fallback():
    assert decode_body("日本語".encode(), None) == "日本語"
    assert "é" in decode_body("é".encode("cp1252"), None)  # utf-8 falha → cp1252


def test_decode_charset_desconhecido_cai_no_proximo():
    assert decode_body("ok".encode(), "text/html; charset=bogus-xyz") == "ok"
