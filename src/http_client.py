"""Bounded HTTP helpers used by metadata providers."""

from __future__ import annotations

import threading
import urllib.parse

import requests

from src.input_validation import canonicalize_spotify_url


MAX_SPOTIFY_HTML_BYTES = 2 * 1024 * 1024
MAX_SPOTIFY_REDIRECTS = 3
_session_local = threading.local()


class ResponseTooLargeError(requests.RequestException):
    """Raised before buffering an oversized provider response."""


def get_http_session() -> requests.Session:
    """Return one requests session per worker thread."""
    session = getattr(_session_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "hitster-card-generator/2.0"})
        _session_local.session = session
    return session


def _read_limited_response(response, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise ResponseTooLargeError(
                    f"Provider response exceeded {max_bytes:,} bytes.",
                    response=response,
                )
        except ValueError:
            pass

    body = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ResponseTooLargeError(
                f"Provider response exceeded {max_bytes:,} bytes.",
                response=response,
            )
    return bytes(body)


def get_spotify_html(
    url: str, expected_kind: str, timeout: int = 10
) -> tuple[str, str]:
    """Fetch a validated Spotify page with bounded, validated redirects."""
    current_url = canonicalize_spotify_url(url, expected_kind=expected_kind)
    session = get_http_session()

    for _ in range(MAX_SPOTIFY_REDIRECTS + 1):
        with session.get(
            current_url,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location:
                    response.raise_for_status()
                redirected = urllib.parse.urljoin(current_url, location)
                current_url = canonicalize_spotify_url(
                    redirected, expected_kind=expected_kind
                )
                continue

            response.raise_for_status()
            body = _read_limited_response(response, MAX_SPOTIFY_HTML_BYTES)
            encoding = response.encoding or "utf-8"
            return body.decode(encoding, errors="replace"), current_url

    raise requests.TooManyRedirects(
        f"Spotify redirected more than {MAX_SPOTIFY_REDIRECTS} times."
    )


def get_bounded_https_content(
    url: str,
    *,
    allowed_hosts: set[str],
    max_bytes: int,
    timeout: int = 10,
) -> tuple[bytes, str, int]:
    """Fetch bounded HTTPS content without leaving an explicit host allowlist."""
    current_url = str(url)
    session = get_http_session()
    for _ in range(MAX_SPOTIFY_REDIRECTS + 1):
        try:
            parsed = urllib.parse.urlsplit(current_url)
            valid_port = parsed.port in (None, 443)
        except ValueError as exc:
            raise requests.exceptions.InvalidURL("Invalid provider URL.") from exc
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or not valid_port
        ):
            raise requests.exceptions.InvalidURL("Provider URL left its allowed hosts.")

        with session.get(
            current_url,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location:
                    response.raise_for_status()
                current_url = urllib.parse.urljoin(current_url, location)
                continue
            response.raise_for_status()
            return (
                _read_limited_response(response, max_bytes),
                current_url,
                response.status_code,
            )
    raise requests.TooManyRedirects(
        f"Provider redirected more than {MAX_SPOTIFY_REDIRECTS} times."
    )
