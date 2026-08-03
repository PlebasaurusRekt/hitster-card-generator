"""Validation helpers for untrusted web-app input."""

from __future__ import annotations

import io
import re
import urllib.parse

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_INPUT_CHARS = 50_000
MAX_TRACK_LINKS = 500
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_PIXELS = 40_000_000
MAX_UPLOAD_DIMENSION = 12_000
MAX_UPLOAD_ASPECT_RATIO = 10.0
SPOTIFY_WEB_HOST = "open.spotify.com"
SPOTIFY_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{22}$")


class InputValidationError(ValueError):
    """Raised when user-provided content is unsafe or unsupported."""


def canonicalize_spotify_url(url: str, expected_kind: str | None = None) -> str:
    """Validate and return a canonical Spotify track or playlist URL."""
    candidate = str(url).strip()
    try:
        parsed = urllib.parse.urlsplit(candidate)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise InputValidationError("Invalid Spotify URL.") from exc

    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != SPOTIFY_WEB_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise InputValidationError(
            "Spotify links must use https://open.spotify.com."
        )

    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) == 3 and parts[0].lower().startswith("intl-"):
        parts = parts[1:]
    if len(parts) != 2 or parts[0] not in {"track", "playlist"}:
        raise InputValidationError(
            "Use a Spotify track or playlist share URL."
        )

    kind, spotify_id = parts
    if expected_kind is not None and kind != expected_kind:
        raise InputValidationError(f"Expected a Spotify {expected_kind} URL.")
    if not SPOTIFY_ID_PATTERN.fullmatch(spotify_id):
        raise InputValidationError("Spotify links contain an invalid ID.")

    return f"https://{SPOTIFY_WEB_HOST}/{kind}/{spotify_id}"


def classify_spotify_input(text: str):
    """Return (kind, value) for validated user input."""
    value = str(text or "")
    if not value.strip():
        return "empty", None
    if len(value) > MAX_INPUT_CHARS:
        raise InputValidationError(
            f"Input is too long; paste at most {MAX_INPUT_CHARS:,} characters."
        )

    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) > MAX_TRACK_LINKS:
        raise InputValidationError(
            f"A maximum of {MAX_TRACK_LINKS} tracks can be processed at once."
        )

    if len(lines) == 1:
        try:
            return "playlist", canonicalize_spotify_url(
                lines[0], expected_kind="playlist"
            )
        except InputValidationError:
            return "tracks", [
                canonicalize_spotify_url(lines[0], expected_kind="track")
            ]

    return "tracks", [
        canonicalize_spotify_url(line, expected_kind="track")
        for line in lines
    ]


def load_uploaded_image(uploaded_file) -> Image.Image:
    """Decode a bounded image upload and return a detached PIL image."""
    raw = uploaded_file.getvalue()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise InputValidationError(
            f"Images must be no larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0:
                raise InputValidationError("The uploaded image has invalid dimensions.")
            if (
                width > MAX_UPLOAD_DIMENSION
                or height > MAX_UPLOAD_DIMENSION
                or width * height > MAX_UPLOAD_PIXELS
            ):
                raise InputValidationError("The uploaded image dimensions are too large.")
            aspect_ratio = max(width / height, height / width)
            if aspect_ratio > MAX_UPLOAD_ASPECT_RATIO:
                raise InputValidationError(
                    "The uploaded image is too narrow or panoramic."
                )

            opened.load()
            safe_image = ImageOps.exif_transpose(opened).copy()
    except InputValidationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise InputValidationError(
            "The uploaded file is not a supported, safe image."
        ) from exc

    return safe_image
