"""Ranking de trechos: BM25, semântico ou híbrido, com queda segura para BM25.

Os scores devolvidos ficam em [0, 1]. No BM25 puro são relativos (o melhor trecho do conjunto vale 1);
no semântico são o cosseno (negativos viram 0); no híbrido, `w * BM25_relativo + (1 - w) * cosseno`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal, Sequence

from websearch.rank.bm25 import normalize_scores
from websearch.rank.select import score_pages as bm25_score_pages
from websearch.rank.semantic import Embedder, SemanticUnavailable, semantic_scores

log = logging.getLogger("websearch.rank")

Mode = Literal["bm25", "semantic", "hybrid"]
MODES = ("bm25", "semantic", "hybrid")
DEFAULT_WEIGHT = 0.2            # peso do BM25 no híbrido (o do semântico é 1 - peso); medido em 15 consultas reais
DEFAULT_MAX_EMBED = 400         # teto de trechos embedados por chamada


@dataclass
class Ranked:
    scores: list[list[float]]   # um valor por trecho, na ordem de entrada
    mode: Mode                  # modo efetivamente usado
    warning: str | None = None


class Ranker:
    def __init__(
        self,
        embedder: Embedder | None = None,
        *,
        default_mode: Mode | None = None,
        bm25_weight: float = DEFAULT_WEIGHT,
        timeout: float = 8.0,
        max_embed: int = DEFAULT_MAX_EMBED,
    ):
        if not 0.0 <= bm25_weight <= 1.0:
            raise ValueError("bm25_weight deve estar entre 0 e 1.")
        self.embedder = embedder
        self.default_mode: Mode = default_mode or ("hybrid" if embedder is not None else "bm25")
        self._w = bm25_weight
        self._timeout = timeout
        self._max_embed = max(1, max_embed)

    @property
    def semantic_available(self) -> bool:
        return self.embedder is not None

    def resolve(self, requested: str | None) -> tuple[Mode, str | None]:
        """Modo pedido → modo possível (+ aviso se o pedido explícito não pôde ser atendido)."""
        if requested is None:
            return self.default_mode, None
        if requested not in MODES:
            raise ValueError(f"ranking deve ser um de: {', '.join(MODES)}.")
        if requested != "bm25" and self.embedder is None:
            return "bm25", f"ranking '{requested}' indisponível (busca semântica desligada); usado BM25."
        return requested, None  # type: ignore[return-value]

    async def score_pages(self, query: str, pages: Sequence[Sequence[str]], requested: str | None = None) -> Ranked:
        mode, warning = self.resolve(requested)
        bm25 = _split(normalize_scores_flat(bm25_score_pages(query, pages)), pages)
        if mode == "bm25":
            return Ranked(bm25, "bm25", warning)
        try:
            sem = await asyncio.wait_for(self._semantic(query, pages, bm25), self._timeout)
        except asyncio.TimeoutError:
            return Ranked(bm25, "bm25", "ranking semântico demorou demais (modelo carregando?); usado BM25.")
        except SemanticUnavailable:
            return Ranked(bm25, "bm25", "ranking semântico indisponível no momento; usado BM25.")
        except Exception as exc:  # o modelo nunca derruba uma busca, nem com bug na biblioteca
            log.warning("falha inesperada no ranking semântico: %s", type(exc).__name__)
            return Ranked(bm25, "bm25", "ranking semântico falhou; usado BM25.")
        if mode == "semantic":
            return Ranked(sem, "semantic", warning)
        mixed = [[self._w * b + (1 - self._w) * s for b, s in zip(bp, sp)] for bp, sp in zip(bm25, sem)]
        return Ranked(mixed, "hybrid", warning)

    async def score_texts(self, query: str, texts: Sequence[str], requested: str | None = None) -> Ranked:
        """Pontua textos independentes (ex.: título + snippet de cada resultado)."""
        ranked = await self.score_pages(query, [list(texts)], requested)
        return Ranked(ranked.scores, ranked.mode, ranked.warning)

    # ---------------------------------------------------------------- internos --
    async def _semantic(self, query: str, pages: Sequence[Sequence[str]], bm25: list[list[float]]) -> list[list[float]]:
        assert self.embedder is not None
        picks = _shortlist(pages, bm25, self._max_embed)
        flat = [pages[p][i] for p, i in picks]
        values = await semantic_scores(self.embedder, query, flat)
        out = [[0.0] * len(page) for page in pages]
        for (p, i), value in zip(picks, values):
            out[p][i] = min(1.0, max(0.0, value))
        return out


def normalize_scores_flat(per_page: Sequence[Sequence[float]]) -> list[float]:
    """Todos os trechos de todas as páginas juntos, normalizados pelo maior (comparáveis entre páginas)."""
    flat = [x for page in per_page for x in page]
    return normalize_scores(flat)


def _split(flat: Sequence[float], pages: Sequence[Sequence[str]]) -> list[list[float]]:
    out, position = [], 0
    for page in pages:
        out.append(list(flat[position:position + len(page)]))
        position += len(page)
    return out


def _shortlist(pages: Sequence[Sequence[str]], bm25: list[list[float]], cap: int) -> list[tuple[int, int]]:
    """Quais trechos embedar. Dentro do teto: todos. Acima: por página, o 1º + os melhores no BM25."""
    total = sum(len(p) for p in pages)
    if total <= cap:
        return [(p, i) for p, page in enumerate(pages) for i in range(len(page))]
    per_page = max(1, cap // max(1, sum(1 for p in pages if p)))
    picks: list[tuple[int, int]] = []
    for p, page in enumerate(pages):
        if not page:
            continue
        order = sorted(range(len(page)), key=lambda i: (-bm25[p][i], i))
        chosen = {0, *order[: per_page - 1]} if per_page > 1 else {order[0]}
        picks.extend((p, i) for i in sorted(chosen))
    return picks[:cap]
