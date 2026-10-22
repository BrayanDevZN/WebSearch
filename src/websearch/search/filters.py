"""Filtros e deduplicação aplicados sobre resultados de qualquer provedor."""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websearch.search.base import SearchHit, SearchQuery

_TRACKING = re.compile(r"^(utm_.+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|igshid|yclid|ref|ref_src|_hsenc|_hsmi)$", re.I)


_DOMAIN = re.compile(r"[a-z0-9_-]+(\.[a-z0-9_-]+)*")


def url_domain(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def valid_domain(value: str) -> str:
    """`clean_domain` + só aceita nomes de host simples (vazio se tiver espaço, aspas, operadores...)."""
    domain = clean_domain(value)
    return domain if _DOMAIN.fullmatch(domain) else ""


def clean_domain(value: str) -> str:
    """Aceita 'https://www.Site.com/x', 'site.com' ou '*.site.com' e devolve 'site.com'."""
    text = value.strip().lower()
    if "://" in text:
        try:
            text = urlsplit(text).hostname or ""
        except ValueError:
            return ""
    text = text.split("/", 1)[0].lstrip("*.").rstrip(".")
    return text[4:] if text.startswith("www.") else text


def matches_domain(url: str, domain: str) -> bool:
    """True se o host da URL é `domain` ou subdomínio dele (não casa 'notexample.com' com 'example.com')."""
    host, wanted = url_domain(url), clean_domain(domain)
    return bool(wanted) and (host == wanted or host.endswith("." + wanted))


def canonical_url(url: str) -> str:
    """Forma canônica para deduplicar: sem fragmento, rastreadores, 'www.', barra final; https."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        port_number = parts.port
    except ValueError:
        return url.strip()                      # malformada: usa o texto como chave
    host = host[4:] if host.startswith("www.") else host
    port = f":{port_number}" if port_number and port_number not in (80, 443) else ""
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host + port, path, query, ""))


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def apply_filters(hits: list[SearchHit], query: SearchQuery) -> list[SearchHit]:
    """Domínios, datas e deduplicação. Resultados sem data não são removidos pelo filtro de data."""
    include = [d for d in query.include_domains if valid_domain(d)]
    exclude = [d for d in query.exclude_domains if valid_domain(d)]
    start, end = parse_date(query.start_date), parse_date(query.end_date)
    seen: set[str] = set()
    out: list[SearchHit] = []
    for hit in hits:
        if not hit.url.lower().startswith(("http://", "https://")) or not url_domain(hit.url):
            continue
        if include and not any(matches_domain(hit.url, d) for d in include):
            continue
        if any(matches_domain(hit.url, d) for d in exclude):
            continue
        published = parse_date(hit.published_date)
        if published and ((start and published < start) or (end and published > end)):
            continue
        key = canonical_url(hit.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out
