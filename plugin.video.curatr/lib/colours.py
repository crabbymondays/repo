"""Shared colour choices for window accents, menu backgrounds and artwork."""

import colorsys


def _tones(middle):
    rgb = tuple(int(middle[i:i + 2], 16) for i in (0, 2, 4))
    light = "".join("%02X" % round(channel + (255 - channel) * 0.27) for channel in rgb)
    dark = "".join("%02X" % round(channel * 0.64) for channel in rgb)
    return light, middle, dark


_PALETTES = {
    "amber": ("F2B45A", "B95D36", "573C38"),
    "gold": ("E7C66D", "B99943", "666046"),
    "turquoise": ("45CBCD", "0E8190", "194D5C"),
    "mauve": ("C88CA9", "9A597D", "573F59"),
    "berry": ("BA667A", "713B53", "433846"),
    "rose": ("EFA2B5", "C57292", "794B6E"),
    "blue": ("849FEC", "506DC0", "394D7B"),
    "violet": ("B4A1DE", "8A68B5", "504163"),
    "steel_blue": ("79AABC", "3F6C8B", "333F59"),
    "periwinkle": ("949ED2", "626691", "414059"),
    "sand": ("CEA173", "9F774E", "605042"),
    "sage": ("8BB8A7", "5C8C7E", "3D5A55"),
    "peach": ("F1B574", "CC7876", "755668"),
    "lilac": ("B08DE0", "7359AA", "483D64"),
    "azure": ("7FADD3", "4B7C9F", "37546B"),
    "slate": ("A9BCC8", "788F9C", "4B606B"),
    "plum": ("C5ACC9", "937C9D", "595064"),
    "grey": ("797979", "555555", "3C3C3C"),
    "red": _tones("B64B59"),
    "deep_blue": _tones("356FC2"),
    "deep_violet": _tones("7653B9"),
    "cyan": _tones("169CB4"),
    "teal": _tones("278F8A"),
    "green": _tones("2E8B69"),
    "pink": _tones("BE558C"),
}


def _wheel_order(key):
    if key in ("slate", "grey"):
        return 1, ("slate", "grey").index(key)
    rgb = tuple(int(_PALETTES[key][1][i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, _saturation, _value = colorsys.rgb_to_hsv(*rgb)
    return 0, (hue - 345 / 360) % 1


COLOURS = {key: _PALETTES[key] for key in sorted(_PALETTES, key=_wheel_order)}


def colour_label(colour):
    return str(colour or "default").replace("_", " ").title()


def normalise_colour(colour):
    colour = "grey" if colour == "neutral" else colour
    return colour if colour in COLOURS else "default"


BACKGROUND_COLOURS = tuple((key, colour_label(key)) for key in COLOURS)
