import pytest

KEY = "k" * 40


@pytest.fixture
def base_env():
    """Ambiente mínimo válido (LLM local, sem nenhuma chave de LLM)."""
    return {"API_SEARCH_KEY": KEY, "LLM_PROVIDER": "local"}
