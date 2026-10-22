import pytest

from websearch.config import ConfigError, load_settings


def problems(env):
    with pytest.raises(ConfigError) as e:
        load_settings(env)
    return e.value.problems


def test_minimo_valido(base_env):
    s = load_settings(base_env)
    assert s.search_providers == ("ddgs",)
    assert s.host == "127.0.0.1" and s.port == 8000 and not s.warnings


def test_api_search_key_obrigatoria(base_env):
    del base_env["API_SEARCH_KEY"]
    assert any("API_SEARCH_KEY" in p for p in problems(base_env))


def test_api_search_key_curta_demais(base_env):
    base_env["API_SEARCH_KEY"] = "curta"
    assert any("API_SEARCH_KEY" in p for p in problems(base_env))


def test_api_search_key_media_gera_aviso(base_env):
    base_env["API_SEARCH_KEY"] = "k" * 20
    assert any("API_SEARCH_KEY" in w for w in load_settings(base_env).warnings)


def test_chave_nunca_aparece_nos_erros_nem_no_repr(base_env):
    segredo = "segredo-super-secreto-123456789012345678"
    base_env.update(API_SEARCH_KEY=segredo)
    assert segredo not in repr(load_settings(base_env))
    base_env["PORT"] = "abc"
    assert segredo not in "".join(problems(base_env))


def test_busca_searxng_exige_url(base_env):
    base_env["SEARCH_PROVIDERS"] = "ddgs,searxng"
    assert any("SEARXNG_URL" in p for p in problems(base_env))
    base_env["SEARXNG_URL"] = "http://localhost:8888"
    assert load_settings(base_env).search_providers == ("ddgs", "searxng")


def test_busca_provedor_invalido(base_env):
    base_env["SEARCH_PROVIDERS"] = "google"
    assert any("SEARCH_PROVIDERS" in p for p in problems(base_env))


@pytest.mark.parametrize("name,value", [
    ("PORT", "abc"), ("PORT", "70000"), ("RATE_LIMIT_PER_MINUTE", "0"),
    ("FETCH_TIMEOUT_SECONDS", "-1"), ("FETCH_MAX_REDIRECTS", "99"), ("LOG_LEVEL", "VERBOSO"),
])
def test_valores_invalidos(base_env, name, value):
    base_env[name] = value
    assert any(name in p for p in problems(base_env))


def test_todos_os_problemas_sao_reportados_de_uma_vez():
    base = {"PORT": "abc", "SEARCH_PROVIDERS": "google"}
    assert len(problems(base)) == 3  # chave + porta + provedor


def test_host_publico_gera_aviso(base_env):
    base_env["HOST"] = "0.0.0.0"
    assert any("HOST" in w for w in load_settings(base_env).warnings)


def test_limites_customizados(base_env):
    base_env.update(FETCH_CONCURRENCY="2", FETCH_MAX_BYTES="5000")
    s = load_settings(base_env)
    assert (s.fetch.concurrency, s.fetch.max_bytes) == (2, 5000)
