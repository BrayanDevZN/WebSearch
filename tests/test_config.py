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


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_timeout_nao_finito_e_rejeitado(base_env, value):
    base_env["FETCH_TIMEOUT_SECONDS"] = value
    assert any("FETCH_TIMEOUT_SECONDS" in p for p in problems(base_env))


def test_provedores_duplicados_sao_unificados(base_env):
    base_env["SEARCH_PROVIDERS"] = " DDGS ,ddgs"
    assert load_settings(base_env).search_providers == ("ddgs",)


@pytest.mark.parametrize("url", ["ftp://x", "localhost:8888", "http://", "javascript:alert(1)"])
def test_searxng_url_invalida(base_env, url):
    base_env.update(SEARCH_PROVIDERS="searxng", SEARXNG_URL=url)
    assert any("SEARXNG_URL" in p for p in problems(base_env))


def test_ca_bundle_precisa_existir(base_env, tmp_path):
    base_env["FETCH_CA_BUNDLE"] = str(tmp_path / "nao-existe.pem")
    assert any("FETCH_CA_BUNDLE" in p for p in problems(base_env))
    arquivo = tmp_path / "ca.pem"
    arquivo.write_text("x")
    base_env["FETCH_CA_BUNDLE"] = str(arquivo)
    assert load_settings(base_env).fetch.ca_bundle == str(arquivo)


def test_semantico_desligado_por_padrao(base_env):
    s = load_settings(base_env).semantic
    assert s.enabled is False and "potion-multilingual" in s.model and s.cache_dir is None


@pytest.mark.parametrize("value,expected", [("true", True), ("TRUE", True), ("1", True), ("on", True),
                                             ("false", False), ("0", False), ("", False)])
def test_semantico_flag(base_env, value, expected):
    base_env["SEMANTIC_ENABLED"] = value
    assert load_settings(base_env).semantic.enabled is expected


def test_semantico_flag_invalida_e_modelo_customizado(base_env):
    base_env["SEMANTIC_ENABLED"] = "talvez"
    assert any("SEMANTIC_ENABLED" in p for p in problems(base_env))
    base_env.update(SEMANTIC_ENABLED="true", SEMANTIC_MODEL="outro/modelo", SEMANTIC_CACHE_DIR="/tmp/x")
    s = load_settings(base_env).semantic
    assert (s.model, s.cache_dir) == ("outro/modelo", "/tmp/x")
