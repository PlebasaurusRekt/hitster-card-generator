import unittest
from unittest.mock import patch

import requests
from PIL import Image, ImageChops

import src.utils as utils


def artist_url(index):
    return f"https://open.spotify.com/artist/{index:022d}"


def relation(artist_type):
    return {
        "target-type": "artist",
        "artist": {"type": artist_type},
    }


class JsonResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if not 200 <= self.status_code < 300:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.payload


class SequenceSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


class PerformerClassificationTests(unittest.TestCase):
    def setUp(self):
        with utils._performer_type_cache_lock:
            utils._performer_type_cache.clear()
        utils._musicbrainz_last_request_at = 0.0

    def test_local_group_rules_do_not_call_musicbrainz(self):
        songs = [
            {
                "name": "Song",
                "original_name": "Song (feat. Guest)",
                "artist": "Artist",
                "spotify_artist_urls": [artist_url(1)],
            },
            {
                "name": "Song",
                "original_name": "Song ft. Guest",
                "artist": "Artist",
                "spotify_artist_urls": [artist_url(2)],
            },
            {
                "name": "Song",
                "original_name": "Song featuring Guest",
                "artist": "Artist",
                "spotify_artist_urls": [artist_url(3)],
            },
            {
                "name": "Song",
                "artist": "Artist One, Artist Two",
                "spotify_artist_urls": [],
            },
            {
                "name": "Song",
                "artist": "Shared Credit",
                "spotify_artist_urls": [artist_url(4), artist_url(5)],
            },
        ]

        with patch(
            "src.utils.get_performer_types_from_musicbrainz",
            return_value=({}, False),
        ) as lookup:
            result = utils.enrich_performer_types(songs)

        lookup.assert_called_once_with({})
        self.assertEqual(
            [song["performer_type"] for song in songs],
            [utils.PERFORMER_TYPE_GROUP] * len(songs),
        )
        self.assertEqual(result["unknown_count"], 0)
        self.assertFalse(result["lookup_failed"])

    def test_exact_musicbrainz_types_and_conflicts_are_mapped(self):
        urls = [artist_url(index) for index in range(7)]
        artist_types = [
            "Person", "Group", "Orchestra", "Choir", "Character", "Other"
        ]
        entries = [
            {
                "resource": url,
                "relations": [relation(artist_type)],
            }
            for url, artist_type in zip(urls, artist_types)
        ]
        entries.append({
            "resource": urls[6],
            "relations": [relation("Person"), relation("Group")],
        })

        with patch(
            "src.utils._request_musicbrainz_json",
            return_value={"urls": entries},
        ) as request:
            result, failed = utils.get_performer_types_from_musicbrainz(urls)

        self.assertFalse(failed)
        self.assertEqual(result[urls[0]], utils.PERFORMER_TYPE_SOLO)
        for url in urls[1:4]:
            self.assertEqual(result[url], utils.PERFORMER_TYPE_GROUP)
        for url in urls[4:]:
            self.assertEqual(result[url], utils.PERFORMER_TYPE_UNKNOWN)
        params = request.call_args.args[1]
        self.assertEqual(
            [value for key, value in params if key == "resource"],
            urls,
        )

        with patch("src.utils._request_musicbrainz_json") as cached_request:
            cached, failed = utils.get_performer_types_from_musicbrainz(urls)
        cached_request.assert_not_called()
        self.assertEqual(cached, result)
        self.assertFalse(failed)

    def test_musicbrainz_artist_urls_are_deduplicated_and_batched(self):
        urls = [artist_url(index) for index in range(101)]
        with patch(
            "src.utils._request_musicbrainz_json",
            return_value={"urls": []},
        ) as request:
            result, failed = utils.get_performer_types_from_musicbrainz(
                urls + [urls[0]]
            )

        self.assertEqual(result, {})
        self.assertFalse(failed)
        self.assertEqual(request.call_count, 2)
        batch_sizes = [
            sum(key == "resource" for key, _ in call.args[1])
            for call in request.call_args_list
        ]
        self.assertEqual(batch_sizes, [100, 1])

    def test_unavailable_exact_lookup_leaves_unknown_and_preserves_edits(self):
        unresolved = {
            "name": "Song",
            "artist": "Artist",
            "spotify_artist_urls": [artist_url(1)],
        }
        manual = {
            "name": "Manual",
            "artist": "Artist",
            "performer_type": "group",
            "spotify_artist_urls": [artist_url(2)],
        }

        with patch(
            "src.utils.get_performer_types_from_musicbrainz",
            return_value=({}, True),
        ):
            result = utils.enrich_performer_types([unresolved, manual])

        self.assertEqual(
            unresolved["performer_type"], utils.PERFORMER_TYPE_UNKNOWN
        )
        self.assertEqual(manual["performer_type"], utils.PERFORMER_TYPE_GROUP)
        self.assertEqual(result, {"unknown_count": 1, "lookup_failed": True})

    def test_musicbrainz_request_retries_one_throttled_response(self):
        session = SequenceSession([
            JsonResponse({}, status_code=503, headers={"Retry-After": "1"}),
            JsonResponse({"urls": []}),
        ])
        with (
            patch("src.utils.get_http_session", return_value=session),
            patch("src.utils.time.monotonic", side_effect=[10.0, 11.0, 12.0]),
            patch("src.utils.time.sleep") as sleep,
        ):
            payload = utils._request_musicbrainz_json(
                "https://musicbrainz.org/ws/2/url",
                [("resource", artist_url(1))],
            )

        self.assertEqual(payload, {"urls": []})
        self.assertEqual(len(session.calls), 2)
        sleep.assert_called_once_with(1.0)


class PerformerIconTests(unittest.TestCase):
    def test_icons_are_distinct_and_stay_in_the_top_right_region(self):
        size = 1000
        settings = utils.get_settings({
            "card_size": size,
            "google_font": "",
        })
        base = Image.new("RGB", (size, size), "#777777")
        boxes = {}

        for performer_type in utils.PERFORMER_TYPES:
            rendered = base.copy()
            utils.render_performer_type_icon(
                rendered, performer_type, settings
            )
            boxes[performer_type] = ImageChops.difference(
                rendered, base
            ).getbbox()
            self.assertIsNotNone(boxes[performer_type])

        top = utils.card_distance_cm_to_pixels(
            size, utils.PERFORMER_ICON_TOP_OFFSET_CM
        )
        right = size - utils.card_distance_cm_to_pixels(
            size, utils.PERFORMER_ICON_RIGHT_OFFSET_CM
        )
        height = utils.card_distance_cm_to_pixels(
            size, utils.PERFORMER_ICON_HEIGHT_CM
        )
        tolerance = max(1, round(size * 0.004))
        for box in boxes.values():
            self.assertGreaterEqual(box[1], top - tolerance)
            self.assertLessEqual(box[2], right + tolerance)
            self.assertLessEqual(box[3], top + height + tolerance)

        solo_width = (
            boxes[utils.PERFORMER_TYPE_SOLO][2]
            - boxes[utils.PERFORMER_TYPE_SOLO][0]
        )
        group_width = (
            boxes[utils.PERFORMER_TYPE_GROUP][2]
            - boxes[utils.PERFORMER_TYPE_GROUP][0]
        )
        self.assertGreater(group_width, solo_width)

    def test_solution_titles_respect_the_icon_clearance(self):
        size = 1000
        performer_type = utils.PERFORMER_TYPE_GROUP
        icon_bounds = utils.get_performer_type_icon_bounds(
            size, performer_type
        )
        right_limit = (
            icon_bounds[0]
            - utils.card_distance_cm_to_pixels(
                size, utils.PERFORMER_ICON_TITLE_GAP_CM
            )
        )
        base = Image.new("RGB", (size, size), "#777777")

        text_settings = utils.get_settings({
            "card_size": size,
            "google_font": "",
            "sol_title_enabled": True,
            "sol_title": "AN EXTREMELY LONG CUSTOM GAME TITLE",
            "sol_title_color": (255, 255, 255),
            "sol_title_opacity": 100,
            "sol_title_size": 188,
        })
        text_image = base.copy()
        utils.render_game_title(
            text_image,
            text_settings,
            side="sol",
            solution_right_limit=right_limit,
        )
        text_box = ImageChops.difference(text_image, base).getbbox()
        self.assertIsNotNone(text_box)
        self.assertLessEqual(text_box[2], right_limit)

        artwork_settings = utils.get_settings({
            "card_size": size,
            "sol_title_enabled": True,
            "sol_title_image": Image.new(
                "RGBA", (1000, 100), (255, 255, 255, 255)
            ),
            "sol_title_opacity": 100,
            "sol_title_size": 188,
        })
        artwork_image = base.copy()
        utils.render_game_title(
            artwork_image,
            artwork_settings,
            side="sol",
            solution_right_limit=right_limit,
        )
        artwork_box = ImageChops.difference(artwork_image, base).getbbox()
        self.assertIsNotNone(artwork_box)
        self.assertLessEqual(artwork_box[2], right_limit)

    def test_missing_performer_type_preserves_existing_rendering(self):
        image = Image.new("RGB", (200, 200), "navy")
        original = image.copy()
        utils.render_performer_type_icon(
            image,
            None,
            utils.get_settings({"card_size": 200, "google_font": ""}),
        )
        self.assertIsNone(ImageChops.difference(image, original).getbbox())


if __name__ == "__main__":
    unittest.main()
