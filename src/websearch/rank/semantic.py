"""Busca semântica: embeddings locais (fastembed/ONNX) e similaridade por cosseno.

Opcional: precisa de `pip install websearch[semantic]`. Sem a dependência (ou se o modelo não carregar),
`FastEmbedder.embed` levanta `SemanticUnavailable` e o chamador deve cair para BM25.

O modelo padrão é multilíngue (português e inglês) e estático (sem transformer): ~900x mais rápido que
um MiniLM na mesma CPU, com a mesma acurácia nos nossos testes de sinônimos e de troca de idioma.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from collections import OrderedDict
from typing import Any, Protocol, Sequence

DEFAULT_MODEL = "minishlab/potion-multilingual-128M"
ACCURATE_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "websearch", "models")
MAX_TEXT_CHARS = 2000


class SemanticUnavailable(RuntimeError):
    """Embeddings indisponíveis (dependência ausente, modelo não baixou...)."""


class Embedder(Protocol):
    name: str

    async def embed(self, texts: Sequence[str]) -> Any:
        """Matriz (n, d) de float32, **já normalizada** (norma 1)."""


def _np():
    try:
        import numpy
    except ImportError:
        raise SemanticUnavailable("numpy não está instalado (pip install websearch[semantic]).") from None
    return numpy


class FastEmbedder:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        cache_dir: str | None = None,
        cache_size: int = 8192,
        threads: int | None = None,
    ):
        self.name = model
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._threads = threads
        self._model: Any = None
        self._load_lock = threading.Lock()          # a carga roda em thread; só uma de cada vez
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._cache_size = max(0, cache_size)

    # ------------------------------------------------------------------ carga --
    def _load(self) -> Any:
        with self._load_lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError:
                    raise SemanticUnavailable(
                        "fastembed não está instalado (pip install websearch[semantic])."
                    ) from None
                try:
                    os.makedirs(self._cache_dir, exist_ok=True)
                    self._model = TextEmbedding(self.name, cache_dir=self._cache_dir, threads=self._threads)
                except Exception as exc:
                    raise SemanticUnavailable(f"não foi possível carregar o modelo {self.name} ({type(exc).__name__}).") from None
            return self._model

    async def warmup(self) -> None:
        """Carrega o modelo (e baixa, na primeira vez) fora do caminho de uma requisição."""
        await asyncio.to_thread(self._load)

    # ------------------------------------------------------------------ vetores --
    async def embed(self, texts: Sequence[str]):
        np = _np()
        if not texts:
            return np.zeros((0, 0), dtype="float32")
        return await asyncio.to_thread(self._embed_sync, list(texts))

    def _embed_sync(self, texts: list[str]):
        np = _np()
        model = self._load()
        keys = [hashlib.blake2b(t[:MAX_TEXT_CHARS].encode("utf-8", "ignore"), digest_size=16).hexdigest() for t in texts]
        missing: dict[str, str] = {}
        for key, text in zip(keys, texts):
            if key not in self._cache and key not in missing:
                missing[key] = text[:MAX_TEXT_CHARS] or " "
        if missing:
            try:
                vectors = np.array(list(model.embed(list(missing.values()))), dtype="float32")
            except Exception as exc:
                raise SemanticUnavailable(f"falha ao gerar embeddings ({type(exc).__name__}).") from None
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            vectors = vectors / np.where(norms == 0, 1.0, norms)
            for key, vector in zip(missing, vectors):
                self._cache[key] = vector
        out = np.stack([self._cache[k] for k in keys])
        for key in keys:                                # LRU: usados recentemente ficam
            if key in self._cache:
                self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return out


# ---------------------------------------------------------------------- pontuação --
async def semantic_scores(embedder: Embedder, query: str, chunks: Sequence[str]) -> list[float]:
    """Similaridade de cosseno (-1..1) entre a consulta e cada trecho, na ordem de `chunks`."""
    if not chunks:
        return []
    np = _np()
    matrix = await embedder.embed([query, *chunks])
    if getattr(matrix, "shape", (0,))[0] != len(chunks) + 1:
        raise SemanticUnavailable("o embedder devolveu um número inesperado de vetores.")
    return [float(x) for x in matrix[1:] @ matrix[0]]
