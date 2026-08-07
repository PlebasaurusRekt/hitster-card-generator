"""Validation helpers for untrusted web-app input."""

from __future__ import annotations

import io
import math
import re
import urllib.parse

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_INPUT_CHARS = 50_000
MAX_TRACK_LINKS = 500
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_PIXELS = 40_000_000
MAX_UPLOAD_DIMENSION = 12_000
MAX_UPLOAD_ASPECT_RATIO = 10.0
SVG_RASTER_MAX_DIMENSION = 2_000
SPOTIFY_WEB_HOST = "open.spotify.com"
SPOTIFY_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{22}$")
SVG_LENGTH_PATTERN = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"\s*(px|pt|pc|mm|cm|in)?\s*$"
)
SVG_EXTERNAL_REFERENCE_PATTERN = re.compile(
    r"(?:href|src)\s*=\s*['\"]\s*(?:https?:|//|file:)|"
    r"url\(\s*['\"]?\s*(?:https?:|//|file:)|"
    r"@import\s+(?:url\()?\s*['\"]?(?:https?:|//|file:)",
    re.IGNORECASE,
)


class InputValidationError(ValueError):
    """Raised when user-provided content is unsafe or unsupported."""


def _looks_like_svg(raw: bytes) -> bool:
    """Return whether an upload starts with an SVG document."""
    prefix = raw.lstrip(b"\xef\xbb\xbf\t\n\r ")[:1_024].lower()
    return prefix.startswith(b"<svg") or (
        prefix.startswith(b"<?xml") and b"<svg" in prefix
    )


def _svg_length_to_pixels(value: str | None) -> float | None:
    """Convert a simple, absolute SVG length to CSS pixels."""
    if value is None:
        return None
    match = SVG_LENGTH_PATTERN.fullmatch(value)
    if not match:
        return None
    number = float(match.group(1))
    if not math.isfinite(number):
        return None
    unit = match.group(2) or "px"
    pixels_per_unit = {
        "px": 1,
        "pt": 96 / 72,
        "pc": 16,
        "mm": 96 / 25.4,
        "cm": 96 / 2.54,
        "in": 96,
    }
    return number * pixels_per_unit[unit]


def _get_svg_aspect_ratio(raw: bytes) -> float:
    """Read a safe SVG's viewport ratio without using a browser default size."""
    try:
        root = ElementTree.fromstring(raw)
    except (DefusedXmlException, ElementTree.ParseError) as exc:
        raise InputValidationError("The uploaded SVG is not valid XML.") from exc

    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise InputValidationError("The uploaded file is not an SVG image.")

    width = _svg_length_to_pixels(root.get("width"))
    height = _svg_length_to_pixels(root.get("height"))
    if width is not None and height is not None:
        if (
            not math.isfinite(width)
            or not math.isfinite(height)
            or width <= 0
            or height <= 0
        ):
            raise InputValidationError("The uploaded SVG has invalid dimensions.")
        return width / height

    view_box = root.get("viewBox")
    if view_box:
        try:
            _, _, view_width, view_height = [
                float(value) for value in re.split(r"[\s,]+", view_box.strip())
            ]
        except ValueError as exc:
            raise InputValidationError(
                "The uploaded SVG has an invalid viewBox."
            ) from exc
        if (
            math.isfinite(view_width)
            and math.isfinite(view_height)
            and view_width > 0
            and view_height > 0
        ):
            return view_width / view_height

    raise InputValidationError(
        "SVG uploads need width and height or a valid viewBox."
    )


def rasterize_svg_image(raw: bytes, width: int, height: int) -> Image.Image:
    """Rasterize a previously validated SVG at an exact, bounded target size."""
    try:
        import cairosvg

        png_bytes = cairosvg.svg2png(
            bytestring=raw,
            output_width=width,
            output_height=height,
            unsafe=False,
        )
        with Image.open(io.BytesIO(png_bytes)) as opened:
            safe_image = opened.convert("RGBA").copy()
    except (ImportError, OSError) as exc:
        raise InputValidationError(
            "SVG support is unavailable on this server."
        ) from exc
    except Exception as exc:
        # CairoSVG may raise parser- and CSS-specific exception types for
        # otherwise well-formed SVG XML. Treat all renderer failures as
        # invalid uploads instead of surfacing them through the web app.
        raise InputValidationError(
            "The uploaded SVG could not be rendered safely."
        ) from exc

    return safe_image


def _load_svg_image(raw: bytes) -> Image.Image:
    """Validate and rasterize a bounded SVG while retaining its vector source."""
    try:
        svg_text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputValidationError("The uploaded SVG must use UTF-8 encoding.") from exc
    if "<!DOCTYPE" in svg_text.upper():
        raise InputValidationError("SVG uploads cannot include a DOCTYPE declaration.")
    if SVG_EXTERNAL_REFERENCE_PATTERN.search(svg_text):
        raise InputValidationError("SVG uploads cannot reference external files.")

    aspect_ratio = _get_svg_aspect_ratio(raw)
    if aspect_ratio > MAX_UPLOAD_ASPECT_RATIO or aspect_ratio < 1 / MAX_UPLOAD_ASPECT_RATIO:
        raise InputValidationError("The uploaded image is too narrow or panoramic.")

    if aspect_ratio >= 1:
        width = SVG_RASTER_MAX_DIMENSION
        height = round(width / aspect_ratio)
    else:
        height = SVG_RASTER_MAX_DIMENSION
        width = round(height * aspect_ratio)
    safe_image = rasterize_svg_image(raw, max(1, width), max(1, height))
    safe_image.info['svg_bytes'] = raw
    return safe_image


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

    if _looks_like_svg(raw):
        return _load_svg_image(raw)

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
