import pytest

KEY = "k" * 40


@pytest.fixture
def base_env():
    """Ambiente mínimo válido."""
    return {"API_SEARCH_KEY": KEY}


@pytest.fixture(scope="session")
def direct_egress():
    """Pula o teste se esta máquina não consegue abrir conexão TCP direta (ex.: sandbox só com proxy).

    O `Fetcher` conecta direto ao IP validado e ignora proxies do ambiente de propósito.
    """
    import socket

    try:
        socket.create_connection(("example.com", 443), timeout=3).close()
    except OSError:
        pytest.skip("sem saída direta à internet (apenas via proxy): o Fetcher não usa proxy")
