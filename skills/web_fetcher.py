from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from skills.errors import SkillError


DEFAULT_CONTENT_TYPES = ("text/plain", "text/html", "application/json")


def _is_allowed_domain(hostname: str, allowed_domains: list[str]) -> bool:
    normalized = hostname.rstrip(".").casefold()
    for value in allowed_domains:
        allowed = value.strip().lstrip(".").rstrip(".").casefold()
        if allowed and (normalized == allowed or normalized.endswith(f".{allowed}")):
            return True
    return False


def _validate_public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value)
    if not address.is_global:
        raise SkillError(
            "URL_PRIVATE_ADDRESS",
            f"URL resolves to a non-public address: {address}",
            category="security",
        )
    return address


def _resolve_public_addresses(hostname: str, port: int) -> set[str]:
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SkillError(
            "WEB_DNS_ERROR",
            f"DNS resolution failed for {hostname}",
            category="network",
            retryable=True,
        ) from exc
    addresses = {record[4][0] for record in records}
    if not addresses:
        raise SkillError(
            "WEB_DNS_ERROR",
            f"DNS returned no address for {hostname}",
            category="network",
            retryable=True,
        )
    for value in addresses:
        _validate_public_ip(value)
    return addresses


def _validate_url(
    url: str,
    allowed_domains: list[str] | None,
    allow_http: bool,
) -> tuple[str, set[str]]:
    if not isinstance(url, str) or not url.strip():
        raise SkillError("INVALID_ARGUMENT", "url must be a non-empty string", category="validation")
    if len(url) > 2048:
        raise SkillError("INPUT_LIMIT_EXCEEDED", "url must not exceed 2048 characters", category="limit")
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise SkillError(
            "URL_SCHEME_DENIED",
            "only HTTP(S) URLs are allowed",
            category="security",
        )
    if parsed.username is not None or parsed.password is not None:
        raise SkillError(
            "URL_CREDENTIALS_DENIED",
            "credential-bearing URLs are not allowed",
            category="security",
        )
    hostname = parsed.hostname
    if not hostname:
        raise SkillError("INVALID_URL", "url must include a hostname", category="validation")
    hostname = hostname.rstrip(".").casefold()
    try:
        direct_address = ipaddress.ip_address(hostname)
    except ValueError:
        direct_address = None
    if direct_address is not None:
        _validate_public_ip(str(direct_address))
    elif hostname == "localhost" or hostname.endswith(".localhost"):
        raise SkillError(
            "URL_PRIVATE_ADDRESS",
            "localhost URLs are not allowed",
            category="security",
        )
    if scheme == "http" and not allow_http:
        raise SkillError(
            "URL_SCHEME_DENIED",
            "unencrypted HTTP is disabled by configuration",
            category="security",
        )
    domains = allowed_domains or []
    if not all(isinstance(item, str) and item.strip() for item in domains):
        raise SkillError(
            "WEB_POLICY_ERROR",
            "allowed_domains must contain non-empty domain names",
            category="configuration",
        )
    if not domains or not _is_allowed_domain(hostname, domains):
        raise SkillError(
            "URL_DOMAIN_DENIED",
            f"domain is not in the configured allowlist: {hostname}",
            category="security",
        )
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise SkillError("INVALID_URL", "url contains an invalid port", category="validation") from exc
    addresses = _resolve_public_addresses(hostname, port)
    normalized = urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    return normalized, addresses


def _peer_address(response: Any) -> str | None:
    connection = getattr(response.raw, "_connection", None)
    socket_object = getattr(connection, "sock", None)
    if socket_object is None:
        original = getattr(response.raw, "_original_response", None)
        stream = getattr(getattr(original, "fp", None), "raw", None)
        socket_object = getattr(stream, "_sock", None)
    if socket_object is None:
        return None
    try:
        return str(socket_object.getpeername()[0])
    except OSError:
        return None


def web_fetcher(
    url: str,
    max_chars: int = 20_000,
    timeout_seconds: float = 5.0,
    *,
    allowed_domains: list[str] | None = None,
    allow_http: bool = False,
    max_response_bytes: int = 1_000_000,
    allowed_content_types: list[str] | None = None,
    max_redirects: int = 3,
) -> dict[str, Any]:
    """Fetch bounded text from an allowlisted public HTTP(S) endpoint.

    Args:
        url: Absolute HTTP(S) URL without embedded credentials.
        max_chars: Maximum decoded response characters returned.
        timeout_seconds: Connect and read timeout for each request.
        allowed_domains: Framework-injected exact or parent domain allowlist.
        allow_http: Framework-injected policy permitting unencrypted HTTP.
        max_response_bytes: Framework-injected upper bound for response bytes.
        allowed_content_types: Framework-injected textual Content-Type prefixes.
        max_redirects: Framework-injected maximum manually validated redirects.

    Returns:
        A mapping with final URL, status, content type, bounded text, and redirects.

    Raises:
        SkillError: If URL policy, DNS, peer, redirect, response, or limits fail.
    """
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 1 <= max_chars <= 100_000:
        raise SkillError(
            "INVALID_ARGUMENT",
            "max_chars must be an integer between 1 and 100000",
            category="validation",
        )
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise SkillError(
            "INVALID_ARGUMENT",
            "timeout_seconds must be a number",
            category="validation",
        )
    timeout = float(timeout_seconds)
    if not 0.1 <= timeout <= 15.0:
        raise SkillError(
            "INVALID_ARGUMENT",
            "timeout_seconds must be between 0.1 and 15",
            category="validation",
        )
    if not isinstance(max_response_bytes, int) or isinstance(max_response_bytes, bool) or not 1024 <= max_response_bytes <= 10_000_000:
        raise SkillError(
            "WEB_POLICY_ERROR",
            "max_response_bytes must be between 1024 and 10000000",
            category="configuration",
        )
    if not isinstance(max_redirects, int) or isinstance(max_redirects, bool) or not 0 <= max_redirects <= 10:
        raise SkillError(
            "WEB_POLICY_ERROR",
            "max_redirects must be between 0 and 10",
            category="configuration",
        )
    content_types = allowed_content_types or list(DEFAULT_CONTENT_TYPES)
    if not all(isinstance(item, str) and item for item in content_types):
        raise SkillError(
            "WEB_POLICY_ERROR",
            "allowed_content_types must contain non-empty strings",
            category="configuration",
        )
    try:
        import requests
    except ImportError as exc:
        raise SkillError(
            "WEB_DEPENDENCY_UNAVAILABLE",
            "requests is not installed",
            category="dependency",
        ) from exc

    session = requests.Session()
    session.trust_env = False
    redirects: list[dict[str, Any]] = []
    current_url = url
    try:
        for redirect_index in range(max_redirects + 1):
            validated_url, resolved_addresses = _validate_url(current_url, allowed_domains, allow_http)
            try:
                response = session.get(
                    validated_url,
                    allow_redirects=False,
                    stream=True,
                    timeout=(timeout, timeout),
                    headers={"User-Agent": "assignment-agent-web-fetcher/1.0"},
                )
            except requests.Timeout as exc:
                raise SkillError(
                    "WEB_TIMEOUT",
                    "web request timed out",
                    category="network",
                    retryable=True,
                ) from exc
            except requests.RequestException as exc:
                raise SkillError(
                    "TRANSIENT_NETWORK_ERROR",
                    f"web request failed: {exc}",
                    category="network",
                    retryable=True,
                ) from exc

            peer = _peer_address(response)
            if peer is None:
                response.close()
                raise SkillError(
                    "WEB_PEER_UNVERIFIED",
                    "unable to verify the connected peer address",
                    category="security",
                )
            _validate_public_ip(peer)
            if peer not in resolved_addresses:
                response.close()
                raise SkillError(
                    "WEB_DNS_REBINDING_DETECTED",
                    "connected peer does not match preflight DNS results",
                    category="security",
                )

            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise SkillError(
                        "WEB_REDIRECT_ERROR",
                        "redirect response is missing Location",
                        category="network",
                    )
                if redirect_index >= max_redirects:
                    raise SkillError(
                        "WEB_REDIRECT_LIMIT_EXCEEDED",
                        "web request exceeded the redirect limit",
                        category="limit",
                    )
                next_url = urljoin(validated_url, location)
                _validate_url(next_url, allowed_domains, allow_http)
                redirects.append({"status_code": response.status_code, "url": next_url})
                current_url = next_url
                continue

            if response.status_code == 429 or response.status_code >= 500:
                status_code = response.status_code
                response.close()
                raise SkillError(
                    "HTTP_RETRYABLE_ERROR",
                    f"HTTP request failed with status {status_code}",
                    category="network",
                    retryable=True,
                    details={"status_code": status_code},
                )
            if response.status_code >= 400:
                status_code = response.status_code
                response.close()
                raise SkillError(
                    "HTTP_ERROR",
                    f"HTTP request failed with status {status_code}",
                    category="network",
                    details={"status_code": status_code},
                )

            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
            if not any(content_type == item.casefold() or content_type.startswith(f"{item.casefold()}+") for item in content_types):
                response.close()
                raise SkillError(
                    "CONTENT_TYPE_DENIED",
                    f"response Content-Type is not allowed: {content_type or '<missing>'}",
                    category="security",
                )
            content_length = response.headers.get("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > max_response_bytes:
                response.close()
                raise SkillError(
                    "RESPONSE_SIZE_LIMIT_EXCEEDED",
                    "response Content-Length exceeds the configured limit",
                    category="limit",
                )
            chunks = bytearray()
            truncated_bytes = False
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                remaining = max_response_bytes - len(chunks)
                if len(chunk) > remaining:
                    chunks.extend(chunk[:remaining])
                    truncated_bytes = True
                    break
                chunks.extend(chunk)
            encoding = response.encoding or "utf-8"
            status_code = response.status_code
            final_url = validated_url
            response.close()
            text = bytes(chunks).decode(encoding, errors="replace")
            truncated_chars = len(text) > max_chars
            return {
                "url": final_url,
                "status_code": status_code,
                "content_type": content_type,
                "text": text[:max_chars],
                "num_bytes": len(chunks),
                "truncated": truncated_bytes or truncated_chars,
                "redirects": redirects,
            }
    finally:
        session.close()
    raise SkillError("WEB_REDIRECT_ERROR", "web request did not produce a response", category="network")
