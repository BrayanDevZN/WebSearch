import pytest

from websearch.net.urlsafe import (
    UnsafeURLError, check_url, is_public_ip, normalize_url,
)


def fake_dns(mapping):
    async def resolver(host, port):
        if host not in mapping:
            raise OSError("nxdomain")
        return mapping[host]
    return resolver


PUBLIC = fake_dns({"example.com": ["93.184.216.34"], "www.example.com": ["93.184.216.34"]})


# ---------- is_public_ip ----------------------------------------------------------
@pytest.mark.parametrize("ip", [
    "8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111",
])
def test_ips_publicos(ip):
    assert is_public_ip(ip)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1",
    "169.254.169.254",            # metadata de nuvem
    "100.64.0.1",                 # CGNAT
    "0.0.0.0", "255.255.255.255", "224.0.0.1", "240.0.0.1",
    "::1", "fe80::1", "fc00::1", "fd12:3456::1", "::",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",  # IPv4 mapeado
    "2002:7f00:0001::",           # 6to4 embutindo 127.0.0.1
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
    "fe80::1%eth0", "não-é-ip", "",
])
def test_ips_nao_publicos(ip):
    assert not is_public_ip(ip)


# ---------- normalize_url ---------------------------------------------------------
def test_normaliza_basico():
    url, scheme, host, port = normalize_url("HTTPS://WWW.Example.COM:443/a/b?x=1#frag")
    assert url == "https://www.example.com/a/b?x=1"
    assert (scheme, host, port) == ("https", "www.example.com", 443)


def test_sem_esquema_assume_https():
    assert normalize_url("example.com/x")[0] == "https://example.com/x"


def test_caminho_vazio_vira_barra():
    assert normalize_url("http://example.com")[0] == "http://example.com/"


def test_remove_ponto_final_do_host():
    assert normalize_url("https://example.com./")[2] == "example.com"


def test_idn_vira_punycode():
    assert normalize_url("https://bücher.example/")[2] == "xn--bcher-kva.example"


def test_porta_padrao_removida_e_nao_padrao_bloqueada():
    assert normalize_url("http://example.com:80/")[0] == "http://example.com/"
    with pytest.raises(UnsafeURLError) as e:
        normalize_url("http://example.com:8080/")
    assert e.value.reason == "port"


def test_porta_extra_permitida_quando_configurada():
    assert normalize_url("http://example.com:8080/", allowed_ports=(80, 443, 8080))[3] == 8080


@pytest.mark.parametrize("raw,reason", [
    ("", "empty"), ("   ", "empty"),
    ("ftp://example.com/", "scheme"), ("file:///etc/passwd", "scheme"),
    ("javascript:alert(1)", "scheme"), ("gopher://example.com", "scheme"),
    ("data:text/html,<h1>x</h1>", "scheme"), ("//example.com/x", "scheme"),
    ("https://user:pass@example.com/", "userinfo"),
    ("https://evil.com@127.0.0.1/", "userinfo"),
    ("https://example.com/a b", "control_chars"),
    ("https://example.com/\r\nHost: x", "control_chars"),
    ("https:///path", "no_host"),
    ("https://example.com:abc/", "bad_port"),
    ("http://localhost/", "blocked_host"), ("http://foo.local/", "blocked_host"),
    ("http://metadata.google.internal/", "blocked_host"), ("http://x.internal/", "blocked_host"),
    ("http://2130706433/", "numeric_host"), ("http://0x7f.1/", "numeric_host"),
    ("http://0177.0.0.1/", "numeric_host"),
    ("https://" + "a" * 3000 + ".com", "too_long"),
])
def test_urls_rejeitadas(raw, reason):
    with pytest.raises(UnsafeURLError) as e:
        normalize_url(raw)
    assert e.value.reason == reason


def test_tipo_invalido():
    with pytest.raises(UnsafeURLError):
        normalize_url(None)  # type: ignore[arg-type]


# ---------- check_url (com DNS) ---------------------------------------------------
async def test_check_url_publica():
    safe = await check_url("https://example.com/p", resolver=PUBLIC)
    assert safe.url == "https://example.com/p"
    assert safe.ips == ("93.184.216.34",)
    assert safe.port == 443


async def test_dns_para_ip_privado_e_bloqueado():
    r = fake_dns({"evil.com": ["10.0.0.5"]})
    with pytest.raises(UnsafeURLError) as e:
        await check_url("https://evil.com/", resolver=r)
    assert e.value.reason == "private_ip"


async def test_basta_um_ip_privado_entre_varios():
    r = fake_dns({"mix.com": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(UnsafeURLError) as e:
        await check_url("https://mix.com/", resolver=r)
    assert e.value.reason == "private_ip"


async def test_dns_para_metadata_da_nuvem_bloqueado():
    r = fake_dns({"rebind.com": ["169.254.169.254"]})
    with pytest.raises(UnsafeURLError):
        await check_url("http://rebind.com/latest/meta-data", resolver=r)


async def test_ip_literal_privado_bloqueado_sem_dns():
    for raw in ("http://127.0.0.1/", "http://[::1]/", "http://10.1.2.3/", "http://169.254.169.254/",
                "http://[::ffff:127.0.0.1]/"):
        with pytest.raises(UnsafeURLError) as e:
            await check_url(raw, resolver=PUBLIC)
        assert e.value.reason == "private_ip", raw


async def test_ip_literal_publico_permitido():
    safe = await check_url("http://8.8.8.8/", resolver=PUBLIC)
    assert safe.ips == ("8.8.8.8",)


async def test_host_inexistente():
    with pytest.raises(UnsafeURLError) as e:
        await check_url("https://nao-existe.example/", resolver=PUBLIC)
    assert e.value.reason == "dns"


async def test_resolver_sem_enderecos():
    with pytest.raises(UnsafeURLError) as e:
        await check_url("https://example.com/", resolver=fake_dns({"example.com": []}))
    assert e.value.reason == "dns"


async def test_mensagens_nao_vazam_ips_internos():
    r = fake_dns({"evil.com": ["10.9.8.7"]})
    with pytest.raises(UnsafeURLError) as e:
        await check_url("https://evil.com/", resolver=r)
    assert "10.9.8.7" not in str(e.value)
