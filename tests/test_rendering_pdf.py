import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import src.utils as utils


TRACK_URL = (
    "https://open.spotify.com/track/0123456789ABCDEFGHIJKL"
)


class RenderingTests(unittest.TestCase):
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
