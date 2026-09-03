import time
import unittest
import urllib.parse
from unittest.mock import patch

import src.utils as utils


CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"
REDIRECT_URI = "https://hitster-card-generator2.streamlit.app/"
PLAYLIST_ID = "ZYXWVUTSRQPONMLKJIHGFE"
TRACK_ID = "0123456789ABCDEFGHIJKL"
TRACK_URL = f"https://open.spotify.com/track/{TRACK_ID}"


class JsonResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.headers = {}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self.payload


class SequenceSession:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = iter(get_responses or [])
        self.post_responses = iter(post_responses or [])
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return next(self.get_responses)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return next(self.post_responses)


class OAuthTests(unittest.TestCase):
    def authorization_state(self):
        authorize_url = utils.begin_spotify_oauth(
            CLIENT_ID, CLIENT_SECRET, REDIRECT_URI
        )
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(authorize_url).query
        )
        self.assertEqual(query["redirect_uri"], [REDIRECT_URI])
        return query["state"][0]

    def test_signed_state_survives_without_process_storage(self):
        state = self.authorization_state()
        self.assertEqual(
            utils._read_spotify_oauth_state(
                state, CLIENT_ID, CLIENT_SECRET
            ),
            REDIRECT_URI,
        )
        payload, signature = state.split(".", 1)
        changed = ("A" if payload[0] != "A" else "B") + payload[1:]
        with self.assertRaises(utils.SpotifyAPIError):
            utils._read_spotify_oauth_state(
                f"{changed}.{signature}", CLIENT_ID, CLIENT_SECRET
            )

    def test_public_state_hints_support_fresh_callback_session(self):
        state = self.authorization_state()
        hints = utils.inspect_spotify_oauth_state(state)
        self.assertEqual(hints, {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
        })
        self.assertNotIn(CLIENT_SECRET, state)

    def test_expired_state_is_rejected(self):
        with patch("src.utils.time.time", return_value=1000):
            state = utils._create_spotify_oauth_state(
                CLIENT_ID, CLIENT_SECRET, REDIRECT_URI
            )
        with patch(
            "src.utils.time.time",
            return_value=1001 + utils.SPOTIFY_OAUTH_STATE_TTL_SECONDS,
        ):
            with self.assertRaisesRegex(
                utils.SpotifyAPIError, "expired"
            ):
                utils._read_spotify_oauth_state(
                    state, CLIENT_ID, CLIENT_SECRET
                )

    def test_callback_exchange_uses_verified_redirect_and_omits_credentials(self):
        state = self.authorization_state()
        session = SequenceSession(post_responses=[
            JsonResponse({
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
            })
        ])
        with patch("src.utils.get_http_session", return_value=session):
            auth = utils.complete_spotify_oauth(
                "code", state, CLIENT_ID, CLIENT_SECRET
            )
        self.assertEqual(auth["access_token"], "access")
        self.assertNotIn("client_secret", auth)
        post_kwargs = session.post_calls[0][1]
        self.assertEqual(
            post_kwargs["data"]["redirect_uri"], REDIRECT_URI
        )

    def test_refresh_raises_specific_authentication_error(self):
        session = SequenceSession(post_responses=[
            JsonResponse({}, status_code=401)
        ])
        auth = {
            "access_token": "old",
            "refresh_token": "refresh",
            "expires_at": time.time() - 1,
        }
        with patch("src.utils.get_http_session", return_value=session):
            with self.assertRaises(utils.SpotifyAuthenticationError):
                utils.get_spotify_access_token(
                    auth, CLIENT_ID, CLIENT_SECRET
                )


class SpotifyPlaylistTests(unittest.TestCase):
    def playlist_url(self):
        return f"https://open.spotify.com/playlist/{PLAYLIST_ID}"

    def valid_track(self, name="Song"):
        return {
            "item": {
                "type": "track",
                "name": name,
                "artists": [{"name": "Artist"}],
                "album": {
                    "name": "Album",
                    "release_date": "1999-01-01",
                },
                "external_urls": {"spotify": TRACK_URL},
            }
        }

    def test_paginates_and_preserves_schema(self):
        next_url = (
            f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/items"
            "?offset=1&limit=50"
        )
        session = SequenceSession(get_responses=[
            JsonResponse({"name": "Playlist"}),
            JsonResponse({
                "items": [self.valid_track("One")],
                "total": 2,
                "next": next_url,
            }),
            JsonResponse({
                "items": [self.valid_track("Two")],
                "total": 2,
                "next": None,
            }),
        ])
        with patch("src.utils.get_http_session", return_value=session):
            playlist = utils.fetch_spotify_playlist_with_token(
                self.playlist_url(), "token"
            )
        songs = utils.parse_playlist_data(playlist)
        self.assertEqual([song["name"] for song in songs], ["One", "Two"])
        self.assertEqual([song["year"] for song in songs], [1999, 1999])

    def test_rejects_pagination_host_escape(self):
        session = SequenceSession(get_responses=[
            JsonResponse({"name": "Playlist"}),
            JsonResponse({
                "items": [self.valid_track()],
                "total": 2,
                "next": "https://127.0.0.1/internal",
            }),
        ])
        with patch("src.utils.get_http_session", return_value=session):
            with self.assertRaisesRegex(
                utils.SpotifyAPIError, "pagination URL"
            ):
                utils.fetch_spotify_playlist_with_token(
                    self.playlist_url(), "token"
                )

    def test_rejects_oversized_playlist_before_next_page(self):
        session = SequenceSession(get_responses=[
            JsonResponse({"name": "Playlist"}),
            JsonResponse({
                "items": [],
                "total": utils.MAX_TRACK_LINKS + 1,
                "next": None,
            }),
        ])
        with patch("src.utils.get_http_session", return_value=session):
            with self.assertRaisesRegex(
                utils.SpotifyAPIError, "limited"
            ):
                utils.fetch_spotify_playlist_with_token(
                    self.playlist_url(), "token"
                )

    def test_skips_malformed_items_instead_of_aborting(self):
        payload = {
            "tracks": {
                "items": [
                    None,
                    {},
                    {"item": {"type": "episode"}},
                    self.valid_track(),
                ]
            }
        }
        songs = utils.parse_playlist_data(payload)
        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0]["artist"], "Artist")

    def test_preserves_all_track_artists_as_comma_separated_text(self):
        track = self.valid_track()
        track["item"]["artists"] = [
            {"name": "Artist One"},
            {"name": "Artist Two"},
        ]

        songs = utils.parse_playlist_data({"tracks": {"items": [track]}})

        self.assertEqual(songs[0]["artist"], "Artist One, Artist Two")


if __name__ == "__main__":
    unittest.main()
