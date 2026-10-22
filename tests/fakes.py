"""Dublês compartilhados pelos testes."""

import numpy as np


class FakeEmbedder:
    """Vetores determinísticos: um eixo por 'conceito' (sinônimos compartilham o eixo)."""

    name = "fake"
    CONCEPTS = [("carro", "automovel", "veiculo", "car"), ("bolo", "receita", "cake"), ("dor", "cabeca", "headache")]

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        rows = []
        for t in texts:
            words = t.lower().split()
            v = np.array([sum(w in group for w in words) for group in self.CONCEPTS] + [0.1], dtype="float32")
            rows.append(v / np.linalg.norm(v))
        return np.stack(rows)
