"""Reusable artwork components and a dependency-free cache for Kodi widgets."""

import os
import struct
import tempfile
import zlib
from functools import lru_cache

from .colours import COLOURS, colour_label, normalise_colour


BUNDLE = "v7"
CHOICES = (
    ("action", "Action"), ("comedy", "Comedy"), ("crime", "Crime"),
    ("drama", "Drama"), ("horror", "Horror"), ("romance", "Romance"),
    ("sci_fi", "Sci-Fi"), ("fantasy", "Fantasy"), ("thriller", "Thriller"),
    ("mystery", "Mystery"), ("western", "Western"),
    ("documentary", "Documentary"), ("animation", "Animation"),
    ("mind_bending", "Mind-Bending"), ("superhero", "Superhero"),
    ("director", "Director"), ("actor", "Actor"),
    ("list", "List"), ("folder", "Folder"), ("movie", "Movie"), ("tv", "TV Show"),
    ("blank", "Blank"),
)
DEFAULT_COLOURS = {
    "action": "amber", "comedy": "gold", "crime": "turquoise", "drama": "mauve",
    "horror": "berry", "romance": "rose", "sci_fi": "blue", "fantasy": "violet",
    "thriller": "steel_blue", "mystery": "periwinkle", "western": "sand",
    "documentary": "sage", "animation": "peach", "mind_bending": "lilac",
    "superhero": "azure", "director": "slate", "actor": "plum",
    "list": "violet", "folder": "amber", "movie": "blue", "tv": "turquoise",
    "blank": "grey",
}


def components(root, key, kind, style, colour="default"):
    key = key if key in DEFAULT_COLOURS else "drama"
    palette = "grey" if style == "monochrome" else (
        colour if colour in COLOURS else DEFAULT_COLOURS[key]
    )
    base = os.path.join(root, "resources", "media", "list_art", BUNDLE)
    symbol = "" if key == "blank" else os.path.join(base, "white" if kind == "icon" else "landscape", key + ".png")
    background = "" if key != "blank" and kind == "icon" and style == "white" else os.path.join(
        base, "backgrounds", "icon" if kind == "icon" else "fanart", palette + ".png"
    )
    return symbol, background, palette


def _chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


def write_png(path, width, height, channels, pixels):
    """Write the unfiltered PNG subset used by our build and compositor."""
    colour_type = {1: 0, 3: 2, 4: 6}[channels]
    stride = width * channels
    if len(pixels) != stride * height:
        raise ValueError("Unexpected artwork pixel count")
    scanlines = b"".join(b"\0" + pixels[y:y + stride]
                         for y in range(0, len(pixels), stride))
    header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(scanlines, 6)) + _chunk(b"IEND", b""))
    folder = os.path.dirname(os.fspath(path))
    os.makedirs(folder, exist_ok=True)
    handle, pending = tempfile.mkstemp(prefix=".art-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.remove(pending)


@lru_cache(maxsize=2)
def _read_component(path):
    """Decode our bundled unfiltered PNGs; never used for downloaded artwork."""
    with open(path, "rb") as stream:
        data = stream.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid bundled artwork")
    offset, compressed, header = 8, [], None
    while offset + 12 <= len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        if len(payload) != size:
            raise ValueError("Incomplete bundled artwork")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.append(payload)
        elif kind == b"IEND":
            break
        offset += size + 12
    if not header or header[2] != 8 or any(header[4:]):
        raise ValueError("Unsupported bundled artwork encoding")
    width, height = header[:2]
    channels = {0: 1, 2: 3, 6: 4}.get(header[3])
    if not channels or not (0 < width <= 1920 and 0 < height <= 1080):
        raise ValueError("Unsupported bundled artwork dimensions")
    stride = width * channels
    raw = zlib.decompress(b"".join(compressed))
    if len(raw) != (stride + 1) * height or any(raw[::stride + 1]):
        raise ValueError("Bundled artwork requires unfiltered scanlines")
    return width, height, channels, b"".join(
        raw[y + 1:y + stride + 1] for y in range(0, len(raw), stride + 1)
    )


def rendered_source(root, profile, key, kind, style, colour="default"):
    """Create a normal image only for a displayed/saved artwork combination."""
    key = key if key in DEFAULT_COLOURS else "drama"
    symbol, background, palette = components(root, key, kind, style, colour)
    if not symbol:
        return background
    if not background:
        return symbol
    if not profile:
        raise ValueError("Artwork cache needs a Kodi profile")
    kind = "icon" if kind == "icon" else "fanart"
    target = os.path.join(profile, "bundled_art", BUNDLE,
                          "%s-%s-%s.png" % (kind, key, palette))
    if os.path.isfile(target):
        return target
    width, height, channels, pixels = _read_component(background)
    if channels != 3:
        raise ValueError("Artwork background must be RGB")
    if kind == "icon":
        sw, sh, sc, shape = _read_component(symbol)
        if sc != 4:
            raise ValueError("Artwork symbol must be RGBA")
        alpha, x, y = shape[3::4], 0, 0
    else:
        ow, oh, sc, overlay = _read_component(symbol)
        if sc != 4 or (ow, oh) != (width, height):
            raise ValueError("Artwork overlay must match the background")
        sw = sh = 512
        x, y = 1510 - sw // 2, 540 - sh // 2
        alpha = b"".join(overlay[((y + row) * ow + x) * 4 + 3:
                                 ((y + row) * ow + x + sw) * 4:4] for row in range(sh))
    if x + sw > width or y + sh > height:
        raise ValueError("Artwork symbol does not fit its background")
    result = bytearray(pixels)
    for row in range(sh):
        start = ((y + row) * width + x) * 3
        for column, opacity in enumerate(alpha[row * sw:(row + 1) * sw]):
            if not opacity:
                continue
            p = start + column * 3
            if opacity == 255:
                result[p:p + 3] = b"\xff\xff\xff"
            else:
                for channel in range(p, p + 3):
                    result[channel] += ((255 - result[channel]) * opacity + 127) // 255
    write_png(target, width, height, 3, result)
    return target
