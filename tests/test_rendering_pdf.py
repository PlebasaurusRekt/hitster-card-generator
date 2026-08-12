import colorsys
import math
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

    def test_solution_title_size_defaults_to_188(self):
        settings = utils.get_settings()
        self.assertEqual(settings["sol_title_size"], 188)

    def test_title_artwork_preserves_aspect_ratio_at_preview_and_pdf_sizes(self):
        size = 2000
        title_artwork = Image.new("RGBA", (2000, 500), "white")
        title_artwork.info["svg_bytes"] = b"validated SVG source"
        settings = utils.get_settings({
            "card_size": size,
            "qr_title_enabled": True,
            "qr_title": "This text must be replaced by the title artwork",
            "qr_title_image": title_artwork,
            "qr_title_pos": "top",
            "qr_title_size": 100,
        })

        preview = Image.new("RGB", (size, size), "black")
        with patch(
            "src.utils.rasterize_svg_image",
            side_effect=lambda raw, width, height: Image.new(
                "RGBA", (width, height), "white"
            ),
        ) as rasterize:
            utils.render_game_title(preview, settings, side="qr")
        self.assertEqual(self._ink_bbox(preview, Image.new("RGB", preview.size, "black")),
                         (800, 100, 1200, 200))
        rasterize.assert_called_once_with(title_artwork.info["svg_bytes"], 400, 100)

        pdf_settings = utils.get_pdf_render_settings(settings)
        pdf = Image.new(
            "RGB", (utils.PDF_RENDER_CARD_SIZE, utils.PDF_RENDER_CARD_SIZE), "black"
        )
        with patch(
            "src.utils.rasterize_svg_image",
            side_effect=lambda raw, width, height: Image.new(
                "RGBA", (width, height), "white"
            ),
        ) as rasterize:
            utils.render_game_title(pdf, pdf_settings, side="qr")
        pdf_bbox = self._ink_bbox(pdf, Image.new("RGB", pdf.size, "black"))
        self.assertEqual(pdf_bbox[2] - pdf_bbox[0], pdf_settings["qr_title_size"] * 4)
        self.assertEqual(pdf_bbox[3] - pdf_bbox[1], pdf_settings["qr_title_size"])
        rasterize.assert_called_once_with(
            title_artwork.info["svg_bytes"],
            pdf_settings["qr_title_size"] * 4,
            pdf_settings["qr_title_size"],
        )

    def test_solution_title_artwork_uses_the_configured_physical_offsets(self):
        size = 2000
        settings = utils.get_settings({
            "card_size": size,
            "sol_title_enabled": True,
            "sol_title_image": Image.new("RGBA", (400, 100), "white"),
            "sol_title_size": 100,
            "sol_title_opacity": 100,
        })
        background = Image.new("RGB", (size, size), "black")
        image = background.copy()

        utils.render_game_title(image, settings, side="sol")

        self.assertEqual(
            self._ink_bbox(image, background),
            (
                utils.card_distance_cm_to_pixels(
                    size, utils.SOLUTION_TITLE_LEFT_OFFSET_CM
                ),
                utils.card_distance_cm_to_pixels(
                    size, utils.SOLUTION_TITLE_TOP_OFFSET_CM
                ),
                utils.card_distance_cm_to_pixels(
                    size, utils.SOLUTION_TITLE_LEFT_OFFSET_CM
                ) + 400,
                utils.card_distance_cm_to_pixels(
                    size, utils.SOLUTION_TITLE_TOP_OFFSET_CM
                ) + 100,
            ),
        )

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

    def test_font_weights_keep_every_value_in_the_100_to_900_range(self):
        self.assertEqual(utils.normalize_font_weight(100), 100)
        self.assertEqual(utils.normalize_font_weight(237), 237)
        self.assertEqual(utils.normalize_font_weight(900), 900)
        self.assertEqual(utils.normalize_font_weight(-10), 100)
        self.assertEqual(utils.normalize_font_weight(1000), 900)
        self.assertEqual(utils.normalize_font_weight(251), 251)

    def test_montserrat_uses_a_cached_variable_font_for_exact_weights(self):
        fallback = object()
        variable_font = object()
        variable_url = utils.GOOGLE_FONT_VARIABLE_TTF_URLS[
            'montserrat'
        ][False]

        with (
            patch.object(utils, '_google_font_cache', utils.OrderedDict()),
            patch(
                'src.utils.get_bounded_https_content',
                return_value=(b'variable font', variable_url, 200),
            ) as download,
            patch(
                'src.utils.ImageFont.truetype', return_value=variable_font
            ),
            patch(
                'src.utils._apply_font_weight_variation',
                return_value=variable_font,
            ) as apply_weight,
        ):
            returned_font = utils.get_google_font(
                'Montserrat', 120, fallback, weight=237
            )

        self.assertIs(returned_font, variable_font)
        download.assert_called_once_with(
            variable_url,
            allowed_hosts=utils.FONT_PROVIDER_HOSTS,
            max_bytes=utils.FONT_FILE_MAX_BYTES,
            timeout=5,
        )
        apply_weight.assert_called_once_with(variable_font, 237)

    def test_google_font_selects_each_standard_weight_exactly(self):
        variants = [
            {'id': str(weight), 'ttf': f'https://example.test/{weight}.ttf'}
            for weight in range(100, 901, 100)
        ]

        for weight in range(100, 901, 100):
            variant = utils._select_google_font_variant(
                variants, weight, italic=False
            )
            self.assertEqual(variant['id'], str(weight))

    def test_intermediate_weight_prefers_a_variable_font(self):
        variants = [
            {
                'id': str(weight),
                'ttf': f'https://example.test/{weight}.ttf',
            }
            for weight in range(100, 901, 100)
        ]
        variants.append({
            'id': 'variable',
            'ttf': 'https://example.test/variable.ttf',
        })

        variant = utils._select_google_font_variant(
            variants, 237, italic=False
        )
        self.assertEqual(variant['id'], 'variable')

    def test_variable_font_uses_the_requested_weight_axis_value(self):
        class FakeVariableFont:
            def __init__(self):
                self.values = None

            def get_variation_axes(self):
                return [
                    {
                        'name': b'Weight',
                        'minimum': 100,
                        'default': 400,
                        'maximum': 900,
                    },
                    {
                        'name': b'Width',
                        'minimum': 75,
                        'default': 100,
                        'maximum': 125,
                    },
                ]

            def set_variation_by_axes(self, values):
                self.values = values

        font = FakeVariableFont()
        returned_font = utils._apply_font_weight_variation(font, 237)

        self.assertIs(returned_font, font)
        self.assertEqual(font.values, [237, 100])

    def test_solution_text_blocks_keep_exact_edge_offsets_when_wrapped(self):
        size = 2000
        artist_edge_offset = utils.card_distance_cm_to_pixels(
            size, utils.SONG_ARTIST_TOP_EDGE_OFFSET_CM
        )
        title_edge_offset = utils.card_distance_cm_to_pixels(
            size, utils.SONG_TITLE_BOTTOM_EDGE_OFFSET_CM
        )
        settings = utils.get_settings({"google_font": ""})
        font = utils.get_font_for_setting(
            settings, 155, role="artist", weight=500
        )

        for edge, edge_y in (
            ("top", artist_edge_offset),
            ("bottom", size - title_edge_offset),
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
                self.assertEqual(bbox[1], artist_edge_offset)
            else:
                self.assertEqual(bbox[3], size - title_edge_offset)

    def test_requested_print_measurements_are_the_default_offsets(self):
        self.assertEqual(utils.SONG_TITLE_BOTTOM_EDGE_OFFSET_CM, 0.8)
        self.assertEqual(utils.SONG_ARTIST_TOP_EDGE_OFFSET_CM, 0.8385)
        self.assertEqual(utils.CARD_NUMBER_BOTTOM_OFFSET_CM, 0.3)
        self.assertEqual(utils.SOLUTION_TITLE_TOP_OFFSET_CM, 0.2)
        settings = utils.get_settings({"google_font": ""})
        font = utils.get_font_for_setting(
            settings, utils.DEFAULT_SONG_YEAR_SIZE, role="year", weight=700
        )
        glyph_height = font.getmask("2").getbbox()[3]
        glyph_height_cm = (
            glyph_height * utils.CARD_PHYSICAL_SIZE_CM / 2000
        )
        self.assertAlmostEqual(glyph_height_cm, 1.4, places=2)

    def test_solution_wash_only_adds_luminance_to_the_base_color(self):
        for color in utils.DEFAULT_DESIGN_SETTINGS["color_gradient"]:
            base, lighter = utils.derive_solution_color_wash_palette(color)
            base_hls = colorsys.rgb_to_hls(*(channel / 255 for channel in base))
            lighter_hls = colorsys.rgb_to_hls(
                *(channel / 255 for channel in lighter)
            )
            self.assertAlmostEqual(lighter_hls[0], base_hls[0], delta=0.01)
            self.assertAlmostEqual(lighter_hls[2], base_hls[2], delta=0.01)
            self.assertGreater(lighter_hls[1], base_hls[1])

        base, unchanged = utils.derive_solution_color_wash_palette(
            "#7030A0", separation=0
        )
        self.assertEqual(unchanged, base)

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

    def test_reference_song_keeps_non_overlapping_text_blocks(self):
        size = 2000
        settings = utils.get_settings({"google_font": ""})
        self.assertEqual(
            settings["song_year_size"], utils.DEFAULT_SONG_YEAR_SIZE
        )
        background = Image.new("RGB", (size, size), "white")
        artist_edge_offset = utils.card_distance_cm_to_pixels(
            size, utils.SONG_ARTIST_TOP_EDGE_OFFSET_CM
        )
        title_edge_offset = utils.card_distance_cm_to_pixels(
            size, utils.SONG_TITLE_BOTTOM_EDGE_OFFSET_CM
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
            (0, artist_edge_offset - artist_bbox[1]), "M",
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
            (0, size - title_edge_offset - title_bbox[3]), "W",
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
        self.assertGreater(artist_gap_cm, 0)
        self.assertGreater(title_gap_cm, 0)

    def test_long_song_text_shrinks_outside_the_year_clearance(self):
        size = 2000
        settings_override = {
            "card_size": size,
            "google_font": "",
            "ink_saving_mode": True,
            "sol_title_enabled": False,
        }
        settings = utils.get_settings(settings_override)
        artist = "The Incredibly Long Artist Name " * 10
        song_name = "A Very Long Song Title That Needs To Be Kept Clear " * 10
        empty_text_card = utils.create_solution_side_in_memory(
            "", "", 2013, [2013], settings_override=settings_override,
        )
        card = utils.create_solution_side_in_memory(
            song_name, artist, 2013, [2013], settings_override=settings_override,
        )

        draw = ImageDraw.Draw(Image.new("RGB", (size, size), "white"))
        year_font = utils.get_font_for_setting(
            settings, settings["song_year_size"], role="year",
            weight=settings["song_year_font_weight"],
        )
        year_bbox = draw.textbbox(
            (size / 2, size / 2), "2013", font=year_font, anchor="mm"
        )
        clearance = utils.card_distance_cm_to_pixels(
            size, utils.SONG_TEXT_TO_YEAR_CLEARANCE_CM
        )
        protected_top = year_bbox[1] - clearance
        protected_bottom = year_bbox[3] + clearance

        text_difference = ImageChops.difference(card, empty_text_card)
        protected_area = text_difference.crop(
            (0, protected_top, size, protected_bottom)
        )
        self.assertIsNone(protected_area.getbbox())

        artist_font, _ = utils.fit_song_text_to_height(
            draw, artist, settings, settings["song_artist_size"],
            role="artist", weight=settings["song_artist_font_weight"],
            italic=False, max_width=size - 2 * round(size * 0.075),
            max_height=year_bbox[1] - clearance - utils.card_distance_cm_to_pixels(
                size, utils.SONG_ARTIST_TOP_EDGE_OFFSET_CM
            ),
        )
        title_font, _ = utils.fit_song_text_to_height(
            draw, song_name, settings, settings["song_title_size"],
            role="song", weight=settings["song_title_font_weight"],
            italic=True, max_width=size - 2 * round(size * 0.075),
            max_height=(size - utils.card_distance_cm_to_pixels(
                size, utils.SONG_TITLE_BOTTOM_EDGE_OFFSET_CM
            )) - year_bbox[3] - clearance,
        )
        self.assertLess(artist_font.size, settings["song_artist_size"])
        self.assertLess(title_font.size, settings["song_title_size"])


class PdfLayoutTests(unittest.TestCase):
    def test_grid_has_equal_opposite_outer_white_borders(self):
        card_size, margin_x, margin_y, gap_x, gap_y = (
            utils.get_pdf_grid_layout(*utils.A4)
        )
        left = margin_x
        right = utils.A4[0] - (
            margin_x + utils.PDF_GRID_COLS * card_size
            + (utils.PDF_GRID_COLS - 1) * gap_x
        )
        bottom = margin_y
        top = utils.A4[1] - (
            margin_y + utils.PDF_GRID_ROWS * card_size
            + (utils.PDF_GRID_ROWS - 1) * gap_y
        )

        self.assertAlmostEqual(left, right)
        self.assertAlmostEqual(top, bottom)

    def test_photoshop_a4_fit_profile_preserves_card_spacing(self):
        photoshop_page_size = utils.get_pdf_page_size(
            utils.PDF_PRINT_PROFILE_PHOTOSHOP_A4_FIT
        )
        self.assertAlmostEqual(
            photoshop_page_size[0],
            utils.A4[0] * utils.PHOTOSHOP_A4_FIT_SCALE,
        )
        self.assertAlmostEqual(
            photoshop_page_size[1],
            utils.A4[1] * utils.PHOTOSHOP_A4_FIT_SCALE,
        )
        standard_layout = utils.get_pdf_grid_layout(*utils.A4)
        photoshop_layout = utils.get_pdf_grid_layout(*photoshop_page_size)

        self.assertAlmostEqual(standard_layout[0], photoshop_layout[0])
    def test_qr_page_rotation_is_opt_in_and_rotates_the_complete_grid(self):
        class CanvasSpy:
            def __init__(self):
                self.calls = []

            def saveState(self):
                self.calls.append(("save",))

            def translate(self, x, y):
                self.calls.append(("translate", x, y))

            def rotate(self, degrees):
                self.calls.append(("rotate", degrees))

            def restoreState(self):
                self.calls.append(("restore",))

        canvas_spy = CanvasSpy()
        self.assertFalse(
            utils.apply_qr_page_rotation(canvas_spy, 100, 200, False)
        )
        self.assertEqual(canvas_spy.calls, [])
        self.assertTrue(
            utils.apply_qr_page_rotation(canvas_spy, 100, 200, True)
        )
        self.assertEqual(
            canvas_spy.calls,
            [("save",), ("translate", 100, 200), ("rotate", 180)],
        )

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
