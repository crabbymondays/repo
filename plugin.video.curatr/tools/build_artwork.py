#!/usr/bin/env python3
"""Build bundled genre artwork. Requires Pillow and CairoSVG; never runs in Kodi."""

import argparse
import sys
from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.bundled_art import BUNDLE, COLOURS, write_png

# Render white geometry and black cutouts into one alpha mask, reused by
# square and landscape artwork without runtime SVG support.
ICONS = {
    "action": '''<path d="M50 7 61 36 81 23 73 49 93 54 73 68 79 87 59 80 55 65 65 60 57 57 62 46 52 51 49 38 44 52 35 47 38 59 30 62 40 67 44 84 19 88 26 69 7 55 29 51 19 24 39 36Z" stroke="white" stroke-width="2" stroke-linejoin="round"/>''',
    "crime": '''<g fill="none" stroke="white" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="24" cy="68" r="18" stroke-width="9"/><circle cx="76" cy="68" r="18" stroke-width="9"/>
      <path d="M20 47V43Q20 39 24 39H27Q31 39 31 43V47M69 47V43Q69 39 73 39H76Q80 39 80 43V47" stroke-width="6"/>
      <path d="M26 39Q19 27 34 25M66 25Q81 27 74 39" stroke-width="4"/>
      <rect x="31" y="17" width="21" height="12" rx="5" stroke-width="4" transform="rotate(-18 41 23)"/>
      <rect x="49" y="17" width="21" height="12" rx="5" stroke-width="4" transform="rotate(18 59 23)"/>
      <path d="M47 23H53" stroke-width="4"/></g>''',
    "horror": '''<path d="M35 57 83 10Q86 7 86 12C85 36 67 55 48 70Z"/>
      <path d="M32 59 44 72 28 87Q23 92 18 87L14 83Q9 78 14 73Z"/>
      <path d="M82 43C79 49 74 55 74 60A8 8 0 0 0 90 60C90 55 85 49 82 43Z"/>''',
    "thriller": '''<path d="M40 17 77 10Q79 10 79 12V77L40 72Z"/>
      <circle cx="59" cy="35" r="5.5" fill="black"/>
      <path d="M50 52Q50 42 59 42Q68 42 68 52V62Q68 65 65 65L65 77H60L59 63 57 77H52V65Q50 65 50 62Z" fill="black"/>
      <path d="M39 76 53 79 35 94H8Z"/>''',
    "mystery": '''<circle cx="43" cy="42" r="29" fill="none" stroke="white" stroke-width="7"/>
      <path d="M66 65 82 82" fill="none" stroke="white" stroke-width="12" stroke-linecap="round"/>
      <path d="M34 33C36 23 54 24 54 34C54 41 43 42 43 49" fill="none" stroke="white" stroke-width="7" stroke-linecap="round"/>
      <circle cx="43" cy="61" r="4"/>''',
    "mind_bending": '''<path d="M47 18C47 8 28 8 26 22C15 21 9 31 14 40C3 46 3 60 14 66C9 78 18 86 29 84C33 98 47 93 47 82Z"/>
      <path d="M53 18C53 8 72 8 74 22C85 21 91 31 86 40C97 46 97 60 86 66C91 78 82 86 71 84C67 98 53 93 53 82Z"/>
      <path d="M19 54Q22 45 34 45M81 54Q78 45 66 45" fill="none" stroke="black" stroke-width="4.5" stroke-linecap="round"/>''',
    "animation": '''<g transform="rotate(-43 39 43)">
      <path d="M31 16Q31 11 36 11H42Q47 11 47 16V23H31Z"/>
      <path d="M31 27H47V65H31Z"/><path d="M31 69H47L39 87Z"/></g>
      <path d="M12 81C12 96 48 90 72 75" fill="none" stroke="white" stroke-width="5" stroke-linecap="round"/>
      <path d="M85 62 89 71 98 75 89 79 85 88 81 79 72 75 81 71Z"/>''',
    "sci_fi": '''<g transform="rotate(42 50 50)">
      <path d="M50 7C64 18 70 37 65 65Q63 70 60 73H40Q37 70 35 65C30 37 36 18 50 7Z"/>
      <circle cx="50" cy="35" r="7.5" fill="black"/>
      <path d="M29 42C20 47 17 59 19 72L29 64Z"/>
      <path d="M29 42C20 47 17 59 19 72L29 64Z" transform="translate(100 0) scale(-1 1)"/>
      <path d="M43 79H57C61 91 52 96 50 99C48 96 39 91 43 79Z"/></g>''',
    "fantasy": '''<path d="M12 37C26 33 41 38 48 45V84C38 77 25 75 12 77ZM52 45C59 38 74 33 88 37V77C75 75 62 77 52 84Z"/>
      <path d="M6 46V87Q27 83 43 88M94 46V87Q73 83 57 88" fill="none" stroke="white" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M50 9C60 22 39 21 50 35C30 24 53 20 50 9Z"/>''',
    "comedy": '''<path d="M24 18Q50 25 76 18Q82 16 82 23C82 59 69 81 54 88Q50 90 46 88C31 81 18 59 18 23Q18 16 24 18Z"/>
      <path d="M31 40Q36 47 42 40M58 40Q64 47 69 40" fill="none" stroke="black" stroke-width="7" stroke-linecap="round"/>
      <path d="M36 58Q50 65 64 58Q68 56 67 62C63 82 37 82 33 62Q32 56 36 58Z" fill="black"/>''',
    "drama": '''<g transform="translate(35 25) rotate(11 29 29) scale(.67)">
      <path d="M24 18Q50 25 76 18Q82 16 82 23C82 59 69 81 54 88Q50 90 46 88C31 81 18 59 18 23Q18 16 24 18Z"/>
      <path d="M31 40Q36 47 42 40M58 40Q64 47 69 40" fill="none" stroke="black" stroke-width="7" stroke-linecap="round"/>
      <path d="M34 71C35 52 65 52 66 71Q66 75 62 73Q50 66 38 73Q34 75 34 71Z" fill="black"/>
      </g><g transform="translate(-1 -1) rotate(-12 40 42) scale(.74)">
      <path d="M24 18Q50 25 76 18Q82 16 82 23C82 59 69 81 54 88Q50 90 46 88C31 81 18 59 18 23Q18 16 24 18Z" stroke="black" stroke-width="7" stroke-linejoin="round"/>
      <path d="M31 40Q36 47 42 40M58 40Q64 47 69 40" fill="none" stroke="black" stroke-width="7" stroke-linecap="round"/>
      <path d="M36 58Q50 65 64 58Q68 56 67 62C63 82 37 82 33 62Q32 56 36 58Z" fill="black"/></g>''',
    "romance": '''<path d="M50 89C43 84 9 57 9 34C9 11 36 6 50 24C64 6 91 11 91 34C91 57 57 84 50 89Z"/>''',
    "western": '''<path d="M24 12Q27 10 30 12L37 15Q40 17 37 23C19 61 33 75 50 75C67 75 81 61 63 23Q60 17 63 15L70 12Q73 10 76 12C108 63 85 95 50 95C15 95-8 63 24 12Z"/>
      <g fill="black"><circle cx="20" cy="37" r="3.7"/><circle cx="20" cy="57" r="3.7"/><circle cx="80" cy="37" r="3.7"/><circle cx="80" cy="57" r="3.7"/></g>
      <path d="M50 31 54 42 66 43 57 51 60 63 50 56 40 63 43 51 34 43 46 42Z" stroke="white" stroke-width="1" stroke-linejoin="round"/>''',
    "documentary": '''<path d="M18 37V29Q18 23 24 23H49" fill="none" stroke="white" stroke-width="9" stroke-linecap="round"/>
      <rect x="7" y="36" width="57" height="41" rx="9"/>
      <path d="M69 46 90 34Q95 31 95 37V76Q95 81 90 78L69 66Z"/>
      <path d="M20 49H36" fill="none" stroke="black" stroke-width="6" stroke-linecap="round"/>''',
    "superhero": '''<path d="M6 45C6 24 21 19 38 26Q50 32 62 26C79 19 94 24 94 45C94 68 73 76 56 61Q50 56 44 61C27 76 6 68 6 45Z"/>
      <path d="M19 42C26 35 37 39 42 44C37 52 25 53 19 46ZM81 42C74 35 63 39 58 44C63 52 75 53 81 46Z" fill="black"/>''',
    "actor": '''<path d="M36 18Q50 22 64 18L91 86Q92 89 89 90Q50 97 11 90Q8 89 9 86Z"/>
      <ellipse cx="50" cy="12" rx="12" ry="3.5"/>
      <path d="M25 96Q25 84 38 81L40 73C36 68 35 62 35 57C35 41 58 38 63 50L61 54 64 60 67 64Q67 66 63 66V72Q63 76 54 76V82Q71 85 72 96Z" fill="black"/>''',
    "director": '''<path d="M14 39 83 16Q89 13 89 20V80Q89 87 83 84L14 61Z"/>
      <rect x="8" y="38" width="11" height="24" rx="4"/>
      <path d="M28 62 27 70Q27 74 31 75L42 78Q47 79 48 74L50 69" fill="none" stroke="white" stroke-width="6" stroke-linejoin="round"/>''',
}

GENERAL_ICONS = {"list": "list-ul", "folder": "folder-open", "movie": "film", "tv": "tv"}


def raster(svg, width, height):
    data = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width, output_height=height)
    with Image.open(BytesIO(data)) as image:
        return image.convert("RGBA")


def glyph_mask(key, icons=None):
    geometry = (ICONS if icons is None else icons)[key]
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<rect width="100" height="100" fill="black"/>'
           '<g fill="white">' + geometry + '</g></svg>')
    mask = raster(svg, 1200, 1200).getchannel("R")
    return mask.crop(mask.getbbox())


def fitted_mask(mask, size):
    scale = size / max(mask.size)
    return mask.resize((round(mask.width * scale), round(mask.height * scale)), Image.Resampling.LANCZOS)


def background(palette, fanart=False):
    light, middle, dark = palette
    width, height = (1920, 1080) if fanart else (512, 512)
    waves = '''<g fill="url(#ribbon)">
      <path opacity=".38" d="M0 95C165 640 813 946 1920 1080H0Z"/>
      <path opacity=".42" d="M0 403C331 819 1041 1021 1920 1080H0Z"/>
      <path opacity=".33" d="M0 684C457 998 1220 1054 1920 1080H0Z"/>
      <path opacity=".24" d="M0 0H650Q380 200 0 215Z"/></g>''' if fanart else ""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">
      <defs><linearGradient id="base" x2="100%" y2="100%">
        <stop stop-color="#{light}"/><stop offset=".48" stop-color="#{middle}"/>
        <stop offset="1" stop-color="#{dark}"/>
      </linearGradient><linearGradient id="ribbon" x2="100%" y2="80%">
        <stop stop-color="#{light}"/><stop offset="1" stop-color="#{middle}"/>
      </linearGradient></defs><rect width="100%" height="100%" fill="url(#base)"/>{waves}</svg>'''
    return raster(svg, width, height).convert("RGB")


def build(destination):
    masks = {key: glyph_mask(key) for key in ICONS}
    for key, name in GENERAL_ICONS.items():
        svg = (ROOT / "tools/icon_sources/fontawesome-7.0.1" / (name + ".svg")).read_text()
        image = raster(svg.replace("currentColor", "white"), 1200, 1200)
        mask = image.getchannel("A")
        masks[key] = mask.crop(mask.getbbox())
    for key, mask in masks.items():
        white = Image.new("RGBA", (512, 512), (255, 255, 255, 0))
        alpha = Image.new("L", (512, 512))
        shape = fitted_mask(mask, 340)
        alpha.paste(shape, ((512 - shape.width) // 2, (512 - shape.height) // 2))
        white.putalpha(alpha)
        write_png(destination / "white" / (key + ".png"), 512, 512, 4, white.tobytes())
        shape = fitted_mask(mask, 370)
        wide = Image.new("RGBA", (1920, 1080), (255, 255, 255, 0))
        alpha = Image.new("L", wide.size)
        alpha.paste(shape, (round(1510 - shape.width / 2), round(540 - shape.height / 2)))
        wide.putalpha(alpha)
        write_png(destination / "landscape" / (key + ".png"), wide.width, wide.height, 4, wide.tobytes())
    for name, palette in COLOURS.items():
        for kind in ("icon", "fanart"):
            image = background(palette, fanart=kind == "fanart")
            write_png(destination / "backgrounds" / kind / (name + ".png"),
                      image.width, image.height, 3, image.tobytes())
    print(f"Built {len(masks)} symbols and {len(COLOURS)} shared palettes in {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "resources" / "media" / "list_art" / BUNDLE)
    build(parser.parse_args().output)
