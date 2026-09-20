MP4_BRANDS = {
    b"avc1",
    b"dash",
    b"iso2",
    b"iso3",
    b"iso4",
    b"iso5",
    b"iso6",
    b"isom",
    b"M4V ",
    b"mp41",
    b"mp42",
    b"MSNV",
}


def valid_mp4_signature(data: bytes) -> bool:
    """Return whether data has a complete, plausible ISO-BMFF MP4 structure."""
    if len(data) < 32:
        return False

    position = 0
    boxes: set[bytes] = set()
    box_count = 0
    recognized_brand = False
    while position + 8 <= len(data) and box_count < 10_000:
        size = int.from_bytes(data[position:position + 4], "big")
        kind = data[position + 4:position + 8]
        header_size = 8
        if size == 1:
            if position + 16 > len(data):
                return False
            size = int.from_bytes(data[position + 8:position + 16], "big")
            header_size = 16
        elif size == 0:
            size = len(data) - position

        if size < header_size or position + size > len(data):
            return False
        if position == 0:
            if kind != b"ftyp" or size < header_size + 8:
                return False
            payload = data[position + header_size:position + size]
            brands = {payload[index:index + 4] for index in range(0, len(payload), 4)}
            recognized_brand = bool(brands & MP4_BRANDS)

        boxes.add(kind)
        position += size
        box_count += 1
        if position == len(data):
            break

    return (
        position == len(data)
        and recognized_brand
        and {b"ftyp", b"moov", b"mdat"}.issubset(boxes)
    )
