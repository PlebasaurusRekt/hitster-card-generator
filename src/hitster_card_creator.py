#!/usr/bin/env python3

"""
Hitster Card Generator
Generate custom Hitster-style music game cards from Spotify playlists.
"""

import os
import json
import re
import sys
import argparse
from dotenv import load_dotenv
if __package__:
    from . import utils
else:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from src import utils

# =============================================================================
# CONFIGURATION
# =============================================================================

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
# Step UP one level to reach the project root
PROJECT_ROOT = os.path.dirname(SRC_DIR)

# Correct paths relative to Project Root
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
FONT_DIR = os.path.join(PROJECT_ROOT, "fonts")
LINKS_FILE = os.path.join(PROJECT_ROOT, "links.txt")

FONT_PATHS = {
    'year': os.path.join(FONT_DIR, "Montserrat-Bold.ttf"),
    'artist': os.path.join(FONT_DIR, "Montserrat-SemiBold.ttf"),
    'song': os.path.join(FONT_DIR, "Montserrat-MediumItalic.ttf")
}
# Color gradient for year-based card colors (oldest to newest)
COLOR_GRADIENT = [
    "#7030A0",  # Purple (oldest)
    "#E31C79",  # Pink
    "#FF6B9D",  # Light pink
    "#FFA500",  # Orange
    "#FFD700",  # Gold
    "#87CEEB",  # Sky blue
    "#4169E1",  # Royal blue (newest)
]

# Card design parameters
CARD_SIZE = 2000  # pixels
NEON_COLORS = [(255, 0, 100), (0, 200, 255), (0, 255, 120), (255, 255, 0)]

db = {"fonts_dict": FONT_PATHS, 
      "color_gradient": COLOR_GRADIENT,
      "card_size": CARD_SIZE,
      "neon_colors": NEON_COLORS}

# =============================================================================
# FINAL INTEGRATED PIPELINE
# =============================================================================

def generate_hitster_cards(
    settings, playlist_url=None, output_dir="hitster_cards", fetch=False
):
    """Load public Spotify metadata, render cards, and create the PDF."""
    print("=== Hitster Card Generator ===\n")
    full_output_path = os.path.join(OUTPUT_DIR, output_dir)
    os.makedirs(full_output_path, exist_ok=True)
    json_file = os.path.join(full_output_path, "songs.json")

    if not fetch and os.path.exists(json_file):
        print(f"Step 1: Loading local data from {json_file}...")
        with open(json_file, 'r', encoding='utf-8') as file_handle:
            songs = json.load(file_handle)
    elif os.path.exists(LINKS_FILE):
        print(f"Step 1: Using {LINKS_FILE} (public scraper mode)...")
        songs = utils.fetch_no_api_data(LINKS_FILE)
    elif playlist_url:
        print("Step 1: Scraping the public Spotify playlist...")
        track_links = utils.scrape_playlist_track_links(playlist_url)
        songs = utils.fetch_no_api_data_from_list(track_links)
    else:
        raise utils.SpotifyAPIError(
            "No cached songs.json, links.txt, or PLAYLIST_URL was found."
        )

    if not isinstance(songs, list) or not songs:
        raise utils.SpotifyAPIError("No usable songs were found.")
    metadata_needs_save = (
        fetch
        or not os.path.exists(json_file)
        or any(
            song.get('performer_type') not in utils.PERFORMER_TYPES
            or 'spotify_artist_urls' not in song
            for song in songs
        )
    )
    classification = utils.enrich_performer_types(songs)
    if classification['lookup_failed']:
        print("⚠ MusicBrainz classification was unavailable for some artists.")
    if classification['unknown_count']:
        print(
            f"⚠ {classification['unknown_count']} song(s) remain Unknown."
        )
    if metadata_needs_save:
        with open(json_file, 'w', encoding='utf-8') as file_handle:
            json.dump(songs, file_handle, indent=2)

    card_pattern = re.compile(
        r'^card_[0-9]+_(?:qr|solution)[.]png$'
    )
    for filename in os.listdir(full_output_path):
        if card_pattern.fullmatch(filename):
            os.remove(os.path.join(full_output_path, filename))

    print(f"\nStep 2: Generating {len(songs)} cards...")
    release_years = [song['year'] for song in songs]
    for index, song in enumerate(songs):
        card_number = int(settings.get('card_number_start', 1)) + index
        qr_path = os.path.join(
            full_output_path, f"card_{card_number:03d}_qr.png"
        )
        solution_path = os.path.join(
            full_output_path, f"card_{card_number:03d}_solution.png"
        )

        qr_code = utils.create_qr_code(song['link'])
        utils.create_qr_with_neon_rings(
            qr_code, qr_path, card_number=card_number,
            settings_override=settings,
        )
        utils.create_solution_side(
            song['name'],
            song['artist'],
            song['year'],
            release_years,
            solution_path,
            card_number=card_number,
            settings_override=settings,
            performer_type=song.get('performer_type'),
        )
        if (index + 1) % 20 == 0:
            print(f"  Progress: {index + 1}/{len(songs)}...")

    print("\nStep 3: Creating PDF...")
    pdf_path = os.path.join(OUTPUT_DIR, f"{output_dir}.pdf")
    utils.create_cards_pdf(
        full_output_path,
        pdf_path,
        qr_pages_upside_down=settings.get('qr_pages_upside_down', False),
        pdf_print_profile=settings.get(
            'pdf_print_profile', utils.DEFAULT_PDF_PRINT_PROFILE
        ),
    )
    print(f"\n✓ Done! PDF ready at: {pdf_path}")
    return pdf_path


if __name__ == "__main__":

    load_dotenv()

    parser = argparse.ArgumentParser(description='Hitster Card Generator')
    parser.add_argument('--fetch', action='store_true', help='Force re-fetching data and remove existing songs.json')
    parser.add_argument('--ink-save-mode', action='store_true', default=None, help='if set, print the qr cards in ink saving mode (white background, black qr code)')
    parser.add_argument('--card-draw-border', action='store_true', default=None, help='if set, draw border around the qr cards for easier cutting')
    parser.add_argument('--card-label', default=None, help='Add a small label to each card (e.g., event name or playlist identifier)')
    parser.add_argument('--start-number', type=int, default=None, help='First card number (default: 1)')
    parser.add_argument('--qr-bg-mode', choices=['transparent', 'solid'], default=None)
    parser.add_argument('--qr-bg-color', default=None)
    parser.add_argument('--qr-module-color', default=None)
    parser.add_argument('--qr-size-ratio', type=float, default=None)
    parser.add_argument(
        '--pdf-print-profile',
        choices=[
            utils.PDF_PRINT_PROFILE_A4,
            utils.PDF_PRINT_PROFILE_PHOTOSHOP_A4_FIT,
        ],
        default=None,
        help='PDF page profile (default: a4).',
    )
    parser.add_argument('--bg-type', choices=['solid', 'neon_rings'], default=None)
    parser.add_argument('--game-title', default=None)
    parser.add_argument('--game-title-pos', default=None)
    parser.add_argument(
        '--qr-pages-upside-down', action='store_true', default=None,
        help='Rotate every QR-side PDF page by 180 degrees.',
    )
    args = parser.parse_args()

    PLAYLIST_URL = os.getenv("PLAYLIST_URL", "")
    INK_SAVING_MODE = os.getenv("INK_SAVING_MODE", "False").lower() == "true"
    CARD_DRAW_BORDER = os.getenv("CARD_DRAW_BORDER", "False").lower() == "true"
    CARD_LABEL = os.getenv("CARD_LABEL", None)
    CARD_START_NUMBER = int(os.getenv("CARD_START_NUMBER", "1"))
    
    PDF_PRINT_PROFILE = os.getenv(
        "PDF_PRINT_PROFILE", utils.DEFAULT_PDF_PRINT_PROFILE
    )
    QR_BG_MODE = os.getenv("QR_BG_MODE", "solid")
    QR_BG_COLOR = os.getenv("QR_BG_COLOR", "#000000")
    QR_MODULE_COLOR = os.getenv("QR_MODULE_COLOR", "#FFFFFF")
    QR_SIZE_RATIO = float(os.getenv(
        "QR_SIZE_RATIO", str(utils.DEFAULT_QR_SIZE_RATIO)
    ))
    BG_TYPE = os.getenv("BG_TYPE", "neon_rings")
    GAME_TITLE = os.getenv("GAME_TITLE", "")
    GAME_TITLE_POS = os.getenv("GAME_TITLE_POS", "top")
    QR_PAGES_UPSIDE_DOWN = (
        os.getenv("QR_PAGES_UPSIDE_DOWN", "False").lower() == "true"
    )

    ink_save_mode = args.ink_save_mode if args.ink_save_mode is not None else INK_SAVING_MODE
    card_draw_border = args.card_draw_border if args.card_draw_border is not None else CARD_DRAW_BORDER
    card_label = args.card_label if args.card_label is not None else CARD_LABEL
    card_number_start = (
        args.start_number if args.start_number is not None else CARD_START_NUMBER
    )
    if card_number_start < 1:
        parser.error("--start-number must be at least 1")

    db['ink_saving_mode'] = ink_save_mode
    db['card_draw_border'] = card_draw_border
    db['card_background_color'] = 'white' if ink_save_mode else 'black'
    db['card_border_color'] = 'black' if ink_save_mode else 'white'
    db['card_label'] = card_label
    db['pdf_print_profile'] = (
        args.pdf_print_profile
        if args.pdf_print_profile is not None
        else PDF_PRINT_PROFILE
    )
    db['card_number_start'] = card_number_start
    
    db['qr_background_mode'] = args.qr_bg_mode if args.qr_bg_mode is not None else QR_BG_MODE
    db['qr_background_color'] = args.qr_bg_color if args.qr_bg_color is not None else QR_BG_COLOR
    db['qr_module_color'] = args.qr_module_color if args.qr_module_color is not None else QR_MODULE_COLOR
    db['qr_size_ratio'] = args.qr_size_ratio if args.qr_size_ratio is not None else QR_SIZE_RATIO
    db['qr_bg_type'] = args.bg_type if args.bg_type is not None else BG_TYPE
    db['qr_title'] = args.game_title if args.game_title is not None else GAME_TITLE
    db['qr_title_pos'] = args.game_title_pos if args.game_title_pos is not None else GAME_TITLE_POS
    db['qr_title_enabled'] = bool(db['qr_title'])
    db['qr_pages_upside_down'] = (
        args.qr_pages_upside_down
        if args.qr_pages_upside_down is not None
        else QR_PAGES_UPSIDE_DOWN
    )

    print(f"Playlist URL: {PLAYLIST_URL or '(using links.txt/cache)'}")
    print(f"Ink saving mode: {db['ink_saving_mode']}, Draw border: {db['card_draw_border']}, Label: {db['card_label']}\n")

    if args.fetch:
        json_file = os.path.join(OUTPUT_DIR, "hitster_cards", "songs.json")
        if os.path.exists(json_file):
            os.remove(json_file)
            print(f"Removed existing {json_file}")

    try:
        generate_hitster_cards(
            db, playlist_url=PLAYLIST_URL, fetch=args.fetch
        )
    except utils.SpotifyAPIError as exc:
        parser.exit(1, f"ERROR: {exc}\n")
