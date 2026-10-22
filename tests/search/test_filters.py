import pytest

from websearch.search.base import SearchHit, SearchQuery
from websearch.search.filters import (
    apply_filters, canonical_url, clean_domain, matches_domain, parse_date, url_domain,
)


def hit(url, title="t", date=None, pos=0):
    return SearchHit(title=title, url=url, snippet="s", provider="x", position=pos, published_date=date)


def test_url_domain_e_clean_domain():
    assert url_domain("https://WWW.Example.com:8080/x") == "example.com"
    assert clean_domain("https://www.Site.com/path") == "site.com"
    assert clean_domain("*.site.com") == "site.com"
    assert clean_domain("  Site.COM. ") == "site.com"
    assert clean_domain("") == ""


@pytest.mark.parametrize("url,domain,ok", [
    ("https://example.com/a", "example.com", True),
    ("https://www.example.com/a", "example.com", True),
    ("https://blog.example.com/a", "example.com", True),
    ("https://notexample.com/a", "example.com", False),
    ("https://example.com.evil.org/a", "example.com", False),
    ("https://example.com/a", "blog.example.com", False),
    ("https://example.com/a", "", False),
])
def test_matches_domain(url, domain, ok):
    assert matches_domain(url, domain) is ok


def test_canonical_url_remove_ruido():
    a = canonical_url("http://www.Example.com/a/b/?utm_source=x&b=2&a=1&fbclid=zz#frag")
    b = canonical_url("https://example.com/a/b?a=1&b=2")
    assert a == b == "https://example.com/a/b?a=1&b=2"
    assert canonical_url("https://example.com") == "https://example.com/"
    assert canonical_url("https://example.com:8443/x") == "https://example.com:8443/x"


@pytest.mark.parametrize("v,expected", [
    ("2026-06-15", "2026-06-15"), ("2026-06-15T10:00:00Z", "2026-06-15"),
    ("2026-06-15T10:00:00+00:00", "2026-06-15"), (None, None), ("", None), ("ontem", None),
])
def test_parse_date(v, expected):
    d = parse_date(v)
    assert (d.isoformat() if d else None) == expected


def test_include_domains():
    hits = [hit("https://a.com/1"), hit("https://b.org/2"), hit("https://sub.a.com/3")]
    out = apply_filters(hits, SearchQuery("q", include_domains=["a.com"]))
    assert [h.url for h in out] == ["https://a.com/1", "https://sub.a.com/3"]


def test_exclude_domains():
    hits = [hit("https://a.com/1"), hit("https://b.org/2")]
    assert [h.url for h in apply_filters(hits, SearchQuery("q", exclude_domains=["https://www.a.com"]))] == ["https://b.org/2"]


def test_datas_filtram_apenas_quando_conhecidas():
    hits = [hit("https://a.com/old", date="2024-01-01"), hit("https://a.com/new", date="2026-06-01"),
            hit("https://a.com/undated")]
    out = apply_filters(hits, SearchQuery("q", start_date="2025-01-01", end_date="2026-12-31"))
    assert [h.url for h in out] == ["https://a.com/new", "https://a.com/undated"]


def test_deduplica_mantendo_o_primeiro():
    hits = [hit("https://a.com/x?utm_source=1", "primeiro"), hit("http://www.a.com/x/", "dup"), hit("https://a.com/y")]
    out = apply_filters(hits, SearchQuery("q"))
    assert [h.title for h in out] == ["primeiro", "t"]


def test_descarta_urls_nao_http():
    hits = [hit("javascript:alert(1)"), hit("ftp://a.com/x"), hit("https://a.com/ok")]
    assert [h.url for h in apply_filters(hits, SearchQuery("q"))] == ["https://a.com/ok"]


import pytest as _pytest

from websearch.search.base import SearchHit as _Hit, SearchQuery as _Query
from websearch.search.ddg import build_query_text as _build
from websearch.search.filters import apply_filters as _apply, canonical_url as _canon


@_pytest.mark.parametrize("url", ["http://a.com:abc/", "http://[::1", "http://a.com:99999/x", "https://"])
def test_url_malformada_nao_derruba_o_filtro(url):
    ok = _Hit("t", "https://ok.com/x", "s", "p", 1)
    out = _apply([_Hit("t", url, "s", "p", 0), ok], _Query("q", include_domains=["ok.com"]))
    assert out == [ok]
    assert _apply([_Hit("t", url, "s", "p", 0)], _Query("q")) in ([], [_Hit("t", url, "s", "p", 0)])
    _canon(url)  # não levanta


def test_dominios_com_operadores_nao_injetam_na_consulta():
    q = _Query("q", include_domains=['a.com" OR site:evil.com', "x y", "https://www.Site.com/p"],
               exclude_domains=["bad.com -foo", "ok.org"])
    assert _build(q) == "q site:site.com -site:ok.org"
