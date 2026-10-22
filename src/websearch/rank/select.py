"""Seleção dos melhores trechos de uma página para a consulta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from websearch.rank.bm25 import BM25

SEPARATOR = " [...] "


@dataclass(frozen=True)
class ScoredChunk:
    index: int       # posição do trecho na página
    text: str
    score: float


def score_pages(query: str, pages: Sequence[Sequence[str]]) -> list[list[float]]:
    """Pontua os trechos de várias páginas num **único** modelo BM25.

    Usar o conjunto inteiro como corpus dá IDF mais estável (termos raros na busca pesam mais) e
    deixa os scores comparáveis entre páginas.
    """
    flat = [c for page in pages for c in page]
    flat_scores = BM25(flat).scores(query) if flat else []
    out: list[list[float]] = []
    position = 0
    for page in pages:
        out.append(flat_scores[position:position + len(page)])
        position += len(page)
    return out


def select_chunks(
    query: str,
    chunks: Sequence[str],
    n: int,
    scores: Sequence[float] | None = None,
) -> list[ScoredChunk]:
    """Os `n` melhores trechos, devolvidos na ordem em que aparecem na página.

    `scores` (opcional) vem de `score_pages`. Sem nenhum termo em comum com a consulta, devolve os
    `n` primeiros trechos (início da página), que costumam ser o resumo.
    """
    if n <= 0 or not chunks:
        return []
    if scores is None:
        scores = BM25(list(chunks)).scores(query)
    if len(scores) != len(chunks):
        raise ValueError("scores e chunks devem ter o mesmo tamanho")
    ranked = [ScoredChunk(i, c, s) for i, (c, s) in enumerate(zip(chunks, scores))]
    best = sorted(ranked, key=lambda c: (-c.score, c.index))[:n]
    if all(c.score == 0 for c in best):
        best = ranked[:n]
    return sorted(best, key=lambda c: c.index)


def join_chunks(selected: Sequence[ScoredChunk]) -> str:
    """Junta trechos: vizinhos por linha em branco, não vizinhos por ` [...] `."""
    parts: list[str] = []
    previous: int | None = None
    for chunk in selected:
        if previous is not None:
            parts.append("\n\n" if chunk.index == previous + 1 else SEPARATOR)
        parts.append(chunk.text)
        previous = chunk.index
    return "".join(parts)
