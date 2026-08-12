import base64
import binascii
import gc
import hashlib
import hmac
import io
import json
import colorsys
import math
import time
import os
import random
import secrets
import re
import zlib
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import qrcode
import requests
import numpy as np
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import cm

from src.http_client import (
    MAX_SPOTIFY_HTML_BYTES, get_bounded_https_content, get_http_session,
    get_spotify_html,
)
from src.input_validation import (
    MAX_TRACK_LINKS, InputValidationError, canonicalize_spotify_url,
    rasterize_svg_image,
)

UTILS_API_VERSION = 6
CARD_PHYSICAL_SIZE_CM = 6.5
DEFAULT_QR_CODE_SIZE_CM = 2.5
DEFAULT_QR_BORDER_CM = 0.1
DEFAULT_QR_TOTAL_SIZE_CM = (
    DEFAULT_QR_CODE_SIZE_CM + 2 * DEFAULT_QR_BORDER_CM
)
DEFAULT_QR_SIZE_RATIO = DEFAULT_QR_CODE_SIZE_CM / CARD_PHYSICAL_SIZE_CM
NEON_RING_EDGE_CLEARANCE_CM = 0.3
# Keep the artist ink edge aligned 8 mm below the top trim edge.
SONG_ARTIST_TOP_EDGE_OFFSET_CM = 0.8
# The raster-to-print calibration puts the rendered song-title ink 0.1 cm
# farther from the trim edge than its coordinate says.  Use 0.8 cm here so
# its printed bottom clearance is the requested 0.9 cm.
SONG_TITLE_BOTTOM_EDGE_OFFSET_CM = 0.8
# Keep artist and song-title text at least 2 mm from the central song year.
SONG_TEXT_TO_YEAR_CLEARANCE_CM = 0.2
SOLUTION_TITLE_TOP_OFFSET_CM = 0.2
SOLUTION_TITLE_LEFT_OFFSET_CM = 0.2
CARD_NUMBER_RIGHT_OFFSET_CM = 0.3
CARD_NUMBER_BOTTOM_OFFSET_CM = 0.3
SONG_ARTIST_TO_YEAR_GAP_CM = 1.4
SONG_YEAR_TO_TITLE_GAP_CM = 1.3
# At the 2000 px preview resolution, this keeps a centered Montserrat year
# at the requested distances from the capital M in "Miley Cyrus" and the
# capital W in "Wrecking Ball". PDF rendering scales the value with the card.
DEFAULT_SONG_YEAR_SIZE = 604


def card_distance_cm_to_pixels(card_size, distance_cm):
    """Convert a physical card distance in centimeters to raster pixels."""
    return round(card_size * distance_cm / CARD_PHYSICAL_SIZE_CM)


def get_qr_code_size_pixels(settings):
    """Return the QR raster side length for the current card resolution."""
    return max(1, round(settings['card_size'] * settings['qr_size_ratio']))


def get_qr_render_geometry(qr_code, settings):
    """Return module-aligned geometry for the borderless QR data square."""
    module_count = int(qr_code.info.get('qr_module_count', 0))
    if module_count <= 0:
        module_count = max(1, qr_code.width // 10)
    requested_side = get_qr_code_size_pixels(settings)
    pixels_per_module = max(1, requested_side // module_count)
    return (
        module_count,
        pixels_per_module,
        module_count * pixels_per_module,
    )


def get_qr_backplate_padding_pixels(settings):
    """Return the sole physical QR quiet-zone width in pixels."""
    padding_cm = settings.get('qr_backplate_padding_cm')
    if padding_cm is not None:
        return max(0, card_distance_cm_to_pixels(
            settings['card_size'], padding_cm
        ))
    return max(0, int(settings.get('qr_backplate_padding', 0)))


def to_rgba(color):
    """Convert a color to (r, g, b, a) floats in 0..1.

    Replaces matplotlib.colors.to_rgba for our use (hex strings from color
    pickers, plus the occasional RGB(A) tuple), so the app doesn't pull in
    the heavy matplotlib dependency just for color parsing. For "#RRGGBB"
    input the result is byte-identical to matplotlib's (both do int/255).
    """
    if isinstance(color, (tuple, list)):
        vals = [float(v) for v in color]
        if any(v > 1 for v in vals):  # looks like 0..255
            vals = [v / 255.0 for v in vals]
        if len(vals) == 3:
            vals.append(1.0)
        return tuple(vals[:4])

    s = str(color).strip().lstrip('#')
    try:
        if len(s) in (3, 4):  # shorthand: expand each nibble
            s = ''.join(ch * 2 for ch in s)
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
        a = int(s[6:8], 16) / 255.0 if len(s) >= 8 else 1.0
        return (r, g, b, a)
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0, 1.0)  # safe fallback, matches old except branches


def hex2color(color):
    """RGB-only variant of to_rgba (replaces matplotlib.colors.hex2color)."""
    return to_rgba(color)[:3]

# =============================================================================
# DEFAULT DESIGN SETTINGS
# =============================================================================
DEFAULT_DESIGN_SETTINGS = {
    "card_size": 2000,
    "pdf_print_profile": "a4",
    "ink_saving_mode": False,
    "card_draw_border": False,
    "card_border_color": (255, 255, 255),
    "card_number_start": 1,
    "neon_colors": [(255, 0, 100), (0, 200, 255), (0, 255, 120), (255, 255, 0)],

    "fonts_dict": {
        "year": os.path.join("fonts", "Montserrat-Bold.ttf"),
        "artist": os.path.join("fonts", "Montserrat-SemiBold.ttf"),
        "song": os.path.join("fonts", "Montserrat-MediumItalic.ttf"),
    },
    "color_gradient": [
        "#7030A0", "#E31C79", "#FF6B9D", "#FFA500",
        "#FFD700", "#87CEEB", "#4169E1",
    ],

    "google_font": "Montserrat",
    "card_number_font_weight": 600,
    "card_set_title_font_weight": 500,
    "song_artist_font_weight": 500,
    "song_year_font_weight": 700,
    "song_title_font_weight": 300,

    # QR Side Settings
    "qr_bg_type": "neon_rings", # "solid", "neon_rings", "image"
    "qr_bg_color": (0, 0, 0),
    "qr_bg_image": None, # PIL Image object
    "qr_bg_scale": 1.0,
    "qr_bg_offset_x": 0.0,
    "qr_bg_offset_y": 0.0,
    
    "qr_background_mode": "solid", # "transparent" or "solid"
    "qr_background_color": (0, 0, 0), # solid backplate color
    "qr_module_color": (255, 255, 255),
    "qr_backplate_padding": 0,
    "qr_backplate_padding_cm": DEFAULT_QR_BORDER_CM,
    "qr_backplate_radius": 0,
    "qr_size_ratio": DEFAULT_QR_SIZE_RATIO,
    
    "neon_ring_opacity": 1.0,
    "neon_ring_thickness": 12,
    "neon_ring_count": 14,
    
    "qr_title": "",
    "qr_title_enabled": False,
    "qr_title_pos": "top", # "top", "bottom", "center_above_qr", "center_below_qr"
    "qr_title_size": 80,
    "qr_title_color": (255, 255, 255),
    "qr_title_bg": False,
    "qr_title_image": None,
    "qr_card_number_opacity": 42,
    "qr_pages_upside_down": False,

    # Solution Side Settings
    "sol_bg_type": "gradient", # "gradient", "image"
    "sol_bg_image": None,
    "sol_bg_scale": 1.0,
    "sol_bg_offset_x": 0.0,
    "sol_bg_offset_y": 0.0,
    "sol_color_wash_enabled": True,
    "sol_color_wash_grain_opacity": 0.012,
    "sol_border_width": 142,

    "song_year_size": DEFAULT_SONG_YEAR_SIZE,
    "song_artist_size": 155,
    "song_title_size": 155,
    "card_number_size": 70,

    "sol_title": "",
    "sol_title_enabled": False,
    "sol_title_pos": "in_border_top_left",
    "sol_title_size": 188,
    "sol_title_color": (255, 255, 255),
    "sol_title_opacity": 60,
    "sol_title_bg": False,
    "sol_title_image": None,
}

def get_settings(override=None):
    """Get settings merged with defaults."""
    settings = DEFAULT_DESIGN_SETTINGS.copy()
    provided_settings = {}
    if override:
        provided_settings.update(override)
    settings.update(provided_settings)
    card_label = str(settings.get('card_label') or '').strip()
    if card_label:
        if not settings.get('qr_title'):
            settings['qr_title'] = card_label
            settings['qr_title_enabled'] = True
        if not settings.get('sol_title'):
            settings['sol_title'] = card_label
            settings['sol_title_enabled'] = True
    if (
        'qr_backplate_padding' in provided_settings
        and 'qr_backplate_padding_cm' not in provided_settings
    ):
        settings['qr_backplate_padding_cm'] = None
    # Ensure colors are tuples if they came as strings
    for key in [
        "card_border_color", "qr_bg_color", "qr_background_color",
        "qr_module_color", "qr_title_color", "sol_title_color",
        "sol_color_wash_base_color",
    ]:
        if isinstance(settings.get(key), str):
            try:
                settings[key] = tuple(int(c * 255) for c in to_rgba(settings[key]))
            except (TypeError, ValueError):
                pass
    return settings

def stable_seed(value):
    """Return a reproducible unsigned 64-bit seed for arbitrary text."""
    digest = hashlib.sha256(str(value).encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big')


def build_generation_fingerprint(records, settings):
    """Return a stable digest for every input that affects a generated PDF."""
    def normalize(value):
        if isinstance(value, Image.Image):
            image_data = {
                'image_mode': value.mode,
                'image_size': value.size,
                'image_sha256': hashlib.sha256(value.tobytes()).hexdigest(),
            }
            if value.info.get('svg_bytes'):
                image_data['svg_sha256'] = hashlib.sha256(
                    value.info['svg_bytes']
                ).hexdigest()
            return image_data
        if isinstance(value, dict):
            return {
                str(key): normalize(item)
                for key, item in sorted(
                    value.items(), key=lambda pair: str(pair[0])
                )
            }
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        if isinstance(value, np.generic):
            return normalize(value.item())
        if isinstance(value, float) and math.isnan(value):
            return None
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return repr(value)

    payload = json.dumps(
        {
            'records': normalize(records),
            'settings': normalize(settings),
        },
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


# =============================================================================
# YEAR VALIDATION
# =============================================================================
MIN_VALID_YEAR = 1500
MAX_VALID_YEAR = datetime.now().year + 1

def _validate_year(year: int | None) -> int | None:
    """Return year only if it falls in a plausible range, else None."""
    if year is None:
        return None
    if MIN_VALID_YEAR <= year <= MAX_VALID_YEAR:
        return year
    return None

# =============================================================================
# Year fetching functions using MusicBrainz and iTunes APIs
# =============================================================================
_musicbrainz_lock = threading.Lock()
_musicbrainz_last_request_at = 0.0


@lru_cache(maxsize=2048)
def get_year_from_musicbrainz(title, artist) -> int | None:
    """Query MusicBrainz with its required global request spacing."""
    query = f'recording:"{title}" AND artist:"{artist}"'
    params = {"query": query, "fmt": "json", "limit": 5}
    headers = {
        "User-Agent": (
            "hitster-card-generator/2.0 "
            "(https://github.com/PlebasaurusRekt/hitster-card-generator)"
        )
    }
    global _musicbrainz_last_request_at
    try:
        with _musicbrainz_lock:
            elapsed = time.monotonic() - _musicbrainz_last_request_at
            wait_seconds = max(0.0, 1.1 - elapsed)
            if wait_seconds:
                time.sleep(wait_seconds)
            response = get_http_session().get(
                "https://musicbrainz.org/ws/2/recording",
                params=params,
                headers=headers,
                timeout=10,
            )
            _musicbrainz_last_request_at = time.monotonic()
            if response.status_code in (429, 503):
                retry_after = response.headers.get('Retry-After', '2')
                try:
                    retry_seconds = min(5.0, max(1.0, float(retry_after)))
                except ValueError:
                    retry_seconds = 2.0
                time.sleep(retry_seconds)
                response = get_http_session().get(
                    "https://musicbrainz.org/ws/2/recording",
                    params=params,
                    headers=headers,
                    timeout=10,
                )
                _musicbrainz_last_request_at = time.monotonic()
        response.raise_for_status()
        result_json = response.json()
        years = []
        if not isinstance(result_json, dict):
            return None
        for recording in result_json.get("recordings", []):
            for release in recording.get("releases", []) or []:
                date = release.get("date")
                if date:
                    year = _validate_year(int(str(date).split("-")[0]))
                    if year is not None:
                        years.append(year)
        return min(years) if years else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        requests.RequestException,
        requests.JSONDecodeError,
    ):
        return None


@lru_cache(maxsize=2048)
def get_year_from_itunes(title, artist) -> int | None:
    """Return the earliest plausible iTunes release year."""
    query = urllib.parse.quote(f"{artist} {title}")
    url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=5"
    try:
        response = get_http_session().get(url, timeout=8)
        response.raise_for_status()
        result_json = response.json()
        if not isinstance(result_json, dict):
            return None
        years = []
        for result in result_json.get("results", []):
            release_date = result.get("releaseDate")
            if release_date:
                year = _validate_year(
                    int(str(release_date).split("-")[0])
                )
                if year is not None:
                    years.append(year)
        return min(years) if years else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        requests.RequestException,
        requests.JSONDecodeError,
    ):
        return None


@lru_cache(maxsize=2048)
def get_year_and_source(
    title, artist, orig_year
) -> tuple[int | None, str | None]:
    """Resolve a release year using cached iTunes/MusicBrainz fallbacks."""
    itunes_year = get_year_from_itunes(title, artist)
    if itunes_year is not None:
        return itunes_year, 'iTunes'

    musicbrainz_year = get_year_from_musicbrainz(title, artist)
    if musicbrainz_year is not None:
        return musicbrainz_year, 'MusicBrainz'

    validated = _validate_year(orig_year)
    if validated is not None:
        return validated, 'Spotify'
    return None, None


# =============================================================================
# NAME SANITIZATION
# =============================================================================
def sanitize_name(name):
    """Remove common version/edition suffixes from song title.

    Strips remaster, live, acoustic, radio edit, feat., extended mix, etc.
    Both parenthetical forms (Live) / [Live] and dash forms - Live are handled.
    """
    # Keyword groups for inside parentheses/brackets — feat. may contain any char except the closing bracket
    _PAREN = (
        r'(?:\d{4}\s*)?remaster(?:ed)?(?:\s*\d{4})?'
        r'|live(?:\s+[^\)\]]*)?'
        r'|acoustic(?:\s+version)?'
        r'|radio\s+edit'
        r'|(?:original|extended|club|deluxe)\s+(?:mix|version|edit)'
        r'|(?:single|album)\s+version'
        r'|bonus\s+track'
        r'|(?:mono|stereo)'
        r'|(?:feat|ft|featuring)\.?\s+[^\)\]]+'
    )
    # Keyword groups after a dash/slash — feat. and live may consume the rest of the title
    _DASH = (
        r'(?:\d{4}\s*)?remaster(?:ed)?(?:\s*\d{4})?'
        r'|(?:\d{4}\s*)?version(?:\s*\d{4})?'
        r'|live(?:\s+.+)?'
        r'|acoustic(?:\s+version)?'
        r'|radio\s+edit'
        r'|(?:original|extended|club|deluxe)\s+(?:mix|version|edit)'
        r'|(?:single|album)\s+version'
        r'|bonus\s+track'
        r'|(?:mono|stereo)'
        r'|(?:feat|ft|featuring)\.?\s+.+'
    )
    pattern = rf'\s*[\(\[](?:{_PAREN})[\)\]]|\s*[-/]\s*(?:{_DASH})'
    return re.sub(pattern, '', name, flags=re.IGNORECASE).strip()


# =============================================================================
# NO-API SCRAPER FUNCTIONS (FALLBACK)
# =============================================================================

def fetch_no_api_data(links_file):
    """Scrapes metadata from public Spotify pages based on links.txt."""
    if not os.path.exists(links_file):
        return None
        
    print(f"Found {links_file}. Switching to No-API Scraper Mode...")
    with open(links_file, 'r', encoding='utf-8') as file_handle:
        raw_urls = [
            line.strip()
            for line in file_handle
            if line.strip()
        ]
    if len(raw_urls) > MAX_TRACK_LINKS:
        raise SpotifyAPIError(
            f"A maximum of {MAX_TRACK_LINKS} tracks can be processed at once."
        )
    try:
        urls = [
            canonicalize_spotify_url(url, expected_kind='track')
            for url in raw_urls
        ]
    except InputValidationError as exc:
        raise SpotifyAPIError(str(exc)) from exc

    return fetch_no_api_data_from_list(urls)

# =============================================================================
# SPOTIFY API FUNCTIONS
# =============================================================================

class SpotifyAPIError(RuntimeError):
    """Raised when Spotify authentication or playlist access fails."""


class SpotifyAuthenticationError(SpotifyAPIError):
    """Raised when Spotify user authorization must be renewed."""


SPOTIFY_OAUTH_SCOPES = (
    'playlist-read-private',
    'playlist-read-collaborative',
)
SPOTIFY_OAUTH_STATE_TTL_SECONDS = 10 * 60


def _validate_spotify_redirect_uri(redirect_uri):
    """Validate an exact OAuth callback URI and return it unchanged."""
    redirect_uri = str(redirect_uri).strip()
    if not redirect_uri or len(redirect_uri) > 2048:
        raise SpotifyAPIError("Enter a valid Spotify redirect URI.")
    try:
        parsed = urllib.parse.urlsplit(redirect_uri)
        parsed.port
    except ValueError as exc:
        raise SpotifyAPIError("Enter a valid Spotify redirect URI.") from exc

    is_loopback = parsed.hostname in ('127.0.0.1', '::1')
    is_allowed = (
        parsed.scheme == 'https'
        or (parsed.scheme == 'http' and is_loopback)
    )
    if (
        not is_allowed
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise SpotifyAPIError(
            "The redirect URI must use HTTPS, or HTTP with 127.0.0.1/::1."
        )
    return redirect_uri


def _urlsafe_b64encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _urlsafe_b64decode(value):
    padding = '=' * (-len(value) % 4)
    try:
        return base64.b64decode(
            value + padding, altchars=b'-_', validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise SpotifyAPIError(
            "Spotify login security state was invalid. Start again."
        ) from exc


def _create_spotify_oauth_state(client_id, client_secret, redirect_uri):
    payload = json.dumps(
        {
            'client_id': client_id,
            'created_at': int(time.time()),
            'nonce': secrets.token_urlsafe(18),
            'redirect_uri': redirect_uri,
            'version': 1,
        },
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')
    payload_segment = _urlsafe_b64encode(payload)
    signature = hmac.new(
        client_secret.encode('utf-8'),
        payload_segment.encode('ascii'),
        hashlib.sha256,
    ).digest()
    return f"{payload_segment}.{_urlsafe_b64encode(signature)}"


def _read_spotify_oauth_state(state, client_id, client_secret):
    try:
        payload_segment, signature_segment = str(state).split('.', 1)
    except ValueError as exc:
        raise SpotifyAPIError(
            "Spotify login security state was invalid. Start again."
        ) from exc

    expected_signature = hmac.new(
        client_secret.encode('utf-8'),
        payload_segment.encode('ascii'),
        hashlib.sha256,
    ).digest()
    supplied_signature = _urlsafe_b64decode(signature_segment)
    if not hmac.compare_digest(expected_signature, supplied_signature):
        raise SpotifyAPIError(
            "Spotify login security state did not match. Start again."
        )

    try:
        payload = json.loads(_urlsafe_b64decode(payload_segment))
        created_at = int(payload['created_at'])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SpotifyAPIError(
            "Spotify login security state was invalid. Start again."
        ) from exc

    age = time.time() - created_at
    if age < -60 or age > SPOTIFY_OAUTH_STATE_TTL_SECONDS:
        raise SpotifyAPIError("Spotify login expired. Start again.")
    if (
        payload.get('version') != 1
        or payload.get('client_id') != client_id
        or not payload.get('nonce')
    ):
        raise SpotifyAPIError(
            "Spotify login configuration changed. Start again."
        )
    return _validate_spotify_redirect_uri(payload.get('redirect_uri'))


def inspect_spotify_oauth_state(state):
    """Return unverified public callback hints from signed OAuth state.

    This is only used to prefill the callback form when Spotify returns in a
    fresh Streamlit session. Callers must still use ``complete_spotify_oauth``
    with the user's secret before trusting any of these values.
    """
    state = str(state or '')
    if not state or len(state) > 4096:
        raise SpotifyAPIError(
            "Spotify login security state was invalid. Start again."
        )
    try:
        payload_segment, _ = state.split('.', 1)
        payload = json.loads(_urlsafe_b64decode(payload_segment))
        created_at = int(payload['created_at'])
        client_id = str(payload['client_id']).strip()
        nonce = str(payload['nonce']).strip()
    except (
        KeyError, TypeError, ValueError, json.JSONDecodeError
    ) as exc:
        raise SpotifyAPIError(
            "Spotify login security state was invalid. Start again."
        ) from exc

    age = time.time() - created_at
    if age < -60 or age > SPOTIFY_OAUTH_STATE_TTL_SECONDS:
        raise SpotifyAPIError("Spotify login expired. Start again.")
    if payload.get('version') != 1 or not client_id or not nonce:
        raise SpotifyAPIError(
            "Spotify login security state was invalid. Start again."
        )
    return {
        'client_id': client_id,
        'redirect_uri': _validate_spotify_redirect_uri(
            payload.get('redirect_uri')
        ),
    }


def begin_spotify_oauth(client_id, client_secret, redirect_uri):
    """Return an authorization URL carrying signed, callback-safe state."""
    client_id = str(client_id or '').strip()
    client_secret = str(client_secret or '').strip()
    if not client_id or not client_secret:
        raise SpotifyAPIError(
            "Enter your Spotify Client ID and Client Secret."
        )
    redirect_uri = _validate_spotify_redirect_uri(redirect_uri)
    state = _create_spotify_oauth_state(
        client_id, client_secret, redirect_uri
    )
    query = urllib.parse.urlencode({
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': ' '.join(SPOTIFY_OAUTH_SCOPES),
    })
    return f'https://accounts.spotify.com/authorize?{query}'


def discard_spotify_oauth(state):
    """Retained for callers; signed OAuth state has no server-side record."""
    return None


def complete_spotify_oauth(
    code, state, client_id=None, client_secret=None
):
    """Verify callback state and exchange a Spotify code for user tokens."""
    client_id = str(client_id or '').strip()
    client_secret = str(client_secret or '').strip()
    if not client_id or not client_secret:
        raise SpotifyAPIError(
            "Enter your Spotify Client ID and Client Secret."
        )
    if not code or not state:
        raise SpotifyAPIError("Spotify returned an incomplete login callback.")

    redirect_uri = _read_spotify_oauth_state(
        state, client_id, client_secret
    )
    try:
        response = get_http_session().post(
            'https://accounts.spotify.com/api/token',
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
            },
            auth=(client_id, client_secret),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise SpotifyAPIError(
            "Spotify's token service could not be reached."
        ) from exc
    if not response.ok:
        raise SpotifyAuthenticationError(
            f"Spotify login could not be completed (HTTP {response.status_code})."
        )

    try:
        payload = response.json()
    except requests.JSONDecodeError as exc:
        raise SpotifyAPIError(
            "Spotify returned an invalid token response."
        ) from exc
    access_token = payload.get('access_token')
    refresh_token = payload.get('refresh_token')
    if not access_token or not refresh_token:
        raise SpotifyAPIError(
            "Spotify did not return the required user and refresh tokens."
        )
    return {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'expires_at': time.time() + int(payload.get('expires_in', 3600)),
        'scope': payload.get('scope', ''),
    }


def get_spotify_access_token(
    auth, client_id=None, client_secret=None
):
    """Return a valid user token, refreshing it when nearly expired."""
    if not isinstance(auth, dict) or not auth.get('access_token'):
        raise SpotifyAuthenticationError(
            "Connect your Spotify account first."
        )
    if float(auth.get('expires_at', 0)) > time.time() + 60:
        return auth['access_token']

    refresh_token = auth.get('refresh_token')
    client_id = str(client_id or '').strip()
    client_secret = str(client_secret or '').strip()
    if not refresh_token:
        raise SpotifyAuthenticationError(
            "Spotify login expired. Connect again."
        )
    if not client_id or not client_secret:
        raise SpotifyAuthenticationError(
            "Spotify credentials are missing. Connect again."
        )
    try:
        response = get_http_session().post(
            'https://accounts.spotify.com/api/token',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            },
            auth=(client_id, client_secret),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise SpotifyAPIError(
            "Spotify's token service could not be reached."
        ) from exc
    if not response.ok:
        raise SpotifyAuthenticationError(
            "Spotify login expired or was revoked. Connect again."
        )

    try:
        payload = response.json()
        access_token = payload['access_token']
    except (KeyError, TypeError, requests.JSONDecodeError) as exc:
        raise SpotifyAPIError(
            "Spotify returned an invalid token response."
        ) from exc
    auth['access_token'] = access_token
    auth['expires_at'] = time.time() + int(payload.get('expires_in', 3600))
    if payload.get('refresh_token'):
        auth['refresh_token'] = payload['refresh_token']
    return auth['access_token']


def fetch_spotify_playlist_with_token(playlist_url, access_token):
    """Fetch every item in an owned/collaborative playlist with user OAuth."""
    try:
        canonical_url = canonicalize_spotify_url(
            playlist_url, expected_kind='playlist'
        )
    except InputValidationError as exc:
        raise SpotifyAPIError(str(exc)) from exc
    playlist_id = canonical_url.rsplit('/', 1)[-1]

    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        meta_response = get_http_session().get(
            f'https://api.spotify.com/v1/playlists/{playlist_id}?fields=name',
            headers=headers, timeout=15,
        )
    except requests.RequestException as exc:
        raise SpotifyAPIError(
            "Spotify playlist metadata could not be reached."
        ) from exc
    if meta_response.status_code == 401:
        raise SpotifyAuthenticationError(
            "Spotify login expired or was revoked. Connect again."
        )
    if not meta_response.ok:
        raise SpotifyAPIError(
            f"Spotify could not open the playlist (HTTP {meta_response.status_code})."
        )
    try:
        playlist_name = meta_response.json().get('name', 'Unknown')
    except (AttributeError, requests.JSONDecodeError) as exc:
        raise SpotifyAPIError(
            "Spotify returned invalid playlist metadata."
        ) from exc
    print(f"Playlist: {playlist_name}")

    items_url = f'https://api.spotify.com/v1/playlists/{playlist_id}/items'
    all_items = []
    total = None
    params = {'limit': 50}
    try:
        while items_url:
            parsed_items_url = urllib.parse.urlsplit(items_url)
            expected_path = f'/v1/playlists/{playlist_id}/items'
            try:
                valid_port = parsed_items_url.port in (None, 443)
            except ValueError:
                valid_port = False
            if (
                parsed_items_url.scheme != 'https'
                or parsed_items_url.hostname != 'api.spotify.com'
                or parsed_items_url.username is not None
                or parsed_items_url.password is not None
                or not valid_port
                or parsed_items_url.path != expected_path
            ):
                raise SpotifyAPIError(
                    "Spotify returned an invalid pagination URL."
                )

            response = get_http_session().get(
                items_url, headers=headers, params=params, timeout=15,
            )
            params = None
            if response.status_code == 401:
                raise SpotifyAuthenticationError(
                    "Spotify login expired or was revoked. Connect again."
                )
            if response.status_code == 403:
                raise SpotifyAPIError(
                    "Spotify only permits playlists owned by or shared "
                    "collaboratively with the connected account."
                )
            if not response.ok:
                raise SpotifyAPIError(
                    f"Spotify playlist fetch failed (HTTP {response.status_code})."
                )
            try:
                page = response.json()
                page_items = page.get('items', [])
            except (AttributeError, requests.JSONDecodeError) as exc:
                raise SpotifyAPIError(
                    "Spotify returned an invalid playlist page."
                ) from exc
            if not isinstance(page_items, list):
                raise SpotifyAPIError(
                    "Spotify returned an invalid playlist item list."
                )
            if total is None:
                total = page.get('total')
                try:
                    if total is not None and int(total) > MAX_TRACK_LINKS:
                        raise SpotifyAPIError(
                            f"Playlists are limited to {MAX_TRACK_LINKS} tracks."
                        )
                except (TypeError, ValueError) as exc:
                    raise SpotifyAPIError(
                        "Spotify returned an invalid playlist size."
                    ) from exc
            all_items.extend(page_items)
            if len(all_items) > MAX_TRACK_LINKS:
                raise SpotifyAPIError(
                    f"Playlists are limited to {MAX_TRACK_LINKS} tracks."
                )
            items_url = page.get('next')
            if items_url is not None and not isinstance(items_url, str):
                raise SpotifyAPIError(
                    "Spotify returned an invalid pagination response."
                )
            if items_url:
                print(
                    f"Fetching more tracks... "
                    f"(currently have {len(all_items)})"
                )
    except requests.RequestException as exc:
        raise SpotifyAPIError(
            "Spotify playlist items could not be reached."
        ) from exc

    print(f"✓ Fetched all {len(all_items)} tracks!")
    return {
        'name': playlist_name,
        'tracks': {
            'items': all_items,
            'total': total if total is not None else len(all_items),
        },
    }


def parse_playlist_data(playlist_data):
    """Extract usable songs while skipping malformed playlist entries."""
    try:
        tracks = playlist_data['tracks']['items']
    except (KeyError, TypeError) as exc:
        raise SpotifyAPIError(
            "Spotify returned an invalid playlist response."
        ) from exc
    if not isinstance(tracks, list):
        raise SpotifyAPIError(
            "Spotify returned an invalid playlist track list."
        )

    songs = []
    skipped = 0
    for item in tracks:
        if not isinstance(item, dict):
            skipped += 1
            continue
        track = item.get('item') or item.get('track')
        if (
            not isinstance(track, dict)
            or track.get('type') not in (None, 'track')
        ):
            skipped += 1
            continue

        artists = track.get('artists')
        album = track.get('album')
        external_urls = track.get('external_urls')
        name = track.get('name')
        if (
            not name
            or not isinstance(artists, list)
            or not artists
            or not isinstance(artists[0], dict)
            or not artists[0].get('name')
            or not isinstance(album, dict)
            or not isinstance(external_urls, dict)
        ):
            skipped += 1
            continue
        try:
            link = canonicalize_spotify_url(
                external_urls.get('spotify'), expected_kind='track'
            )
        except InputValidationError:
            skipped += 1
            continue

        release_date = album.get('release_date', '')
        try:
            spotify_year = _validate_year(
                int(str(release_date).split('-')[0])
            )
        except (TypeError, ValueError):
            spotify_year = None

        songs.append({
            'name': sanitize_name(name),
            'original_name': name,
            'original_year': spotify_year,
            'year': spotify_year,
            'year_source': (
                'Spotify' if spotify_year is not None else None
            ),
            'artist': artists[0]['name'],
            'link': link,
            'album': str(album.get('name') or ''),
        })

    if skipped:
        print(f"⚠ Skipped {skipped} malformed or unavailable track item(s).")
    no_year = [song for song in songs if song['year'] is None]
    if no_year:
        print(
            f"\n⚠ {len(no_year)} song(s) have no year — "
            "edit songs.json manually before re-running:"
        )
        for song in no_year:
            print(f"  - {song['artist']} — {song['original_name']}")

    return songs


# =============================================================================
# SPOTIFY SCRAPER — extract track links from a public playlist page
# =============================================================================

def scrape_playlist_track_links(playlist_url) -> list[str]:
    """Extract bounded, validated track URLs from a public playlist page."""
    try:
        html_text, canonical_url = get_spotify_html(
            playlist_url, expected_kind='playlist'
        )
    except (InputValidationError, requests.RequestException) as exc:
        raise SpotifyAPIError(
            "The public Spotify playlist page could not be loaded."
        ) from exc

    soup = BeautifulSoup(html_text, 'html.parser')
    candidates = []
    for tag in soup.find_all('meta'):
        content = tag.get('content', '')
        candidates.extend(
            re.findall(
                r'https://open[.]spotify[.]com/(?:intl-[^/]+/)?'
                r'track/[A-Za-z0-9]{22}',
                content,
            )
        )
    for tag in soup.find_all('a', href=True):
        candidates.append(urllib.parse.urljoin(canonical_url, tag['href']))

    track_links = []
    for candidate in candidates:
        try:
            track_url = canonicalize_spotify_url(
                candidate, expected_kind='track'
            )
        except InputValidationError:
            continue
        if track_url not in track_links:
            track_links.append(track_url)
        if len(track_links) >= MAX_TRACK_LINKS:
            break

    if not track_links:
        raise SpotifyAPIError(
            "Spotify returned no public tracks for this playlist. "
            "Connect Spotify to access private or restricted playlists."
        )
    return track_links


# =============================================================================
# CARD GENERATION FUNCTIONS
# =============================================================================

def create_qr_code(song_link):
    """Generate an inverted QR data square without embedded padding."""
    qr = qrcode.QRCode(version=1, box_size=10, border=0)
    qr.add_data(song_link)
    qr.make(fit=True)
    generated = qr.make_image(
        fill_color='black', back_color='white'
    ).convert('L')
    inverted = ImageOps.invert(generated)
    inverted.info['qr_module_count'] = qr.modules_count
    return inverted


def create_qr_with_neon_rings(
    qr_code, output_path, card_number=None, settings_override=None
):
    """
    Create QR code card with colorful neon rings background.
    """
    img = create_qr_with_neon_rings_in_memory(
        qr_code, card_number=card_number,
        settings_override=settings_override,
    )
    img.save(output_path)
    return output_path


def get_year_color(year, all_years, settings=None):
    """
    Get color for a year based on its percentile in the distribution.
    """
    if settings is None:
        settings = get_settings()
    gradient = settings.get('color_gradient', [])

    sorted_years = sorted(all_years)

    # Calculate percentile position
    count_below = sum(1 for y in sorted_years if y < year)
    count_equal = sum(1 for y in sorted_years if y == year)
    percentile = (count_below + count_equal / 2) / len(sorted_years)

    n_colors = len(gradient)
    if n_colors == 0:
        return (0.0, 0.0, 0.0) # Fallback to black if no colors
    if n_colors == 1:
        return to_rgba(gradient[0])[:3]

    idx = percentile * (n_colors - 1)
    idx_low = int(np.floor(idx))
    idx_high = int(np.ceil(idx))

    if idx_low == idx_high:
        return to_rgba(gradient[idx_low])[:3]

    # Linear interpolation
    color_low = to_rgba(gradient[idx_low])
    color_high = to_rgba(gradient[idx_high])
    frac = idx - idx_low
    
    r = color_low[0] + (color_high[0] - color_low[0]) * frac
    g = color_low[1] + (color_high[1] - color_low[1]) * frac
    b = color_low[2] + (color_high[2] - color_low[2]) * frac
    
    return (r, g, b)


FONT_CACHE_MAX_ENTRIES = 96
FONT_VARIANT_CACHE_MAX_ENTRIES = 32
FONT_API_MAX_BYTES = 1024 * 1024
FONT_FILE_MAX_BYTES = 5 * 1024 * 1024
FONT_PROVIDER_HOSTS = {
    'gwfh.mranftl.com', 'fonts.gstatic.com',
}
_google_font_cache = OrderedDict()
_google_font_variants_cache = OrderedDict()
_font_cache_lock = threading.RLock()


def _bounded_cache_get(cache, key):
    with _font_cache_lock:
        try:
            value = cache.pop(key)
        except KeyError:
            return None
        cache[key] = value
        return value


def _bounded_cache_put(cache, key, value, max_entries):
    with _font_cache_lock:
        cache.pop(key, None)
        cache[key] = value
        while len(cache) > max_entries:
            cache.popitem(last=False)


def normalize_font_weight(weight, default=400):
    """Return a CSS font weight clamped to the supported 100–900 range."""
    try:
        weight = round(float(weight))
    except (TypeError, ValueError, OverflowError):
        weight = default
    return min(900, max(100, weight))


def _font_variant_details(variant):
    """Return ``(weight, italic)`` for a Google Webfonts Helper variant."""
    variant_id = str(variant.get('id', '')).lower()
    italic = variant_id.endswith('italic')
    weight_id = variant_id[:-6] if italic else variant_id
    if weight_id in ('', 'regular'):
        weight = 400
    else:
        match = re.search(r'\d{3}', weight_id)
        weight = int(match.group()) if match else 400
    return normalize_font_weight(weight), italic


def _is_variable_font_variant(variant):
    """Return whether a provider variant is a variable font file."""
    variant_id = str(variant.get('id', '')).lower()
    return 'variable' in variant_id or bool(variant.get('axes'))


def _select_google_font_variant(variants, weight, italic):
    """Select the exact or closest available weight for the requested style."""
    candidates = [variant for variant in variants if variant.get('ttf')]
    if not candidates:
        return None

    matching_style = [
        variant for variant in candidates
        if _font_variant_details(variant)[1] == italic
    ]
    pool = matching_style or candidates
    exact_weight = [
        variant for variant in pool
        if _font_variant_details(variant)[0] == weight
    ]
    if exact_weight:
        return exact_weight[0]

    variable_variants = [
        variant for variant in pool if _is_variable_font_variant(variant)
    ]
    if variable_variants:
        return variable_variants[0]

    return min(
        pool,
        key=lambda variant: (
            abs(_font_variant_details(variant)[0] - weight),
            _font_variant_details(variant)[0],
        ),
    )


def _apply_font_weight_variation(font, weight):
    """Set a variable font's ``wght`` axis when the font exposes one.

    Static font files deliberately pass through unchanged: their nearest
    available face has already been selected by ``_select_google_font_variant``.
    """
    try:
        axes = font.get_variation_axes()
    except (AttributeError, OSError):
        return font

    try:
        values = [axis['default'] for axis in axes]
        for index, axis in enumerate(axes):
            axis_name = axis.get('name', b'')
            if isinstance(axis_name, bytes):
                axis_name = axis_name.decode('ascii', errors='ignore')
            if str(axis_name).strip().lower() not in ('weight', 'wght'):
                continue
            values[index] = max(
                axis['minimum'], min(axis['maximum'], weight)
            )
            font.set_variation_by_axes(values)
            break
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        pass
    return font


def get_google_font(family_name, size, fallback_font, italic=False, weight=700):
    """Download and cache the closest Google Font weight, or return fallback."""
    if not family_name:
        return fallback_font

    weight = normalize_font_weight(weight, default=700)
    font_id = family_name.lower().replace(" ", "-")
    font_bytes_key = (font_id, weight, italic)
    cache_key = (font_id, weight, italic, size)

    cached_font = _bounded_cache_get(_google_font_cache, cache_key)
    if cached_font is not None:
        return cached_font

    font_bytes = _bounded_cache_get(
        _google_font_cache, font_bytes_key
    )
    if not font_bytes:
        try:
            variants = _bounded_cache_get(
                _google_font_variants_cache, font_id
            )
            if variants is None:
                quoted_font_id = urllib.parse.quote(font_id, safe='-')
                api_url = (
                    "https://gwfh.mranftl.com/api/fonts/"
                    f"{quoted_font_id}"
                )
                api_bytes, _, _ = get_bounded_https_content(
                    api_url,
                    allowed_hosts=FONT_PROVIDER_HOSTS,
                    max_bytes=FONT_API_MAX_BYTES,
                    timeout=5,
                )
                api_payload = json.loads(api_bytes)
                variants = api_payload.get('variants', [])
                if not isinstance(variants, list):
                    variants = []
                _bounded_cache_put(
                    _google_font_variants_cache, font_id, variants,
                    FONT_VARIANT_CACHE_MAX_ENTRIES,
                )

            variant = _select_google_font_variant(variants, weight, italic)
            if variant:
                font_bytes, _, _ = get_bounded_https_content(
                    variant['ttf'],
                    allowed_hosts=FONT_PROVIDER_HOSTS,
                    max_bytes=FONT_FILE_MAX_BYTES,
                    timeout=5,
                )
                _bounded_cache_put(
                    _google_font_cache, font_bytes_key, font_bytes,
                    FONT_CACHE_MAX_ENTRIES,
                )
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            requests.RequestException,
        ) as exc:
            print(f"Error downloading font: {type(exc).__name__}")

    if font_bytes:
        try:
            font = ImageFont.truetype(io.BytesIO(font_bytes), size)
            font = _apply_font_weight_variation(font, weight)
            _bounded_cache_put(
                _google_font_cache, cache_key, font,
                FONT_CACHE_MAX_ENTRIES,
            )
            return font
        except (OSError, TypeError):
            pass

    return fallback_font


def get_font_for_setting(settings, size, role="artist", italic=False, weight=700):
    """Get the preferred font (Google Font or fallback) for a given size."""
    try:
        fallback = ImageFont.truetype(settings['fonts_dict'][role], size)
    except (KeyError, OSError, TypeError):
        fallback = ImageFont.load_default()

    return get_google_font(
        settings.get('google_font', 'Montserrat'), size, fallback,
        italic=italic, weight=weight,
    )


def create_solution_side(
    song_name, artist, year, all_years, output_path, card_number=None,
    settings_override=None,
):
    """
    Create solution card with year-based color background.
    """
    img = create_solution_side_in_memory(
        song_name, artist, year, all_years, card_number=card_number,
        settings_override=settings_override,
    )
    img.save(output_path)
    return output_path

# =============================================================================
# PDF GENERATION
# =============================================================================

PDF_CARD_SIZE = CARD_PHYSICAL_SIZE_CM * cm
PDF_GRID_COLS = 3
PDF_GRID_ROWS = 4
PDF_CARDS_PER_PAGE = PDF_GRID_COLS * PDF_GRID_ROWS
PDF_INNER_GUTTER_SCALE = 0.25
PDF_PRINT_PROFILE_A4 = "a4"
PDF_PRINT_PROFILE_PHOTOSHOP_A4_FIT = "photoshop_a4_fit"
DEFAULT_PDF_PRINT_PROFILE = PDF_PRINT_PROFILE_A4
# Photoshop reports this scale when its A4 "Scale to Fit Media" option is
# constrained by the printable area of the target printer.
PHOTOSHOP_A4_FIT_SCALE = 0.9727
PDF_PHOTOSHOP_A4_FIT_PAGE_SIZE = tuple(
    dimension * PHOTOSHOP_A4_FIT_SCALE for dimension in A4
)
PDF_RENDER_DPI = 720
PDF_RENDER_CARD_SIZE = round(PDF_RENDER_DPI * PDF_CARD_SIZE / 72)
PDF_SCALED_PIXEL_SETTINGS = (
    'qr_backplate_padding', 'qr_backplate_radius', 'neon_ring_thickness',
    'qr_title_size', 'sol_border_width', 'song_year_size',
    'song_artist_size', 'song_title_size', 'card_number_size',
    'sol_title_size',
)

def get_pdf_page_size(print_profile=DEFAULT_PDF_PRINT_PROFILE):
    """Return the PDF page size for the selected physical print profile."""
    if print_profile == PDF_PRINT_PROFILE_A4:
        return A4
    if print_profile == PDF_PRINT_PROFILE_PHOTOSHOP_A4_FIT:
        return PDF_PHOTOSHOP_A4_FIT_PAGE_SIZE
    raise ValueError(
        f"Unsupported PDF print profile: {print_profile!r}."
    )

def get_pdf_grid_layout(page_width, page_height):
    """Return a centered grid with fixed card spacing and outer borders."""
    previous_gap_x = (
        A4[0] - PDF_GRID_COLS * PDF_CARD_SIZE
    ) / (PDF_GRID_COLS + 1)
    previous_gap_y = (
        A4[1] - PDF_GRID_ROWS * PDF_CARD_SIZE
    ) / (PDF_GRID_ROWS + 1)
    gap_x = previous_gap_x * PDF_INNER_GUTTER_SCALE
    gap_y = previous_gap_y * PDF_INNER_GUTTER_SCALE
    margin_x = (
        page_width - PDF_GRID_COLS * PDF_CARD_SIZE
        - (PDF_GRID_COLS - 1) * gap_x
    ) / 2
    margin_y = (
        page_height - PDF_GRID_ROWS * PDF_CARD_SIZE
        - (PDF_GRID_ROWS - 1) * gap_y
    ) / 2
    return PDF_CARD_SIZE, margin_x, margin_y, gap_x, gap_y


def get_pdf_card_positions(card_count, mirrored=False, page_size=A4):
    """Return shared front/back positions for one PDF card batch."""
    if not 0 <= card_count <= PDF_CARDS_PER_PAGE:
        raise ValueError(
            f"PDF batches must contain 0-{PDF_CARDS_PER_PAGE} cards."
        )
    page_width, page_height = page_size
    card_size, margin_x, margin_y, gap_x, gap_y = (
        get_pdf_grid_layout(page_width, page_height)
    )
    positions = []
    for index in range(card_count):
        row = index // PDF_GRID_COLS
        column = index % PDF_GRID_COLS
        if mirrored:
            column = PDF_GRID_COLS - 1 - column
        x = margin_x + column * (card_size + gap_x)
        y = (
            page_height - margin_y - (row + 1) * card_size
            - row * gap_y
        )
        positions.append((index, x, y))
    return positions


def get_pdf_render_settings(settings):
    """Scale pixel-based design values to the configured PDF output DPI."""
    render_settings = dict(settings)
    source_size = max(1, int(render_settings.get('card_size', 2000)))
    target_size = PDF_RENDER_CARD_SIZE
    if target_size == source_size:
        return render_settings

    scale = target_size / source_size
    render_settings['card_size'] = target_size
    for key in PDF_SCALED_PIXEL_SETTINGS:
        if key in render_settings:
            value = render_settings[key]
            render_settings[key] = (
                0 if value == 0 else max(1, round(value * scale))
            )
    return render_settings


def draw_pdf_card_image(c, image_source, x, y, card_size):
    """Draw a card at the raster dimensions required for the PDF output DPI."""
    with Image.open(image_source) as source_image:
        if source_image.size != (PDF_RENDER_CARD_SIZE, PDF_RENDER_CARD_SIZE):
            source_image = source_image.resize(
                (PDF_RENDER_CARD_SIZE, PDF_RENDER_CARD_SIZE),
                Image.Resampling.LANCZOS,
            )
        c.drawImage(
            ImageReader(source_image), x, y,
            width=card_size, height=card_size, preserveAspectRatio=True,
        )


def apply_qr_page_rotation(pdf_canvas, page_width, page_height, enabled):
    """Rotate all QR-side artwork by 180 degrees when duplex printing needs it.

    The white A4 page remains unchanged; the QR-side card grid, QR codes, and
    labels rotate as one unit.  The return value tells the caller whether the
    ReportLab graphics state must be restored after drawing that page.
    """
    if not enabled:
        return False
    pdf_canvas.saveState()
    pdf_canvas.translate(page_width, page_height)
    pdf_canvas.rotate(180)
    return True


def create_cards_pdf(
    cards_folder, output_pdf_path, qr_pages_upside_down=False,
    pdf_print_profile=DEFAULT_PDF_PRINT_PROFILE,
):
    """Create a duplex PDF from matched disk-rendered card pairs."""
    file_pattern = re.compile(
        r'^card_([0-9]+)_(qr|solution)[.]png$'
    )
    qr_images = {}
    solution_images = {}
    for filename in os.listdir(cards_folder):
        match = file_pattern.fullmatch(filename)
        if not match:
            continue
        card_number = int(match.group(1))
        target = qr_images if match.group(2) == 'qr' else solution_images
        target[card_number] = filename

    if set(qr_images) != set(solution_images):
        missing_qr = sorted(set(solution_images) - set(qr_images))
        missing_solution = sorted(set(qr_images) - set(solution_images))
        raise ValueError(
            "Card image pairs are incomplete "
            f"(missing QR: {missing_qr}; "
            f"missing solutions: {missing_solution})."
        )
    card_numbers = sorted(qr_images)
    if not card_numbers:
        raise ValueError("No card image pairs were found.")

    page_size = get_pdf_page_size(pdf_print_profile)
    pdf_canvas = canvas.Canvas(output_pdf_path, pagesize=page_size)
    page_width, page_height = page_size
    card_size = PDF_CARD_SIZE
    total_pages = (
        len(card_numbers) + PDF_CARDS_PER_PAGE - 1
    ) // PDF_CARDS_PER_PAGE

    for page_index in range(total_pages):
        start = page_index * PDF_CARDS_PER_PAGE
        batch_numbers = card_numbers[
            start:start + PDF_CARDS_PER_PAGE
        ]

        pdf_canvas.setFillColorRGB(1, 1, 1)
        pdf_canvas.rect(
            0, 0, page_width, page_height, stroke=0, fill=1
        )
        qr_page_rotated = apply_qr_page_rotation(
            pdf_canvas, page_width, page_height, qr_pages_upside_down
        )
        for index, x, y in get_pdf_card_positions(
            len(batch_numbers), page_size=page_size
        ):
            card_number = batch_numbers[index]
            qr_path = os.path.join(
                cards_folder, qr_images[card_number]
            )
            draw_pdf_card_image(
                pdf_canvas, qr_path, x, y, card_size
            )
        if qr_page_rotated:
            pdf_canvas.restoreState()
        pdf_canvas.showPage()

        pdf_canvas.setFillColorRGB(1, 1, 1)
        pdf_canvas.rect(
            0, 0, page_width, page_height, stroke=0, fill=1
        )
        for index, x, y in get_pdf_card_positions(
            len(batch_numbers), mirrored=True, page_size=page_size
        ):
            card_number = batch_numbers[index]
            solution_path = os.path.join(
                cards_folder, solution_images[card_number]
            )
            draw_pdf_card_image(
                pdf_canvas, solution_path, x, y, card_size
            )
        pdf_canvas.showPage()

    pdf_canvas.save()
    print(f"\n✓ Created PDF: {output_pdf_path}")
    print(f"  - {len(card_numbers)} cards total")
    print(f"  - {total_pages * 2} pages (alternating front/back)")
    print("  - Ready for duplex printing!")
    return output_pdf_path


# =============================================================================
# WEBUTILS
# =============================================================================

def apply_background_image(img, bg_img, scale, offset_x, offset_y, card_size):
    """Apply an image without allocating a scaled canvas larger than the card."""
    if bg_img.width <= 0 or bg_img.height <= 0:
        return

    aspect = bg_img.width / bg_img.height
    if aspect > 1:
        base_h = card_size
        base_w = round(card_size * aspect)
    else:
        base_w = card_size
        base_h = round(card_size / aspect)

    safe_scale = max(0.01, min(float(scale), 3.0))
    new_w = max(1, round(base_w * safe_scale))
    new_h = max(1, round(base_h * safe_scale))
    x = (card_size - new_w) // 2 + round(float(offset_x) * card_size)
    y = (card_size - new_h) // 2 + round(float(offset_y) * card_size)

    source = bg_img.convert('RGBA')
    x_ratio = source.width / new_w
    y_ratio = source.height / new_h
    transformed = source.transform(
        (card_size, card_size),
        Image.Transform.AFFINE,
        (x_ratio, 0, -x * x_ratio, 0, y_ratio, -y * y_ratio),
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )
    img.paste(transformed, (0, 0), transformed)


def _derive_lighter_solution_color(base_rgb):
    """Return the base colour with luminance added, without shifting its hue."""
    red, green, blue = (channel / 255 for channel in base_rgb)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    lighter = colorsys.hls_to_rgb(
        hue,
        lightness + (1.0 - lightness) * 0.22,
        saturation,
    )
    return tuple(round(channel * 255) for channel in lighter)


def derive_solution_color_wash_palette(base_color, separation=1.0):
    """Return the base colour and a luminance-only lighter companion colour."""
    base_rgb = tuple(round(channel * 255) for channel in to_rgba(base_color)[:3])
    separation = max(0.0, min(1.0, float(separation)))
    lighter_rgb = _derive_lighter_solution_color(base_rgb)
    luminance_rgb = tuple(
        round(base_channel + (lighter_channel - base_channel) * separation)
        for base_channel, lighter_channel in zip(base_rgb, lighter_rgb)
    )
    return base_rgb, luminance_rgb


def render_solution_color_wash(img, settings, seed=42):
    """Render a base-to-lighter solution colour wash."""
    rng = np.random.default_rng(seed & 0xFFFFFFFF)
    base_color = settings.get('sol_color_wash_base_color', (213, 43, 131))
    base = tuple(round(channel * 255) for channel in to_rgba(base_color)[:3])
    mesh_size = max(128, min(384, img.width // 5))
    y_grid, x_grid = np.mgrid[0:mesh_size, 0:mesh_size].astype(np.float32)
    x_grid /= mesh_size - 1
    y_grid /= mesh_size - 1
    canvas = np.empty((mesh_size, mesh_size, 3), dtype=np.float32)
    canvas[:] = base

    def apply_color_field(field_color, field_opacity, edge_influence, angle):
        center_distance = rng.uniform(0.52, 0.68)
        center_x = 0.5 + np.cos(angle) * center_distance
        center_y = 0.5 + np.sin(angle) * center_distance
        maximum_radius = 0.95 + edge_influence * (1.35 - 0.95)
        radius_x = maximum_radius * rng.uniform(0.90, 1.08)
        radius_y = maximum_radius * rng.uniform(0.90, 1.08)
        distance = np.sqrt(
            ((x_grid - center_x) / radius_x) ** 2
            + ((y_grid - center_y) / radius_y) ** 2
        )
        strength = np.clip(1.0 - distance, 0.0, 1.0)
        strength = strength * strength * (3.0 - 2.0 * strength)
        alpha = (strength * field_opacity)[..., np.newaxis]
        canvas[:] += (np.asarray(field_color, dtype=np.float32) - canvas) * alpha

    # Keep a clearly visible lighter field while retaining per-card variation.
    luminance_separation = rng.uniform(0.90, 1.0)
    luminance_opacity = rng.uniform(0.75, 0.95)
    luminance_edge_influence = rng.uniform(0.80, 1.0)
    _, lighter = derive_solution_color_wash_palette(
        base, separation=luminance_separation
    )
    luminance_angle = rng.uniform(0, 2 * np.pi)
    apply_color_field(
        lighter, luminance_opacity, luminance_edge_influence, luminance_angle
    )

    # The deliberately strong fields survive 8-bit conversion cleanly. Resize
    # the compact mesh once instead of resampling three full-resolution float
    # channels, which keeps large PDF batches practical.
    wash = Image.fromarray(
        np.clip(np.rint(canvas), 0, 255).astype(np.uint8), mode="RGB"
    ).resize(img.size, Image.Resampling.LANCZOS)

    grain_opacity = max(
        0.0, min(0.05, float(settings.get('sol_color_wash_grain_opacity', 0.012)))
    )
    if grain_opacity:
        grain_values = rng.integers(0, 256, img.size[::-1], dtype=np.uint8)
        grain_channel = Image.fromarray(grain_values)
        grain = Image.merge("RGB", (grain_channel, grain_channel, grain_channel))
        wash = Image.blend(wash, grain, grain_opacity)

    img.paste(wash)


def render_card_background(
    img, settings, side="qr", seed=42, qr_code=None
):
    """Render the card background (solid, neon rings, or image)."""
    size = settings['card_size']
    
    if side == "qr":
        bg_type = settings['qr_bg_type']
        bg_color = settings['qr_bg_color']
        bg_img = settings.get('qr_bg_image')
        scale = settings['qr_bg_scale']
        offset_x = settings['qr_bg_offset_x']
        offset_y = settings['qr_bg_offset_y']
    else:
        bg_type = settings['sol_bg_type']
        # Solution side uses its dynamic year color if type is "gradient", else maybe image over it
        bg_img = settings.get('sol_bg_image')
        scale = settings['sol_bg_scale']
        offset_x = settings['sol_bg_offset_x']
        offset_y = settings['sol_bg_offset_y']
        bg_color = (0, 0, 0) # Fallback

    draw = ImageDraw.Draw(img)
    
    # Fill the entire background with the selected color first
    if side == "qr":
        draw.rectangle([(0, 0), (size, size)], fill=bg_color)
    
    if (
        side == "sol"
        and settings.get('sol_color_wash_enabled', True)
        and not settings.get('ink_saving_mode', False)
    ):
        render_solution_color_wash(img, settings, seed=seed)
    elif bg_type == "image" and bg_img:
        apply_background_image(img, bg_img, scale, offset_x, offset_y, size)
    elif bg_type == "neon_rings" and side == "qr":
        # Draw neon rings — unique pattern per card
        center = size // 2
        edge_clearance = math.ceil(
            size * NEON_RING_EDGE_CLEARANCE_CM / CARD_PHYSICAL_SIZE_CM
        )
        max_radius = min(center, size - 1 - center) - edge_clearance
        
        qr_size = (
            get_qr_render_geometry(qr_code, settings)[2]
            if qr_code is not None
            else get_qr_code_size_pixels(settings)
        )
        qr_padding = get_qr_backplate_padding_pixels(settings)
        safety_radius = (
            (qr_size // 2) + qr_padding
            + round(size * 0.01)
        )
        
        rng = random.Random(seed)
        neon_colors = settings['neon_colors']
        ring_count = settings.get('neon_ring_count', 14)
        thickness = settings.get('neon_ring_thickness', 12)
        
        for i in range(ring_count):
            color = neon_colors[i % len(neon_colors)]
            radius = max_radius - i * (max_radius // ring_count)
            if radius <= 0:
                break
            
            is_inside_safety = radius < safety_radius
            
            if settings['qr_background_mode'] == "solid" and is_inside_safety:
                continue
                
            num_gaps = rng.randint(1, 3)
            for gap in range(num_gaps):
                gap_start = rng.randint(0, 360)
                gap_length = rng.randint(20, 60)
                
                draw.arc(
                    (center - radius, center - radius, center + radius, center + radius),
                    start=0, end=360, fill=color, width=thickness
                )
                draw.arc(
                    (center - radius, center - radius, center + radius, center + radius),
                    start=gap_start, end=gap_start + gap_length, fill=bg_color, width=thickness
                )

    # Draw border
    if settings.get('card_draw_border'):
        border_width = max(1, round(size * 0.01))
        draw.rectangle(
            [(border_width, border_width), (size - border_width, size - border_width)],
            outline=settings['card_border_color'],
            width=border_width
        )

def render_qr_backplate(img, qr_code, settings):
    """Render the single configured quiet zone around the QR data."""
    if settings['qr_background_mode'] != "solid":
        return
        
    size = settings['card_size']
    qr_size = get_qr_render_geometry(qr_code, settings)[2]
    padding = get_qr_backplate_padding_pixels(settings)
    radius = settings['qr_backplate_radius']
    bg_color = settings['qr_background_color']
    
    center = size // 2
    side = qr_size + 2 * padding
    left = center - side // 2
    top = center - side // 2
    right = left + side - 1
    bottom = top + side - 1
    
    overlay = Image.new("RGBA", (size, size), (0,0,0,0))
    overlay_draw = ImageDraw.Draw(overlay)
    
    if radius > 0:
        overlay_draw.rounded_rectangle([left, top, right, bottom], radius=radius, fill=bg_color)
    else:
        overlay_draw.rectangle([left, top, right, bottom], fill=bg_color)
        
    img.paste(overlay, (0, 0), overlay)

def render_qr_code(img, qr_code, settings):
    """Render crisp, module-aligned QR pixels on top of the card."""
    size = settings['card_size']
    center = size // 2
    _, _, rendered_side = get_qr_render_geometry(
        qr_code, settings
    )

    qr_code_resized = qr_code.convert('L').resize(
        (rendered_side, rendered_side), Image.Resampling.NEAREST
    )
    modules_mask = np.asarray(qr_code_resized) > 128
    mask_img = Image.fromarray(
        modules_mask.astype('uint8') * 255
    ).convert('1')

    left = center - rendered_side // 2
    top = center - rendered_side // 2
    module_color = settings['qr_module_color']

    if settings['qr_background_mode'] == 'transparent':
        bg_crop = img.crop(
            (left, top, left + rendered_side, top + rendered_side)
        ).convert('L')
        bg_mean = np.asarray(bg_crop).mean()
        if module_color in ((255, 255, 255), (0, 0, 0)):
            module_color = (
                (0, 0, 0) if bg_mean > 127 else (255, 255, 255)
            )

        quiet_color = (
            (0, 0, 0)
            if sum(module_color[:3]) / 3 > 127
            else (255, 255, 255)
        )
        quiet_pixels = get_qr_backplate_padding_pixels(settings)
        quiet_draw = ImageDraw.Draw(img)
        right = left + rendered_side - 1
        bottom = top + rendered_side - 1
        if quiet_pixels:
            outer_left = left - quiet_pixels
            outer_top = top - quiet_pixels
            outer_right = right + quiet_pixels
            outer_bottom = bottom + quiet_pixels
            quiet_draw.rectangle(
                (outer_left, outer_top, outer_right, top - 1),
                fill=quiet_color,
            )
            quiet_draw.rectangle(
                (outer_left, bottom + 1, outer_right, outer_bottom),
                fill=quiet_color,
            )
            quiet_draw.rectangle(
                (outer_left, top, left - 1, bottom),
                fill=quiet_color,
            )
            quiet_draw.rectangle(
                (right + 1, top, outer_right, bottom),
                fill=quiet_color,
            )

    overlay = Image.new('RGB', (rendered_side, rendered_side), module_color)
    img.paste(overlay, (left, top), mask_img)


def render_game_title(img, settings, side="qr", qr_code=None):
    """Render the game title / card label."""
    prefix = "qr" if side == "qr" else "sol"
    title_image = settings.get(f'{prefix}_title_image')
    title = settings.get(f'{prefix}_title')
    if not settings.get(f'{prefix}_title_enabled') or not (
        title or isinstance(title_image, Image.Image)
    ):
        return

    if isinstance(title_image, Image.Image):
        render_title_image(img, title_image, settings, side, qr_code)
        return

    default_pos = 'top' if side == 'qr' else 'in_border_top_left'
    pos = settings.get(f'{prefix}_title_pos', default_pos)
    default_font_size = 80 if side == 'qr' else 188
    font_size = settings.get(f'{prefix}_title_size', default_font_size)
    color = settings.get(f'{prefix}_title_color', (255, 255, 255))
    default_opacity = 100 if side == 'qr' else 60
    opacity = max(0, min(100, settings.get(f'{prefix}_title_opacity', default_opacity)))
    bg_enabled = settings.get(f'{prefix}_title_bg', False)
    bg_color = settings.get('qr_bg_color', (0, 0, 0)) if side == 'qr' else (255, 255, 255)

    size = settings['card_size']
    margin = round(size * 0.05)
    
    draw = ImageDraw.Draw(img)
    title_is_italic = side == "qr"
    title_role = "song" if title_is_italic else "artist"
    font = get_font_for_setting(
        settings, font_size, role=title_role, italic=title_is_italic,
        weight=settings.get('card_set_title_font_weight', 500),
    )
        
    bbox = draw.textbbox((0, 0), title, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    ink_bbox = font.getmask(title).getbbox()
    ink_left, ink_top, ink_right, ink_bottom = ink_bbox or (0, 0, tw, th)
    
    center = size // 2
    qr_size = (
        get_qr_render_geometry(qr_code, settings)[2]
        if side == 'qr' and qr_code is not None
        else get_qr_code_size_pixels(settings)
    )
    qr_bound = (
        center + qr_size // 2
        + get_qr_backplate_padding_pixels(settings)
    )
    bw = settings.get('sol_border_width', 142) // 2

    if side == "sol":
        left = card_distance_cm_to_pixels(size, SOLUTION_TITLE_LEFT_OFFSET_CM)
        top = card_distance_cm_to_pixels(size, SOLUTION_TITLE_TOP_OFFSET_CM)
        ink_offsets = (
            bbox[0] + ink_left, bbox[1] + ink_top,
            bbox[0] + ink_right, bbox[1] + ink_bottom,
        )
        x, y, anchor = left - ink_offsets[0], top - ink_offsets[1], None
        positioned_bbox = (
            left, top,
            left + ink_offsets[2] - ink_offsets[0],
            top + ink_offsets[3] - ink_offsets[1],
        )
    else:
        positions = {
            "top": (center, margin + th // 2, "mm"),
            "bottom": (center, size - margin - th // 2, "mm"),
            "top_left": (margin + tw // 2, margin + th // 2, "mm"),
            "top_right": (size - margin - tw // 2, margin + th // 2, "mm"),
            "bottom_left": (margin + tw // 2, size - margin - th // 2, "mm"),
            "bottom_right": (size - margin - tw // 2, size - margin - th // 2, "mm"),
            "center_above_qr": (center, size - qr_bound - margin - th // 2, "mm"),
            "center_below_qr": (center, qr_bound + margin + th // 2, "mm"),
            "in_border_bottom_right": (size - bw - ink_right, size - bw - ink_bottom, "lt"),
            "in_border_bottom_left": (bw - ink_left, size - bw - ink_bottom, "lt"),
            "in_border_top_right": (size - bw - ink_right, bw - ink_top, "lt"),
            "in_border_top_left": (bw - ink_left, bw - ink_top, "lt"),
        }

        if pos not in positions:
            return

        x, y, anchor = positions[pos]
        if pos.startswith("in_border_"):
            positioned_bbox = (
                x + ink_left, y + ink_top, x + ink_right, y + ink_bottom
            )
        else:
            positioned_bbox = draw.textbbox(
                (x, y), title, font=font, anchor=anchor
            )
    if bg_enabled:
        bg_padding = max(1, round(size * 0.0075))
        overlay = Image.new("RGBA", (size, size), (0,0,0,0))
        overlay_draw = ImageDraw.Draw(overlay)
        
        left, top, right, bottom = positioned_bbox
        overlay_draw.rectangle(
            [left - bg_padding, top - bg_padding,
             right + bg_padding, bottom + bg_padding],
            fill=bg_color
        )
        img.paste(overlay, (0, 0), overlay)

    text_overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_overlay)
    rgb = tuple(round(channel * 255) for channel in to_rgba(color)[:3])
    text_draw.text(
        (x, y), title, fill=rgb + (round(opacity * 255 / 100),),
        font=font, anchor=anchor
    )
    img.paste(text_overlay, (0, 0), text_overlay)


def render_title_image(img, title_image, settings, side="qr", qr_code=None):
    """Render title artwork at the same physical size as a title font setting."""
    if title_image.width <= 0 or title_image.height <= 0:
        return

    prefix = "qr" if side == "qr" else "sol"
    default_pos = 'top' if side == 'qr' else 'in_border_top_left'
    pos = settings.get(f'{prefix}_title_pos', default_pos)
    default_size = 80 if side == 'qr' else 188
    height = max(1, round(settings.get(f'{prefix}_title_size', default_size)))
    width = max(1, round(title_image.width * height / title_image.height))
    opacity = max(0, min(
        100, settings.get(f'{prefix}_title_opacity', 100 if side == 'qr' else 60)
    ))
    bg_enabled = settings.get(f'{prefix}_title_bg', False)
    bg_color = settings.get('qr_bg_color', (0, 0, 0)) if side == 'qr' else (255, 255, 255)

    size = settings['card_size']
    margin = round(size * 0.05)
    center = size // 2
    qr_size = (
        get_qr_render_geometry(qr_code, settings)[2]
        if side == 'qr' and qr_code is not None
        else get_qr_code_size_pixels(settings)
    )
    qr_bound = (
        center + qr_size // 2 + get_qr_backplate_padding_pixels(settings)
    )
    border_width = settings.get('sol_border_width', 142) // 2

    if side == "sol":
        left = card_distance_cm_to_pixels(size, SOLUTION_TITLE_LEFT_OFFSET_CM)
        top = card_distance_cm_to_pixels(size, SOLUTION_TITLE_TOP_OFFSET_CM)
    else:
        positions = {
            "top": ((size - width) // 2, margin),
            "bottom": ((size - width) // 2, size - margin - height),
            "top_left": (margin, margin),
            "top_right": (size - margin - width, margin),
            "bottom_left": (margin, size - margin - height),
            "bottom_right": (size - margin - width, size - margin - height),
            "center_above_qr": (
                (size - width) // 2, size - qr_bound - margin - height,
            ),
            "center_below_qr": ((size - width) // 2, qr_bound + margin),
            "in_border_bottom_right": (
                size - border_width - width, size - border_width - height,
            ),
            "in_border_bottom_left": (border_width, size - border_width - height),
            "in_border_top_right": (size - border_width - width, border_width),
            "in_border_top_left": (border_width, border_width),
        }
        if pos not in positions:
            return
        left, top = positions[pos]

    right = left + width
    bottom = top + height
    if bg_enabled:
        background_padding = max(1, round(size * 0.0075))
        ImageDraw.Draw(img).rectangle(
            [
                left - background_padding, top - background_padding,
                right + background_padding, bottom + background_padding,
            ],
            fill=bg_color,
        )

    svg_bytes = title_image.info.get('svg_bytes')
    if svg_bytes:
        try:
            artwork = rasterize_svg_image(svg_bytes, width, height)
        except InputValidationError:
            artwork = title_image.convert("RGBA").resize(
                (width, height), Image.Resampling.LANCZOS
            )
    else:
        artwork = title_image.convert("RGBA").resize(
            (width, height), Image.Resampling.LANCZOS
        )
    if opacity < 100:
        alpha = artwork.getchannel("A").point(
            lambda value: round(value * opacity / 100)
        )
        artwork.putalpha(alpha)
    img.paste(artwork, (left, top), artwork)


def render_card_number(img, card_number, settings, side="qr"):
    """Render a card number in the bottom-right corner."""
    if card_number is None:
        return

    font_size = settings.get('card_number_size', max(20, round(img.width * 0.035)))
    opacity = max(0, min(100, settings.get('qr_card_number_opacity', 42))) if side == "qr" else 100
    alpha = round(opacity * 255 / 100)

    font = get_font_for_setting(
        settings, font_size, role="artist",
        weight=settings.get('card_number_font_weight', 600),
    )

    number_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(number_overlay)
    number_text = str(card_number)
    bbox = overlay_draw.textbbox((0, 0), number_text, font=font)
    mask_bbox = font.getmask(number_text).getbbox()
    if mask_bbox:
        ink_bbox = (
            bbox[0] + mask_bbox[0], bbox[1] + mask_bbox[1],
            bbox[0] + mask_bbox[2], bbox[1] + mask_bbox[3],
        )
    else:
        ink_bbox = bbox
    right = card_distance_cm_to_pixels(
        img.width, CARD_NUMBER_RIGHT_OFFSET_CM
    )
    bottom = card_distance_cm_to_pixels(
        img.height, CARD_NUMBER_BOTTOM_OFFSET_CM
    )
    x = img.width - right - ink_bbox[2]
    y = img.height - bottom - ink_bbox[3]
    overlay_draw.text(
        (x, y), number_text, fill=(255, 255, 255, alpha), font=font
    )
    img.paste(number_overlay, (0, 0), number_overlay)


def create_qr_with_neon_rings_in_memory(
    qr_code, seed=42, settings_override=None, card_number=None
):
    """
    Create QR code card with colorful neon rings background.
    seed: per-card seed for unique ring patterns.
    settings_override: dict of settings to override defaults.
    """
    settings = get_settings(settings_override)
    size = settings['card_size']
    
    # Base background (will be overriden by render_card_background if needed)
    img = Image.new("RGB", (size, size), settings['qr_bg_color'])
    
    render_card_background(
        img, settings, side="qr", seed=seed, qr_code=qr_code
    )
    render_qr_backplate(img, qr_code, settings)
    render_qr_code(img, qr_code, settings)
    render_game_title(img, settings, side="qr", qr_code=qr_code)
    render_card_number(img, card_number, settings, side="qr")
        
    return img


def draw_centered_text_at_edge(draw, text, font, center_x, edge_y, edge):
    """Draw a centered text block whose visible bounds meet a fixed edge."""
    bbox = draw.multiline_textbbox(
        (0, 0), text, font=font, align="center"
    )
    x = center_x - (bbox[0] + bbox[2]) / 2
    if edge == "top":
        y = edge_y - bbox[1]
    elif edge == "bottom":
        y = edge_y - bbox[3]
    else:
        raise ValueError(f"Unsupported text edge: {edge}")
    draw.multiline_text(
        (x, y), text, fill="#000000", font=font, align="center"
    )


def wrap_text_to_width(draw, text, font, max_width):
    """Wrap text to an exact pixel width, preserving explicit line breaks."""
    text = str(text or "")
    if not text:
        return ""

    def text_width(value):
        bbox = draw.textbbox((0, 0), value, font=font)
        return bbox[2] - bbox[0]

    lines = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue

        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if line and text_width(candidate) > max_width:
                lines.append(line)
                line = ""

            if not line and text_width(word) > max_width:
                fragment = ""
                for character in word:
                    candidate = f"{fragment}{character}"
                    if fragment and text_width(candidate) > max_width:
                        lines.append(fragment)
                        fragment = character
                    else:
                        fragment = candidate
                line = fragment
            else:
                line = word if not line else candidate

        lines.append(line)

    return "\n".join(lines)


def fit_song_text_to_height(
    draw, text, settings, font_size, role, weight, italic, max_width,
    max_height,
):
    """Return the largest wrapped song-text block that fits ``max_height``."""
    requested_size = max(1, int(font_size))
    max_height = max(0, max_height)

    def layout_for_size(candidate_size):
        font = get_font_for_setting(
            settings, candidate_size, role=role, italic=italic, weight=weight,
        )
        wrapped_text = wrap_text_to_width(draw, text, font, max_width)
        bbox = draw.multiline_textbbox(
            (0, 0), wrapped_text, font=font, align="center"
        )
        return font, wrapped_text, bbox[3] - bbox[1]

    if not text:
        font, wrapped_text, _ = layout_for_size(requested_size)
        return font, wrapped_text

    best_layout = None
    low, high = 1, requested_size
    while low <= high:
        candidate_size = (low + high) // 2
        candidate = layout_for_size(candidate_size)
        if candidate[2] <= max_height:
            best_layout = candidate
            low = candidate_size + 1
        else:
            high = candidate_size - 1

    if best_layout is None:
        best_layout = layout_for_size(1)
    return best_layout[:2]


def create_solution_side_in_memory(
    song_name, artist, year, all_years, settings_override=None, card_number=None
):
    """
    Create solution card and return the PIL Image object directly.
    settings_override: dict of settings to override defaults.
    """
    settings = get_settings(settings_override)
    size = settings['card_size']
    margin = round(size * 0.075)
    max_width = size - (2 * margin)
    
    # Handle unknown year gracefully
    display_year = str(year) if year is not None else "????"

    # Filter None years out of all_years for color calculation
    valid_years = [y for y in all_years if y is not None]
    if not valid_years:
        valid_years = [2000]  # fallback

    effective_year = year if year is not None else int(np.median(valid_years))

    # Get color for this year
    color_rgb = get_year_color(effective_year, valid_years, settings)
    color_int = tuple(int(c * 255) for c in color_rgb)
    
    # Create the base image
    ink_saving_mode = settings.get('ink_saving_mode', False)
    background_color = (255, 255, 255) if ink_saving_mode else color_int
    border_width = settings.get('sol_border_width', 142)

    img = Image.new("RGB", (size, size), background_color)
    
    if settings.get('sol_color_wash_enabled', True):
        settings['sol_color_wash_base_color'] = color_int
    # Apply background overrides for solution side
    background_seed = zlib.crc32(
        f"{card_number}|{song_name}|{artist}|{year}".encode("utf-8")
    )
    render_card_background(img, settings, side="sol", seed=background_seed)
    
    if ink_saving_mode:
        draw = ImageDraw.Draw(img)
        draw.rectangle([(0, 0), (size - 1, size - 1)], outline=color_int, width=border_width)
    draw = ImageDraw.Draw(img)
    
    font_year = get_font_for_setting(
        settings, settings.get(
            'song_year_size', DEFAULT_SONG_YEAR_SIZE
        ), role="year",
        weight=settings.get('song_year_font_weight', 700),
    )
    # Song title, artist, and year are always rendered in solid black.
    text_color = "#000000"
    center_x = size / 2
    center_y = size / 2
    artist_edge_offset = card_distance_cm_to_pixels(
        size, SONG_ARTIST_TOP_EDGE_OFFSET_CM
    )
    title_edge_offset = card_distance_cm_to_pixels(
        size, SONG_TITLE_BOTTOM_EDGE_OFFSET_CM
    )
    year_text = display_year
    year_bbox = draw.textbbox(
        (center_x, center_y), year_text, font=font_year, anchor="mm"
    )
    text_clearance = card_distance_cm_to_pixels(
        size, SONG_TEXT_TO_YEAR_CLEARANCE_CM
    )

    # Fit each text block independently in the space outside the 2 mm
    # protected area around the central year.
    font_artist, artist_text = fit_song_text_to_height(
        draw, artist, settings, settings.get('song_artist_size', 155),
        role="artist", weight=settings.get('song_artist_font_weight', 500),
        italic=False, max_width=max_width,
        max_height=year_bbox[1] - text_clearance - artist_edge_offset,
    )
    font_song, song_text = fit_song_text_to_height(
        draw, song_name, settings, settings.get('song_title_size', 155),
        role="song", weight=settings.get('song_title_font_weight', 300),
        italic=True, max_width=max_width,
        max_height=(size - title_edge_offset) - year_bbox[3] - text_clearance,
    )

    # Draw centered text
    draw.text((center_x, center_y), year_text, fill=text_color,
              font=font_year, anchor="mm")
    draw_centered_text_at_edge(
        draw, artist_text, font_artist, center_x, artist_edge_offset, "top"
    )
    draw_centered_text_at_edge(
        draw, song_text, font_song, center_x, size - title_edge_offset, "bottom"
    )
        
    # Render Custom Title on Solution Side
    render_game_title(img, settings, side="sol")
    render_card_number(img, card_number, settings, side="sol")
    
    return img


def _fetch_spotify_embed_metadata(canonical_url):
    """Return track metadata from Spotify's bounded public embed page."""
    spotify_id = canonical_url.rsplit('/', 1)[-1]
    embed_url = f"https://open.spotify.com/embed/track/{spotify_id}"
    body, _, _ = get_bounded_https_content(
        embed_url,
        allowed_hosts={'open.spotify.com'},
        max_bytes=MAX_SPOTIFY_HTML_BYTES,
    )
    soup = BeautifulSoup(body.decode('utf-8', errors='replace'), 'html.parser')
    data_tag = soup.find('script', id='__NEXT_DATA__')
    if data_tag is None:
        raise ValueError("Missing Spotify embed metadata")

    payload = json.loads(data_tag.get_text())
    entity = payload['props']['pageProps']['state']['data']['entity']
    if entity.get('type') != 'track' or entity.get('id') != spotify_id:
        raise ValueError("Spotify embed returned an unexpected track")

    title = str(entity.get('name') or '').strip()
    artist = ', '.join(
        str(item.get('name') or '').strip()
        for item in entity.get('artists') or []
        if str(item.get('name') or '').strip()
    )
    release_date = entity.get('releaseDate') or {}
    release_text = str(release_date.get('isoString') or '')
    try:
        original_year = _validate_year(int(release_text[:4]))
    except ValueError:
        original_year = None
    if not title or not artist:
        raise ValueError("Incomplete Spotify embed metadata")
    return title, artist, original_year


def _fetch_public_track_metadata(url):
    canonical_url = canonicalize_spotify_url(url, expected_kind='track')
    try:
        html_text, canonical_url = get_spotify_html(
            canonical_url, expected_kind='track'
        )
    except requests.RequestException:
        title, artist, original_year = _fetch_spotify_embed_metadata(
            canonical_url
        )
    else:
        soup = BeautifulSoup(html_text, 'html.parser')
        title_tag = soup.find('meta', property='og:title')
        description_tag = soup.find('meta', property='og:description')
        if title_tag and description_tag:
            title = title_tag.get('content', '').strip()
            description = description_tag.get('content', '')
            artist = description.split(' · ')[0].strip()
            original_year = None
        else:
            title, artist, original_year = _fetch_spotify_embed_metadata(
                canonical_url
            )

    if not title or not artist:
        raise ValueError("Incomplete metadata")

    year, year_source = get_year_and_source(title, artist, original_year)
    return {
        'name': sanitize_name(title),
        'original_name': title,
        'original_year': original_year,
        'year': year,
        'year_source': year_source,
        'artist': artist,
        'link': canonical_url,
    }


def fetch_no_api_data_from_list(
    urls, progress_bar=None, errors_out=None
):
    """Scrape validated tracks concurrently with bounded worker count."""
    if len(urls) > MAX_TRACK_LINKS:
        raise SpotifyAPIError(
            f"A maximum of {MAX_TRACK_LINKS} tracks can be processed at once."
        )
    try:
        validated_urls = [
            canonicalize_spotify_url(url, expected_kind='track')
            for url in urls
        ]
    except InputValidationError as exc:
        raise SpotifyAPIError(str(exc)) from exc

    total = len(validated_urls)
    if total == 0:
        return []

    ordered_songs = [None] * total
    errors = []
    worker_count = min(4, total)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_fetch_public_track_metadata, url): (index, url)
            for index, url in enumerate(validated_urls)
        }
        completed = 0
        for future in as_completed(futures):
            index, url = futures[future]
            completed += 1
            try:
                song = future.result()
            except (
                InputValidationError,
                requests.RequestException,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                print(f"  Error scraping {url}: {exc}")
                errors.append({
                    'url': url,
                    'error': type(exc).__name__,
                })
                progress_text = f"Skipped {completed}/{total}"
            else:
                ordered_songs[index] = song
                print(
                    f"  {song['year']} | "
                    f"{song['artist']} - {song['original_name']}"
                )
                progress_text = (
                    f"Scraped {completed}/{total}: "
                    f"{song['original_name'][:30]}..."
                )

            if progress_bar:
                progress_bar.progress(
                    completed / total, text=progress_text
                )

    songs = [song for song in ordered_songs if song is not None]
    if errors_out is not None:
        errors_out.extend(errors)
    if errors:
        print(f"\n⚠ {len(errors)} song(s) failed to scrape:")
        for error in errors:
            print(f"  - {error['url']}: {error['error']}")
    if not songs:
        raise SpotifyAPIError(
            "No track metadata could be fetched from Spotify's public pages."
        )

    no_year = [song for song in songs if song['year'] is None]
    if no_year:
        print(
            f"\n⚠ {len(no_year)} song(s) have no year — "
            "edit songs.json manually before re-running:"
        )
        for song in no_year:
            print(f"  - {song['artist']} — {song['original_name']}")

    return songs


def create_pdf_in_memory(songs, progress_bar=None, settings_override=None):
    if not songs:
        return None

    settings = get_pdf_render_settings(get_settings(settings_override))

    page_size = get_pdf_page_size(settings['pdf_print_profile'])
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=page_size)
    width, height = page_size
    
    # Grid settings (6.5x6.5 cm cards, 12 per page)
    card_size = PDF_CARD_SIZE

    total_cards = len(songs)
    years = [song['year'] for song in songs]
    starting_number = int(settings.get('card_number_start', 1))

    for i in range(0, total_cards, PDF_CARDS_PER_PAGE):
        batch_songs = list(songs[i:i + PDF_CARDS_PER_PAGE])

        # --- PAGE 1: FRONT (QR CODES) — WHITE MARGINS/GUTTERS ---
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, width, height, stroke=0, fill=1)
        qr_page_rotated = apply_qr_page_rotation(
            c, width, height, settings.get('qr_pages_upside_down', False)
        )

        for idx, x, y in get_pdf_card_positions(
            len(batch_songs), page_size=page_size
        ):
            song = batch_songs[idx]
            card_number = starting_number + i + idx

            base_qr = create_qr_code(song['link'])
            # Per-card unique ring pattern based on link hash
            qr_pil = create_qr_with_neon_rings_in_memory(
                base_qr, seed=stable_seed(song['link']),
                settings_override=settings,
                card_number=card_number
            )

            img_byte_arr = io.BytesIO()
            qr_pil.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)

            c.drawImage(ImageReader(img_byte_arr), x, y, width=card_size, height=card_size)

            # Free the per-card rasters; otherwise peak memory grows with playlist size.
            del base_qr, qr_pil, img_byte_arr

        if qr_page_rotated:
            c.restoreState()
        c.showPage()

        # --- PAGE 2: BACK (SOLUTIONS - MIRRORED) ---
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, width, height, stroke=0, fill=1)

        for idx, x, y in get_pdf_card_positions(
            len(batch_songs), mirrored=True, page_size=page_size
        ):
            song = batch_songs[idx]
            card_number = starting_number + i + idx

            sol_pil = create_solution_side_in_memory(
                song['name'], song['artist'], song['year'], years,
                settings_override=settings, card_number=card_number
            )

            sol_byte_arr = io.BytesIO()
            # The solution side is continuous-tone artwork. High-quality 4:4:4
            # JPEG preserves its text and wash while avoiding enormous, slow
            # per-pixel-grain PNG streams in large PDFs.
            sol_pil.save(
                sol_byte_arr, format='JPEG', quality=95, subsampling=0,
                optimize=False,
            )
            sol_byte_arr.seek(0)

            c.drawImage(ImageReader(sol_byte_arr), x, y, width=card_size, height=card_size)

            del sol_pil, sol_byte_arr

        if progress_bar:
            processed = min(i + PDF_CARDS_PER_PAGE, total_cards)
            percent = processed / total_cards
            progress_bar.progress(percent, text=f"Generated {processed}/{total_cards} cards...")
        c.showPage()

        # Reclaim the batch's rasters before building the next page.
        gc.collect()

    c.save()
    pdf_data = buffer.getvalue()
    buffer.close()
    return pdf_data
