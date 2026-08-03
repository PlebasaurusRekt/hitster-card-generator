import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

import src.utils as utils


TRACK_URL = (
    "https://open.spotify.com/track/0123456789ABCDEFGHIJKL"
)


class RenderingTests(unittest.TestCase):
    @staticmethod
    def _ink_bbox(image, background):
        return ImageChops.difference(image, background).getbbox()

    def test_qr_has_no_embedded_quiet_zone(self):
        qr = utils.create_qr_code(TRACK_URL)
        module_count = qr.info["qr_module_count"]
        self.assertEqual(qr.size, (module_count * 10, module_count * 10))
        self.assertNotIn("qr_quiet_zone", qr.info)

    def test_qr_has_one_exact_physical_quiet_zone(self):
        qr = utils.create_qr_code(TRACK_URL)
        settings = utils.get_settings({
            "card_size": 2000,
            "qr_bg_type": "solid",
            "qr_bg_color": (255, 0, 0),
            "qr_background_mode": "solid",
            "qr_background_color": (0, 0, 0),
            "qr_module_color": (255, 255, 255),
            "qr_backplate_radius": 0,
            "google_font": "",
        })
        card = utils.create_qr_with_neon_rings_in_memory(
            qr, settings_override=settings
        )
        _, _, rendered_side = utils.get_qr_render_geometry(qr, settings)
        quiet_pixels = utils.get_qr_backplate_padding_pixels(settings)
        center = settings["card_size"] // 2
        qr_top = center - rendered_side // 2
        quiet_top = qr_top - quiet_pixels

        self.assertEqual(
            quiet_pixels,
            utils.card_distance_cm_to_pixels(2000, 0.1),
        )
        self.assertEqual(card.getpixel((center, quiet_top)), (0, 0, 0))
        self.assertEqual(card.getpixel((center, qr_top - 1)), (0, 0, 0))
        self.assertEqual(
            card.getpixel((center, quiet_top - 1)), (255, 0, 0)
        )

    def test_qr_rendering_is_integer_aligned_and_preserves_rng(self):
        qr = utils.create_qr_code(TRACK_URL)
        settings = utils.get_settings({
            "card_size": 2000,
            "qr_bg_type": "solid",
            "qr_bg_color": (0, 0, 0),
            "qr_background_mode": "solid",
            "qr_module_color": (255, 255, 255),
            "google_font": "",
        })
        before = random.getstate()
        card = utils.create_qr_with_neon_rings_in_memory(
            qr,
            seed=utils.stable_seed(TRACK_URL),
            settings_override=settings,
        )
        self.assertEqual(random.getstate(), before)
        self.assertEqual(card.size, (2000, 2000))

        modules, _, rendered = utils.get_qr_render_geometry(qr, settings)
        self.assertEqual(rendered % modules, 0)

    def test_background_transform_is_bounded_to_card(self):
        target = Image.new("RGB", (200, 200), "white")
        panoramic = Image.new("RGB", (1000, 100), "red")
        utils.apply_background_image(
            target, panoramic, 3.0, 0.0, 0.0, 200
        )
        self.assertEqual(target.size, (200, 200))
        self.assertEqual(target.getpixel((100, 100)), (255, 0, 0))

    def test_card_label_maps_to_both_rendered_titles(self):
        settings = utils.get_settings({
            "card_label": "Game Night",
            "qr_title": "",
            "sol_title": "",
        })
        self.assertTrue(settings["qr_title_enabled"])
        self.assertTrue(settings["sol_title_enabled"])
        self.assertEqual(settings["qr_title"], "Game Night")
        self.assertEqual(settings["sol_title"], "Game Night")

    def test_generation_fingerprint_tracks_rows_settings_and_images(self):
        image_a = Image.new("RGB", (2, 2), "red")
        image_b = Image.new("RGB", (2, 2), "blue")
        base = utils.build_generation_fingerprint(
            [{"Year": 1999}], {"image": image_a}
        )
        self.assertNotEqual(
            base,
            utils.build_generation_fingerprint(
                [{"Year": 2000}], {"image": image_a}
            ),
        )
        self.assertNotEqual(
            base,
            utils.build_generation_fingerprint(
                [{"Year": 1999}], {"image": image_b}
            ),
        )

    def test_font_cache_is_bounded(self):
        for index in range(utils.FONT_CACHE_MAX_ENTRIES + 20):
            utils._bounded_cache_put(
                utils._google_font_cache,
                ("test", index),
                index,
                utils.FONT_CACHE_MAX_ENTRIES,
            )
        self.assertLessEqual(
            len(utils._google_font_cache),
            utils.FONT_CACHE_MAX_ENTRIES,
        )

    def test_solution_text_blocks_keep_exact_edge_offsets_when_wrapped(self):
        size = 2000
        edge_offset = utils.card_distance_cm_to_pixels(
            size, utils.SONG_TEXT_EDGE_OFFSET_CM
        )
        settings = utils.get_settings({"google_font": ""})
        font = utils.get_font_for_setting(
            settings, 155, role="artist", weight=500
        )

        for edge, edge_y in (
            ("top", edge_offset),
            ("bottom", size - edge_offset),
        ):
            background = Image.new("RGB", (size, size), "white")
            image = background.copy()
            utils.draw_centered_text_at_edge(
                ImageDraw.Draw(image),
                "First wrapped line\nSecond wrapped line",
                font,
                size / 2,
                edge_y,
                edge,
            )
            bbox = self._ink_bbox(image, background)
            self.assertIsNotNone(bbox)
            if edge == "top":
                self.assertEqual(bbox[1], edge_offset)
            else:
                self.assertEqual(bbox[3], size - edge_offset)

    def test_solution_title_and_card_numbers_keep_physical_offsets(self):
        size = 2000
        settings = utils.get_settings({
            "card_size": size,
            "google_font": "",
            "sol_title_enabled": True,
            "sol_title": "HITSTER",
            "sol_title_color": (255, 255, 255),
            "sol_title_opacity": 100,
        })
        background = Image.new("RGB", (size, size), "black")

        title_image = background.copy()
        utils.render_game_title(title_image, settings, side="sol")
        title_bbox = self._ink_bbox(title_image, background)
        self.assertEqual(
            title_bbox[:2],
            (
                utils.card_distance_cm_to_pixels(
                    size, utils.SOLUTION_TITLE_LEFT_OFFSET_CM
                ),
                utils.card_distance_cm_to_pixels(
                    size, utils.SOLUTION_TITLE_TOP_OFFSET_CM
                ),
            ),
        )

        expected_right = utils.card_distance_cm_to_pixels(
            size, utils.CARD_NUMBER_RIGHT_OFFSET_CM
        )
        expected_bottom = utils.card_distance_cm_to_pixels(
            size, utils.CARD_NUMBER_BOTTOM_OFFSET_CM
        )
        for side in ("qr", "sol"):
            number_image = background.copy()
            utils.render_card_number(
                number_image, 123, settings, side=side
            )
            number_bbox = self._ink_bbox(number_image, background)
            self.assertEqual(size - number_bbox[2], expected_right)
            self.assertEqual(size - number_bbox[3], expected_bottom)

    def test_reference_song_has_requested_capital_to_year_gaps(self):
        size = 2000
        settings = utils.get_settings({"google_font": ""})
        self.assertEqual(
            settings["song_year_size"], utils.DEFAULT_SONG_YEAR_SIZE
        )
        background = Image.new("RGB", (size, size), "white")
        edge_offset = utils.card_distance_cm_to_pixels(
            size, utils.SONG_TEXT_EDGE_OFFSET_CM
        )
        center = size / 2

        font_artist = utils.get_font_for_setting(
            settings, settings["song_artist_size"], role="artist",
            weight=settings["song_artist_font_weight"],
        )
        artist_bbox = ImageDraw.Draw(background).multiline_textbbox(
            (0, 0), "Miley Cyrus", font=font_artist, align="center"
        )
        capital_m = background.copy()
        ImageDraw.Draw(capital_m).text(
            (0, edge_offset - artist_bbox[1]), "M",
            fill="black", font=font_artist,
        )
        capital_m_bbox = self._ink_bbox(capital_m, background)

        font_title = utils.get_font_for_setting(
            settings, settings["song_title_size"], role="song",
            italic=True, weight=settings["song_title_font_weight"],
        )
        title_bbox = ImageDraw.Draw(background).multiline_textbbox(
            (0, 0), "Wrecking Ball", font=font_title, align="center"
        )
        capital_w = background.copy()
        ImageDraw.Draw(capital_w).text(
            (0, size - edge_offset - title_bbox[3]), "W",
            fill="black", font=font_title,
        )
        capital_w_bbox = self._ink_bbox(capital_w, background)

        year_image = background.copy()
        font_year = utils.get_font_for_setting(
            settings, settings["song_year_size"], role="year",
            weight=settings["song_year_font_weight"],
        )
        ImageDraw.Draw(year_image).text(
            (center, center), "2013", fill="black",
            font=font_year, anchor="mm",
        )
        year_bbox = self._ink_bbox(year_image, background)

        artist_gap_cm = (
            year_bbox[1] - capital_m_bbox[3]
        ) * utils.CARD_PHYSICAL_SIZE_CM / size
        title_gap_cm = (
            capital_w_bbox[1] - year_bbox[3]
        ) * utils.CARD_PHYSICAL_SIZE_CM / size
        self.assertAlmostEqual(
            artist_gap_cm, utils.SONG_ARTIST_TO_YEAR_GAP_CM, places=1
        )
        self.assertAlmostEqual(
            title_gap_cm, utils.SONG_YEAR_TO_TITLE_GAP_CM, places=1
        )


class PdfLayoutTests(unittest.TestCase):
    def test_front_and_back_positions_mirror_each_row(self):
        front = utils.get_pdf_card_positions(12)
        back = utils.get_pdf_card_positions(12, mirrored=True)
        for index in range(12):
            row_start = (index // 3) * 3
            mirrored_index = row_start + (2 - index % 3)
            self.assertAlmostEqual(
                front[index][1], back[mirrored_index][1]
            )
            self.assertAlmostEqual(front[index][2], back[index][2])

    def test_rejects_incomplete_disk_card_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "card_001_qr.png"
            Image.new("RGB", (10, 10)).save(image_path)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                utils.create_cards_pdf(
                    directory, str(Path(directory) / "cards.pdf")
                )

    def test_disk_pdf_uses_shared_positions_for_both_sides(self):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            for number in (1, 2):
                for side in ("qr", "solution"):
                    Image.new(
                        "RGB", (10, 10), (number * 20, 0, 0)
                    ).save(
                        directory_path
                        / f"card_{number:03d}_{side}.png"
                    )

            class CanvasSpy:
                def __init__(self, *args, **kwargs):
                    self.pages = 0

                def setFillColorRGB(self, *args):
                    pass

                def rect(self, *args, **kwargs):
                    pass

                def showPage(self):
                    self.pages += 1

                def save(self):
                    pass

            spy = CanvasSpy()
            placements = []

            def record_draw(canvas, source, x, y, card_size):
                placements.append((Path(source).name, x, y))

            with (
                patch("src.utils.canvas.Canvas", return_value=spy),
                patch("src.utils.draw_pdf_card_image", side_effect=record_draw),
            ):
                utils.create_cards_pdf(
                    directory, str(directory_path / "cards.pdf")
                )

            self.assertEqual(spy.pages, 2)
            self.assertEqual(len(placements), 4)
            front = utils.get_pdf_card_positions(2)
            back = utils.get_pdf_card_positions(2, mirrored=True)
            self.assertEqual(
                [(x, y) for _, x, y in placements[:2]],
                [(x, y) for _, x, y in front],
            )
            self.assertEqual(
                [(x, y) for _, x, y in placements[2:]],
                [(x, y) for _, x, y in back],
            )


if __name__ == "__main__":
    unittest.main()
