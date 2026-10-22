import pytest

from tests.tools.test_search import FakeFetcher
from websearch.net.fetcher import FetchResult
from websearch.tools.common import InvalidRequest
from websearch.tools.crawl import CrawlRequest, CrawlTool
from websearch.tools.map import MapTool
from websearch.tools.crawler import CrawlParams

TEXT = "Conteúdo de exemplo bem longo para a extração funcionar. " * 6


def page(title, *links):
    anchors = "".join(f'<a href="{h}">{t}</a> ' for h, t in links)
    return f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1><p>{TEXT}</p><p>{anchors}</p></article></body></html>"


SITE = {
    "https://x.com/": page("Home", ("/a", "A"), ("/b", "B"), ("https://other.com/z", "ext"), ("/img.png", "i")),
    "https://x.com/a": page("A", ("/a/deep", "deep"), ("/", "home")),
    "https://x.com/b": page("B", ("/a", "dup")),
    "https://x.com/a/deep": page("Deep"),
    "https://other.com/z": page("Z"),
}


class SiteFetcher(FakeFetcher):
    def __init__(self, pages, robots=None, **kw):
        super().__init__(pages, **kw)
        self.robots = robots

    async def fetch(self, url):
        if url.endswith("/robots.txt"):
            self.calls.append(url)
            if self.robots is None:
                from websearch.net.fetcher import FetchError
                raise FetchError("http_status", "404", url=url, status=404)
            return FetchResult(url=url, requested_url=url, status=200, content_type="text/plain",
                               body=self.robots.encode(), text=self.robots, truncated=False)
        return await super().fetch(url)


def urls(resp):
    return [r if isinstance(r, str) else r["url"] for r in resp.results]


async def test_map_depth_one_and_dedupe():
    resp = await MapTool(SiteFetcher(SITE), request_id=lambda: "r").map(CrawlParams("x.com", max_depth=1))
    assert urls(resp) == ["https://x.com/", "https://x.com/a", "https://x.com/b"]   # sem externo, sem png, sem /a/deep
    assert resp.base_url == "https://x.com/" and resp.to_dict()["request_id"] == "r"


async def test_depth_two_reaches_deep_and_limit():
    deep = await MapTool(SiteFetcher(SITE)).map(CrawlParams("https://x.com/", max_depth=2))
    assert "https://x.com/a/deep" in urls(deep)
    capped = await MapTool(SiteFetcher(SITE)).map(CrawlParams("https://x.com/", max_depth=2, limit=2))
    assert len(urls(capped)) == 2


async def test_breadth_and_allow_external():
    resp = await MapTool(SiteFetcher(SITE)).map(CrawlParams("https://x.com/", max_breadth=1))
    assert urls(resp) == ["https://x.com/", "https://x.com/a"]
    ext = await MapTool(SiteFetcher(SITE)).map(CrawlParams("https://x.com/", allow_external=True))
    assert "https://other.com/z" in urls(ext)


async def test_path_and_domain_filters():
    sel = await MapTool(SiteFetcher(SITE)).map(CrawlParams("https://x.com/", select_paths=[r"^/a"]))
    assert urls(sel) == ["https://x.com/", "https://x.com/a"]
    exc = await MapTool(SiteFetcher(SITE)).map(CrawlParams("https://x.com/", exclude_paths=[r"^/a"]))
    assert urls(exc) == ["https://x.com/", "https://x.com/b"]
    dom = await MapTool(SiteFetcher(SITE)).map(
        CrawlParams("https://x.com/", allow_external=True, exclude_domains=[r"other\.com"]))
    assert "https://other.com/z" not in urls(dom)


async def test_robots_txt_respected():
    f = SiteFetcher(SITE, robots="User-agent: *\nDisallow: /a")
    resp = await CrawlTool(f).crawl(CrawlRequest("https://x.com/", max_depth=2))
    assert urls(resp) == ["https://x.com/", "https://x.com/b"]
    assert {"url": "https://x.com/a", "error": "robots_disallowed"} in resp.failed_results
    free = await MapTool(SiteFetcher(SITE, robots="User-agent: *\nDisallow: /")).map(
        CrawlParams("https://x.com/", respect_robots=False))
    assert len(urls(free)) == 3


async def test_crawl_content_and_failures():
    pages = dict(SITE)
    f = SiteFetcher(pages, errors={"https://x.com/b": "timeout"})
    resp = await CrawlTool(f).crawl(CrawlRequest("https://x.com/", include_favicon=True))
    assert resp.results[0]["title"] == "Home" and "Conteúdo" in resp.results[0]["raw_content"]
    assert resp.failed_results == [{"url": "https://x.com/b", "error": "timeout"}]


async def test_budget_stops_crawl():
    f = SiteFetcher(SITE, delay=5)
    resp = await CrawlTool(f, budget_seconds=0.05).crawl(CrawlRequest("https://x.com/"))
    assert resp.results == [] and resp.failed_results[0]["error"] == "timeout"


@pytest.mark.parametrize("kw", [
    {"url": ""}, {"url": "ftp://x.com"}, {"url": "http://127.0.0.1"} if False else {"url": "x.com", "max_depth": 6},
    {"url": "x.com", "limit": 0}, {"url": "x.com", "max_breadth": 501}, {"url": "x.com", "select_paths": ["("]},
    {"url": "x.com", "exclude_paths": ["a" * 201]}, {"url": "x.com", "format": "pdf"},
])
async def test_validation(kw):
    with pytest.raises(InvalidRequest):
        await CrawlTool(SiteFetcher(SITE)).crawl(CrawlRequest(**kw))


async def test_robots_com_curinga_e_ancora_no_crawl():
    f = SiteFetcher(SITE, robots="User-agent: *\nDisallow: /*/deep\nDisallow: /b$")
    resp = await MapTool(f).map(CrawlParams("https://x.com/", max_depth=2))
    assert urls(resp) == ["https://x.com/", "https://x.com/a"]


async def test_robots_para_agente_especifico_bloqueia_tudo():
    f = SiteFetcher(SITE, robots="User-agent: WebSearchBot\nDisallow: /")
    resp = await CrawlTool(f).crawl(CrawlRequest("https://x.com/"))
    assert resp.results == [] and resp.failed_results == [{"url": "https://x.com/", "error": "robots_disallowed"}]
