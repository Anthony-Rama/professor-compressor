"""Minimal MP4 byte builders that pass the relay's structural signature check."""


def box(kind: bytes, payload: bytes = b"") -> bytes:
    """One ISO BMFF box: a 32-bit size, a four-byte type, then the payload."""
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def mp4() -> bytes:
    """A small structurally valid MP4 with an mp41 compatible brand."""
    return (
        box(b"ftyp", b"isom" + bytes(4) + b"mp41")
        + box(b"moov", b"meta")
        + box(b"mdat", b"video")
    )


def valid_test_mp4() -> bytes:
    """A structurally valid MP4 with a nonzero minor version and two brands."""
    return (
        box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"iso2mp41")
        + box(b"moov", b"metadata")
        + box(b"mdat", b"video-data")
    )
