"""Configuração a partir do ambiente (.env) com validação cruzada.

`load_settings` nunca imprime nem devolve valores de chaves em mensagens de erro:
só o nome da variável.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

LLM_PROVIDERS = ("local", "groq", "openai")
LOCAL_SIZES = ("low", "medium", "high")
SEARCH_PROVIDER_NAMES = ("ddgs", "searxng")

DEFAULT_LOCAL_MODELS = {
    "low": "qwen3:4b",
    "medium": "qwen3:14b",
    "high": "qwen3:32b",
}

MIN_KEY_LENGTH = 16
RECOMMENDED_KEY_LENGTH = 32


class ConfigError(Exception):
    """Configuração inválida. `problems` lista todos os erros encontrados."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("Configuração inválida:\n" + "\n".join(f"  - {p}" for p in self.problems))


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = None


@dataclass(frozen=True)
class FetchSettings:
    timeout_seconds: float = 15.0
    max_bytes: int = 2_000_000
    max_redirects: int = 3
    concurrency: int = 5
    user_agent: str = "Mozilla/5.0 (compatible; WebSearchBot/0.1)"


@dataclass(frozen=True)
class AgentLimits:
    max_iterations: int = 6
    max_searches: int = 8
    max_pages: int = 10
    timeout_seconds: float = 120.0
    max_context_tokens: int = 6000
    page_max_chars: int = 6000


@dataclass(frozen=True)
class Settings:
    api_search_key: str = field(repr=False)
    llm: LLMSettings
    search_providers: tuple[str, ...]
    searxng_url: str | None
    fetch: FetchSettings
    limits: AgentLimits
    host: str
    port: int
    rate_limit_per_minute: int
    log_level: str
    warnings: tuple[str, ...] = ()


def resolve_local_model(env: Mapping[str, str]) -> str:
    """LOCAL_MODEL > LOCAL_MODEL_<PORTE> > padrão do porte."""
    explicit = (env.get("LOCAL_MODEL") or "").strip()
    if explicit:
        return explicit
    size = (env.get("LOCAL_MODEL_SIZE") or "medium").strip().lower()
    override = (env.get(f"LOCAL_MODEL_{size.upper()}") or "").strip()
    return override or DEFAULT_LOCAL_MODELS[size]


def _get(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def _number(env, name, default, kind, problems, minimum=1, maximum=None):
    raw = _get(env, name)
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError:
        problems.append(f"{name} deve ser {'um número inteiro' if kind is int else 'um número'}.")
        return default
    if value < minimum or (maximum is not None and value > maximum):
        bound = f">= {minimum}" if maximum is None else f"entre {minimum} e {maximum}"
        problems.append(f"{name} deve ser {bound}.")
        return default
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Valida o ambiente e devolve `Settings` imutável, ou levanta `ConfigError`."""
    env = os.environ if env is None else env
    problems: list[str] = []
    warnings: list[str] = []

    # --- chave de acesso da API -------------------------------------------------
    api_key = _get(env, "API_SEARCH_KEY")
    if not api_key:
        problems.append("API_SEARCH_KEY é obrigatória (a chave que todo cliente deve enviar).")
    elif len(api_key) < MIN_KEY_LENGTH:
        problems.append(f"API_SEARCH_KEY deve ter pelo menos {MIN_KEY_LENGTH} caracteres.")
    elif len(api_key) < RECOMMENDED_KEY_LENGTH:
        warnings.append(f"API_SEARCH_KEY tem menos de {RECOMMENDED_KEY_LENGTH} caracteres; use uma chave maior.")

    # --- LLM ---------------------------------------------------------------------
    provider = _get(env, "LLM_PROVIDER").lower()
    llm: LLMSettings | None = None
    if not provider:
        problems.append(f"LLM_PROVIDER é obrigatório. Valores aceitos: {', '.join(LLM_PROVIDERS)}.")
    elif provider not in LLM_PROVIDERS:
        problems.append(f"LLM_PROVIDER inválido. Valores aceitos: {', '.join(LLM_PROVIDERS)}.")
    elif provider == "local":
        size = _get(env, "LOCAL_MODEL_SIZE").lower() or "medium"
        if size not in LOCAL_SIZES:
            problems.append(f"LOCAL_MODEL_SIZE inválido. Valores aceitos: {', '.join(LOCAL_SIZES)}.")
        else:
            llm = LLMSettings(
                provider="local",
                model=resolve_local_model(env),
                api_key=None,
                base_url=_get(env, "LOCAL_BASE_URL") or "http://localhost:11434/v1",
            )
    else:
        prefix = provider.upper()
        key = _get(env, f"{prefix}_API_KEY")
        model = _get(env, f"{prefix}_MODEL")
        if not key:
            problems.append(f"{prefix}_API_KEY é obrigatória quando LLM_PROVIDER={provider}.")
        if not model:
            problems.append(f"{prefix}_MODEL é obrigatório quando LLM_PROVIDER={provider}.")
        if key and model:
            llm = LLMSettings(provider=provider, model=model, api_key=key)

    # --- busca -------------------------------------------------------------------
    raw_providers = _get(env, "SEARCH_PROVIDERS") or "ddgs"
    names = tuple(p.strip().lower() for p in raw_providers.split(",") if p.strip())
    bad = [p for p in names if p not in SEARCH_PROVIDER_NAMES]
    if bad:
        problems.append(
            f"SEARCH_PROVIDERS contém valor inválido ({', '.join(bad)}). "
            f"Valores aceitos: {', '.join(SEARCH_PROVIDER_NAMES)}."
        )
    if not names:
        problems.append("SEARCH_PROVIDERS não pode ficar vazio.")
    searxng_url = _get(env, "SEARXNG_URL") or None
    if "searxng" in names and not searxng_url:
        problems.append("SEARXNG_URL é obrigatória quando SEARCH_PROVIDERS inclui searxng.")

    # --- numéricos ---------------------------------------------------------------
    fetch = FetchSettings(
        timeout_seconds=_number(env, "FETCH_TIMEOUT_SECONDS", 15.0, float, problems),
        max_bytes=_number(env, "FETCH_MAX_BYTES", 2_000_000, int, problems, minimum=1024),
        max_redirects=_number(env, "FETCH_MAX_REDIRECTS", 3, int, problems, minimum=0, maximum=10),
        concurrency=_number(env, "FETCH_CONCURRENCY", 5, int, problems, maximum=50),
        user_agent=_get(env, "FETCH_USER_AGENT") or FetchSettings.user_agent,
    )
    limits = AgentLimits(
        max_iterations=_number(env, "AGENT_MAX_ITERATIONS", 6, int, problems, maximum=50),
        max_searches=_number(env, "AGENT_MAX_SEARCHES", 8, int, problems, maximum=100),
        max_pages=_number(env, "AGENT_MAX_PAGES", 10, int, problems, maximum=100),
        timeout_seconds=_number(env, "AGENT_TIMEOUT_SECONDS", 120.0, float, problems),
        max_context_tokens=_number(env, "AGENT_MAX_CONTEXT_TOKENS", 6000, int, problems, minimum=500),
        page_max_chars=_number(env, "PAGE_MAX_CHARS", 6000, int, problems, minimum=200),
    )

    # --- servidor ----------------------------------------------------------------
    host = _get(env, "HOST") or "127.0.0.1"
    port = _number(env, "PORT", 8000, int, problems, maximum=65535)
    rate = _number(env, "RATE_LIMIT_PER_MINUTE", 20, int, problems)
    log_level = (_get(env, "LOG_LEVEL") or "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        problems.append("LOG_LEVEL inválido. Valores aceitos: DEBUG, INFO, WARNING, ERROR, CRITICAL.")
    if host not in ("127.0.0.1", "localhost", "::1"):
        warnings.append("HOST expõe o serviço fora da máquina; use TLS/proxy reverso.")

    if problems:
        raise ConfigError(problems)
    assert llm is not None

    return Settings(
        api_search_key=api_key,
        llm=llm,
        search_providers=names,
        searxng_url=searxng_url,
        fetch=fetch,
        limits=limits,
        host=host,
        port=port,
        rate_limit_per_minute=rate,
        log_level=log_level,
        warnings=tuple(warnings),
    )


def load_dotenv_settings(path: str | None = None) -> Settings:
    """Carrega `.env` (sem sobrescrever variáveis já definidas) e valida."""
    from dotenv import load_dotenv

    load_dotenv(path, override=False)
    return load_settings(os.environ)
