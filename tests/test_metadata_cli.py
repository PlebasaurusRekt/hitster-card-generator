import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import src.utils as utils
from src import hitster_card_creator as cli


def track_url(last_digit):
    return (
        "https://open.spotify.com/track/"
        f"000000000000000000000{last_digit}"
    )


class MetadataTests(unittest.TestCase):
    def test_concurrent_fetch_preserves_input_order_and_reports_errors(self):
        urls = [track_url(index) for index in range(4)]

        def fake_fetch(url):
            index = int(url[-1])
            time.sleep((3 - index) * 0.002)
            if index == 2:
                raise ValueError("bad metadata")
            return {
                "name": str(index),
                "original_name": str(index),
                "year": 2000,
                "year_source": "test",
                "artist": "Artist",
                "link": url,
            }

        errors = []
        with patch(
            "src.utils._fetch_public_track_metadata",
            side_effect=fake_fetch,
        ):
            songs = utils.fetch_no_api_data_from_list(
                urls, errors_out=errors
            )
        self.assertEqual(
            [song["name"] for song in songs], ["0", "1", "3"]
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["url"], urls[2])

    def test_year_result_cache_avoids_duplicate_provider_calls(self):
        utils.get_year_and_source.cache_clear()
        with patch(
            "src.utils.get_year_from_itunes", return_value=1999
        ) as itunes:
            first = utils.get_year_and_source("Song", "Artist", None)
            second = utils.get_year_and_source("Song", "Artist", None)
        self.assertEqual(first, (1999, "iTunes"))
        self.assertEqual(first, second)
        itunes.assert_called_once()


class CliTests(unittest.TestCase):
    def test_cli_playlist_uses_public_scraper_and_clears_stale_cards(self):
        song = {
            "name": "Song",
            "artist": "Artist",
            "year": 1999,
            "link": track_url(0),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            cards = output / "hitster_cards"
            cards.mkdir(parents=True)
            stale_qr = cards / "card_999_qr.png"
            stale_solution = cards / "card_999_solution.png"
            stale_qr.write_bytes(b"old")
            stale_solution.write_bytes(b"old")

            settings = {
                "card_number_start": 1,
                "card_size": 20,
                "google_font": "",
            }
            with (
                patch.object(cli, "OUTPUT_DIR", str(output)),
                patch.object(cli, "LINKS_FILE", str(root / "missing.txt")),
                patch.object(
                    cli.utils,
                    "scrape_playlist_track_links",
                    return_value=[track_url(0)],
                ) as scrape,
                patch.object(
                    cli.utils,
                    "fetch_no_api_data_from_list",
                    return_value=[song],
                ),
                patch.object(
                    cli.utils, "create_qr_code", return_value=object()
                ),
                patch.object(
                    cli.utils, "create_qr_with_neon_rings"
                ) as qr_render,
                patch.object(cli.utils, "create_solution_side") as sol_render,
                patch.object(
                    cli.utils,
                    "create_cards_pdf",
                    return_value=str(output / "hitster_cards.pdf"),
                ) as create_pdf,
            ):
                result = cli.generate_hitster_cards(
                    settings,
                    playlist_url=(
                        "https://open.spotify.com/playlist/"
                        "ZYXWVUTSRQPONMLKJIHGFE"
                    ),
                )

            scrape.assert_called_once()
            self.assertFalse(stale_qr.exists())
            self.assertFalse(stale_solution.exists())
            self.assertTrue((cards / "songs.json").exists())
            self.assertEqual(
                json.loads((cards / "songs.json").read_text())[0]["name"],
                "Song",
            )
            self.assertEqual(
                qr_render.call_args.kwargs["settings_override"], settings
            )
            self.assertEqual(
                sol_render.call_args.kwargs["settings_override"], settings
            )
            create_pdf.assert_called_once()
            self.assertEqual(
                result, str(output / "hitster_cards.pdf")
            )


if __name__ == "__main__":
    unittest.main()
