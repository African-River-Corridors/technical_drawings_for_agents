"""Raster images (clause 9.10): read the size of a JPEG file, with no imaging library.

A reader needs only the frame header (SOF) of the file: the width, the height, the number of
components and the sample precision. The pixels are never decoded. The SVG and the PDF carry the
JPEG bytes as they are (clause 15).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# SOF markers of baseline, extended and progressive DCT (not lossless, not arithmetic coding)
_SOF = {0xC0, 0xC1, 0xC2}
_NO_LENGTH = {0x01, *range(0xD0, 0xD8)}  # TEM and RSTn have no length field


@dataclass(frozen=True)
class JpegInfo:
    width_px: int
    height_px: int
    components: int  # 1 grey, 3 colour


def jpeg_info(data: bytes):
    """9.10.1: the frame header of a JPEG with 8-bit samples and 1 or 3 components, or None."""
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in _NO_LENGTH:
            i += 2
            continue
        if marker in (0xD9, 0xDA):  # end of image, or start of scan before any frame header
            return None
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if length < 2 or i + 2 + length > len(data):
            return None
        if marker in _SOF:
            seg = data[i + 4 : i + 2 + length]
            if len(seg) < 6:
                return None
            precision, height, width, n = seg[0], int.from_bytes(seg[1:3], "big"), int.from_bytes(seg[3:5], "big"), seg[5]
            if precision != 8 or n not in (1, 3) or width == 0 or height == 0:
                return None
            return JpegInfo(width, height, n)
        if 0xC3 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return None  # another kind of frame: lossless or arithmetic coding
        i += 2 + length
    return None


def resolve(path: str, base_dir) -> Path:
    return Path(base_dir or ".") / path


def read(path: str, base_dir):
    """The bytes, the SHA-256 and the frame header of an image file; ``None`` parts when unreadable."""
    try:
        data = resolve(path, base_dir).read_bytes()
    except OSError:
        return None, None, None
    return data, hashlib.sha256(data).hexdigest(), jpeg_info(data)


def load_images(doc: dict, base_dir) -> dict[str, bytes]:
    """The image bytes of a document, keyed by SHA-256, for the renderers (clause 15)."""
    out = {}
    for vp in doc.get("viewports", []):
        for e in vp.get("entities", []):
            if e.get("type") == "image":
                data, digest, _ = read(e["path"], base_dir)
                if data is not None and digest == e["sha256"]:
                    out[digest] = data
    return out
