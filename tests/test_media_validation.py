import unittest

from professor_compressor.media_validation import valid_mp4_signature


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


class Mp4SignatureTests(unittest.TestCase):
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
