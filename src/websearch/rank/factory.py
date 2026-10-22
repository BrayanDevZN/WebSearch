from __future__ import annotations

from websearch.config import ConfigError, Settings
from websearch.rank.hybrid import Ranker


def build_ranker(settings: Settings) -> Ranker:
    """BM25 puro, ou híbrido se `SEMANTIC_ENABLED=true` (e a dependência opcional estiver instalada)."""
    if not settings.semantic.enabled:
        return Ranker()
    try:
        import fastembed  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        raise ConfigError(["SEMANTIC_ENABLED=true exige a dependência opcional: pip install 'websearch[semantic]'."]) from None
    from websearch.rank.semantic import FastEmbedder

    return Ranker(FastEmbedder(settings.semantic.model, cache_dir=settings.semantic.cache_dir))
