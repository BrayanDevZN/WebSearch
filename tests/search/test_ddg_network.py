import pytest

from websearch.search.base import SearchQuery
from websearch.search.ddg import DDGSProvider
from websearch.search.router import SearchRouter

pytestmark = pytest.mark.network


async def test_ddgs_real():
    out = await SearchRouter([DDGSProvider()]).search(SearchQuery("python programming language", max_results=5))
    assert out.provider == "ddgs" and 1 <= len(out.hits) <= 5
    assert all(h.url.startswith("http") and h.title for h in out.hits)


async def test_ddgs_real_com_filtro_de_dominio():
    out = await SearchRouter([DDGSProvider()]).search(
        SearchQuery("python", max_results=3, include_domains=["python.org"]))
    assert out.hits and all("python.org" in h.url for h in out.hits)
