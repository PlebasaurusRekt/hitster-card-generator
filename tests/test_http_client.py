import unittest
from unittest.mock import patch

import requests

from src.http_client import (
    ResponseTooLargeError,
    _read_limited_response,
    get_bounded_https_content,
    get_spotify_html,
)
from src.input_validation import InputValidationError


TRACK_ID = "0123456789ABCDEFGHIJKL"
TRACK_URL = f"https://open.spotify.com/track/{TRACK_ID}"


class FakeResponse:
    def __init__(
        self,
        body=b"",
        status_code=200,
        headers=None,
        encoding="utf-8",
    ):
        self.body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.encoding = encoding
        self.response = self

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    @property
    def is_permanent_redirect(self):
        return self.status_code in (301, 308)

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(
                f"HTTP {self.status_code}", response=self
            )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return next(self.responses)


class BoundedHttpTests(unittest.TestCase):
    def test_rejects_content_length_and_stream_overflow(self):
        response = FakeResponse(
            b"small", headers={"Content-Length": "101"}
        )
        with self.assertRaises(ResponseTooLargeError):
            _read_limited_response(response, 100)

        response = FakeResponse(b"x" * 101)
        with self.assertRaises(ResponseTooLargeError):
            _read_limited_response(response, 100)

    def test_spotify_redirect_stays_on_allowlisted_host(self):
        session = FakeSession([
            FakeResponse(
                status_code=302,
                headers={"Location": f"/track/{TRACK_ID}?si=1"},
            ),
            FakeResponse(b"<html>ok</html>"),
        ])
        with patch("src.http_client.get_http_session", return_value=session):
            body, final_url = get_spotify_html(
                TRACK_URL, expected_kind="track"
            )
        self.assertEqual(body, "<html>ok</html>")
        self.assertEqual(final_url, TRACK_URL)
        self.assertEqual(session.urls, [TRACK_URL, TRACK_URL])

    def test_spotify_redirect_cannot_escape_to_internal_host(self):
        session = FakeSession([
            FakeResponse(
                status_code=302,
                headers={"Location": "http://127.0.0.1/admin"},
            )
        ])
        with patch("src.http_client.get_http_session", return_value=session):
            with self.assertRaises(InputValidationError):
                get_spotify_html(TRACK_URL, expected_kind="track")

    def test_generic_allowlist_validates_redirects_and_size(self):
        session = FakeSession([
            FakeResponse(
                status_code=302,
                headers={"Location": "https://fonts.gstatic.com/font.ttf"},
            ),
            FakeResponse(b"font"),
        ])
        with patch("src.http_client.get_http_session", return_value=session):
            body, final_url, status = get_bounded_https_content(
                "https://gwfh.mranftl.com/api/fonts/test",
                allowed_hosts={
                    "gwfh.mranftl.com", "fonts.gstatic.com",
                },
                max_bytes=100,
            )
        self.assertEqual((body, final_url, status), (
            b"font", "https://fonts.gstatic.com/font.ttf", 200
        ))

        escaping = FakeSession([
            FakeResponse(
                status_code=302,
                headers={"Location": "https://example.com/font.ttf"},
            )
        ])
        with patch("src.http_client.get_http_session", return_value=escaping):
            with self.assertRaises(requests.exceptions.InvalidURL):
                get_bounded_https_content(
                    "https://gwfh.mranftl.com/api/fonts/test",
                    allowed_hosts={"gwfh.mranftl.com"},
                    max_bytes=100,
                )


if __name__ == "__main__":
    unittest.main()
