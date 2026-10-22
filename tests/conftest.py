import pytest

KEY = "k" * 40


@pytest.fixture
def base_env():
    """Ambiente mínimo válido."""
    return {"API_SEARCH_KEY": KEY}
