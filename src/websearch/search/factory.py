from __future__ import annotations

from websearch.config import Settings
from websearch.search.base import SearchProvider
from websearch.search.ddg import DDGSProvider
from websearch.search.router import SearchRouter
from websearch.search.searxng import SearXNGProvider


def build_router(settings: Settings) -> SearchRouter:
    """Monta o roteador na ordem de `SEARCH_PROVIDERS`."""
    providers: list[SearchProvider] = []
    for name in settings.search_providers:
        if name == "ddgs":
            providers.append(DDGSProvider())
        elif name == "searxng":
            assert settings.searxng_url  # garantido por load_settings
            providers.append(SearXNGProvider(settings.searxng_url))
    return SearchRouter(providers)
