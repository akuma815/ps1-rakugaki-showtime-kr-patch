from __future__ import annotations

import hashlib
import struct
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from psx_disc import RAW_SECTOR_SIZE, expected_msf  # noqa: E402
from psx_edc_ecc import regenerate_mode2_form1, verify_mode2_form1  # noqa: E402
from psx_tim import decode_tim, parse_tim, scan_tims  # noqa: E402
from survey_assets import parse_hagecho_blocks  # noqa: E402
from scan_font_strings import cell_codes, encode  # noqa: E402
from analyze_text_population import decode_exact, payload_offset, scan_candidate_runs  # noqa: E402
from analyze_text_callsites import decode_nul  # noqa: E402
from census_hangul_glyphs import glyph_class  # noqa: E402
from export_text_ssot import expected_rows  # noqa: E402
from analyze_callback_dispatch import analyze as analyze_callback_dispatch  # noqa: E402
from trace_callback_dispatch import normalize_ram_pointer, normalize_readable_pointer, read_regs  # noqa: E402
from analyze_callback_loadimage import extract as extract_callback_loadimage  # noqa: E402
from render_menu_strips import SMALL_FONT_PATH, SMALL_FONT_SIZE, encode_strip_4bpp  # noqa: E402
from extract_psx_movies import (  # noqa: E402
    STR_MAGIC,
    decode_xa_audio_coding,
    iso_extent_sector_count,
    parse_str_header,
)
from render_movie_subtitle_preview import ass_text, ass_timestamp, build_ass  # noqa: E402
from merge_psx_movie_video import select_donor_frame_pairs  # noqa: E402
from build_hangul_atlas_movie import changed_sector_indices  # noqa: E402


class DiscTests(unittest.TestCase):
    def test_expected_msf_includes_lead_in(self) -> None:
        self.assertEqual(expected_msf(0), bytes.fromhex("000200"))
        self.assertEqual(expected_msf(75), bytes.fromhex("000300"))

    def test_mode2_form1_edc_ecc_against_original_sector(self) -> None:
        project = Path(__file__).resolve().parents[2]
        image = project / "original/Rakugaki Showtime (Japan).bin"
        if not image.exists():
            self.skipTest("original disc image is not available")
        with image.open("rb") as source:
            source.seek(615 * RAW_SECTOR_SIZE)
            original = source.read(RAW_SECTOR_SIZE)
        self.assertTrue(verify_mode2_form1(original))
        mutated = bytearray(original)
        mutated[24 + 123] ^= 0x5A
        self.assertFalse(verify_mode2_form1(bytes(mutated)))
        repaired = regenerate_mode2_form1(bytes(mutated))
        self.assertTrue(verify_mode2_form1(repaired))
        self.assertEqual(repaired[24 + 123], mutated[24 + 123])

    def test_movie_iso_size_counts_2048_byte_extent_blocks(self) -> None:
        self.assertEqual(iso_extent_sector_count(0), 0)
        self.assertEqual(iso_extent_sector_count(1), 1)
        self.assertEqual(iso_extent_sector_count(6144000), 3000)
        self.assertEqual(iso_extent_sector_count(70238208), 34296)

    def test_parse_str_sector_header(self) -> None:
        payload = bytearray(2048)
        struct.pack_into("<IHHIIHH", payload, 0, STR_MAGIC, 0, 9, 1, 1064, 320, 240)
        self.assertEqual(
            parse_str_header(bytes(payload)),
            {
                "chunk_index": 0,
                "chunks_in_frame": 9,
                "frame_number": 1,
                "demuxed_size": 1064,
                "width": 320,
                "height": 240,
            },
        )
        payload[0] ^= 0xFF
        self.assertIsNone(parse_str_header(bytes(payload)))

    def test_xa_stereo_37800_four_bit_sector_duration(self) -> None:
        profile = decode_xa_audio_coding(0x01)
        self.assertEqual(profile["channels"], 2)
        self.assertEqual(profile["sample_rate"], 37800)
        self.assertEqual(profile["bits_per_sample"], 4)
        self.assertEqual(profile["samples_per_sector_per_channel"], 2016)
        self.assertAlmostEqual(profile["seconds_per_sector"], 1 / 18.75)

    def test_movie_subtitle_frame_boundary_to_ass_timestamp(self) -> None:
        self.assertEqual(ass_timestamp(0, 15), "0:00:00.00")
        self.assertEqual(ass_timestamp(1, 15), "0:00:00.06")
        self.assertEqual(ass_timestamp(15, 15), "0:00:01.00")
        self.assertEqual(ass_timestamp(5400, 15), "0:06:00.00")

    def test_movie_subtitle_ass_text_preserves_line_breaks(self) -> None:
        self.assertEqual(ass_text("첫 줄\\N둘째 {줄}"), "첫 줄\\N둘째 \\{줄\\}")

    def test_movie_subtitle_ass_uses_frame_boundaries(self) -> None:
        style = {
            "frame_rate": 15,
            "play_resolution": {"width": 320, "height": 240},
            "font_family": "Galmuri11",
            "font_size": 18,
            "primary_colour": "&H00FFFFFF",
            "outline_colour": "&H00000000",
            "back_colour": "&H80000000",
            "border_style": 1,
            "outline": 2,
            "shadow": 0,
            "alignment": 2,
            "margin_left": 12,
            "margin_right": 12,
            "margin_vertical": 14,
        }
        output = build_ass([{
            "start_frame": 15,
            "end_frame": 30,
            "speaker": "",
            "korean": "테스트",
        }], style)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.00", output)

    def test_movie_donor_alignment_matches_original_chunk_phase(self) -> None:
        def frames(counts):
            return {
                frame_number: {index: {} for index in range(chunk_count)}
                for frame_number, chunk_count in enumerate(counts, start=1)
            }

        original = frames([9, 9, 9, 8, 9, 9, 9, 8])
        donor = frames([8, 9, 9, 9, 8, 9, 9, 9, 8])
        pairs = select_donor_frame_pairs(original, donor, drop_leading_frames=1)
        self.assertEqual(pairs[0], (1, 2))
        self.assertEqual(pairs[-1], (8, 9))

    def test_movie_build_changed_sector_plan_uses_raw_2352_boundaries(self) -> None:
        before = bytes(RAW_SECTOR_SIZE * 3)
        after = bytearray(before)
        after[RAW_SECTOR_SIZE + 24] = 1
        after[RAW_SECTOR_SIZE * 3 - 1] = 2
        self.assertEqual(changed_sector_indices(before, bytes(after)), [1, 2])

    def test_movie_build_rejects_unaligned_sector_plan(self) -> None:
        with self.assertRaises(ValueError):
            changed_sector_indices(b"short", b"short")


class TimTests(unittest.TestCase):
    def test_valid_16_bpp_tim(self) -> None:
        payload = bytes.fromhex("1f00e003")
        block = struct.pack("<I4H", 12 + len(payload), 0, 0, 2, 1) + payload
        data = b"prefix" + b"\x10\x00\x00\x00" + struct.pack("<I", 2) + block
        tim = parse_tim(data, 6)
        self.assertIsNotNone(tim)
        self.assertEqual((tim["pixel_width"], tim["pixel_height"], tim["bpp"]), (2, 1, 16))
        self.assertEqual(len(scan_tims(data)), 1)
        decoded = decode_tim(data, tim)
        self.assertEqual(decoded.size, (2, 1))

    def test_rejects_bad_payload_size(self) -> None:
        data = b"\x10\x00\x00\x00" + struct.pack("<I", 2) + struct.pack("<I4H", 14, 0, 0, 2, 1) + b"xx"
        self.assertIsNone(parse_tim(data, 0))


class ContainerTests(unittest.TestCase):
    def test_do_offsets_partition_hagecho(self) -> None:
        hagecho = b"abcdEFGHij"
        do_data = struct.pack("<4I", 0, 4, 8, 10)
        blocks = parse_hagecho_blocks(do_data, hagecho)
        self.assertEqual([item["size"] for item in blocks], [4, 4, 2])
        self.assertEqual(blocks[0]["sha256"], hashlib.sha256(b"abcd").hexdigest())

    def test_rejects_non_monotonic_offsets(self) -> None:
        with self.assertRaises(ValueError):
            parse_hagecho_blocks(struct.pack("<3I", 0, 4, 4), b"1234")


class CandidateFontEncodingTests(unittest.TestCase):
    def test_expected_menu_anchor_codes(self) -> None:
        self.assertEqual(encode("オプション"), bytes.fromhex("83498376835683878393"))
        self.assertEqual(cell_codes("オプション"), [0xAC, 0xD9, 0xB9, 0xE9, 0xF5])
        self.assertEqual(cell_codes("ス"), [0xBB])


class TextPopulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapping = {
            0x8140: {"character": "　", "cell": "00"},
            0x8341: {"character": "ア", "cell": "83"},
        }

    def test_candidate_run_requires_two_codes_and_zero_lead(self) -> None:
        data = bytes.fromhex("81 40 83 41 00 FF 83 41 00 FF")
        found = scan_candidate_runs(data, self.mapping)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["offset"], 0)
        self.assertEqual(found[0]["raw_hex"], "81408341")
        self.assertEqual(found[0]["text"], "　ア")

    def test_exact_decode_roundtrip_and_rejects_odd_length(self) -> None:
        raw = bytes.fromhex("83 41 81 40")
        text, codes = decode_exact(raw, self.mapping)
        self.assertEqual(text, "ア　")
        self.assertEqual(codes, [0x8341, 0x8140])
        with self.assertRaises(ValueError):
            decode_exact(raw + b"\x83", self.mapping)

    def test_wrapped_module_payload_offset(self) -> None:
        wrapped = bytes.fromhex("00 07 00 00 10 00 00 00 00 00 00 00 04 00 00 00") + b"data"
        self.assertEqual(payload_offset(wrapped), 0x10)
        self.assertEqual(payload_offset(b"plain module"), 0)

    def test_consumed_unmapped_code_is_preserved(self) -> None:
        runtime_mapping = {0x8341: self.mapping[0x8341]}
        decoded = decode_nul(bytes.fromhex("81 40 83 41 00 00"), 0, runtime_mapping, 6)
        self.assertEqual(decoded["text"], "　ア")
        self.assertEqual(decoded["codes"], ["8140", "8341"])
        self.assertEqual(decoded["unmapped_codes"], ["8140"])


class TranslationSsotTests(unittest.TestCase):
    def test_expected_row_keeps_source_contract_and_empty_translation(self) -> None:
        rows = expected_rows([
            {
                "id": "TXT-ARA-000010",
                "source": "ARA.BIN",
                "file_offset": 0x10,
                "block_index": None,
                "original_hex": "8341",
                "original_text": "ア",
            }
        ])
        self.assertEqual(rows[0]["file_offset_hex"], "0x10")
        self.assertEqual(rows[0]["translation"], "")
        self.assertEqual(rows[0]["status"], "untranslated")

    def test_glyph_classes_distinguish_hangul_latin_and_symbols(self) -> None:
        self.assertEqual(glyph_class("한"), "hangul_syllable")
        self.assertEqual(glyph_class("ㄱ"), "hangul_jamo")
        self.assertEqual(glyph_class("A"), "latin")
        self.assertEqual(glyph_class("7"), "digit")
        self.assertEqual(glyph_class("?"), "symbol_or_other")


class VramOccupancyTests(unittest.TestCase):
    def test_probe_stub_assembles_within_scratch_budget(self) -> None:
        from capture_vram_occupancy import REGSAVE_OFF, SCRATCH_BASE, assemble_stub

        for rect in ((704, 256, 64, 256), (960, 256, 64, 256)):
            code, tramp_offset = assemble_stub(rect)
            self.assertEqual(len(code) % 4, 0)
            self.assertLessEqual(len(code), REGSAVE_OFF)
            self.assertEqual(tramp_offset % 4, 0)
            self.assertEqual(len(code) - tramp_offset, 16)
            self.assertEqual(SCRATCH_BASE & 0x3, 0)

    def test_probe_stub_rejects_oversized_rect(self) -> None:
        from capture_vram_occupancy import assemble_stub

        with self.assertRaises(ValueError):
            assemble_stub((0, 0, 128, 256))

    def test_encode_j_reaches_scratch_from_kseg0(self) -> None:
        from capture_vram_occupancy import SCRATCH_BASE, encode_j

        word = encode_j(SCRATCH_BASE)
        self.assertEqual(word >> 26, 0x02)
        self.assertEqual((word & 0x03FFFFFF) << 2 | (SCRATCH_BASE & 0xF0000000), SCRATCH_BASE)

    def test_poc2_plane_compose_and_mini_tim_layout(self) -> None:
        from PIL import Image
        from build_two_bank_poc_002 import compose_cell_nibbles

        planes = []
        for k in range(4):
            plane = Image.new("L", (16, 16), 0)
            plane.putpixel((k, 0), 255)
            planes.append(plane)
        nibbles = compose_cell_nibbles(planes)
        self.assertEqual([nibbles[0][k] for k in range(4)], [1, 2, 4, 8])
        self.assertEqual(sum(v for row in nibbles for v in row), 15)

    def test_poc2_builder_patch_matches_original_length(self) -> None:
        import json
        from build_two_bank_poc import assemble

        config = json.loads(
            (Path(__file__).resolve().parents[1] / "config/font_two_bank_poc_002.json").read_text(encoding="utf-8")
        )
        builder = config["builder_patch"]
        patch = assemble(builder["instructions"], int(builder["runtime_start"], 16))
        self.assertEqual(len(patch), len(bytes.fromhex(builder["expected_hex"])))
        self.assertEqual(len(patch), 108)

    def test_font_coverage_probe_classifies_glyphs(self) -> None:
        from PIL import ImageFont
        from audit_font_coverage import probe_glyph

        font_path = Path(r"g:/Downloads/Korean Traslate Project/Font/Room No.703.ttf")
        if not font_path.exists():
            self.skipTest("project font is not available")
        font = ImageFont.truetype(str(font_path), 16)
        ok = probe_glyph("한", font, 128)
        self.assertEqual(ok["status"], "OK")
        self.assertGreater(ok["ink_pixels"], 0)
        self.assertEqual(probe_glyph("뷁", font, 128)["status"], "EMPTY_BBOX")

    def test_diff_rects_reports_changed_bounds(self) -> None:
        from analyze_vram_occupancy import diff_rects

        width = 64
        before = bytes(64 * 4 * 2)
        after = bytearray(before)
        after[2 * (width * 1 + 3)] = 0xFF
        after[2 * (width * 2 + 10) + 1] = 0x7F
        diff = diff_rects(bytes(before), bytes(after), width)
        self.assertEqual(diff["changed_words"], 2)
        self.assertEqual(diff["bounds_in_page"], [3, 1, 8, 2])
        self.assertIsNone(diff_rects(bytes(before), bytes(before), width))


class CallbackDispatchTests(unittest.TestCase):
    def test_charselect_catalog_keeps_verified_targets_and_protected_labels(self) -> None:
        import json

        self.assertEqual(SMALL_FONT_PATH.name, "Galmuri9.ttf")
        self.assertEqual(SMALL_FONT_SIZE, 8)

        project = Path(__file__).resolve().parents[2]
        config = json.loads(
            (project / "workspace/config/charselect_labels.json").read_text(encoding="utf-8")
        )
        targets = {
            item["disc_offset"]: (item["japanese"], item["korean"], item["width_px"])
            for item in config["labels"]
        }
        self.assertEqual(
            targets,
            {
                "0x4FA98": ("チーム", "Team", 32),
                "0x4D6D8": ("カラー", "Color", 28),
            },
        )
        excluded = {item["disc_offset"]: item["text"] for item in config["excluded"]}
        self.assertEqual(
            excluded,
            {
                "0x52F98": "PRESS",
                "0x52ED8": "START",
                "0x4CB9C": "COM",
                "0x53058": "EXIT",
            },
        )
        self.assertTrue(targets.keys().isdisjoint(excluded))

        hagecho_path = project / "extracted/HAGECHO.BIN"
        if not hagecho_path.exists():
            self.skipTest("extracted HAGECHO.BIN is not available")
        hagecho = hagecho_path.read_bytes()
        for item in config["labels"]:
            offset = int(item["disc_offset"], 16)
            length = item["stride_bytes"] * item["height_rows"]
            self.assertEqual(
                hashlib.sha256(hagecho[offset:offset + length]).hexdigest(),
                item["sha256"],
            )

    def test_ps1_ram_pointer_normalization(self) -> None:
        self.assertEqual(normalize_ram_pointer(0x00001234), 0x80001234)
        self.assertEqual(normalize_ram_pointer(0x80001234), 0x80001234)
        self.assertEqual(normalize_ram_pointer(0xA0001234), 0x80001234)
        self.assertEqual(normalize_ram_pointer(0x807FFEA0), 0x807FFEA0)
        self.assertEqual(normalize_ram_pointer(0xA07FFEA0), 0x807FFEA0)
        self.assertIsNone(normalize_ram_pointer(0x80800000))
        self.assertIsNone(normalize_ram_pointer(0x12345678))
        self.assertEqual(
            normalize_readable_pointer(0x1F800174),
            (0x1F800174, 0x1F800400, "scratchpad"),
        )

    def test_register_read_reissues_request_after_extra_stop_packet(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.replies = [
                    b"T05thread:01;",
                    bytes(32 * 4).hex().encode("ascii") + b"xxxxxxxx" * 41,
                ]
                self.requests = []

            def send_packet(self, payload: str):
                self.requests.append(payload)
                return self.replies.pop(0)

        client = FakeClient()
        self.assertEqual(read_regs(client), [0] * 32)
        self.assertEqual(client.requests, ["g", "g"])

    def test_callback_dispatch_abi_matches_supported_ara(self) -> None:
        import json

        project = Path(__file__).resolve().parents[2]
        ara_path = project / "extracted/ARA.BIN"
        if not ara_path.exists():
            self.skipTest("extracted ARA.BIN is not available")
        manifest = json.loads((project / "workspace/config/source_manifest.json").read_text(encoding="utf-8"))
        report = analyze_callback_dispatch(
            ara_path.read_bytes(), manifest["expected_files"]["ARA.BIN"]["sha256"]
        )
        self.assertEqual(report["status"], "ABI_CONFIRMED_RUNTIME_IDENTITY_PENDING")
        self.assertEqual(report["dispatcher"]["direct_callers"], ["0x8008C890"])
        self.assertIn("directory walker", report["assessment"]["rejected"])

    def test_callback_loadimage_extracts_rect_and_unique_location(self) -> None:
        rect = struct.pack("<4h", 10, 20, 6, 24)
        signature = bytes(range(32))
        trace = {"calls": [{
            "index": 7,
            "elapsed_s": 1.25,
            "callback": {"value": "0x8008C2D8"},
            "payload": {"hex": rect.hex()},
            "callback_argument": {"value": "0x80100000", "hex": signature.hex()},
        }]}
        uploads, located = extract_callback_loadimage(trace, {"HAGECHO": b"xx" + signature + b"yy"})
        self.assertEqual(uploads[0]["rect"], [10, 20, 6, 24])
        self.assertEqual(located[0]["offset"], "0x2")

    def test_eight_row_strip_encoder_preserves_fixed_box(self) -> None:
        encoded = encode_strip_4bpp("팀", 24, height=8)
        self.assertEqual(len(encoded), 24 * 8)
        self.assertTrue(any(encoded))


if __name__ == "__main__":
    unittest.main()
