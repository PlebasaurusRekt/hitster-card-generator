import io
import gc
import colorsys
import math
import time
import os
import random
import secrets
import threading
import textwrap
import re
import zlib
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
db = None
_font_cache = None

CARD_PHYSICAL_SIZE_CM = 6.5
DEFAULT_QR_CODE_SIZE_CM = 2.5
DEFAULT_QR_BORDER_CM = 0.1
DEFAULT_QR_TOTAL_SIZE_CM = (
    DEFAULT_QR_CODE_SIZE_CM + 2 * DEFAULT_QR_BORDER_CM
)
DEFAULT_QR_SIZE_RATIO = DEFAULT_QR_CODE_SIZE_CM / CARD_PHYSICAL_SIZE_CM
NEON_RING_EDGE_CLEARANCE_CM = 0.3
SONG_TEXT_EDGE_OFFSET_CM = 0.9
SOLUTION_TITLE_TOP_OFFSET_CM = 0.3
SOLUTION_TITLE_LEFT_OFFSET_CM = 0.2
CARD_NUMBER_RIGHT_OFFSET_CM = 0.3
CARD_NUMBER_BOTTOM_OFFSET_CM = 0.2


def card_distance_cm_to_pixels(card_size, distance_cm):
    """Convert a physical card distance in centimeters to raster pixels."""
    return round(card_size * distance_cm / CARD_PHYSICAL_SIZE_CM)


def get_qr_code_size_pixels(settings):
    """Return the QR raster side length for the current card resolution."""
    return max(1, round(settings['card_size'] * settings['qr_size_ratio']))


def get_qr_backplate_padding_pixels(settings):
    """Return the physical QR border width, with pixel-setting compatibility."""
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
    "qr_quiet_zone": 2, 
    "qr_backplate_padding": 0,
    "qr_backplate_padding_cm": DEFAULT_QR_BORDER_CM,
    "qr_backplate_radius": 20,
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
    "qr_card_number_opacity": 42,

    # Solution Side Settings
    "sol_bg_type": "gradient", # "gradient", "image"
    "sol_bg_image": None,
    "sol_bg_scale": 1.0,
    "sol_bg_offset_x": 0.0,
    "sol_bg_offset_y": 0.0,
    "sol_color_wash_enabled": True,
    "sol_color_wash_grain_opacity": 0.012,
    "sol_border_width": 142,

    "song_year_size": 570,
    "song_artist_size": 155,
    "song_title_size": 155,
    "card_number_size": 70,

    "sol_title": "",
    "sol_title_enabled": False,
    "sol_title_pos": "in_border_top_left",
    "sol_title_size": 140,
    "sol_title_color": (255, 255, 255),
    "sol_title_opacity": 60,
    "sol_title_bg": False,
}

def get_settings(override=None):
    """Get settings merged with defaults."""
    settings = DEFAULT_DESIGN_SETTINGS.copy()
    provided_settings = {}
    if db:
        provided_settings.update(db)
    if override:
        provided_settings.update(override)
    settings.update(provided_settings)
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
            except:
                pass
    return settings

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
def get_year_from_musicbrainz(title, artist) -> int | None:
    q = f'recording:"{title}" AND artist:"{artist}"'
    params = {"query": q, "fmt": "json", "limit": 5}
    headers = {"User-Agent": "hitster-card-generator/2.0 (https://github.com/WhiteShunpo/hitster-cards-generator)"}
    try:
        r = requests.get("https://musicbrainz.org/ws/2/recording", params=params, headers=headers, timeout=10)
        if r.status_code in (429, 503):
            time.sleep(2)
            r = requests.get("https://musicbrainz.org/ws/2/recording", params=params, headers=headers, timeout=10)
        r.raise_for_status()
        result_json = r.json()
        years = []
        if not result_json or "recordings" not in result_json:
            return None
        for rec in result_json.get("recordings", []):
            for rel in rec.get("releases", []) or []:
                date = rel.get("date")
                if date:
                    y = _validate_year(int(date.split("-")[0]))
                    if y is not None:
                        years.append(y)
        if not years:
            return None
        return min(years)
    except Exception:
        return None

def get_year_from_itunes(title, artist) -> int | None:
    q = urllib.parse.quote(f"{artist} {title}")
    url = f"https://itunes.apple.com/search?term={q}&entity=song&limit=5"
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        result_json = r.json()
        if not result_json or "results" not in result_json:
            return None
        years = []
        for res in result_json.get("results", []):
            rd = res.get("releaseDate")
            if rd:
                y = _validate_year(int(rd.split("-")[0]))
                if y is not None:
                    years.append(y)
        if not years:
            return None
        return min(years)
    except Exception:
        return None
    
def get_year_and_source(title, artist, orig_year) -> tuple[int | None, str | None]:
    """Get release year and source ('iTunes' or 'MusicBrainz') for a song.
    
    Args:
        title: Song title (first!)
        artist: Artist name (second!)
        orig_year: Fallback year from Spotify
    """
    itunes_year = get_year_from_itunes(title, artist)
    if itunes_year is not None:
        return itunes_year, 'iTunes'
    
    # Rate-limit: MusicBrainz enforces ~1 req/sec
    time.sleep(1.1)
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
    with open(links_file, 'r') as f:
        urls = [line.strip() for line in f.readlines() if 'spotify.com/track/' in line]

    return fetch_no_api_data_from_list(urls)

# =============================================================================
# SPOTIFY API FUNCTIONS
# =============================================================================

class SpotifyAPIError(RuntimeError):
    """Raised when Spotify authentication or playlist access fails."""


SPOTIFY_OAUTH_SCOPES = (
    'playlist-read-private',
    'playlist-read-collaborative',
)
SPOTIFY_OAUTH_PENDING_TTL_SECONDS = 10 * 60
_spotify_oauth_pending = {}
_spotify_oauth_lock = threading.Lock()


def _purge_expired_spotify_oauth_requests():
    """Remove expired OAuth attempts. Caller must hold the OAuth lock."""
    cutoff = time.time() - SPOTIFY_OAUTH_PENDING_TTL_SECONDS
    for state, details in list(_spotify_oauth_pending.items()):
        if details['created_at'] < cutoff:
            del _spotify_oauth_pending[state]


def begin_spotify_oauth(client_id, client_secret, redirect_uri):
    """Register an OAuth attempt and return Spotify's authorization URL."""
    client_id = str(client_id).strip()
    client_secret = str(client_secret).strip()
    redirect_uri = str(redirect_uri).strip()
    if not client_id or not client_secret:
        raise SpotifyAPIError("Enter both Spotify credentials first.")

    parsed_redirect = urllib.parse.urlparse(redirect_uri)
    is_loopback = parsed_redirect.hostname in ('127.0.0.1', '::1')
    if not (
        parsed_redirect.scheme == 'https'
        or (parsed_redirect.scheme == 'http' and is_loopback)
    ):
        raise SpotifyAPIError(
            "The redirect URI must use HTTPS, or HTTP with 127.0.0.1/::1."
        )

    state = secrets.token_urlsafe(32)
    with _spotify_oauth_lock:
        _purge_expired_spotify_oauth_requests()
        _spotify_oauth_pending[state] = {
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'created_at': time.time(),
        }

    query = urllib.parse.urlencode({
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': ' '.join(SPOTIFY_OAUTH_SCOPES),
    })
    return f'https://accounts.spotify.com/authorize?{query}'


def discard_spotify_oauth(state):
    """Discard a denied or abandoned OAuth request."""
    if not state:
        return
    with _spotify_oauth_lock:
        _spotify_oauth_pending.pop(str(state), None)


def complete_spotify_oauth(code, state):
    """Exchange a Spotify callback code for user and refresh tokens."""
    with _spotify_oauth_lock:
        _purge_expired_spotify_oauth_requests()
        pending = _spotify_oauth_pending.pop(str(state), None)
    if pending is None:
        raise SpotifyAPIError(
            "Spotify login expired or its security state did not match. Start again."
        )

    try:
        response = requests.post(
            'https://accounts.spotify.com/api/token',
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': pending['redirect_uri'],
            },
            auth=(pending['client_id'], pending['client_secret']),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise SpotifyAPIError(
            "Spotify's token service could not be reached."
        ) from exc
    if not response.ok:
        raise SpotifyAPIError(
            f"Spotify login could not be completed (HTTP {response.status_code})."
        )

    payload = response.json()
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
        'client_id': pending['client_id'],
        'client_secret': pending['client_secret'],
    }


def get_spotify_access_token(auth):
    """Return a valid user token, refreshing it when nearly expired."""
    if not isinstance(auth, dict) or not auth.get('access_token'):
        raise SpotifyAPIError("Connect your Spotify account first.")
    if float(auth.get('expires_at', 0)) > time.time() + 60:
        return auth['access_token']

    refresh_token = auth.get('refresh_token')
    if not refresh_token:
        raise SpotifyAPIError("Spotify login expired. Connect again.")
    try:
        response = requests.post(
            'https://accounts.spotify.com/api/token',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            },
            auth=(auth['client_id'], auth['client_secret']),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise SpotifyAPIError(
            "Spotify's token service could not be reached."
        ) from exc
    if not response.ok:
        raise SpotifyAPIError(
            "Spotify login expired or was revoked. Connect again."
        )

    payload = response.json()
    auth['access_token'] = payload['access_token']
    auth['expires_at'] = time.time() + int(payload.get('expires_in', 3600))
    if payload.get('refresh_token'):
        auth['refresh_token'] = payload['refresh_token']
    return auth['access_token']


def fetch_spotify_playlist_with_token(playlist_url, access_token):
    """Fetch every item in an owned/collaborative playlist with user OAuth."""
    try:
        playlist_id = playlist_url.split('/playlist/')[1].split('?')[0]
    except (IndexError, AttributeError) as exc:
        raise SpotifyAPIError("Invalid Spotify playlist URL.") from exc

    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        meta_response = requests.get(
            f'https://api.spotify.com/v1/playlists/{playlist_id}?fields=name',
            headers=headers, timeout=15,
        )
    except requests.RequestException as exc:
        raise SpotifyAPIError(
            "Spotify playlist metadata could not be reached."
        ) from exc
    if not meta_response.ok:
        raise SpotifyAPIError(
            f"Spotify could not open the playlist (HTTP {meta_response.status_code})."
        )
    playlist_name = meta_response.json().get('name', 'Unknown')
    print(f"Playlist: {playlist_name}")

    items_url = f'https://api.spotify.com/v1/playlists/{playlist_id}/items'
    all_items = []
    total = None
    params = {'limit': 50}
    try:
        while items_url:
            response = requests.get(
                items_url, headers=headers, params=params, timeout=15,
            )
            params = None
            if response.status_code == 401:
                raise SpotifyAPIError(
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
            page = response.json()
            if total is None:
                total = page.get('total')
            all_items.extend(page.get('items', []))
            items_url = page.get('next')
            if items_url:
                print(f"Fetching more tracks... (currently have {len(all_items)})")
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


def fetch_spotify_playlist(playlist_url, client_id, client_secret):
    """Reject the obsolete app-only playlist flow with a clear error."""
    raise SpotifyAPIError(
        "Spotify no longer permits playlist-item access through Client "
        "Credentials. Use the web app's Connect Spotify flow."
    )


def parse_playlist_data(playlist_data):
    """
    Extract song information from playlist data.
    
    Returns:
        array of songs ('name', 'year', 'artist', 'link')
    """
    tracks = playlist_data['tracks']['items']

    songs = []

    for item in tracks:
        # Spotify renamed playlist entry `track` to `item` in February 2026.
        # Accept both shapes so cached/older responses remain compatible.
        track = item.get('item') or item.get('track')
        if not isinstance(track, dict) or track.get('type') not in (None, 'track'):
            continue

        name = track['name']
        artist = track['artists'][0]['name']
        release_date = track['album'].get('release_date', '')
        try:
            spotify_year = _validate_year(int(release_date.split("-")[0]))
        except (TypeError, ValueError):
            spotify_year = None
        year = spotify_year
        year_source = 'Spotify' if spotify_year is not None else None

        song = {}
        song['name'] = sanitize_name(name)
        song['original_name'] = name
        song['original_year'] = spotify_year
        song['year'] = year
        song['year_source'] = year_source
        song['artist'] = artist
        song['link'] = track['external_urls']['spotify']
        song['album'] = track['album']['name']
        songs.append(song)

    no_year = [s for s in songs if s['year'] is None]
    if no_year:
        print(f"\n⚠ {len(no_year)} song(s) have no year — edit songs.json manually before re-running:")
        for s in no_year:
            print(f"  - {s['artist']} — {s['original_name']}")

    return songs


# =============================================================================
# SPOTIFY SCRAPER — extract track links from a public playlist page
# =============================================================================

def scrape_playlist_track_links(playlist_url) -> list[str]:
    """
    Scrape individual track URLs from a public Spotify playlist page.
    Returns a list of 'https://open.spotify.com/track/...' URLs.
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(playlist_url, headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        track_links = []
        # Spotify embeds track links in <meta> and <a> tags on the public page
        for tag in soup.find_all("meta"):
            content = tag.get("content", "")
            if "open.spotify.com/track/" in content:
                # Extract just the track URL (strip query params)
                url = content.split("?")[0]
                if url not in track_links:
                    track_links.append(url)

        # Also look in <a> href attributes
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if "/track/" in href:
                if href.startswith("/"):
                    href = "https://open.spotify.com" + href
                url = href.split("?")[0]
                if url not in track_links:
                    track_links.append(url)

        return track_links
    except Exception as e:
        print(f"Error scraping playlist: {e}")
        return []


# =============================================================================
# CARD GENERATION FUNCTIONS
# =============================================================================

def create_qr_code(song_link):
    """Generate inverted QR code (white on black)."""
    qr = qrcode.QRCode(version=1, box_size=10, border=0)
    qr.add_data(song_link)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    return ImageOps.invert(img)


def create_qr_with_neon_rings(qr_code, output_path, card_number=None):
    """
    Create QR code card with colorful neon rings background.
    """
    img = create_qr_with_neon_rings_in_memory(qr_code, card_number=card_number)
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


_google_font_cache = {}
_google_font_variants_cache = {}


def normalize_font_weight(weight, default=400):
    """Return a supported CSS font weight from 100 through 900."""
    try:
        weight = int(weight)
    except (TypeError, ValueError):
        weight = default
    return min(900, max(100, round(weight / 100) * 100))


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
    return min(
        pool,
        key=lambda variant: (
            abs(_font_variant_details(variant)[0] - weight),
            _font_variant_details(variant)[0],
        ),
    )


def get_google_font(family_name, size, fallback_font, italic=False, weight=700):
    """Download and cache the closest Google Font weight, or return fallback."""
    if not family_name:
        return fallback_font

    weight = normalize_font_weight(weight, default=700)
    font_id = family_name.lower().replace(" ", "-")
    font_bytes_key = (font_id, weight, italic)
    cache_key = (font_id, weight, italic, size)

    if cache_key in _google_font_cache:
        return _google_font_cache[cache_key]

    font_bytes = _google_font_cache.get(font_bytes_key)
    if not font_bytes:
        try:
            variants = _google_font_variants_cache.get(font_id)
            if variants is None:
                api_url = f"https://gwfh.mranftl.com/api/fonts/{font_id}"
                response = requests.get(api_url, timeout=5)
                variants = response.json().get('variants', []) if response.status_code == 200 else []
                _google_font_variants_cache[font_id] = variants

            variant = _select_google_font_variant(variants, weight, italic)
            if variant:
                response = requests.get(variant['ttf'], timeout=5)
                if response.status_code == 200:
                    font_bytes = response.content
                    _google_font_cache[font_bytes_key] = font_bytes
        except Exception as e:
            print(f"Error downloading font: {e}")

    if font_bytes:
        try:
            font = ImageFont.truetype(io.BytesIO(font_bytes), size)
            _google_font_cache[cache_key] = font
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
    song_name, artist, year, all_years, output_path, card_number=None
):
    """
    Create solution card with year-based color background.
    """
    img = create_solution_side_in_memory(
        song_name, artist, year, all_years, card_number=card_number
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
PDF_RENDER_DPI = 720
PDF_RENDER_CARD_SIZE = round(PDF_RENDER_DPI * PDF_CARD_SIZE / 72)
PDF_SCALED_PIXEL_SETTINGS = (
    'qr_backplate_padding', 'qr_backplate_radius', 'neon_ring_thickness',
    'qr_title_size', 'sol_border_width', 'song_year_size',
    'song_artist_size', 'song_title_size', 'card_number_size',
    'sol_title_size',
)


def get_pdf_grid_layout(page_width, page_height):
    """Return a centered A4 grid with quarter-size gaps between cards."""
    previous_gap_x = (
        page_width - PDF_GRID_COLS * PDF_CARD_SIZE
    ) / (PDF_GRID_COLS + 1)
    previous_gap_y = (
        page_height - PDF_GRID_ROWS * PDF_CARD_SIZE
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


def create_cards_pdf(cards_folder, output_pdf_path):
    """
    Create print-ready PDF with alternating front/back pages.
    3x4 grid (12 cards per page), 6.5cm x 6.5cm cards, ready for duplex printing.
    """
    c = canvas.Canvas(output_pdf_path, pagesize=A4)
    width, height = A4
    
    # Card configuration
    card_size, margin_x, margin_y, gap_x, gap_y = get_pdf_grid_layout(
        width, height
    )
    cards_per_row = PDF_GRID_COLS
    cards_per_page = PDF_CARDS_PER_PAGE
    
    # Get sorted card files
    qr_images = sorted([f for f in os.listdir(cards_folder) if f.endswith('_qr.png')],
                      key=lambda x: int(re.search(r'(\d+)', x).group()))
    solution_images = sorted([f for f in os.listdir(cards_folder) if f.endswith('_solution.png')],
                            key=lambda x: int(re.search(r'(\d+)', x).group()))
    
    total_pages = (len(qr_images) + cards_per_page - 1) // cards_per_page
    
    # Create alternating front/back pages
    for page_idx in range(total_pages):
        start_card = page_idx * cards_per_page
        end_card = min(start_card + cards_per_page, len(qr_images))
        
        # FRONT PAGE (QR codes) — keep sheet margins/gutters ink-free.
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, width, height, stroke=0, fill=1)
        
        for card_idx in range(start_card, end_card):
            idx = card_idx - start_card
            row = idx // cards_per_row
            col = idx % cards_per_row
            
            x = margin_x + col * (card_size + gap_x)
            y = height - margin_y - (row + 1) * card_size - row * gap_y
            
            qr_path = os.path.join(cards_folder, qr_images[card_idx])
            draw_pdf_card_image(c, qr_path, x, y, card_size)
        
        c.showPage()
        
        # BACK PAGE (Solutions) - white background, mirrored
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, width, height, stroke=0, fill=1)
        
        for card_idx in range(start_card, end_card):
            idx = card_idx - start_card
            row = idx // cards_per_row
            col = idx % cards_per_row
            col_mirrored = cards_per_row - 1 - col  # Mirror for duplex
            
            x = margin_x + col_mirrored * (card_size + gap_x)
            y = height - margin_y - (row + 1) * card_size - row * gap_y
            
            sol_path = os.path.join(cards_folder, solution_images[card_idx])
            draw_pdf_card_image(c, sol_path, x, y, card_size)
        
        c.showPage()
    
    c.save()
    print(f"\n✓ Created PDF: {output_pdf_path}")
    print(f"  - {len(qr_images)} cards total")
    print(f"  - {total_pages * 2} pages (alternating front/back)")
    print(f"  - Ready for duplex printing!")
    return output_pdf_path


# =============================================================================
# WEBUTILS
# =============================================================================

def apply_background_image(img, bg_img, scale, offset_x, offset_y, card_size):
    """Applies scaled and offset background image to card."""
    aspect = bg_img.width / bg_img.height
    
    if aspect > 1:
        base_h = card_size
        base_w = int(card_size * aspect)
    else:
        base_w = card_size
        base_h = int(card_size / aspect)
        
    new_w = int(base_w * scale)
    new_h = int(base_h * scale)
    
    resized = bg_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    x = (card_size - new_w) // 2 + int(offset_x * card_size)
    y = (card_size - new_h) // 2 + int(offset_y * card_size)
    
    if resized.mode in ('RGBA', 'LA') or (resized.mode == 'P' and 'transparency' in resized.info):
        resized = resized.convert("RGBA")
        img.paste(resized, (x, y), resized)
    else:
        img.paste(resized, (x, y))


def derive_solution_color_wash_palette(base_color, separation=1.0):
    """Return the base and a randomized-strength warmer/brighter RGB color."""
    base_rgb = tuple(round(channel * 255) for channel in to_rgba(base_color)[:3])
    red, green, blue = (channel / 255 for channel in base_rgb)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)

    def move_hue_toward(target, amount=0.24):
        distance = (target - hue + 0.5) % 1.0 - 0.5
        return (hue + distance * amount) % 1.0

    adjusted_saturation = min(1.0, max(saturation, 0.08) + 0.05)
    maximum_warm = colorsys.hls_to_rgb(
        move_hue_toward(25 / 360),
        lightness + (1.0 - lightness) * 0.22,
        adjusted_saturation,
    )
    separation = max(0.0, min(1.0, float(separation)))
    maximum_warm_rgb = tuple(round(channel * 255) for channel in maximum_warm)
    warm_rgb = tuple(
        round(base_channel + (warm_channel - base_channel) * separation)
        for base_channel, warm_channel in zip(base_rgb, maximum_warm_rgb)
    )
    return base_rgb, warm_rgb


def derive_solution_cool_color(base_color, separation=1.0):
    """Return a randomized-strength cooler/darker companion RGB color."""
    base_rgb = tuple(round(channel * 255) for channel in to_rgba(base_color)[:3])
    cool_target = (35, 55, 145)
    maximum_cool_rgb = tuple(
        round((base_channel * 0.84 + target_channel * 0.16) * 0.88)
        for base_channel, target_channel in zip(base_rgb, cool_target)
    )
    separation = max(0.0, min(1.0, float(separation)))
    return tuple(
        round(base_channel + (cool_channel - base_channel) * separation)
        for base_channel, cool_channel in zip(base_rgb, maximum_cool_rgb)
    )


def render_solution_color_wash(img, settings, seed=42):
    """Render independently randomized warm and cool solution color fields."""
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

    # Keep every field unmistakably visible while retaining per-card variation.
    warm_separation = rng.uniform(0.80, 1.0)
    warm_opacity = rng.uniform(0.55, 0.78)
    warm_edge_influence = rng.uniform(0.75, 1.0)
    _, warm = derive_solution_color_wash_palette(
        base, separation=warm_separation
    )
    warm_angle = rng.uniform(0, 2 * np.pi)
    apply_color_field(warm, warm_opacity, warm_edge_influence, warm_angle)

    cool_separation = rng.uniform(0.80, 1.0)
    cool_opacity = rng.uniform(0.55, 0.78)
    cool_edge_influence = rng.uniform(0.75, 1.0)
    cool = derive_solution_cool_color(base, separation=cool_separation)
    cool_angle = warm_angle + np.pi + rng.uniform(-0.35, 0.35)
    apply_color_field(cool, cool_opacity, cool_edge_influence, cool_angle)

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


def render_card_background(img, settings, side="qr", seed=42):
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
        
        qr_size = get_qr_code_size_pixels(settings)
        qr_padding = get_qr_backplate_padding_pixels(settings)
        safety_radius = (
            (qr_size // 2) + qr_padding
            + round(size * 0.01)
        )
        
        random.seed(seed)
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
                
            num_gaps = random.randint(1, 3)
            for gap in range(num_gaps):
                gap_start = random.randint(0, 360)
                gap_length = random.randint(20, 60)
                
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

def render_qr_backplate(img, settings):
    """Render a solid backplate for the QR code if configured."""
    if settings['qr_background_mode'] != "solid":
        return
        
    size = settings['card_size']
    qr_size = get_qr_code_size_pixels(settings)
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
    """Render the QR code modules on top of the card."""
    size = settings['card_size']
    center = size // 2
    qr_size_base = get_qr_code_size_pixels(settings)
    quiet_zone = settings.get('qr_quiet_zone', 2)
    
    qr_code_rgb = qr_code.convert('RGB')
    qr_code_resized = qr_code_rgb.resize((qr_size_base, qr_size_base), Image.Resampling.LANCZOS)
    
    qr_l = qr_code_resized.convert('L')
    arr = np.array(qr_l)
    modules_mask = arr > 128
    mask_img = Image.fromarray((modules_mask.astype('uint8') * 255)).convert('1')
    
    left = center - qr_size_base // 2
    top = center - qr_size_base // 2
    
    module_color = settings['qr_module_color']
    
    if settings['qr_background_mode'] == "transparent":
        bg_crop = img.crop((left, top, left + qr_size_base, top + qr_size_base)).convert('L')
        bg_mean = np.array(bg_crop).mean()
        if module_color == (255, 255, 255) or module_color == (0, 0, 0):
             module_color = (0, 0, 0) if bg_mean > 127 else (255, 255, 255)
    
    overlay = Image.new('RGB', (qr_size_base, qr_size_base), module_color)
    img.paste(overlay, (left, top), mask_img)

def render_game_title(img, settings, side="qr"):
    """Render the game title / card label."""
    prefix = "qr" if side == "qr" else "sol"
    
    if not settings.get(f'{prefix}_title_enabled') or not settings.get(f'{prefix}_title'):
        return
        
    title = settings[f'{prefix}_title']
    default_pos = 'top' if side == 'qr' else 'in_border_top_left'
    pos = settings.get(f'{prefix}_title_pos', default_pos)
    default_font_size = 80 if side == 'qr' else 140
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
    qr_bound = (
        center
        + get_qr_code_size_pixels(settings) // 2
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
    
    render_card_background(img, settings, side="qr", seed=seed)
    render_qr_backplate(img, settings)
    render_qr_code(img, qr_code, settings)
    render_game_title(img, settings, side="qr")
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
        settings, settings.get('song_year_size', 570), role="year",
        weight=settings.get('song_year_font_weight', 700),
    )
    font_artist = get_font_for_setting(
        settings, settings.get('song_artist_size', 155), role="artist",
        weight=settings.get('song_artist_font_weight', 500),
    )
    font_song = get_font_for_setting(
        settings, settings.get('song_title_size', 155), role="song", italic=True,
        weight=settings.get('song_title_font_weight', 300),
    )
    
    # Song title, artist, and year are always rendered in solid black.
    text_color = "#000000"

    def get_fitted_text_in_memory(text, font, max_width):
        """Wrap text to fit within max_width."""
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        
        if text_width <= max_width:
            return text
        
        avg_char_width = text_width / len(text)
        chars_per_line = int(max_width / avg_char_width * 0.85)
        wrapped = '\n'.join(textwrap.wrap(text, width=max(chars_per_line, 10)))
        
        return wrapped
    
    # Prepare text
    song_text = get_fitted_text_in_memory(song_name, font_song, max_width)
    artist_text = get_fitted_text_in_memory(artist, font_artist, max_width)
    year_text = display_year
    
    # Draw centered text
    center_x = size / 2
    center_y = size / 2
    draw.text((center_x, center_y), year_text, fill=text_color, 
             font=font_year, anchor="mm")

    edge_offset = card_distance_cm_to_pixels(size, SONG_TEXT_EDGE_OFFSET_CM)
    draw_centered_text_at_edge(
        draw, artist_text, font_artist, center_x, edge_offset, "top"
    )
    draw_centered_text_at_edge(
        draw, song_text, font_song, center_x, size - edge_offset, "bottom"
    )
        
    # Render Custom Title on Solution Side
    render_game_title(img, settings, side="sol")
    render_card_number(img, card_number, settings, side="sol")
    
    return img


def fetch_no_api_data_from_list(urls, progress_bar=None):
    """
    Scrapes metadata from public Spotify pages based on a provided list of URLs.
    """
    songs = []
    errors = []
    total = len(urls)
    
    for i, url in enumerate(urls):
        idx = i + 1
        print(f"  [{idx}/{total}] Scraping: {url}")
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            res = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # Metadata from OpenGraph tags
            title_tag = soup.find("meta", property="og:title")
            desc_tag = soup.find("meta", property="og:description")
            if not title_tag or not desc_tag:
                errors.append({"url": url, "error": "Missing metadata tags"})
                continue

            title = title_tag['content']
            desc = desc_tag['content']
            artist = desc.split(" · ")[0]
            
            # FIX: correct argument order — (title, artist, fallback)
            year, year_source = get_year_and_source(title, artist, None)
            
            song = {
                'name': sanitize_name(title),
                'original_name': title,
                'original_year': None,
                'year': year,
                'year_source': year_source,
                'artist': artist,
                'link': url,
            }
            songs.append(song)
            
            print(f"  {year} | {artist} - {title}")
            time.sleep(0.5)
            
            # Update Progress — fixed off-by-one
            if progress_bar:
                percent = idx / total
                progress_bar.progress(percent, text=f"Scraped {idx}/{total}: {title[:30]}...")

        except Exception as e:
            print(f"  Error scraping {url}: {e}")
            errors.append({"url": url, "error": str(e)})
    
    if errors:
        print(f"\n⚠ {len(errors)} song(s) failed to scrape:")
        for err in errors:
            print(f"  - {err['url']}: {err['error']}")

    no_year = [s for s in songs if s['year'] is None]
    if no_year:
        print(f"\n⚠ {len(no_year)} song(s) have no year — edit songs.json manually before re-running:")
        for s in no_year:
            print(f"  - {s['artist']} — {s['original_name']}")

    return songs

def create_pdf_in_memory(songs, progress_bar=None, settings_override=None):
    if not songs:
        return None

    settings = get_pdf_render_settings(get_settings(settings_override))

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Grid settings (6.5x6.5 cm cards, 12 per page)
    card_size, margin_x, margin_y, gap_x, gap_y = get_pdf_grid_layout(
        width, height
    )
    cols = PDF_GRID_COLS

    total_cards = len(songs)
    years = [song['year'] for song in songs]
    starting_number = int(settings.get('card_number_start', 1))

    card_label = settings.get('card_label', None)

    for i in range(0, total_cards, PDF_CARDS_PER_PAGE):
        batch_songs = list(songs[i:i + PDF_CARDS_PER_PAGE])

        # --- PAGE 1: FRONT (QR CODES) — WHITE MARGINS/GUTTERS ---
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, width, height, stroke=0, fill=1)

        for idx, song in enumerate(batch_songs):
            card_number = starting_number + i + idx
            col = idx % cols
            row = (idx // cols) 
            x = margin_x + col * (card_size + gap_x)
            y = height - margin_y - (row + 1) * card_size - row * gap_y
            
            base_qr = create_qr_code(song['link'])
            # Per-card unique ring pattern based on link hash
            qr_pil = create_qr_with_neon_rings_in_memory(
                base_qr, seed=hash(song['link']), settings_override=settings,
                card_number=card_number
            )

            img_byte_arr = io.BytesIO()
            qr_pil.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)

            c.drawImage(ImageReader(img_byte_arr), x, y, width=card_size, height=card_size)

            # Free the per-card rasters; otherwise peak memory grows with playlist size.
            del base_qr, qr_pil, img_byte_arr

        c.showPage()

        # --- PAGE 2: BACK (SOLUTIONS - MIRRORED) ---
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, width, height, stroke=0, fill=1)

        for idx, song in enumerate(batch_songs):
            card_number = starting_number + i + idx
            orig_col = idx % cols
            mirrored_col = (cols - 1) - orig_col
            row = (idx // cols)
            
            x = margin_x + mirrored_col * (card_size + gap_x)
            y = height - margin_y - (row + 1) * card_size - row * gap_y
            
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
