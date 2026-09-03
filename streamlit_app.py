import importlib
import urllib.parse
import uuid

import streamlit as st
import pandas as pd
import src.input_validation as input_validation
import src.utils as utils

EXPECTED_UTILS_API_VERSION = 9
if getattr(utils, 'UTILS_API_VERSION', 0) < EXPECTED_UTILS_API_VERSION:
    utils = importlib.reload(utils)


# Per-session default for the year-color gradient. Settings are kept in
# st.session_state (per user) rather than a shared module global to avoid
# cross-session bleed when multiple users hit the same Streamlit server.
DEFAULT_COLOR_GRADIENT = [
    "#7030A0", "#E31C79", "#FF6B9D", "#FFA500",
    "#FFD700", "#87CEEB", "#4169E1",
]
FONT_WEIGHT_MIN = 100
FONT_WEIGHT_MAX = 900
FONT_WEIGHT_STEP = 1

OUTPUT_DIR = "output"
LINKS_FILE = "links.txt"

# --- STATE INITIALIZATION ---
if "songs" not in st.session_state:
    st.session_state.songs = []
if "pdf_data" not in st.session_state:
    st.session_state.pdf_data = None

def reset_generation():
    st.session_state.pdf_data = None
    st.session_state.pop('pdf_fingerprint', None)


def font_weight_slider(label, default, key):
    """Render a continuous 100–900 variable-font weight slider."""
    saved_weight = st.session_state.get(key)
    slider_kwargs = {
        "label": label,
        "min_value": FONT_WEIGHT_MIN,
        "max_value": FONT_WEIGHT_MAX,
        "step": FONT_WEIGHT_STEP,
        "help": (
            "Choose any weight from 100 (Thin) through 900 (Black). "
            "Montserrat uses its variable-font weight axis for exact "
            "per-point preview changes."
        ),
        "key": key,
    }
    if saved_weight is not None:
        normalized_weight = utils.normalize_font_weight(
            saved_weight, default
        )
        if normalized_weight != saved_weight:
            st.session_state[key] = normalized_weight
    else:
        slider_kwargs["value"] = default
    return st.slider(**slider_kwargs)

def set_example_playlist():
    st.session_state.user_input = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
    reset_generation()

def set_example_links():
    st.session_state.user_input = (
        "https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8?si=44d4b8822cac4dc8\n"
        "https://open.spotify.com/track/0Bo5fjMtTfCD8vHGebivqc?si=5bc94c4aadf84bca\n"
        "https://open.spotify.com/track/6Sy9BUbgFse0n0LPA5lwy5?si=ac74b629e3834310"
    )
    reset_generation()

def parse_input(text):
    try:
        return input_validation.classify_spotify_input(text)
    except input_validation.InputValidationError as exc:
        return 'invalid', str(exc)


def default_spotify_redirect_uri():
    """Return the exact current app URL in Spotify-compatible form."""
    url = str(st.context.url).split('?', 1)[0].split('#', 1)[0]
    parsed = urllib.parse.urlsplit(url)
    if not parsed.path:
        url += '/'
    return url.replace('://localhost', '://127.0.0.1', 1)


def add_color_cb(key_prefix):
    st.session_state[f"{key_prefix}_items"].append({"id": str(uuid.uuid4()), "color": "#FFFFFF"})

def del_color_cb(key_prefix, index):
    st.session_state[f"{key_prefix}_items"].pop(index)

def move_up_cb(key_prefix, index):
    items = st.session_state[f"{key_prefix}_items"]
    items[index], items[index-1] = items[index-1], items[index]

def move_down_cb(key_prefix, index):
    items = st.session_state[f"{key_prefix}_items"]
    items[index], items[index+1] = items[index+1], items[index]

def dynamic_color_list(key_prefix, title, default_colors, help_text=""):
    """Renders a collapsible dynamic list of color pickers using UUIDs and callbacks to preserve state."""
    if f"{key_prefix}_items" not in st.session_state:
        st.session_state[f"{key_prefix}_items"] = [{"id": str(uuid.uuid4()), "color": c} for c in default_colors]
        
    items = st.session_state[f"{key_prefix}_items"]
    
    with st.expander(title, expanded=False):
        if help_text:
            st.caption(help_text)
            
        st.button("➕ Add Color", key=f"add_{key_prefix}", on_click=add_color_cb, args=(key_prefix,))
            
        for i, item in enumerate(items):
            color = item["color"]
            item_id = item["id"]
            
            # Parse color string
            hex_c = color[:7] if len(color) >= 7 else "#000000"
            
            col1, col2, col3, col4 = st.columns([5, 1, 1, 1])
            with col1:
                new_hex = st.color_picker(f"Color {i+1}", value=hex_c, key=f"{key_prefix}_c_{item_id}")
                items[i]["color"] = new_hex
            
            with col2:
                st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                st.button("🗑️", key=f"{key_prefix}_del_{item_id}", on_click=del_color_cb, args=(key_prefix, i))
            with col3:
                st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                if i > 0:
                    st.button("⬆️", key=f"{key_prefix}_up_{item_id}", on_click=move_up_cb, args=(key_prefix, i))
            with col4:
                st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                if i < len(items) - 1:
                    st.button("⬇️", key=f"{key_prefix}_down_{item_id}", on_click=move_down_cb, args=(key_prefix, i))
                    
        st.session_state[f"{key_prefix}_items"] = items
        return [item["color"] for item in items]

# --- UI INTERFACE ---
st.set_page_config(page_title="Hitster Generator", page_icon="🎵", layout="wide", initial_sidebar_state="expanded")

spotify_redirect_default = default_spotify_redirect_uri()
spotify_client_id = str(
    st.session_state.get('spotify_client_id', '') or ''
).strip()
spotify_client_secret = str(
    st.session_state.get('spotify_client_secret', '') or ''
).strip()
spotify_redirect_uri = str(
    st.session_state.get(
        'spotify_redirect_uri', spotify_redirect_default
    ) or spotify_redirect_default
).strip()

spotify_callback_pending = None
spotify_callback_code = st.query_params.get('code')
spotify_callback_state = st.query_params.get('state')
spotify_callback_error = st.query_params.get('error')
if spotify_callback_error:
    utils.discard_spotify_oauth(spotify_callback_state)
    st.session_state.pop('spotify_oauth_url', None)
    st.session_state.spotify_oauth_notice = (
        'error', f"Spotify authorization failed: {spotify_callback_error}"
    )
    st.query_params.clear()
elif spotify_callback_code:
    callback_completed = False
    callback_failure = None
    if spotify_client_id and spotify_client_secret:
        try:
            st.session_state.spotify_auth = utils.complete_spotify_oauth(
                spotify_callback_code,
                spotify_callback_state,
                spotify_client_id,
                spotify_client_secret,
            )
        except utils.SpotifyAPIError as exc:
            callback_failure = str(exc)
        else:
            callback_completed = True
            st.session_state.pop('spotify_oauth_url', None)
            st.session_state.spotify_oauth_notice = (
                'success', 'Spotify account connected successfully.'
            )
            st.query_params.clear()

    if not callback_completed:
        try:
            callback_hints = utils.inspect_spotify_oauth_state(
                spotify_callback_state
            )
        except utils.SpotifyAPIError as exc:
            st.session_state.pop('spotify_oauth_url', None)
            st.session_state.spotify_oauth_notice = ('error', str(exc))
            st.query_params.clear()
        else:
            spotify_callback_pending = {
                'code': spotify_callback_code,
                'state': spotify_callback_state,
                **callback_hints,
            }
            if callback_failure:
                st.session_state.spotify_oauth_notice = (
                    'error',
                    f"{callback_failure} Re-enter your Spotify credentials "
                    "to try again.",
                )

spotify_oauth_notice = st.session_state.pop('spotify_oauth_notice', None)

with st.sidebar:
    st.header("⚙️ Settings")
    st.caption("Customize your print layout")
    
    tabs = st.tabs(["Global", "QR Side (Front)", "Solution Side (Back)"])
    
    with tabs[0]:
        st.subheader("📄 Print & Layout")
        pdf_print_profile = st.selectbox(
            "PDF Print Profile",
            options=[
                utils.PDF_PRINT_PROFILE_A4,
                utils.PDF_PRINT_PROFILE_PHOTOSHOP_A4_FIT,
            ],
            format_func=lambda profile: (
                "Standard A4"
                if profile == utils.PDF_PRINT_PROFILE_A4
                else "Photoshop A4 fit (97.27%)"
            ),
            help=(
                "Use Photoshop A4 fit when Photoshop is set to A4 and "
                "Scale to Fit Media reports 97.27%. It preserves the 6.5 cm "
                "cards and the gaps between them, while changing only the "
                "outer white borders."
            ),
        )
        ink_mode = st.toggle("Ink Saving Mode", value=st.session_state.get('ink_mode', False), 
                            help="Use white background and black text to save ink.")
        border_mode = st.toggle("Draw Cutting Borders", value=st.session_state.get('border_mode', False),
                               help="Draw a line around each card for easier cutting.")
        qr_pages_upside_down = st.toggle(
            "Rotate QR Pages 180° (Upside Down)",
            value=st.session_state.get('qr_pages_upside_down', False),
            help=(
                "Rotates the complete QR-side PDF page, including the card "
                "grid, QR codes, labels, and card numbers."
            ),
        )
        card_number_start = st.number_input(
            "Starting Card Number", min_value=1, value=1, step=1,
            help="Cards are numbered automatically from this value."
        )
        card_label = st.text_input(
            "Card Label",
            help="Optional set name printed on both sides of each card.",
        )

        font_choice = st.selectbox("Font Selection", ["Montserrat", "Oswald", "Roboto", "Dancing Script", "Pacifico", "Custom..."])
        if font_choice == "Custom...":
            google_font = st.text_input("Custom Google Font Name", value=st.session_state.get('google_font', "Montserrat"),
                                        help="Type any font name from Google Fonts.")
            st.markdown("[🔍 Browse Google Fonts here](https://fonts.google.com/)", unsafe_allow_html=True)
        else:
            google_font = font_choice

        with st.expander("🔤 Font Weights", expanded=True):
            card_number_font_weight = font_weight_slider(
                "Card Number", 600, "card_number_font_weight"
            )
            card_set_title_font_weight = font_weight_slider(
                "Card Set Name", 500, "card_set_title_font_weight"
            )
            song_artist_font_weight = font_weight_slider(
                "Song Artist", 565, "song_artist_font_weight"
            )
            song_year_font_weight = font_weight_slider(
                "Song Year", 700, "song_year_font_weight"
            )
            song_title_font_weight = font_weight_slider(
                "Song Title", 300, "song_title_font_weight"
            )
            
        st.divider()

        with st.expander(
            "🔑 Connect Spotify",
            expanded=(
                spotify_oauth_notice is not None
                or spotify_callback_pending is not None
            ),
        ):
            st.caption(
                "Use your own Spotify developer app to authorize private and "
                "collaborative playlists."
            )
            if spotify_oauth_notice:
                notice_type, notice_text = spotify_oauth_notice
                if notice_type == 'success':
                    st.success(notice_text)
                else:
                    st.error(notice_text)

            spotify_auth = st.session_state.get('spotify_auth')
            if spotify_auth:
                st.success("Connected with Spotify user authorization")
                if st.button("Disconnect Spotify"):
                    for session_key in (
                        'spotify_auth',
                        'spotify_client_id',
                        'spotify_client_secret',
                        'spotify_redirect_uri',
                        'spotify_oauth_url',
                    ):
                        st.session_state.pop(session_key, None)
                    st.rerun()
            elif spotify_callback_pending:
                st.info(
                    "Spotify returned to the app. Enter the same Client Secret "
                    "once to verify the signed login and finish connecting."
                )
                st.caption("Redirect URI used for this login:")
                st.code(
                    spotify_callback_pending['redirect_uri'],
                    language=None,
                )
                with st.form("spotify_callback_credentials"):
                    callback_client_id = st.text_input(
                        "Client ID",
                        value=spotify_callback_pending['client_id'],
                    )
                    callback_client_secret = st.text_input(
                        "Client Secret",
                        type="password",
                    )
                    finish_spotify_oauth = st.form_submit_button(
                        "Finish Spotify Connection",
                        type="primary",
                    )
                if finish_spotify_oauth:
                    try:
                        spotify_auth = utils.complete_spotify_oauth(
                            spotify_callback_pending['code'],
                            spotify_callback_pending['state'],
                            callback_client_id,
                            callback_client_secret,
                        )
                    except utils.SpotifyAPIError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.spotify_auth = spotify_auth
                        st.session_state.spotify_client_id = (
                            callback_client_id.strip()
                        )
                        st.session_state.spotify_client_secret = (
                            callback_client_secret.strip()
                        )
                        st.session_state.spotify_redirect_uri = (
                            spotify_callback_pending['redirect_uri']
                        )
                        st.session_state.pop('spotify_oauth_url', None)
                        st.session_state.spotify_oauth_notice = (
                            'success',
                            'Spotify account connected successfully.',
                        )
                        st.query_params.clear()
                        st.rerun()

                if st.button("Cancel and start again"):
                    for session_key in (
                        'spotify_client_id',
                        'spotify_client_secret',
                        'spotify_redirect_uri',
                        'spotify_oauth_url',
                    ):
                        st.session_state.pop(session_key, None)
                    st.query_params.clear()
                    st.rerun()
            else:
                st.markdown(
                    "[Open the Spotify Developer Dashboard]"
                    "(https://developer.spotify.com/dashboard)"
                )
                with st.form("spotify_credentials"):
                    entered_client_id = st.text_input(
                        "Client ID",
                        value=spotify_client_id,
                    )
                    entered_client_secret = st.text_input(
                        "Client Secret",
                        type="password",
                    )
                    entered_redirect_uri = st.text_input(
                        "Redirect URI",
                        value=spotify_redirect_uri,
                        help=(
                            "Register this exact URI in your Spotify app "
                            "settings before authorizing."
                        ),
                    )
                    prepare_spotify_oauth = st.form_submit_button(
                        "Prepare Spotify Login",
                        type="primary",
                    )

                if prepare_spotify_oauth:
                    try:
                        spotify_oauth_url = utils.begin_spotify_oauth(
                            entered_client_id,
                            entered_client_secret,
                            entered_redirect_uri,
                        )
                    except utils.SpotifyAPIError as exc:
                        st.error(str(exc))
                    else:
                        spotify_client_id = entered_client_id.strip()
                        spotify_client_secret = entered_client_secret.strip()
                        spotify_redirect_uri = entered_redirect_uri.strip()
                        st.session_state.spotify_client_id = (
                            spotify_client_id
                        )
                        st.session_state.spotify_client_secret = (
                            spotify_client_secret
                        )
                        st.session_state.spotify_redirect_uri = (
                            spotify_redirect_uri
                        )
                        st.session_state.spotify_oauth_url = spotify_oauth_url

                spotify_oauth_url = st.session_state.get(
                    'spotify_oauth_url'
                )
                if spotify_oauth_url:
                    st.info(
                        "Spotify opens as a top-level page and returns here "
                        "after you approve access."
                    )
                    st.link_button(
                        "Authorize with Spotify",
                        spotify_oauth_url,
                        type="primary",
                    )

                st.caption(
                    "Your credentials are kept only in this Streamlit session "
                    "for login and token refresh. The Client Secret is never "
                    "put in the OAuth URL. A fresh callback session will ask "
                    "for it once more."
                )

        st.divider()
        st.header("Feedback")
        st.write("Found a bug or have a feature idea? Let me know on GitHub!")
        st.link_button(
            label="Open GitHub Issues", 
            url="https://github.com/PlebasaurusRekt/hitster-card-generator/issues",
            type="secondary",
        )

        st.divider()
        st.markdown("### ☕ Support the Project")
        button_html = """
        <a href="https://www.buymeacoffee.com/WhiteShunpo" target="_blank">
            <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" 
            alt="Buy Me A Coffee" style="height: 50px !important;width: 181px !important;" >
        </a>
        """
        st.markdown(button_html, unsafe_allow_html=True)
        
        st.session_state.ink_mode = ink_mode
        st.session_state.border_mode = border_mode
        st.session_state.qr_pages_upside_down = qr_pages_upside_down
        st.session_state.google_font = google_font

    with tabs[1]:
        st.subheader("🖼️ Background")
        qr_bg_type = st.selectbox("Background Type", ["neon_rings", "solid", "image"], key="qr_bg_type")
        qr_bg_color = st.color_picker("Background Color", value="#000000", key="qr_bg_color")
        
        if qr_bg_type == "image":
            qr_bg_upload = st.file_uploader("Upload Image (QR Side)", type=["png", "jpg", "jpeg"], key="qr_bg_up")
            if qr_bg_upload:
                try:
                    st.session_state.qr_bg_img = (
                        input_validation.load_uploaded_image(qr_bg_upload)
                    )
                except input_validation.InputValidationError as exc:
                    st.session_state.qr_bg_img = None
                    st.error(str(exc))
            else:
                st.session_state.qr_bg_img = None
            
            st.session_state.qr_bg_scale = st.slider("Image Scale", 0.1, 3.0, 1.0, 0.1, key="qr_scale")
            st.session_state.qr_bg_x = st.slider("X Offset", -1.0, 1.0, 0.0, 0.05, key="qr_x")
            st.session_state.qr_bg_y = st.slider("Y Offset", -1.0, 1.0, 0.0, 0.05, key="qr_y")
        
        if qr_bg_type == "neon_rings":
            st.session_state.neon_ring_thickness = st.slider("Ring Thickness", 1, 50, 16, key="neon_thick")
            st.session_state.neon_ring_count = st.slider("Ring Count", 1, 20, 14, key="neon_count")
            
            neon_hex_list = dynamic_color_list("neon", "Neon Ring Colors", ["#FF0064", "#00C8FF", "#00FF78", "#FFFF00"])
            try:
                st.session_state.neon_colors = [tuple(int(val * 255) for val in utils.to_rgba(c)) for c in neon_hex_list]
            except (TypeError, ValueError):
                st.session_state.neon_colors = [(255, 0, 100), (0, 200, 255), (0, 255, 120), (255, 255, 0)]
                
        st.subheader("📱 QR Settings")
        qr_bg_mode = st.selectbox("QR Background Mode", ["solid", "transparent"], key="qr_bg_mode")
        qr_module_color = st.color_picker("QR Module Color", value="#FFFFFF", key="qr_mod_c")
        qr_border_cm = st.slider(
            "QR Quiet Zone (cm)", 0.0, 0.5,
            utils.DEFAULT_QR_BORDER_CM, 0.05, key="qr_border_cm",
            help="The single blank border surrounding the QR data.",
        )
        if qr_bg_mode == "solid":
            st.session_state.qr_backplate_color = st.color_picker("QR Backplate Color", value="#000000", key="qr_bp_c")
            st.session_state.qr_radius = st.slider(
                "Backplate Corner Radius", 0, 100, 0,
                key="qr_quiet_radius",
            )
        qr_size_cm = st.slider(
            "QR Code Size (cm)", 1.0, 5.0,
            utils.DEFAULT_QR_CODE_SIZE_CM, 0.1, key="qr_size_cm",
        )
        st.session_state.qr_card_number_opacity = st.slider("Card Number Opacity (%)", 0, 100, 42, key="qr_card_num_opacity")
        
        st.subheader("🔤 Title")
        st.session_state.qr_title_en = st.toggle("Enable Title", key="qr_t_en")
        if st.session_state.qr_title_en:
            st.session_state.qr_title = st.text_input("Title Text", value="HITSTER", key="qr_t_t")
            qr_title_upload = st.file_uploader(
                "Title Artwork (replaces title text)",
                type=["png", "jpg", "jpeg", "svg"], key="qr_title_up",
                help="SVG artwork keeps its aspect ratio at preview and print resolution.",
            )
            if qr_title_upload:
                try:
                    st.session_state.qr_title_img = (
                        input_validation.load_uploaded_image(qr_title_upload)
                    )
                except input_validation.InputValidationError as exc:
                    st.session_state.qr_title_img = None
                    st.error(str(exc))
            else:
                st.session_state.qr_title_img = None
            pos_options = ["top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right", "center_above_qr", "center_below_qr"]
            st.session_state.qr_title_pos = st.selectbox("Position", pos_options, key="qr_t_p")
            st.session_state.qr_title_size = st.slider("Card Set Title Size", 20, 200, 80, key="qr_t_s")
            st.session_state.qr_title_color = st.color_picker("Title Color", value="#FFFFFF", key="qr_t_c")
            st.session_state.qr_title_bg = st.toggle("Draw Background Box", key="qr_t_bg")

    with tabs[2]:
        st.subheader("🎨 Color Gradient")
        default_grad = DEFAULT_COLOR_GRADIENT
        st.session_state.color_gradient = dynamic_color_list(
            "gradient", 
            "Year Color Gradient", 
            default_grad, 
            help_text="Colors map to the oldest to newest years."
        )

        st.subheader("🖼️ Background")
        sol_color_wash_enabled = st.toggle(
            "Enable Soft Color Wash",
            value=True,
            help=(
                "Uses each card's year-gradient color with a randomized "
                "lighter field of the same color."
            ),
        )
        sol_bg_type = "gradient"
        if sol_color_wash_enabled:
            st.caption(
                "The base comes from the Year Color Gradient. Each card blends "
                "from that base into a lighter version of the same colour, "
                "with per-card variation."
            )
        else:
            sol_bg_type = st.selectbox(
                "Background Type", ["gradient", "image"], key="sol_bg_type"
            )

        if not sol_color_wash_enabled and sol_bg_type == "image":
            sol_bg_upload = st.file_uploader("Upload Image (Solution Side)", type=["png", "jpg", "jpeg"], key="sol_bg_up")
            if sol_bg_upload:
                try:
                    st.session_state.sol_bg_img = (
                        input_validation.load_uploaded_image(sol_bg_upload)
                    )
                except input_validation.InputValidationError as exc:
                    st.session_state.sol_bg_img = None
                    st.error(str(exc))
            else:
                st.session_state.sol_bg_img = None
                
            st.session_state.sol_bg_scale = st.slider("Image Scale", 0.1, 3.0, 1.0, 0.1, key="sol_scale")
            st.session_state.sol_bg_x = st.slider("X Offset", -1.0, 1.0, 0.0, 0.05, key="sol_x")
            st.session_state.sol_bg_y = st.slider("Y Offset", -1.0, 1.0, 0.0, 0.05, key="sol_y")
        
        st.session_state.sol_border_width = st.slider("Ink Saving Border Thickness", 10, 500, 142, key="sol_bw")

        st.subheader("🔤 Song Text")
        st.session_state.song_year_size = st.slider(
            "Song Year Font Size", 20, 800,
            utils.DEFAULT_SONG_YEAR_SIZE, key="song_year_font_size"
        )
        st.session_state.song_artist_size = st.slider("Song Artist Font Size", 20, 500, 155, key="song_artist_font_size")
        st.session_state.song_title_size = st.slider("Song Title Font Size", 20, 500, 155, key="song_title_font_size")
        st.session_state.card_number_size = st.slider("Card Number Font Size", 10, 200, 70, key="card_number_font_size")

        st.subheader("🔤 Card Set Title")
        st.session_state.sol_title_en = st.toggle("Enable Title", key="sol_t_en")
        if st.session_state.sol_title_en:
            st.session_state.sol_title = st.text_input("Title Text", value="HITSTER", key="sol_t_t")
            sol_title_upload = st.file_uploader(
                "Title Artwork (replaces title text)",
                type=["png", "jpg", "jpeg", "svg"], key="sol_title_up",
                help="SVG artwork keeps its aspect ratio at preview and print resolution.",
            )
            if sol_title_upload:
                try:
                    st.session_state.sol_title_img = (
                        input_validation.load_uploaded_image(sol_title_upload)
                    )
                except input_validation.InputValidationError as exc:
                    st.session_state.sol_title_img = None
                    st.error(str(exc))
            else:
                st.session_state.sol_title_img = None
            st.session_state.sol_title_size = st.slider("Card Set Title Size", 20, 200, 188, key="sol_t_s")
            st.session_state.sol_title_color = st.color_picker("Title Color", value="#FFFFFF", key="sol_t_c")
            st.session_state.sol_title_opacity = st.slider("Title Opacity (%)", 0, 100, 60, key="sol_t_opacity")
            st.session_state.sol_title_bg = st.toggle("Draw Background Box", key="sol_t_bg")

    # Build this session's settings. Kept in st.session_state (per user) and
    # passed explicitly into utils functions, never written to a shared global.
    st.session_state.design_settings = {
        "ink_saving_mode": ink_mode,
        "pdf_print_profile": pdf_print_profile,
        "card_draw_border": border_mode,
        "qr_pages_upside_down": qr_pages_upside_down,
        "card_number_start": int(card_number_start),
        "card_label": card_label,
        "google_font": google_font,
        "card_number_font_weight": card_number_font_weight,
        "card_set_title_font_weight": card_set_title_font_weight,
        "song_artist_font_weight": song_artist_font_weight,
        "song_year_font_weight": song_year_font_weight,
        "song_title_font_weight": song_title_font_weight,
        "color_gradient": st.session_state.get('color_gradient', DEFAULT_COLOR_GRADIENT),

        "qr_bg_type": qr_bg_type,
        "qr_bg_color": qr_bg_color,
        "qr_bg_image": st.session_state.get('qr_bg_img'),
        "qr_bg_scale": st.session_state.get('qr_scale', 1.0),
        "qr_bg_offset_x": st.session_state.get('qr_x', 0.0),
        "qr_bg_offset_y": st.session_state.get('qr_y', 0.0),
        "neon_colors": st.session_state.get('neon_colors', utils.DEFAULT_DESIGN_SETTINGS['neon_colors']),
        "neon_ring_thickness": st.session_state.get('neon_thick', 16),
        "neon_ring_count": st.session_state.get('neon_count', 14),
        "qr_background_mode": qr_bg_mode,
        "qr_module_color": qr_module_color,
        "qr_background_color": st.session_state.get('qr_backplate_color', "#000000"),
        "qr_backplate_padding_cm": qr_border_cm,
        "qr_backplate_radius": st.session_state.get(
            'qr_quiet_radius', 0
        ),
        "qr_size_ratio": qr_size_cm / utils.CARD_PHYSICAL_SIZE_CM,
        "qr_card_number_opacity": st.session_state.get('qr_card_num_opacity', 42),
        "qr_title_enabled": st.session_state.get('qr_t_en', False),
        "qr_title": st.session_state.get('qr_t_t', ""),
        "qr_title_pos": st.session_state.get('qr_t_p', "top"),
        "qr_title_size": st.session_state.get('qr_t_s', 80),
        "qr_title_color": st.session_state.get('qr_title_color', "#FFFFFF"),
        "qr_title_bg": st.session_state.get('qr_t_bg', False),
        "qr_title_image": st.session_state.get('qr_title_img'),
        
        "sol_bg_type": sol_bg_type,
        "sol_bg_image": st.session_state.get('sol_bg_img'),
        "sol_bg_scale": st.session_state.get('sol_scale', 1.0),
        "sol_bg_offset_x": st.session_state.get('sol_x', 0.0),
        "sol_bg_offset_y": st.session_state.get('sol_y', 0.0),
        "sol_color_wash_enabled": sol_color_wash_enabled,
        "sol_border_width": st.session_state.get('sol_bw', 142),
        "song_year_size": st.session_state.get(
            'song_year_font_size', utils.DEFAULT_SONG_YEAR_SIZE
        ),
        "song_artist_size": st.session_state.get('song_artist_font_size', 155),
        "song_title_size": st.session_state.get('song_title_font_size', 155),
        "card_number_size": st.session_state.get('card_number_font_size', 70),
        "sol_title_enabled": st.session_state.get('sol_t_en', False),
        "sol_title": st.session_state.get('sol_t_t', ""),
        "sol_title_size": st.session_state.get('sol_t_s', 188),
        "sol_title_color": st.session_state.get('sol_title_color', "#FFFFFF"),
        "sol_title_opacity": st.session_state.get('sol_t_opacity', 60),
        "sol_title_bg": st.session_state.get('sol_t_bg', False),
        "sol_title_image": st.session_state.get('sol_title_img'),
    }
with st.expander("Disclaimer, Accuracy & Support"):
    st.info("""
    **Why are some years wrong?**
    Metadata providers often list the date a song was added to a digital album (like a 'Greatest Hits' or 'Remaster') rather than the original single release date.
    You can now **manually correct years** in the table below after scraping!
    
    **📱 Mobile User Note:**
    If the download button doesn't respond on your phone, please try a desktop browser. Mobile browsers sometimes struggle with large in-memory PDF streams.
    """)

# --- MAIN PAGE ---
col1, col2 = st.columns([2, 1])
with col1:
    st.title("🎵 Hitster Card Generator")
    st.markdown("Generate custom music game cards from any Spotify playlist or track links.")
with col2:
    pass

st.divider()

# Instructions and Preview Columns
info_col, preview_col = st.columns([1, 1])

with info_col:
    with st.expander("How do I get links?", expanded=True):
        st.write("**Option A — Individual Tracks:**")
        st.write("1. Open **Spotify Desktop**.")
        st.write("2. Go to your playlist.")
        st.write("3. Select songs (**Ctrl+A**).")
        st.write("4. Copy (**Ctrl+C**).")
        st.write("5. Paste below!")
        st.write("")
        st.write("**Option B — Playlist URL:**")
        st.write("1. Right-click your playlist → **Share** → **Copy link**.")
        st.write("2. Paste the playlist URL below!")

with preview_col:
    st.info("**Supported formats:**\n"
            "- Track links: `https://open.spotify.com/track/...`\n"
            "- Playlist links: `https://open.spotify.com/playlist/...`")

# Input Area
st.subheader("Input")

btn_col1, btn_col2 = st.columns(2)
with btn_col1:
    st.button("✨ Load Example Tracks", on_click=set_example_links)
with btn_col2:
    st.button("📋 Load Example Playlist", on_click=set_example_playlist)

user_input = st.text_area(
    "Paste Spotify links here:", 
    height=200, 
    key="user_input",
    max_chars=input_validation.MAX_INPUT_CHARS,
    placeholder="https://open.spotify.com/track/...\nor\nhttps://open.spotify.com/playlist/...",
    on_change=reset_generation,
)

# Detect input type
input_type, input_data = parse_input(user_input)

if input_type == 'playlist':
    st.success("🎶 Spotify **playlist** URL detected!")
elif input_type == 'tracks':
    st.success(f"🎵 {len(input_data)} Spotify **track link(s)** detected.")
elif input_type == 'invalid':
    st.error(input_data)
else:
    st.warning("No valid Spotify links detected yet.")


# --- STEP 1: SCRAPE / FETCH METADATA ---
if st.button("🔍 Fetch Song Metadata", type="primary"):
    if input_type == 'empty':
        st.error("Please paste some valid Spotify links first!")
    elif input_type == 'invalid':
        st.error(input_data)
    else:
        with st.status("Fetching metadata...", expanded=True) as status:
            progress_bar = st.progress(0, text="Starting...")

            if input_type == 'playlist':
                playlist_url = input_data
                spotify_auth = st.session_state.get('spotify_auth')
                if spotify_auth:
                    st.write("Fetching the complete playlist from Spotify...")
                    try:
                        access_token = utils.get_spotify_access_token(
                            spotify_auth,
                            spotify_client_id,
                            spotify_client_secret,
                        )
                        playlist_data = utils.fetch_spotify_playlist_with_token(
                            playlist_url, access_token
                        )
                        songs = utils.parse_playlist_data(playlist_data)
                        if not songs:
                            raise utils.SpotifyAPIError(
                                "Spotify returned no usable track items."
                            )
                        st.session_state.spotify_auth = spotify_auth
                        progress_bar.progress(1.0, text="Done!")
                    except utils.SpotifyAuthenticationError as exc:
                        st.session_state.pop('spotify_auth', None)
                        status.update(
                            label="Spotify authorization expired",
                            state="error",
                        )
                        st.error(str(exc))
                        st.stop()
                    except utils.SpotifyAPIError as exc:
                        status.update(
                            label="Spotify playlist fetch failed",
                            state="error",
                        )
                        st.error(str(exc))
                        st.stop()
                else:
                    st.write("Scraping playlist page for track links...")
                    try:
                        track_links = utils.scrape_playlist_track_links(
                            playlist_url
                        )
                        st.write(
                            f"Found {len(track_links)} tracks. "
                            "Scraping metadata..."
                        )
                        songs = utils.fetch_no_api_data_from_list(
                            track_links,
                            progress_bar,
                        )
                        if not songs:
                            raise utils.SpotifyAPIError(
                                "No track metadata could be fetched from "
                                "Spotify's public pages."
                            )
                        skipped_track_count = len(track_links) - len(songs)
                    except utils.SpotifyAPIError as exc:
                        status.update(
                            label="Public playlist fetch failed",
                            state="error",
                        )
                        st.error(str(exc))
                        st.stop()
                    if skipped_track_count:
                        st.warning(
                            f"{skipped_track_count} track(s) were skipped "
                            "because Spotify returned incomplete metadata."
                        )
            else:
                st.write("Scraping track metadata...")
                try:
                    songs = utils.fetch_no_api_data_from_list(
                        input_data,
                        progress_bar,
                    )
                    if not songs:
                        raise utils.SpotifyAPIError(
                            "No track metadata could be fetched from "
                            "Spotify's public pages."
                        )
                    skipped_track_count = len(input_data) - len(songs)
                except utils.SpotifyAPIError as exc:
                    status.update(
                        label="Track metadata fetch failed",
                        state="error",
                    )
                    st.error(str(exc))
                    st.stop()
                if skipped_track_count:
                    st.warning(
                        f"{skipped_track_count} track(s) were skipped "
                        "because Spotify returned incomplete metadata."
                    )

            st.write("Classifying solo artists and groups...")
            classification = utils.enrich_performer_types(songs)
            if classification['lookup_failed']:
                st.warning(
                    "MusicBrainz could not classify some artists. "
                    "They were left as Unknown."
                )
            status.update(
                label=(
                    f"✅ Fetched and classified {len(songs)} songs!"
                ),
                state="complete",
            )
            progress_bar.empty()

        st.session_state.songs = songs
        st.session_state.pdf_data = None  # reset PDF when songs change

# --- REVIEW AND GENERATE ---
# --- STEP 2: REVIEW & EDIT SONGS TABLE ---
songs = st.session_state.songs
if songs:
    st.divider()
    st.subheader("📝 Review & Edit Songs")
    st.caption("Fix any incorrect years before generating cards. "
               "Songs with unknown years show as empty — fill them in!")

    # Build an editable dataframe
    df = pd.DataFrame([
        {
            "Artist": s['artist'],
            "Song": s['name'],
            "Year": s['year'] if s['year'] is not None else None,
            "Source": s.get('year_source', ''),
            "Performer Type": s.get('performer_type', 'Unknown'),
            "Link": s['link'],
        }
        for s in songs
    ])

    edited_df = st.data_editor(
        df,
        column_config={
            "Artist": st.column_config.TextColumn("Artist", disabled=False),
            "Song": st.column_config.TextColumn("Song", disabled=False),
            "Year": st.column_config.NumberColumn("Year", min_value=1900, max_value=2030, step=1,
                                                   help="Edit this to fix incorrect years!"),
            "Source": st.column_config.TextColumn("Source", disabled=True, 
                                                   help="Where the year came from"),
            "Link": st.column_config.TextColumn("Link", disabled=False),
            "Performer Type": st.column_config.SelectboxColumn(
                "Performer Type",
                options=list(utils.PERFORMER_TYPES),
                required=True,
                help="Automatically classified; choose a different value if needed.",
            ),
        },
        width="stretch",
        num_rows="fixed",
        hide_index=True,
    )

    current_pdf_fingerprint = utils.build_generation_fingerprint(
        edited_df.to_dict(orient='records'),
        st.session_state.get('design_settings', {}),
    )
    if (
        st.session_state.get('pdf_data') is not None
        and st.session_state.get('pdf_fingerprint')
        != current_pdf_fingerprint
    ):
        st.session_state.pdf_data = None
        st.session_state.pop('pdf_fingerprint', None)

    # Count problems
    unknown_count = edited_df['Year'].isna().sum()
    if unknown_count > 0:
        st.warning(f"⚠️ {unknown_count} song(s) have no year. Please fill them in above, "
                   "or they will show as '????' on the cards.")
    unknown_performer_count = (
        edited_df['Performer Type'] == utils.PERFORMER_TYPE_UNKNOWN
    ).sum()
    if unknown_performer_count:
        st.info(
            f"{unknown_performer_count} song(s) have an Unknown performer type."
        )

    # --- CARD PREVIEW ---
    st.divider()
    st.subheader("👀 Card Preview")
    
    # Pick a sample song for preview
    preview_idx = st.selectbox(
        "Preview card for:", 
        range(len(edited_df)),
        format_func=lambda i: f"{edited_df.iloc[i]['Artist']} — {edited_df.iloc[i]['Song']}",
    )
    
    preview_song = edited_df.iloc[preview_idx]
    preview_year = int(preview_song['Year']) if pd.notna(preview_song['Year']) else None
    all_preview_years = [int(y) if pd.notna(y) else None for y in edited_df['Year']]
    valid_preview_years = [y for y in all_preview_years if y is not None]
    if not valid_preview_years:
        valid_preview_years = [2000]

    settings = st.session_state.get('design_settings')
    preview_card_number = settings.get('card_number_start', 1) + int(preview_idx)

    link_str = (
        str(preview_song['Link'])
        if pd.notna(preview_song['Link'])
        else "https://open.spotify.com/"
    )
    preview_render_fingerprint = utils.build_generation_fingerprint(
        {
            'artist': str(preview_song['Artist']),
            'song': str(preview_song['Song']),
            'year': preview_year,
            'link': link_str,
            'all_years': valid_preview_years,
            'card_number': preview_card_number,
            'performer_type': str(preview_song['Performer Type']),
        },
        settings,
    )
    if (
        st.session_state.get('preview_render_fingerprint')
        != preview_render_fingerprint
    ):
        qr_img = utils.create_qr_code(link_str)
        st.session_state.preview_qr_card = (
            utils.create_qr_with_neon_rings_in_memory(
                qr_img,
                seed=utils.stable_seed(link_str),
                settings_override=settings,
                card_number=preview_card_number,
            )
        )
        st.session_state.preview_solution_card = (
            utils.create_solution_side_in_memory(
                str(preview_song['Song']),
                str(preview_song['Artist']),
                preview_year,
                valid_preview_years,
                settings_override=settings,
                card_number=preview_card_number,
                performer_type=str(preview_song['Performer Type']),
            )
        )
        st.session_state.preview_render_fingerprint = (
            preview_render_fingerprint
        )

    st.markdown(
        """
        <style>
        .st-key-card-preview-images [data-testid="stImage"] img {
            border-radius: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="card-preview-images"):
        pcol1, pcol2 = st.columns(2)
        with pcol1:
            st.caption("QR Side")
            st.image(st.session_state.preview_qr_card, width="stretch")
        with pcol2:
            st.caption("Solution Side")
            st.image(
                st.session_state.preview_solution_card, width="stretch"
            )

    # --- STEP 3: GENERATE PDF ---
    st.divider()
    
    if st.button("🎴 Create My PDF", type="primary"):
        # Apply edited years back to songs
        for i, song in enumerate(songs):
            new_artist = edited_df.iloc[i]['Artist']
            new_song_name = edited_df.iloc[i]['Song']
            new_year = edited_df.iloc[i]['Year']
            new_link = edited_df.iloc[i]['Link']
            new_performer_type = edited_df.iloc[i]['Performer Type']
            
            if pd.notna(new_artist):
                song['artist'] = str(new_artist)
            if pd.notna(new_song_name):
                song['name'] = str(new_song_name)
            if pd.notna(new_link):
                song['link'] = str(new_link)
            if (
                pd.notna(new_performer_type)
                and new_performer_type in utils.PERFORMER_TYPES
            ):
                song['performer_type'] = str(new_performer_type)

            previous_year = song.get('year')
            if pd.notna(new_year):
                song['year'] = int(new_year)
                if song['year'] != previous_year:
                    song['year_source'] = 'Manual'
            else:
                song['year'] = None
                if previous_year is not None:
                    song['year_source'] = 'Manual'

        generation_failed = False
        with st.status("PDF generation queued...", expanded=True) as status:
            progress_bar = st.progress(0, text="Starting PDF generation...")

            def update_queue_position(position):
                status.update(label=f"⏳ PDF generation queued: position {position}")

            try:
                with utils.pdf_generation_slot(
                    on_wait=update_queue_position
                ):
                    status.update(label="Generating cards...")
                    pdf_data = utils.create_pdf_in_memory(
                        songs,
                        progress_bar,
                        settings_override=st.session_state.get(
                            'design_settings'
                        ),
                    )
            except RuntimeError as exc:
                generation_failed = True
                status.update(label="PDF generation failed", state="error")
                st.error(str(exc))
            else:
                status.update(label="✅ All Cards Generated!", state="complete")
            finally:
                progress_bar.empty()

        if not generation_failed:
            st.session_state.pdf_data = pdf_data

    # Show download button if PDF exists
    if st.session_state.pdf_data:
        st.download_button(
            label="💾 Download Printable PDF",
            data=st.session_state.pdf_data,
            file_name="my_hitster_cards.pdf",
            mime="application/pdf",
            width="stretch"
        )
