import pytest

from websearch.tools.robots import Robots

UA = "WebSearchBot"


def ok(text, url):
    return Robots(text, UA).can_fetch(url)


@pytest.mark.parametrize("text,url,expected", [
    ("User-agent: *\nDisallow: /private", "https://x.com/private/a", False),
    ("User-agent: *\nDisallow: /private", "https://x.com/public", True),
    ("User-agent: *\nDisallow:", "https://x.com/anything", True),                       # vazio = tudo liberado
    ("User-agent: *\nDisallow: /", "https://x.com/", False),
    ("User-agent: *\nDisallow: /*/deep", "https://x.com/a/deep", False),                 # curinga
    ("User-agent: *\nDisallow: /*/deep", "https://x.com/a/shallow", True),
    ("User-agent: *\nDisallow: /*.pdf$", "https://x.com/f.pdf", False),                  # âncora $
    ("User-agent: *\nDisallow: /*.pdf$", "https://x.com/f.pdf?x=1", True),
    ("User-agent: *\nDisallow: /p\nAllow: /p/ok", "https://x.com/p/ok", True),           # mais longa vence
    ("User-agent: *\nDisallow: /p/ok\nAllow: /p", "https://x.com/p/ok", False),
    ("User-agent: *\nDisallow: /p\nAllow: /p", "https://x.com/p", True),                 # empate: Allow
    ("User-agent: *\nDisallow: /search?q=", "https://x.com/search?q=1", False),
    ("User-agent: *\nDisallow: /a b", "https://x.com/a%20b", False),
    ("User-agent: WebSearchBot\nDisallow: /\n\nUser-agent: *\nAllow: /", "https://x.com/", False),
    ("User-agent: OtherBot\nDisallow: /\n\nUser-agent: *\nAllow: /", "https://x.com/", True),
    ("User-agent: *\nUser-agent: websearchbot\nDisallow: /x", "https://x.com/x", False),   # grupo com 2 agentes
    ("Disallow: /", "https://x.com/", True),                                             # sem User-agent: ignorado
    ("# só comentário\nUser-agent: * # c\nDisallow: /no # c", "https://x.com/no", False),
    ("", "https://x.com/", True),
    ("\x00\x01 lixo " * 1000, "https://x.com/", True),
    ("User-agent: *\r\nDisallow: /crlf\r\n", "https://x.com/crlf", False),
])
def test_robots(text, url, expected):
    assert ok(text, url) is expected
