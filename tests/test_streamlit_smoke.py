import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import src.utils as utils


class StreamlitSmokeTests(unittest.TestCase):
    def test_app_reloads_stale_utils_module(self):
        app_path = Path.cwd() / "streamlit_app.py"
        with patch.object(utils, "UTILS_API_VERSION", 0):
            app = AppTest.from_file(str(app_path), default_timeout=30)
            app.run()
            self.assertEqual(utils.UTILS_API_VERSION, 4)

        self.assertEqual(list(app.exception), [])

    def test_app_starts_without_oauth_secrets(self):
        app_path = Path.cwd() / "streamlit_app.py"
        app = AppTest.from_file(str(app_path), default_timeout=30)
        app.run()
        self.assertEqual(list(app.exception), [])
        self.assertTrue(
            any("Hitster Card Generator" in title.value for title in app.title)
        )
        input_labels = {widget.label for widget in app.text_input}
        self.assertIn("Client ID", input_labels)
        self.assertIn("Client Secret", input_labels)
        self.assertIn("Redirect URI", input_labels)

    def test_public_scraper_empty_result_is_an_error(self):
        app_path = Path.cwd() / "streamlit_app.py"
        with patch(
            "src.utils.fetch_no_api_data_from_list",
            return_value=[],
        ):
            app = AppTest.from_file(str(app_path), default_timeout=30)
            app.run()
            app.text_area[0].set_value(
                "https://open.spotify.com/track/"
                "0000000000000000000000"
            ).run()
            fetch_button = next(
                button
                for button in app.button
                if button.label == "🔍 Fetch Song Metadata"
            )
            fetch_button.click().run()

        self.assertEqual(list(app.exception), [])
        self.assertTrue(
            any(
                "No track metadata could be fetched" in error.value
                for error in app.error
            )
        )
        self.assertEqual(app.status[0].label, "Track metadata fetch failed")
        self.assertEqual(app.status[0].state, "error")


if __name__ == "__main__":
    unittest.main()
