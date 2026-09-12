#!/usr/bin/env python3
"""Size the canonical menu icons and build their landscape widget versions."""

import argparse
import os
import xml.etree.ElementTree as ET
from io import BytesIO

import cairosvg
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.menu_art import MENU_BUNDLE  # noqa: E402

BUNDLE = ROOT / "resources/media/menu" / MENU_BUNDLE
GLYPH_SIZE = 340  # Matches the visible bounds of the bundled genre icons.


FONT_AWESOME = {
    "menu_privacy": "shield-halved", "menu_hidden": "eye-slash",
    "menu_usage": "database", "menu_activity": "bell", "menu_refresh": "arrows-rotate",
    "menu_fresh": "clock-rotate-left", "menu_random": "gift", "menu_templates": "bookmark",
    "menu_quick": "pizza-slice", "menu_all": "border-all", "menu_backup": "cloud",
    "menu_widget_folders": "folder-open", "menu_add_folder": "folder-plus",
    "menu_branching_v2": "shuffle", "menu_preferences": "heart-pulse",
    "menu_taste_v3": "fingerprint", "menu_save_results": "file-lines",
}


def badge_svg(search=False):
    # The page, circle size and baseline are shared by Browse and Create.
    badge = ('<circle cx="73" cy="74" r="6.5" fill="none" stroke="black" stroke-width="4.25"/>'
             '<path d="m78 79 5.5 5.5" stroke="black" stroke-width="4.25" stroke-linecap="round"/>') if search else (
             '<path d="M75 66V86M65 76H85" stroke="black" stroke-width="5" stroke-linecap="round"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<rect width="100" height="100" fill="black"/>'
            '<rect x="11" y="9" width="64" height="80" rx="10" fill="white"/>'
            '<path d="M23 27H62M23 43H62M23 59H45" stroke="black" stroke-width="6" stroke-linecap="round"/>'
            '<circle cx="75" cy="76" r="21" fill="white" stroke="black" stroke-width="4"/>'
            + badge + '</svg>')


def vector_mask(svg, cutouts=False):
    root = ET.fromstring(svg)
    view = list(map(float, root.attrib["viewBox"].split()))
    scale = 1200 / max(view[2:])
    png = cairosvg.svg2png(bytestring=svg.replace("currentColor", "white").encode(),
                          output_width=round(view[2] * scale), output_height=round(view[3] * scale))
    with Image.open(BytesIO(png)) as image:
        mask = image.convert("RGBA").getchannel("R" if cutouts else "A")
    return mask.crop(mask.getbbox())


def build(destination, source=None):
    source = Path(source) if source is not None else BUNDLE / "square"
    masks = []
    names = {p.name for p in source.glob("menu_*.png")}
    names.update(name + ".png" for name in FONT_AWESOME)
    names.update(("menu_create_v2.png", "menu_list.png"))
    for filename in sorted(names):
        stem = Path(filename).stem
        if stem in FONT_AWESOME:
            svg = (ROOT / "tools/icon_sources/fontawesome-7.0.1" / (FONT_AWESOME[stem] + ".svg")).read_text()
            shape = vector_mask(svg)
        elif stem in ("menu_create_v2", "menu_list"):
            shape = vector_mask(badge_svg(stem == "menu_list"), cutouts=True)
        else:
            with Image.open(source / filename) as image:
                alpha = image.convert("RGBA").getchannel("A")
                bounds = alpha.getbbox()
                if bounds is None:
                    raise ValueError("Empty menu icon: %s" % filename)
                shape = alpha.crop(bounds)
        if max(shape.size) != GLYPH_SIZE:
            scale = GLYPH_SIZE / max(shape.size)
            shape = shape.resize(
                (round(shape.width * scale), round(shape.height * scale)),
                Image.Resampling.LANCZOS,
            )
        masks.append((filename, shape))
    if not masks:
        raise ValueError("No canonical menu icons found in %s" % source)

    for style, size in (("square", (512, 512)), ("landscape", (960, 540))):
        folder = destination / style
        folder.mkdir(parents=True, exist_ok=True)
        for filename, shape in masks:
            image = Image.new("RGBA", size, (255, 255, 255, 0))
            alpha = Image.new("L", size)
            alpha.paste(shape, ((size[0] - shape.width) // 2, (size[1] - shape.height) // 2))
            image.putalpha(alpha)
            pending = folder / (filename + ".tmp")
            image.save(pending, format="PNG", optimize=True)
            os.replace(pending, folder / filename)
    print("Built %d menu images in %s" % (len(masks) * 2, destination))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=BUNDLE / "square")
    parser.add_argument("--output", type=Path, default=BUNDLE)
    args = parser.parse_args()
    build(args.output, args.source)
