#!/usr/bin/env python3
"""Generate Kodi-safe PNG controls for the Keyword Matching filter editor."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "plugin.video.curatr" / "resources" / "media" / "keyword_controls_v5"
PALETTE = {
    "genre": ((75, 38, 50), (255, 210, 217)),
    "person": ((48, 75, 98), (213, 238, 255)),
    "film": ((73, 54, 100), (232, 220, 255)),
    "number": ((88, 74, 38), (255, 236, 181)),
    "year": ((92, 57, 46), (255, 218, 201)),
    "runtime": ((56, 63, 102), (221, 226, 255)),
    "place": ((42, 85, 75), (210, 244, 234)),
}


def pencil(draw, colour, offset=(0, 0), width=5):
    ox, oy = offset
    draw.line((14 + ox, 34 + oy, 35 + ox, 13 + oy), fill=colour, width=width)
    draw.line((18 + ox, 38 + oy, 39 + ox, 17 + oy), fill=colour, width=width)
    draw.polygon(((11 + ox, 41 + oy), (15 + ox, 30 + oy), (22 + ox, 37 + oy)), fill=colour)
    draw.polygon(((35 + ox, 10 + oy), (42 + ox, 17 + oy), (45 + ox, 14 + oy), (38 + ox, 7 + oy)), fill=colour)


def edit_square(draw, colour, offset=(0, 0), compact=False):
    ox, oy = offset
    if compact:
        draw.line((10 + ox, 13 + oy, 10 + ox, 36 + oy, 34 + ox, 36 + oy), fill=colour, width=3)
        draw.line((10 + ox, 13 + oy, 27 + ox, 13 + oy), fill=colour, width=3)
        draw.line((18 + ox, 29 + oy, 35 + ox, 12 + oy), fill=colour, width=5)
        draw.polygon(((15 + ox, 33 + oy), (18 + ox, 24 + oy), (24 + ox, 30 + oy)), fill=colour)
        draw.polygon(((33 + ox, 9 + oy), (39 + ox, 15 + oy), (41 + ox, 13 + oy), (35 + ox, 7 + oy)), fill=colour)
    else:
        draw.line((10 + ox, 12 + oy, 10 + ox, 42 + oy, 41 + ox, 42 + oy), fill=colour, width=4)
        draw.line((10 + ox, 12 + oy, 32 + ox, 12 + oy), fill=colour, width=4)
        draw.line((19 + ox, 33 + oy, 40 + ox, 12 + oy), fill=colour, width=7)
        draw.polygon(((15 + ox, 38 + oy), (19 + ox, 27 + oy), (27 + ox, 35 + oy)), fill=colour)
        draw.polygon(((38 + ox, 8 + oy), (45 + ox, 15 + oy), (48 + ox, 12 + oy), (41 + ox, 5 + oy)), fill=colour)


def segment(background, foreground, symbol, focused=False):
    image = Image.new("RGBA", (48, 46), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (2, 2, 45, 43), radius=10,
        fill=(*background, 168 if focused else 112),
    )
    colour = (255, 255, 255, 255) if focused else (*foreground, 255)
    if symbol == "minus":
        draw.rounded_rectangle((13, 21, 35, 25), radius=2, fill=colour)
    return image


def header(focused=False):
    image = Image.new("RGBA", (54, 52), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if focused:
        draw.rounded_rectangle((2, 2, 51, 49), radius=12, fill=(75, 61, 99, 110))
    edit_square(draw, (255, 255, 255, 255) if focused else (189, 183, 199, 255), offset=(0, 1))
    return image


def plus(focused=False):
    image = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, 59, 59), radius=14,
        fill=(92, 89, 99, 168 if focused else 112),
    )
    return image


def header_action():
    return Image.new("RGBA", (170, 52), (0, 0, 0, 0))


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for kind, (background, foreground) in PALETTE.items():
        for symbol in ("minus", "text"):
            for state in ("normal", "focus"):
                segment(background, foreground, symbol, state == "focus").save(
                    OUTPUT / f"{kind}_{symbol}_{state}.png"
                )
    header_action().save(OUTPUT / "header_action.png")
    plus(False).save(OUTPUT / "plus_normal.png")
    plus(True).save(OUTPUT / "plus_focus.png")


if __name__ == "__main__":
    main()
