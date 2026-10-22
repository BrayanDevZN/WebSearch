import pytest

from websearch.config import ConfigError, load_settings, resolve_local_model


def problems(env):
    with pytest.raises(ConfigError) as e:
        load_settings(env)
    return e.value.problems


def test_local_minimo_nao_exige_chave_de_llm(base_env):
    s = load_settings(base_env)
    assert s.llm.provider == "local"
    assert s.llm.api_key is None
    assert s.llm.model == "qwen3:14b"  # porte medium por padrão
    assert s.llm.base_url == "http://localhost:11434/v1"
    assert s.search_providers == ("ddgs",)


def test_api_search_key_obrigatoria(base_env):
    del base_env["API_SEARCH_KEY"]
    assert any("API_SEARCH_KEY" in p for p in problems(base_env))


def test_api_search_key_curta_demais(base_env):
    base_env["API_SEARCH_KEY"] = "curta"
    assert any("pelo menos" in p for p in problems(base_env))


def test_api_search_key_media_gera_aviso(base_env):
    base_env["API_SEARCH_KEY"] = "x" * 20
    assert any("API_SEARCH_KEY" in w for w in load_settings(base_env).warnings)


def test_chave_nunca_aparece_nos_erros_nem_no_repr(base_env):
    segredo = "segredo-super-longo-1234567890"
    base_env.update(API_SEARCH_KEY=segredo, LLM_PROVIDER="groq", GROQ_API_KEY="gsk_abcdefghijk")
    msg = str(pytest.raises(ConfigError, load_settings, base_env).value)
    assert segredo not in msg and "gsk_abcdefghijk" not in msg
    base_env["GROQ_MODEL"] = "m"
    s = load_settings(base_env)
    assert segredo not in repr(s) and "gsk_abcdefghijk" not in repr(s)


@pytest.mark.parametrize("provider", ["groq", "openai"])
def test_provedor_remoto_exige_chave_e_modelo(base_env, provider):
    base_env["LLM_PROVIDER"] = provider
    p = problems(base_env)
    assert any(f"{provider.upper()}_API_KEY" in x for x in p)
    assert any(f"{provider.upper()}_MODEL" in x for x in p)


@pytest.mark.parametrize("provider", ["groq", "openai"])
def test_provedor_remoto_valido(base_env, provider):
    base_env.update(LLM_PROVIDER=provider, **{f"{provider.upper()}_API_KEY": "k123", f"{provider.upper()}_MODEL": "mod"})
    s = load_settings(base_env)
    assert (s.llm.provider, s.llm.model, s.llm.api_key) == (provider, "mod", "k123")


def test_chave_de_provedor_nao_selecionado_e_ignorada(base_env):
    base_env.update(GROQ_API_KEY="x", OPENAI_API_KEY="y")
    assert load_settings(base_env).llm.api_key is None


def test_provedor_invalido_ou_ausente(base_env):
    base_env["LLM_PROVIDER"] = "claude"
    assert any("LLM_PROVIDER" in p for p in problems(base_env))
    del base_env["LLM_PROVIDER"]
    assert any("obrigatório" in p for p in problems(base_env))


def test_provedor_case_insensitive(base_env):
    base_env["LLM_PROVIDER"] = "LOCAL"
    assert load_settings(base_env).llm.provider == "local"


@pytest.mark.parametrize("size,model", [("low", "qwen3:4b"), ("medium", "qwen3:14b"), ("high", "qwen3:32b")])
def test_porte_escolhe_modelo(base_env, size, model):
    base_env["LOCAL_MODEL_SIZE"] = size
    assert load_settings(base_env).llm.model == model


def test_porte_invalido(base_env):
    base_env["LOCAL_MODEL_SIZE"] = "gigante"
    assert any("LOCAL_MODEL_SIZE" in p for p in problems(base_env))


def test_ordem_de_resolucao_do_modelo_local():
    env = {"LOCAL_MODEL_SIZE": "high", "LOCAL_MODEL_HIGH": "qwen-custom:72b"}
    assert resolve_local_model(env) == "qwen-custom:72b"
    env["LOCAL_MODEL"] = "outro:7b"
    assert resolve_local_model(env) == "outro:7b"  # LOCAL_MODEL vence o porte


def test_local_base_url_customizada(base_env):
    base_env["LOCAL_BASE_URL"] = "http://gpu-box:8080/v1"
    assert load_settings(base_env).llm.base_url == "http://gpu-box:8080/v1"


def test_busca_searxng_exige_url(base_env):
    base_env["SEARCH_PROVIDERS"] = "ddgs,searxng"
    assert any("SEARXNG_URL" in p for p in problems(base_env))
    base_env["SEARXNG_URL"] = "http://localhost:8888"
    s = load_settings(base_env)
    assert s.search_providers == ("ddgs", "searxng")


def test_busca_provedor_invalido(base_env):
    base_env["SEARCH_PROVIDERS"] = "google"
    assert any("SEARCH_PROVIDERS" in p for p in problems(base_env))


@pytest.mark.parametrize("name,value", [
    ("PORT", "abc"), ("PORT", "70000"), ("AGENT_MAX_ITERATIONS", "0"),
    ("FETCH_TIMEOUT_SECONDS", "-1"), ("FETCH_MAX_REDIRECTS", "99"), ("LOG_LEVEL", "VERBOSO"),
])
def test_valores_numericos_invalidos(base_env, name, value):
    base_env[name] = value
    assert any(name in p for p in problems(base_env))


def test_todos_os_problemas_sao_reportados_de_uma_vez():
    p = problems({})
    assert len(p) >= 2  # chave de acesso + provedor de LLM


def test_host_publico_gera_aviso(base_env):
    base_env["HOST"] = "0.0.0.0"
    assert any("HOST" in w for w in load_settings(base_env).warnings)


def test_limites_customizados(base_env):
    base_env.update(AGENT_MAX_SEARCHES="3", PAGE_MAX_CHARS="1000", FETCH_CONCURRENCY="2")
    s = load_settings(base_env)
    assert (s.limits.max_searches, s.limits.page_max_chars, s.fetch.concurrency) == (3, 1000, 2)
