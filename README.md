# Hitster Card Generator 🎵

Generate printable, duplex-ready Hitster-style cards from Spotify tracks and playlists.

[Open the live Streamlit app](https://hitster-card-generator2.streamlit.app/)

The app creates 6.5 × 6.5 cm cards on A4 sheets (3 × 4 per page), with QR-code fronts and year-based solution backs. It also provides a Photoshop A4-fit profile for printers where **Scale to Fit Media** reduces A4 to 97.27%. Preview cards stay at 2000 × 2000 pixels; PDF cards are rendered at 720 DPI.

## Features

- Paste individual Spotify track links or one public playlist link.
- Import up to 500 tracks per run.
- Optionally connect Spotify to read complete owned or collaborative playlists.
- Correct artists, titles, years, and links before generation.
- Automatically classify and correct tracks as Solo, Group, or Unknown.
- Customize backgrounds, QR colors, neon rings, fonts, titles, labels, numbering, and ink-saving borders.
- Generate a mirrored, duplex-ready PDF.
- Use a four-module QR quiet zone with integer-aligned QR modules.
- Upload custom PNG or JPEG background images up to 10 MB; 2000 × 2000 pixels is recommended.
- Use PNG, JPEG, or SVG artwork for card set titles; SVG title artwork preserves its aspect ratio in previews and PDFs.

## Web app

Use [https://hitster-card-generator2.streamlit.app/](https://hitster-card-generator2.streamlit.app/).

1. Paste Spotify track links or one playlist share URL.
2. Select **Fetch Song Metadata**.
3. Review and correct the song table, including its performer classifications.
4. Customize the cards in the sidebar.
5. Select **Create My PDF**, then download it.

Public pages work without API credentials. If Spotify does not expose every playlist item publicly, connect Spotify or paste the individual track links.

## Solo and group classification

Every imported track is assigned an editable **Performer Type**. Collaborations
are marked **Group** when Spotify lists multiple artists or when the original
title or artist credit contains `feat.`, `ft.`, or `featuring`. Remaining
single-artist credits are matched through an exact Spotify artist URL in
MusicBrainz:

- MusicBrainz people become **Solo**.
- Groups, orchestras, and choirs become **Group**.
- Missing, unsupported, or conflicting data remains **Unknown**.

## Local setup

The solution side shows one-person, three-person, or question-mark pictograms.
MusicBrainz failures never block card generation, and every result can be
changed in the review table before creating the PDF.

```bash
git clone https://github.com/PlebasaurusRekt/hitster-card-generator.git
cd hitster-card-generator
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the web app:

```bash
streamlit run streamlit_app.py
```

Run the CLI using a root-level `links.txt` file:

```bash
python src/hitster_card_creator.py
python src/hitster_card_creator.py --ink-save-mode --card-draw-border
python src/hitster_card_creator.py --card-label "Game Night" --start-number 25
python src/hitster_card_creator.py --qr-bg-mode solid --game-title "Hits"
```

Alternatively, set a public playlist in `.env` and run the same CLI command:

```dotenv
PLAYLIST_URL=https://open.spotify.com/playlist/3cEYpjA9oz9GiPac4AsH4n
```

The CLI intentionally uses public Spotify pages. Spotify's client-credentials flow cannot read playlist items, so client ID/secret values are not an API mode for the CLI.

## Spotify user authorization

Each web-app user connects with their own Spotify developer application. The
deployment does not need a hardcoded Client ID or Client Secret.

1. Create or open an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Open **Connect Spotify** in the Hitster sidebar.
3. Copy the displayed **Redirect URI** into the Spotify app's redirect URI allowlist.
4. Enter that app's **Client ID** and **Client Secret**, then select **Prepare Spotify Login**.
5. Select **Authorize with Spotify**. Spotify opens as a top-level page and returns after approval.

The Redirect URI must match exactly, including the scheme and trailing slash.
For local HTTP callbacks, use an explicit loopback address such as
`http://127.0.0.1:8501/`; do not use `localhost`.

Credentials are kept in the user's Streamlit session for login and token
refresh. The Client Secret is not written to the OAuth URL or signed callback
state. If Spotify returns in a fresh Streamlit session, the callback page asks
for the same Client Secret once so it can verify the signed state and complete
the token exchange.

## CLI configuration

| Setting | CLI flag | `.env` variable | Default |
|---|---|---|---|
| Public playlist | — | `PLAYLIST_URL` | *(none)* |
| Ink saving | `--ink-save-mode` | `INK_SAVING_MODE=true` | `false` |
| Cutting borders | `--card-draw-border` | `CARD_DRAW_BORDER=true` | `false` |
| Card label | `--card-label "text"` | `CARD_LABEL=text` | *(none)* |
| Starting number | `--start-number 1` | `CARD_START_NUMBER=1` | `1` |
| QR background | `--qr-bg-mode` | `QR_BG_MODE=solid` | `solid` |
| QR module color | `--qr-module-color` | `QR_MODULE_COLOR=#FFFFFF` | `#FFFFFF` |
| Background type | `--bg-type` | `BG_TYPE=neon_rings` | `neon_rings` |
| Game title | `--game-title` | `GAME_TITLE=MyGame` | *(none)* |
| PDF print profile | `--pdf-print-profile photoshop_a4_fit` | `PDF_PRINT_PROFILE=photoshop_a4_fit` | `a4` |

The CLI caches editable metadata in `output/hitster_cards/songs.json`. Use `--fetch` to refresh it. Generated card images from older runs are cleared before rebuilding so stale cards cannot enter the PDF.

## Output and printing

| File | Description |
|---|---|
| `output/hitster_cards/card_NNN_qr.png` | QR side |
| `output/hitster_cards/card_NNN_solution.png` | Solution side |
| `output/hitster_cards/songs.json` | Editable song metadata |
| `output/hitster_cards.pdf` | Duplex-ready PDF using the selected print profile |

Print at **Actual size**, double-sided, flipping on the long edge.

If Photoshop is set to A4 and **Scale to Fit Media** reports 97.27%, choose **Photoshop A4 fit (97.27%)** in the app (or `photoshop_a4_fit` in the CLI). It preserves the 6.5 cm cards and internal gaps while using a 20.427 × 28.889 cm PDF canvas, so Photoshop prints it at 100% with that option enabled.

## Troubleshooting

| Problem | Fix |
|---|---|
| Spotify reports a redirect mismatch | Copy the displayed URI exactly, including the scheme and trailing slash. |
| Spotify shows a frame/X-Frame-Options error | Open the app directly and use its **Authorize with Spotify** link; Spotify must open as a top-level page/new tab. |
| Playlist fetch returns 401 | Reopen **Connect Spotify**, re-enter your credentials, and authorize again. |
| Public playlist yields no tracks | Make it public, connect Spotify, or paste individual track links. |
| QR codes do not scan | Use the solid QR background, print at Actual size, and avoid low-quality print settings. |
| Mobile download is unresponsive | Generate/download from a desktop browser. |

## Development

Runtime dependencies are exactly pinned in `requirements.txt`. Optional development tooling is in `requirements-dev.txt`.

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
flake8 src streamlit_app.py tests
```

## Contributing and license

Issues and pull requests are welcome at [PlebasaurusRekt/hitster-card-generator](https://github.com/PlebasaurusRekt/hitster-card-generator).

MIT License — see [LICENSE](LICENSE).

Inspired by the original Hitster game. Montserrat is by Julieta Ulanovsky.
