import pytest

from websearch.extract.html import extract_from_fetch
from websearch.net.fetcher import Fetcher

pytestmark = pytest.mark.network


async def test_pagina_real_wikipedia():
    async with Fetcher() as f:
        r = await f.fetch("https://en.wikipedia.org/wiki/Web_search_engine")
    p = extract_from_fetch(r)
    assert "search engine" in p.text.lower() and len(p.text) > 3000
    assert p.title and p.links and p.method == "trafilatura"
