import io
import unittest
from unittest.mock import patch

from PIL import Image

from src.input_validation import (
    MAX_INPUT_CHARS,
    MAX_TRACK_LINKS,
    MAX_UPLOAD_BYTES,
    InputValidationError,
    canonicalize_spotify_url,
    classify_spotify_input,
    load_uploaded_image,
)


TRACK_ID = "0123456789ABCDEFGHIJKL"
PLAYLIST_ID = "ZYXWVUTSRQPONMLKJIHGFE"
ARTIST_ID = "1111111111111111111111"


class Upload:
    def __init__(self, data):
        self._data = data

    def getvalue(self):
        return self._data


class SpotifyInputValidationTests(unittest.TestCase):
    def test_canonicalizes_supported_track_and_international_playlist(self):
        self.assertEqual(
            canonicalize_spotify_url(
                f"https://open.spotify.com/track/{TRACK_ID}?si=abc#fragment"
            ),
            f"https://open.spotify.com/track/{TRACK_ID}",
        )
        self.assertEqual(
            canonicalize_spotify_url(
                f"https://open.spotify.com/intl-nl/playlist/{PLAYLIST_ID}"
            ),
            f"https://open.spotify.com/playlist/{PLAYLIST_ID}",
        )
        self.assertEqual(
            canonicalize_spotify_url(
                f"https://open.spotify.com/artist/{ARTIST_ID}?si=abc",
                expected_kind="artist",
            ),
            f"https://open.spotify.com/artist/{ARTIST_ID}",
        )

    def test_rejects_ssrf_and_ambiguous_urls(self):
        invalid_urls = [
            f"http://open.spotify.com/track/{TRACK_ID}",
            f"https://evil.example/track/{TRACK_ID}",
            f"https://open.spotify.com.evil.example/track/{TRACK_ID}",
            f"https://open.spotify.com@evil.example/track/{TRACK_ID}",
            f"https://open.spotify.com:444/track/{TRACK_ID}",
            "https://open.spotify.com/track/not-an-id",
            f"https://open.spotify.com/album/{TRACK_ID}",
        ]
        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(InputValidationError):
                    canonicalize_spotify_url(url)

    def test_classifies_playlist_and_ordered_tracks(self):
        kind, playlist = classify_spotify_input(
            f"https://open.spotify.com/playlist/{PLAYLIST_ID}"
        )
        self.assertEqual(kind, "playlist")
        self.assertEqual(
            playlist,
            f"https://open.spotify.com/playlist/{PLAYLIST_ID}",
        )

        second_id = "1123456789ABCDEFGHIJKL"
        kind, tracks = classify_spotify_input(
            f"https://open.spotify.com/track/{TRACK_ID}\n"
            f"https://open.spotify.com/track/{second_id}?si=1"
        )
        self.assertEqual(kind, "tracks")
        self.assertEqual(
            tracks,
            [
                f"https://open.spotify.com/track/{TRACK_ID}",
                f"https://open.spotify.com/track/{second_id}",
            ],
        )

    def test_enforces_input_and_track_limits(self):
        with self.assertRaises(InputValidationError):
            classify_spotify_input("x" * (MAX_INPUT_CHARS + 1))
        links = "\n".join(
            f"https://open.spotify.com/track/{TRACK_ID}"
            for _ in range(MAX_TRACK_LINKS + 1)
        )
        with self.assertRaises(InputValidationError):
            classify_spotify_input(links)


class UploadValidationTests(unittest.TestCase):
    @staticmethod
    def image_upload(size=(20, 20), image_format="PNG"):
        buffer = io.BytesIO()
        Image.new("RGB", size, "blue").save(buffer, format=image_format)
        return Upload(buffer.getvalue())

    def test_loads_and_detaches_safe_image(self):
        image = load_uploaded_image(self.image_upload())
        self.assertEqual(image.size, (20, 20))
        image.getpixel((0, 0))

    def test_rasterizes_svg_at_a_bounded_size_with_its_viewbox_ratio(self):
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 1">
            <rect width="4" height="1" fill="#00ff00" />
        </svg>'''
        with patch(
            "src.input_validation.rasterize_svg_image",
            return_value=Image.new("RGBA", (2000, 500), "#00ff00"),
        ) as rasterize:
            image = load_uploaded_image(Upload(svg))

        self.assertEqual(image.size, (2000, 500))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((1000, 250)), (0, 255, 0, 255))
        self.assertEqual(image.info["svg_bytes"], svg)
        rasterize.assert_called_once_with(svg, 2000, 500)

    def test_rejects_svg_without_a_viewport(self):
        with self.assertRaisesRegex(InputValidationError, "width and height"):
            load_uploaded_image(Upload(b"<svg xmlns='http://www.w3.org/2000/svg' />"))

    def test_rejects_large_bytes_and_extreme_aspect_ratio(self):
        with self.assertRaises(InputValidationError):
            load_uploaded_image(Upload(b"x" * (MAX_UPLOAD_BYTES + 1)))
        with self.assertRaises(InputValidationError):
            load_uploaded_image(self.image_upload((101, 10)))


if __name__ == "__main__":
    unittest.main()
