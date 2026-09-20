"""URL validation for public, read-only acquisition."""

import ipaddress
import socket
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit


class URLPolicyError(ValueError):
    pass


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(address.is_global)


def validate_public_url(
    raw_url: str,
    resolver: Callable[..., Iterable] = socket.getaddrinfo,
    resolve_dns: bool = True,
) -> str:
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise URLPolicyError("url is required")
    if len(raw_url) > 4096:
        raise URLPolicyError("url is too long")
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise URLPolicyError("only http and https URLs are allowed")
    if not parsed.hostname:
        raise URLPolicyError("url must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise URLPolicyError("credential-bearing URLs are not allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
        raise URLPolicyError("local hosts are not allowed")

    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        literal_address = None

    if literal_address is not None:
        if not literal_address.is_global:
            raise URLPolicyError("non-public IP addresses are not allowed")
    else:
        if resolve_dns:
            try:
                results = resolver(hostname, parsed.port or (443 if parsed.scheme.lower() == "https" else 80), type=socket.SOCK_STREAM)
            except OSError as exc:
                raise URLPolicyError("host could not be resolved") from exc
            addresses = {result[4][0] for result in results}
            if not addresses or any(not _is_public_ip(address) for address in addresses):
                raise URLPolicyError("host must resolve only to public addresses")

    netloc = "[%s]" % hostname if literal_address is not None and literal_address.version == 6 else hostname
    if parsed.port is not None:
        netloc += ":%d" % parsed.port
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))
