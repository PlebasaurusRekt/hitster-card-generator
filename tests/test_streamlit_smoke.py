import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


class StreamlitSmokeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
