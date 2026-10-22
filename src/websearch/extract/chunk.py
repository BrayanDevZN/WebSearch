"""Divisão de texto em trechos (chunks) para ranqueamento e para montar o `content` dos resultados.

Regras:
- respeita parágrafos; junta parágrafos curtos até ~`target` caracteres;
- parágrafos maiores que `max_chars` são quebrados por frases e, em último caso, por palavras;
- um título markdown (`# ...`) nunca fica sozinho no fim de um trecho: gruda no texto seguinte.
"""

from __future__ import annotations

import re

DEFAULT_TARGET = 500
DEFAULT_MAX = 800
_HEADING = re.compile(r"^#{1,6}\s+\S")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[\"'“(\[]?[A-ZÀ-ÖØ-Þ0-9])")


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > max_chars:  # frase gigante: quebra por palavras
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            head, sentence = sentence[:cut].strip(), sentence[cut:].strip()
            if current:
                pieces.append(current)
                current = ""
            pieces.append(head)
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def split_chunks(text: str, target: int = DEFAULT_TARGET, max_chars: int = DEFAULT_MAX) -> list[str]:
    """Divide `text` em trechos de ~`target` caracteres (nunca acima de `max_chars`, salvo títulos)."""
    if target <= 0 or max_chars < target:
        raise ValueError("target deve ser > 0 e max_chars >= target")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(_split_long(paragraph, max_chars))

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif _HEADING.match(current) and "\n" not in current:
            current = f"{current}\n\n{unit}"       # título gruda no que vem depois
        elif len(current) + 2 + len(unit) <= target:
            current = f"{current}\n\n{unit}"
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks
