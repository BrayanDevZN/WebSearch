"""Validação e normalização de URLs com proteção contra SSRF.

O agente e a API recebem URLs de fontes não confiáveis (resultados de busca, conteúdo de páginas,
clientes). Antes de qualquer conexão, a URL passa por `check_url`:

1. normalização (esquema, IDN, porta padrão, fragmento);
2. rejeição de userinfo, caracteres de controle, esquemas e portas não permitidos;
3. resolução de DNS e rejeição se **qualquer** IP resolvido não for público.

O resolvedor de DNS é injetável para testes. O resultado inclui os IPs validados para que o
fetcher conecte neles diretamente (evita DNS rebinding entre a checagem e a conexão).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
DEFAULT_ALLOWED_PORTS = (80, 443)
_DEFAULT_PORT = {"http": 80, "https": 443}
_CONTROL = re.compile(r"[\x00-\x20\x7f-\x9f]")
_HOSTNAME = re.compile(r"(?!-)[a-z0-9_-]{1,63}(?<!-)(\.(?!-)[a-z0-9_-]{1,63}(?<!-))*")
_BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".localdomain", ".lan", ".home.arpa")
_BLOCKED_NAMES = {"localhost", "ip6-localhost", "ip6-loopback", "metadata.google.internal"}

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


class UnsafeURLError(ValueError):
    """URL rejeitada. `reason` é um código estável para logs e testes."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SafeURL:
    url: str          # URL normalizada
    scheme: str
    host: str         # nome (punycode) ou IP literal, minúsculo
    port: int
    ips: tuple[str, ...]  # IPs públicos validados


def is_public_ip(value: str) -> bool:
    """True só para endereços globalmente roteáveis (nega privados, loopback, link-local, etc.)."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address):
        # 6to4 e Teredo embutem um IPv4 arbitrário: tratar pelo endereço embutido.
        if ip.sixtofour is not None:
            return is_public_ip(str(ip.sixtofour))
        if ip.teredo is not None:
            return False
    return (
        ip.is_global
        and not ip.is_multicast
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_private
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def _split(raw: str, default_scheme: str) -> SplitResult:
    if not isinstance(raw, str):
        raise UnsafeURLError("invalid", "A URL deve ser texto.")
    text = raw.strip()
    if not text:
        raise UnsafeURLError("empty", "URL vazia.")
    if len(text) > MAX_URL_LENGTH:
        raise UnsafeURLError("too_long", f"URL maior que {MAX_URL_LENGTH} caracteres.")
    if _CONTROL.search(text):
        raise UnsafeURLError("control_chars", "URL contém espaços ou caracteres de controle.")
    if "://" not in text:
        if text.startswith(("//", "/")) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)", text):
            raise UnsafeURLError("scheme", "Esquema não permitido (use http ou https).")
        text = f"{default_scheme}://{text}"
    try:
        return urlsplit(text)
    except ValueError as exc:
        raise UnsafeURLError("invalid", f"URL inválida: {exc}") from None


def normalize_url(
    raw: str,
    *,
    default_scheme: str = "https",
    allowed_ports: Sequence[int] = DEFAULT_ALLOWED_PORTS,
) -> tuple[str, str, str, int]:
    """Devolve (url_normalizada, esquema, host, porta) ou levanta `UnsafeURLError`."""
    parts = _split(raw, default_scheme)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORT:
        raise UnsafeURLError("scheme", "Esquema não permitido (use http ou https).")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeURLError("userinfo", "URLs com usuário/senha não são permitidas.")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("no_host", "URL sem host.")
    try:
        port = _DEFAULT_PORT[scheme] if parts.port is None else parts.port
    except ValueError:
        raise UnsafeURLError("bad_port", "Porta inválida.") from None
    if port not in allowed_ports:
        raise UnsafeURLError("port", f"Porta {port} não permitida.")

    host = host.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise UnsafeURLError("bad_host", "Host inválido.") from None
    if ":" not in host and not _HOSTNAME.fullmatch(host):
        raise UnsafeURLError("bad_host", "Host inválido.")
    if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeURLError("blocked_host", "Host interno não permitido.")

    is_ip = False
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        pass
    if not is_ip and re.fullmatch(r"[0-9a-fx.]+", host) and not host.endswith("."):
        # Formas numéricas ambíguas (2130706433, 0x7f.1, 0177.0.0.1) que o resolvedor aceitaria.
        raise UnsafeURLError("numeric_host", "Host numérico ambíguo não permitido.")

    netloc_host = f"[{host}]" if ":" in host else host
    netloc = netloc_host if port == _DEFAULT_PORT[scheme] else f"{netloc_host}:{port}"
    path = parts.path or "/"
    url = urlunsplit((scheme, netloc, path, parts.query, ""))  # remove o fragmento
    return url, scheme, host, port


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.append(addr)
    return seen


async def check_url(
    raw: str,
    *,
    resolver: Resolver | None = None,
    allowed_ports: Sequence[int] = DEFAULT_ALLOWED_PORTS,
    default_scheme: str = "https",
) -> SafeURL:
    """Normaliza e valida; resolve DNS e exige que todos os IPs sejam públicos."""
    url, scheme, host, port = normalize_url(raw, default_scheme=default_scheme, allowed_ports=allowed_ports)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_ip(host):
            raise UnsafeURLError("private_ip", "Endereço IP não público.")
        return SafeURL(url, scheme, host, port, (str(literal),))

    resolve = resolver or system_resolver
    try:
        ips = [ip for ip in await resolve(host, port)]
    except (OSError, UnicodeError):
        raise UnsafeURLError("dns", "Não foi possível resolver o host.") from None
    if not ips:
        raise UnsafeURLError("dns", "Host sem endereços.")
    if not all(is_public_ip(ip) for ip in ips):
        raise UnsafeURLError("private_ip", "O host resolve para um endereço não público.")
    return SafeURL(url, scheme, host, port, tuple(ips))
