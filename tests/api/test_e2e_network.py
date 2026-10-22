"""Ponta a ponta com rede real (rodar com: pytest -m network)."""
import pytest
from fastapi.testclient import TestClient

from websearch.api.app import create_app
from websearch.config import load_settings

pytestmark = pytest.mark.network
KEY = "k" * 40
AUTH = {"Authorization": f"Bearer {KEY}"}


def test_search_extract_crawl_reais(direct_egress):
    with TestClient(create_app(load_settings({"API_SEARCH_KEY": KEY}))) as c:
        ex = c.post("/v1/extract", headers=AUTH, json={"urls": "https://example.com/"}).json()
        assert "documentation examples" in ex["results"][0]["raw_content"]
        cr = c.post("/v1/crawl", headers=AUTH, json={"url": "https://example.com/", "max_depth": 1}).json()
        assert cr["results"]
        s = c.post("/v1/search", headers=AUTH, json={
            "query": "python programming language", "search_depth": "advanced", "max_results": 3})
        assert s.status_code == 200 and s.json()["results"]
        assert all(r["content"] for r in s.json()["results"])
