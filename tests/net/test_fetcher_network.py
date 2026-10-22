"""Testes com rede real (rodar com: pytest -m network)."""
import pytest

from websearch.net.fetcher import FetchError, Fetcher

pytestmark = pytest.mark.network


async def test_https_real_com_pin_de_ip_valida_certificado():
    async with Fetcher() as f:
        r = await f.fetch("https://example.com/")
    assert r.status == 200 and "Example Domain" in r.text


async def test_metadata_da_nuvem_real_e_bloqueada():
    async with Fetcher() as f:
        with pytest.raises(FetchError) as e:
            await f.fetch("http://169.254.169.254/latest/meta-data/")
    assert e.value.reason == "unsafe_url"
