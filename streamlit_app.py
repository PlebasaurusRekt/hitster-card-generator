import streamlit as st
import os
from PIL import Image
import pandas as pd
import matplotlib.colors as mcolors
import src.utils as utils

if getattr(utils, 'db', None) is None or not utils.db:
    utils.db = {
        "fonts_dict": {
            'year': os.path.join("fonts", "Montserrat-Bold.ttf"),
            'artist': os.path.join("fonts", "Montserrat-SemiBold.ttf"),
            'song': os.path.join("fonts", "Montserrat-MediumItalic.ttf")
        },
        "color_gradient": [
            "#7030A0", "#E31C79", "#FF6B9D", "#FFA500", 
            "#FFD700", "#87CEEB", "#4169E1"
        ],
        "card_size": 2000,
        "neon_colors": [(255, 0, 100), (0, 200, 255), (0, 255, 120), (255, 255, 0)]
    }
db = utils.db

OUTPUT_DIR = "output"
LINKS_FILE = "links.txt"

# --- STATE INITIALIZATION ---
if "songs" not in st.session_state:
    st.session_state.songs = []
if "pdf_data" not in st.session_state:
    st.session_state.pdf_data = None

def reset_generation():
    st.session_state.pdf_data = None

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
    if not text or not text.strip():
        return 'empty', None
    
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return 'empty', None

    if len(lines) == 1 and '/playlist/' in lines[0]:
        return 'playlist', lines[0]
    
    track_lines = [l for l in lines if '/track/' in l]
    if track_lines:
        return 'tracks', track_lines
    
    return 'empty', None

import uuid

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
            alpha = int(color[7:9], 16) if len(color) == 9 else 255
            
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

with st.sidebar:
    st.header("⚙️ Settings")
    st.caption("Customize your print layout")
    
    tabs = st.tabs(["Global", "QR Side (Front)", "Solution Side (Back)"])
    
    with tabs[0]:
        st.subheader("📄 Print & Layout")
        ink_mode = st.toggle("Ink Saving Mode", value=st.session_state.get('ink_mode', False), 
                            help="Use white background and black text to save ink.")
        border_mode = st.toggle("Draw Cutting Borders", value=st.session_state.get('border_mode', False),
                               help="Draw a line around each card for easier cutting.")
        
        font_choice = st.selectbox("Font Selection", ["Montserrat", "Oswald", "Roboto", "Dancing Script", "Pacifico", "Custom..."])
        if font_choice == "Custom...":
            google_font = st.text_input("Custom Google Font Name", value=st.session_state.get('google_font', "Montserrat"),
                                        help="Type any font name from Google Fonts.")
            st.markdown("[🔍 Browse Google Fonts here](https://fonts.google.com/)", unsafe_allow_html=True)
        else:
            google_font = font_choice
            
        st.divider()

        with st.expander("🔑 Spotify API Credentials (optional)"):
            st.caption("Only needed when pasting a playlist URL. "
                       "Without credentials, playlist URLs are limited to ~100 tracks. "
                       "Pasting individual track links works without limits.")
            spotify_client_id = st.text_input("Client ID", type="password")
            spotify_client_secret = st.text_input("Client Secret", type="password")
        
        st.divider()
        st.header("Feedback")
        st.write("Found a bug or have a feature idea? Let me know on GitHub!")
        st.link_button(
            label="Open GitHub Issues", 
            url="https://github.com/WhiteShunpo/hitster-cards-generator/issues",
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
        st.session_state.google_font = google_font

    with tabs[1]:
        st.subheader("🖼️ Background")
        qr_bg_type = st.selectbox("Background Type", ["neon_rings", "solid", "image"], key="qr_bg_type")
        qr_bg_color = st.color_picker("Background Color", value="#000000", key="qr_bg_color")
        
        if qr_bg_type == "image":
            qr_bg_upload = st.file_uploader("Upload Image (QR Side)", type=["png", "jpg", "jpeg"], key="qr_bg_up")
            if qr_bg_upload:
                st.session_state.qr_bg_img = Image.open(qr_bg_upload)
            else:
                st.session_state.qr_bg_img = None
            
            st.session_state.qr_bg_scale = st.slider("Image Scale", 0.1, 3.0, 1.0, 0.1, key="qr_scale")
            st.session_state.qr_bg_x = st.slider("X Offset", -1.0, 1.0, 0.0, 0.05, key="qr_x")
            st.session_state.qr_bg_y = st.slider("Y Offset", -1.0, 1.0, 0.0, 0.05, key="qr_y")
        
        if qr_bg_type == "neon_rings":
            st.session_state.neon_ring_thickness = st.slider("Ring Thickness", 1, 50, 12, key="neon_thick")
            st.session_state.neon_ring_count = st.slider("Ring Count", 1, 20, 8, key="neon_count")
            
            neon_hex_list = dynamic_color_list("neon", "Neon Ring Colors", ["#FF0064", "#00C8FF", "#00FF78", "#FFFF00"])
            try:
                st.session_state.neon_colors = [tuple(int(val * 255) for val in mcolors.to_rgba(c)) for c in neon_hex_list]
            except:
                st.session_state.neon_colors = [(255, 0, 100), (0, 200, 255), (0, 255, 120), (255, 255, 0)]
                
        st.subheader("📱 QR Settings")
        qr_bg_mode = st.selectbox("QR Background Mode", ["solid", "transparent"], key="qr_bg_mode")
        qr_module_color = st.color_picker("QR Module Color", value="#000000", key="qr_mod_c")
        if qr_bg_mode == "solid":
            st.session_state.qr_backplate_color = st.color_picker("QR Backplate Color", value="#FFFFFF", key="qr_bp_c")
            st.session_state.qr_padding = st.slider("Backplate Padding", 0, 200, 40, key="qr_pad")
            st.session_state.qr_radius = st.slider("Backplate Corner Radius", 0, 100, 20, key="qr_rad")
        st.session_state.qr_size_ratio = st.slider("QR Size Ratio (%)", 10, 80, 45, key="qr_size") / 100.0
        
        st.subheader("🔤 Title")
        st.session_state.qr_title_en = st.toggle("Enable Title", key="qr_t_en")
        if st.session_state.qr_title_en:
            st.session_state.qr_title = st.text_input("Title Text", value="HITSTER", key="qr_t_t")
            pos_options = ["top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right", "center_above_qr", "center_below_qr"]
            st.session_state.qr_title_pos = st.selectbox("Position", pos_options, key="qr_t_p")
            st.session_state.qr_title_size = st.slider("Font Size", 20, 200, 80, key="qr_t_s")
            st.session_state.qr_title_color = st.color_picker("Title Color", value="#FFFFFF", key="qr_t_c")
            st.session_state.qr_title_bg = st.toggle("Draw Background Box", key="qr_t_bg")

    with tabs[2]:
        st.subheader("🎨 Color Gradient")
        default_grad = db.get('color_gradient', ["#7030A0", "#E31C79", "#FF6B9D", "#FFA500", "#FFD700", "#87CEEB", "#4169E1"])
        st.session_state.color_gradient = dynamic_color_list(
            "gradient", 
            "Year Color Gradient", 
            default_grad, 
            help_text="Colors map to the oldest to newest years."
        )

        st.subheader("🖼️ Background")
        sol_bg_type = st.selectbox("Background Type", ["gradient", "image"], key="sol_bg_type")
        if sol_bg_type == "image":
            sol_bg_upload = st.file_uploader("Upload Image (Solution Side)", type=["png", "jpg", "jpeg"], key="sol_bg_up")
            if sol_bg_upload:
                st.session_state.sol_bg_img = Image.open(sol_bg_upload)
            else:
                st.session_state.sol_bg_img = None
                
            st.session_state.sol_bg_scale = st.slider("Image Scale", 0.1, 3.0, 1.0, 0.1, key="sol_scale")
            st.session_state.sol_bg_x = st.slider("X Offset", -1.0, 1.0, 0.0, 0.05, key="sol_x")
            st.session_state.sol_bg_y = st.slider("Y Offset", -1.0, 1.0, 0.0, 0.05, key="sol_y")
        
        st.session_state.sol_border_width = st.slider("Ink Saving Border Thickness", 10, 500, 100, key="sol_bw")

        st.subheader("🔤 Title")
        st.session_state.sol_title_en = st.toggle("Enable Title", key="sol_t_en")
        if st.session_state.sol_title_en:
            st.session_state.sol_title = st.text_input("Title Text", value="HITSTER", key="sol_t_t")
            pos_options_sol = [
                "in_border_bottom_right", "in_border_bottom_left", "in_border_top_right", "in_border_top_left",
                "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"
            ]
            st.session_state.sol_title_pos = st.selectbox("Position", pos_options_sol, key="sol_t_p")
            st.session_state.sol_title_size = st.slider("Font Size", 20, 200, 80, key="sol_t_s")
            st.session_state.sol_title_color = st.color_picker("Title Color", value="#000000", key="sol_t_c")
            st.session_state.sol_title_bg = st.toggle("Draw Background Box", key="sol_t_bg")

    # Update db with all settings
    db.update({
        "ink_saving_mode": ink_mode,
        "card_draw_border": border_mode,
        "card_background_color": "#FFFFFF" if ink_mode else qr_bg_color,
        "google_font": google_font,
        "color_gradient": st.session_state.get('color_gradient', db.get('color_gradient')),
        
        "qr_bg_type": qr_bg_type,
        "qr_bg_color": qr_bg_color,
        "qr_bg_image": st.session_state.get('qr_bg_img'),
        "qr_bg_scale": st.session_state.get('qr_scale', 1.0),
        "qr_bg_offset_x": st.session_state.get('qr_x', 0.0),
        "qr_bg_offset_y": st.session_state.get('qr_y', 0.0),
        "neon_colors": st.session_state.get('neon_colors', db.get('neon_colors', utils.DEFAULT_DESIGN_SETTINGS['neon_colors'])),
        "neon_ring_thickness": st.session_state.get('neon_thick', 12),
        "neon_ring_count": st.session_state.get('neon_count', 8),
        "qr_background_mode": qr_bg_mode,
        "qr_module_color": qr_module_color,
        "qr_background_color": st.session_state.get('qr_backplate_color', "#FFFFFF"),
        "qr_backplate_padding": st.session_state.get('qr_pad', 40),
        "qr_backplate_radius": st.session_state.get('qr_rad', 20),
        "qr_size_ratio": st.session_state.get('qr_size', 45) / 100.0 if 'qr_size' in st.session_state else 0.45,
        "qr_title_enabled": st.session_state.get('qr_t_en', False),
        "qr_title": st.session_state.get('qr_t_t', ""),
        "qr_title_pos": st.session_state.get('qr_t_p', "top"),
        "qr_title_size": st.session_state.get('qr_t_s', 80),
        "qr_title_color": st.session_state.get('qr_title_color', "#FFFFFF"),
        "qr_title_bg": st.session_state.get('qr_t_bg', False),
        
        "sol_bg_type": sol_bg_type,
        "sol_bg_image": st.session_state.get('sol_bg_img'),
        "sol_bg_scale": st.session_state.get('sol_scale', 1.0),
        "sol_bg_offset_x": st.session_state.get('sol_x', 0.0),
        "sol_bg_offset_y": st.session_state.get('sol_y', 0.0),
        "sol_border_width": st.session_state.get('sol_bw', 100),
        "sol_title_enabled": st.session_state.get('sol_t_en', False),
        "sol_title": st.session_state.get('sol_t_t', ""),
        "sol_title_pos": st.session_state.get('sol_t_p', "in_border_bottom_right"),
        "sol_title_size": st.session_state.get('sol_t_s', 80),
        "sol_title_color": st.session_state.get('sol_title_color', "#000000"),
        "sol_title_bg": st.session_state.get('sol_t_bg', False),
    })
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
    placeholder="https://open.spotify.com/track/...\nor\nhttps://open.spotify.com/playlist/...",
    on_change=reset_generation,
)

# Detect input type
input_type, input_data = parse_input(user_input)

if input_type == 'playlist':
    st.success("🎶 Spotify **playlist** URL detected!")
elif input_type == 'tracks':
    st.success(f"🎵 {len(input_data)} Spotify **track link(s)** detected.")
else:
    st.warning("No valid Spotify links detected yet.")


# --- STEP 1: SCRAPE / FETCH METADATA ---
if st.button("🔍 Fetch Song Metadata", type="primary"):
    if input_type == 'empty':
        st.error("Please paste some valid Spotify links first!")
    else:
        with st.status("Fetching metadata...", expanded=True) as status:
            progress_bar = st.progress(0, text="Starting...")

            if input_type == 'playlist':
                playlist_url = input_data
                if spotify_client_id and spotify_client_secret:
                    st.write("Using Spotify API to fetch playlist...")
                    playlist_data = utils.fetch_spotify_playlist(
                        playlist_url, spotify_client_id, spotify_client_secret
                    )
                    songs = utils.parse_playlist_data(playlist_data)
                    progress_bar.progress(1.0, text="Done!")
                else:
                    st.write("Scraping playlist page for track links (no API key)...")
                    track_links = utils.scrape_playlist_track_links(playlist_url)
                    if not track_links:
                        st.error("Could not extract tracks from the playlist page. "
                                 "Try adding Spotify API credentials in the sidebar, "
                                 "or paste individual track links instead.")
                        st.stop()
                    st.write(f"Found {len(track_links)} tracks. Scraping metadata...")
                    songs = utils.fetch_no_api_data_from_list(track_links, progress_bar)
            else:
                st.write("Scraping track metadata...")
                songs = utils.fetch_no_api_data_from_list(input_data, progress_bar)

            status.update(label=f"✅ Fetched {len(songs)} songs!", state="complete")
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
    import pandas as pd
    df = pd.DataFrame([
        {
            "Artist": s['artist'],
            "Song": s['name'],
            "Year": s['year'] if s['year'] is not None else None,
            "Source": s.get('year_source', ''),
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
        },
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
    )

    # Count problems
    unknown_count = edited_df['Year'].isna().sum()
    if unknown_count > 0:
        st.warning(f"⚠️ {unknown_count} song(s) have no year. Please fill them in above, "
                   "or they will show as '????' on the cards.")

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

    pcol1, pcol2 = st.columns(2)
    with pcol1:
        st.caption("QR Side")
        link_str = str(preview_song['Link']) if pd.notna(preview_song['Link']) else "https://open.spotify.com/"
        qr_img = utils.create_qr_code(link_str)
        qr_card = utils.create_qr_with_neon_rings_in_memory(qr_img, seed=hash(link_str))
        st.image(qr_card, use_container_width=True)
    with pcol2:
        st.caption("Solution Side")
        sol_card = utils.create_solution_side_in_memory(
            str(preview_song['Song']), str(preview_song['Artist']), 
            preview_year, valid_preview_years
        )
        st.image(sol_card, use_container_width=True)

    # --- STEP 3: GENERATE PDF ---
    st.divider()
    
    if st.button("🎴 Create My PDF", type="primary"):
        # Apply edited years back to songs
        for i, song in enumerate(songs):
            new_artist = edited_df.iloc[i]['Artist']
            new_song_name = edited_df.iloc[i]['Song']
            new_year = edited_df.iloc[i]['Year']
            new_link = edited_df.iloc[i]['Link']
            
            if pd.notna(new_artist):
                song['artist'] = str(new_artist)
            if pd.notna(new_song_name):
                song['name'] = str(new_song_name)
            if pd.notna(new_link):
                song['link'] = str(new_link)

            if pd.notna(new_year):
                song['year'] = int(new_year)
                if int(new_year) != (songs[i].get('year') or 0):
                    song['year_source'] = 'Manual'
            else:
                song['year'] = None

        with st.status("Generating cards...", expanded=True) as status:
            progress_bar = st.progress(0, text="Starting PDF generation...")
            pdf_data = utils.create_pdf_in_memory(songs, progress_bar)
            status.update(label="✅ All Cards Generated!", state="complete")
            progress_bar.empty()

        st.session_state.pdf_data = pdf_data
        st.balloons()

    # Show download button if PDF exists
    if st.session_state.pdf_data:
        st.download_button(
            label="💾 Download Printable PDF",
            data=st.session_state.pdf_data,
            file_name="my_hitster_cards.pdf",
            mime="application/pdf",
            use_container_width=True
        )