import tracemalloc
import unittest

from professor_compressor.media_validation import MAX_FTYP_BYTES, valid_mp4_signature


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


class Mp4SignatureTests(unittest.TestCase):
    def test_oversized_brand_table_has_bounded_allocations(self) -> None:
        video = box(b"ftyp", b"isom" + bytes(1024 * 1024)) + box(b"moov") + box(b"mdat")
        tracemalloc.start()
        try:
            self.assertFalse(valid_mp4_signature(video))
            self.assertLess(tracemalloc.get_traced_memory()[1], 64 * 1024)
        finally:
            tracemalloc.stop()

    def test_minor_version_is_not_a_brand(self) -> None:
        video = box(b"ftyp", b"fakeisom") + box(b"moov") + box(b"mdat", b"12345678")
        self.assertFalse(valid_mp4_signature(video))

    def test_compatible_brand_and_header_boundaries(self) -> None:
        for extended in (False, True):
            payload = b"fake" + bytes(4) + b"mp42"
            ftyp = (
                b"\x00\x00\x00\x01ftyp"
                + (16 + len(payload)).to_bytes(8, "big")
                + payload
                if extended
                else box(b"ftyp", payload)
            )
            self.assertTrue(valid_mp4_signature(ftyp + box(b"moov") + box(b"mdat")))
        for length in (9, MAX_FTYP_BYTES):
            self.assertFalse(
                valid_mp4_signature(
                    box(b"ftyp", b"isom" + bytes(length - 4))
                    + box(b"moov")
                    + box(b"mdat")
                )
            )

    def test_accepts_structural_mp4(self) -> None:
        video = (
            box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"iso2mp41")
            + box(b"moov", b"metadata")
            + box(b"mdat", b"video-data")
        )
        self.assertTrue(valid_mp4_signature(video))

    def test_rejects_filename_style_spoof(self) -> None:
        self.assertFalse(valid_mp4_signature(b"this is not an mp4"))

    def test_requires_movie_and_media_boxes(self) -> None:
        incomplete = box(b"ftyp", b"isom" + b"\x00" * 4) + box(b"mdat", b"video")
        self.assertFalse(valid_mp4_signature(incomplete))

    def test_rejects_unknown_brand(self) -> None:
        spoofed = (
            box(b"ftyp", b"fake" + b"\x00" * 4)
            + box(b"moov", b"metadata")
            + box(b"mdat", b"video")
        )
        self.assertFalse(valid_mp4_signature(spoofed))

    def test_rejects_truncated_box(self) -> None:
        truncated = (
            box(b"ftyp", b"isom" + b"\x00" * 4)
            + box(b"moov", b"metadata")
            + (100).to_bytes(4, "big")
            + b"mdatshort"
        )
        self.assertFalse(valid_mp4_signature(truncated))


if __name__ == "__main__":
    unittest.main()
