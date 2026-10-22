"""Cliente HTTP seguro para baixar páginas.

Garantias:
- toda URL (inclusive cada redirecionamento) passa por `check_url` (SSRF);
- a conexão vai direto para o IP já validado (anti DNS rebinding), mantendo `Host` e SNI/certificado
  do nome original;
- limite de bytes **depois** de descomprimir (anti zip-bomb), limite de tempo total e de redirecionamentos;
- só tipos de conteúdo úteis (HTML, texto, PDF); nada de cookies entre requisições;
- nunca usa proxies/credenciais do ambiente (`trust_env=False`).
"""

from __future__ import annotations

import asyncio
import os
import re
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from websearch.config import FetchSettings
from websearch.net.urlsafe import DEFAULT_ALLOWED_PORTS, Resolver, UnsafeURLError, check_url

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "text/xml", "application/xml")
PDF_TYPE = "application/pdf"
_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?\s*([A-Za-z0-9_\-:.]+)""", re.I)


class FetchError(Exception):
    """Falha ao baixar uma URL. `reason` é um código estável."""

    def __init__(self, reason: str, message: str, *, url: str = "", status: int | None = None):
        super().__init__(message)
        self.reason = reason
        self.url = url
        self.status = status


@dataclass
class FetchResult:
    url: str                       # URL final (após redirecionamentos)
    requested_url: str
    status: int
    content_type: str              # sem parâmetros, minúsculo
    body: bytes
    text: str | None               # None quando o conteúdo é binário (PDF)
    truncated: bool
    redirects: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


def _media_type(header: str | None) -> str:
    return (header or "").split(";", 1)[0].strip().lower()


def _charset_from_header(header: str | None) -> str | None:
    match = re.search(r"charset=([^;\s]+)", header or "", re.I)
    return match.group(1).strip("\"'").lower() if match else None


def decode_body(body: bytes, content_type_header: str | None) -> str:
    """Decodifica por: charset do header → <meta charset> → UTF-8 → cp1252."""
    for bom, name in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if body.startswith(bom):
            try:
                return body.decode(name).lstrip("\ufeff")
            except UnicodeDecodeError:
                break
    candidates: list[str] = []
    declared = _charset_from_header(content_type_header)
    if declared:
        candidates.append(declared)
    meta = _META_CHARSET.search(body[:4096])
    if meta:
        candidates.append(meta.group(1).decode("ascii", "ignore").lower())
    candidates.append("utf-8")
    for name in candidates:
        try:
            return body.decode(name)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("cp1252", errors="replace")


def _tls_verify(ca_bundle: str | None):
    """CA própria (FETCH_CA_BUNDLE ou SSL_CERT_FILE, o padrão do OpenSSL); senão, os certificados do certifi."""
    path = ca_bundle or os.environ.get("SSL_CERT_FILE") or ""
    if path and os.path.isfile(path):
        try:
            return ssl.create_default_context(cafile=path)
        except (ssl.SSLError, OSError):
            if ca_bundle:
                raise
    return True


def _ip_netloc(ip: str, port: int, default_port: int) -> str:
    host = f"[{ip}]" if ":" in ip else ip
    return host if port == default_port else f"{host}:{port}"


class Fetcher:
    def __init__(
        self,
        settings: FetchSettings | None = None,
        *,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        allowed_ports: tuple[int, ...] = DEFAULT_ALLOWED_PORTS,
        pin_ip: bool = True,
    ):
        self.settings = settings or FetchSettings()
        self._resolver = resolver
        self._allowed_ports = allowed_ports
        self._pin_ip = pin_ip
        self._sem = asyncio.Semaphore(self.settings.concurrency)
        self._client = httpx.AsyncClient(
            transport=transport,
            verify=_tls_verify(self.settings.ca_bundle),
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(self.settings.timeout_seconds),
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,application/pdf;q=0.8,*/*;q=0.1",
                "Accept-Language": "en,pt-BR;q=0.8,pt;q=0.7",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ API --
    async def fetch(self, url: str) -> FetchResult:
        started = time.monotonic()
        async with self._sem:
            try:
                async with asyncio.timeout(self.settings.timeout_seconds):
                    result = await self._fetch(url)
            except TimeoutError:
                raise FetchError("timeout", "Tempo limite excedido.", url=url) from None
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    # ------------------------------------------------------------- internals --
    async def _fetch(self, url: str) -> FetchResult:
        redirects: list[str] = []
        current = url
        seen: set[str] = set()
        for _ in range(self.settings.max_redirects + 1):
            try:
                safe = await check_url(current, resolver=self._resolver, allowed_ports=self._allowed_ports)
            except UnsafeURLError as exc:
                raise FetchError("unsafe_url", str(exc), url=current) from None
            if safe.url in seen:
                raise FetchError("redirect_loop", "Laço de redirecionamento.", url=safe.url)
            seen.add(safe.url)

            response, body, truncated = await self._request(safe)
            status = response.status_code

            if status in REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise FetchError("bad_redirect", "Redirecionamento sem Location.", url=safe.url, status=status)
                redirects.append(safe.url)
                current = urljoin(safe.url, location)
                continue

            if status >= 400:
                raise FetchError("http_status", f"HTTP {status}.", url=safe.url, status=status)

            ctype = _media_type(response.headers.get("content-type"))
            text = None
            if ctype != PDF_TYPE:
                text = decode_body(body, response.headers.get("content-type"))
            return FetchResult(
                url=safe.url,
                requested_url=url,
                status=status,
                content_type=ctype,
                body=body,
                text=text,
                truncated=truncated,
                redirects=redirects,
            )
        raise FetchError("too_many_redirects", "Redirecionamentos demais.", url=current)

    async def _request(self, safe):
        """Faz o GET em um dos IPs validados e lê o corpo com limite de bytes."""
        last_error: Exception | None = None
        default_port = 443 if safe.scheme == "https" else 80
        targets = safe.ips if self._pin_ip else (None,)
        for ip in targets:
            if ip is None:
                request_url, headers, extensions = safe.url, {}, {}
            else:
                parts = urlsplit(safe.url)
                rest = parts.path + (f"?{parts.query}" if parts.query else "")
                request_url = f"{safe.scheme}://{_ip_netloc(ip, safe.port, default_port)}{rest}"
                host_header = safe.host if safe.port == default_port else f"{safe.host}:{safe.port}"
                headers = {"Host": host_header}
                extensions = {"sni_hostname": safe.host}
            try:
                return await self._get(request_url, headers, extensions, safe.url)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
                continue
            except httpx.TimeoutException:
                raise FetchError("timeout", "Tempo limite excedido.", url=safe.url) from None
            except httpx.HTTPError as exc:
                raise FetchError("network", f"Erro de rede: {type(exc).__name__}.", url=safe.url) from None
        raise FetchError("connect", f"Falha ao conectar ({type(last_error).__name__}).", url=safe.url)

    async def _get(self, request_url, headers, extensions, display_url):
        limit = self.settings.max_bytes
        async with self._client.stream("GET", request_url, headers=headers, extensions=extensions) as response:
            try:
                if response.status_code in REDIRECT_STATUSES or response.status_code >= 400:
                    return response, b"", False
                ctype = _media_type(response.headers.get("content-type"))
                if ctype and ctype != PDF_TYPE and ctype not in TEXT_TYPES:
                    raise FetchError(
                        "unsupported_content_type", f"Tipo de conteúdo não suportado: {ctype}.",
                        url=display_url, status=response.status_code,
                    )
                chunks: list[bytes] = []
                size = 0
                truncated = False
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        chunks.append(chunk[: len(chunk) - (size - limit)])
                        truncated = True
                        break
                    chunks.append(chunk)
                return response, b"".join(chunks), truncated
            finally:
                self._client.cookies.clear()
