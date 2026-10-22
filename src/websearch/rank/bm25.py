"""BM25 (Okapi) para ranquear trechos contra a consulta. Sem dependências externas.

O corpus do IDF é o conjunto de trechos passado a `BM25`, normalmente todos os trechos de todas as
páginas de uma busca, de modo que termos raros na busca pesam mais.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Iterable, Sequence

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")

STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the this to was were will with
    o os as um uma uns umas de do da dos das em no na nos nas por para com sem e ou que se é são foi ser
    ao aos à às como mais mas seu sua seus suas ele ela eles elas isso isto esse essa este esta
    el la los las un una y en es del con por para que""".split()
)


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Minúsculas, sem acentos; CJK vira um token por caractere; remove palavras vazias."""
    tokens: list[str] = []
    for word in _WORD.findall(strip_accents((text or "").lower())):
        if _CJK.search(word):
            tokens.extend(_CJK.findall(word))
            continue
        if not keep_stopwords and (word in STOPWORDS or len(word) < 2 and not word.isdigit()):
            continue
        tokens.append(word)
    return tokens


class BM25:
    def __init__(self, documents: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self._tf = [Counter(tokenize(d)) for d in documents]
        self._len = [sum(tf.values()) for tf in self._tf]
        self.n = len(documents)
        self._avg = (sum(self._len) / self.n) if self.n else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        # IDF "plus 1" (sempre positivo), variante usada por Lucene.
        self._idf = {t: math.log(1 + (self.n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = list(dict.fromkeys(tokenize(query)))
        out = [0.0] * self.n
        if not terms or not self.n:
            return out
        for i, tf in enumerate(self._tf):
            norm = self.k1 * (1 - self.b + self.b * (self._len[i] / self._avg if self._avg else 0))
            total = 0.0
            for term in terms:
                f = tf.get(term)
                if f:
                    total += self._idf[term] * (f * (self.k1 + 1)) / (f + norm)
            out[i] = total
        return out


def normalize_scores(scores: Iterable[float]) -> list[float]:
    """Escala para [0, 1] dividindo pelo maior valor (tudo zero continua zero)."""
    values = list(scores)
    top = max(values, default=0.0)
    return [v / top if top > 0 else 0.0 for v in values]
