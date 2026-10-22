"""Extração de conteúdo de páginas: texto principal, metadados, links e imagens.

- HTML: `trafilatura` isola o conteúdo principal (sem menus/rodapés/anúncios); se falhar ou vier
  quase vazio, usa um *fallback* simples com `selectolax`.
- Texto puro: usado como está (normalizado).
- PDF: texto das primeiras páginas via `pypdf`.

Todo texto de saída é saneado (sem caracteres de controle) porque ele vai para terminais e prompts.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urljoin, urlsplit

import trafilatura
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from websearch.net.fetcher import FetchResult

Format = Literal["markdown", "text"]

MIN_MAIN_TEXT = 80          # abaixo disso o resultado do trafilatura é considerado falho
MAX_LINKS = 200
MAX_IMAGES = 50
MAX_PDF_PAGES = 30
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_BLANKS = re.compile(r"\n{3,}")
_SKIP_HREF = ("javascript:", "mailto:", "tel:", "data:", "sms:", "#")
_NOISE_TAGS = ("script", "style", "noscript", "template", "nav", "footer", "header", "aside", "form", "svg", "iframe")


@dataclass
class Link:
    url: str
    text: str


@dataclass
class Image:
    url: str
    alt: str = ""


@dataclass
class ExtractedPage:
    url: str
    title: str = ""
    text: str = ""
    description: str = ""
    published_date: str | None = None
    language: str | None = None
    favicon: str | None = None
    links: list[Link] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    method: str = "none"        # trafilatura | fallback | plain | pdf | none


def clean_text(value: str) -> str:
    value = _CONTROL.sub("", value or "").replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    return _BLANKS.sub("\n\n", value).strip()


def _http_url(base: str, href: str) -> str | None:
    href = (href or "").strip()
    if not href or href.lower().startswith(_SKIP_HREF):
        return None
    absolute = urljoin(base, href)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return absolute.split("#", 1)[0]


def _links(tree: HTMLParser, base: str) -> list[Link]:
    seen: set[str] = set()
    out: list[Link] = []
    for node in tree.css("a[href]"):
        url = _http_url(base, node.attributes.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(Link(url, clean_text(node.text(separator=" ", strip=True))[:200]))
        if len(out) >= MAX_LINKS:
            break
    return out


def _images(tree: HTMLParser, base: str) -> list[Image]:
    seen: set[str] = set()
    out: list[Image] = []
    for node in tree.css("img"):
        src = node.attributes.get("src") or node.attributes.get("data-src") or ""
        url = _http_url(base, src)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(Image(url, clean_text(node.attributes.get("alt") or "")[:200]))
        if len(out) >= MAX_IMAGES:
            break
    return out


def _title(tree: HTMLParser) -> str:
    """og:title > <title>. (O título inferido pelo trafilatura às vezes é só o primeiro <h1>.)"""
    og = tree.css_first('meta[property="og:title"]')
    if og is not None and (og.attributes.get("content") or "").strip():
        return clean_text(og.attributes["content"])
    node = tree.css_first("title")
    return clean_text(node.text(strip=True)) if node is not None else ""


def _favicon(tree: HTMLParser, base: str) -> str | None:
    for node in tree.css("link[rel]"):
        rel = (node.attributes.get("rel") or "").lower().split()
        if "icon" in rel:
            url = _http_url(base, node.attributes.get("href") or "")
            if url:
                return url
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}/favicon.ico" if parts.netloc else None


def _fallback_text(tree: HTMLParser, fmt: Format) -> str:
    for tag in _NOISE_TAGS:
        for node in tree.css(tag):
            node.decompose()
    body = tree.body or tree.root
    if body is None:
        return ""
    blocks: list[str] = []
    for node in body.css("h1,h2,h3,h4,h5,h6,p,li,pre,blockquote,td,th"):
        text = clean_text(node.text(separator=" ", strip=True))
        if not text:
            continue
        if fmt == "markdown" and node.tag and re.fullmatch(r"h[1-6]", node.tag):
            text = "#" * int(node.tag[1]) + " " + text
        elif fmt == "markdown" and node.tag == "li":
            text = "- " + text
        blocks.append(text)
    if not blocks:
        return clean_text(body.text(separator="\n", strip=True))
    return "\n\n".join(blocks)


def extract_html(html: str, url: str, fmt: Format = "markdown") -> ExtractedPage:
    page = ExtractedPage(url=url)
    if not html or not html.strip():
        return page
    tree = HTMLParser(html)
    base = tree.css_first("base[href]")
    base_url = (_http_url(url, base.attributes.get("href") or "") if base is not None else None) or url
    page.links = _links(tree, base_url)
    page.images = _images(tree, base_url)
    page.favicon = _favicon(tree, url)

    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
    except Exception:  # metadados são "melhor esforço"
        meta = None
    if meta is not None:
        page.description = clean_text(meta.description or "")
        page.published_date = meta.date or None
        page.language = meta.language or None
    page.title = _title(tree) or (clean_text(meta.title or "") if meta is not None else "")
    root = tree.css_first("html")
    lang = (root.attributes.get("lang") or "") if root is not None else ""
    if lang.strip():
        page.language = lang.strip().split("-")[0].split("_")[0].lower()

    main = ""
    try:
        main = trafilatura.extract(
            html,
            url=url,
            output_format="markdown" if fmt == "markdown" else "txt",
            include_comments=False,
            include_tables=True,
            include_links=False,
            include_images=False,
            favor_recall=True,
        ) or ""
    except Exception:
        main = ""
    main = clean_text(main)
    if len(main) >= MIN_MAIN_TEXT:
        page.text, page.method = main, "trafilatura"
    else:
        fallback = clean_text(_fallback_text(HTMLParser(html), fmt))
        # O fallback remove menus/scripts; vale mais que um resultado curto do trafilatura
        # (que pode ser só ruído), a menos que ele seja bem menor.
        if fallback and len(fallback) >= len(main) * 0.5:
            page.text, page.method = fallback, "fallback"
        else:
            page.text, page.method = main, "trafilatura" if main else "none"
    return page


def extract_pdf(data: bytes, url: str) -> ExtractedPage:
    from pypdf import PdfReader

    page = ExtractedPage(url=url, method="pdf")
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for pdf_page in reader.pages[:MAX_PDF_PAGES]:
            try:
                parts.append(pdf_page.extract_text() or "")
            except Exception:
                continue
        info = reader.metadata
        if info is not None and info.title:
            page.title = clean_text(str(info.title))
    except Exception:
        page.method = "none"
        return page
    page.text = clean_text("\n\n".join(parts))
    if not page.title:
        page.title = urlsplit(url).path.rsplit("/", 1)[-1]
    return page


def extract_from_fetch(result: FetchResult, fmt: Format = "markdown") -> ExtractedPage:
    """Escolhe o extrator pelo tipo de conteúdo."""
    if result.content_type == "application/pdf":
        return extract_pdf(result.body, result.url)
    text = result.text or ""
    if result.content_type in ("text/plain", "text/markdown"):
        return ExtractedPage(url=result.url, text=clean_text(text), method="plain",
                             title=clean_text(text.split("\n", 1)[0])[:120])
    return extract_html(text, result.url, fmt)
