#!/usr/bin/env python3
"""Build menu PNGs from the same mask renderer as the genre artwork."""

import argparse
import sys
from pathlib import Path

from PIL import Image
from build_artwork import fitted_mask, glyph_mask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.menu_art import MENU_BUNDLE  # noqa: E402


def stack(lines):
    return '''<rect x="9" y="15" width="65" height="47" rx="8"/>
      <rect x="18" y="26" width="66" height="47" rx="8" stroke="black" stroke-width="5"/>
      <rect x="29" y="39" width="64" height="46" rx="8" stroke="black" stroke-width="5"/>''' + ''.join(
        '<path d="M42 %sH79" stroke="black" stroke-width="5" stroke-linecap="round"/>' % y
        for y in lines
    )


FOLDER = '''<path d="M10 24Q10 18 16 18H34Q37 18 40 21L47 28H80Q86 28 86 34V73H10Z"/>
  <path d="M7 43Q6 38 12 38H32L39 31H51L58 38H90Q96 38 94 45L86 78Q85 83 79 83H18Q12 83 11 78Z"
  stroke="black" stroke-width="4" stroke-linejoin="round"/>'''
BADGE = '''<circle cx="76" cy="74" r="21" stroke="black" stroke-width="5"/>
  <path d="M76 63V85M65 74H87" fill="none" stroke="black" stroke-width="6" stroke-linecap="round"/>'''
SLIDERS = '''<g stroke="white" stroke-width="7" stroke-linecap="round">
  <path d="M12 23H88M12 50H88M12 77H88"/></g>
  <circle cx="32" cy="23" r="9"/><circle cx="69" cy="50" r="9"/><circle cx="43" cy="77" r="9"/>'''
GEAR = ''.join('<rect x="42" y="5" width="16" height="24" rx="5" transform="rotate(%d 50 50)"/>' % angle
               for angle in range(0, 360, 45))

ICONS = {
    "menu_my_lists": stack((52, 63, 74)),
    "menu_list": '''<rect x="13" y="10" width="57" height="74" rx="8"/>
      <path d="M26 29H56M26 43H56M26 57H42" stroke="black" stroke-width="6" stroke-linecap="round"/>
      <circle cx="67" cy="64" r="22" fill="black" stroke="black" stroke-width="5"/>
      <circle cx="67" cy="64" r="17" fill="none" stroke="white" stroke-width="6"/>
      <path d="M81 80 92 91" stroke="black" stroke-width="13" stroke-linecap="round"/>
      <path d="M81 80 92 91" stroke="white" stroke-width="8" stroke-linecap="round"/>''',
    "menu_create_v2": '''<rect x="15" y="9" width="58" height="79" rx="9"/>
      <path d="M28 29H59M28 44H59M28 59H42" stroke="black" stroke-width="6" stroke-linecap="round"/>''' + BADGE,
    "menu_manage": SLIDERS,
    "menu_widget_folders": FOLDER,
    "menu_add_folder": FOLDER + BADGE,
    "menu_manage_folders": FOLDER + '''<g stroke="black" stroke-width="3.8" stroke-linecap="round">
      <path d="M29 50H75M29 61H75M29 72H75"/></g><g fill="black">
      <circle cx="42" cy="50" r="5"/><circle cx="63" cy="61" r="5"/><circle cx="46" cy="72" r="5"/></g>''',
    "menu_usage": '''<rect x="12" y="56" width="18" height="32" rx="5"/>
      <rect x="41" y="34" width="18" height="54" rx="5"/>
      <rect x="70" y="12" width="18" height="76" rx="5"/>''',
    "menu_backup": '''<path d="M20 10H61L80 29V86Q80 91 74 91H20Q14 91 14 85V16Q14 10 20 10Z"/>
      <path d="M61 10V29H80" fill="none" stroke="black" stroke-width="4" stroke-linejoin="round"/>
      <g stroke="black" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round">
      <path d="M34 76V42M25 52 34 42 43 52M60 42V76M51 66 60 76 69 66"/></g>''',
    "menu_explore": '''<circle cx="50" cy="50" r="34" fill="none" stroke="white" stroke-width="7"/>
      <path d="M43 16 50 4 57 16M84 43 96 50 84 57M43 84 50 96 57 84M16 43 4 50 16 57Z"
      stroke="white" stroke-width="2" stroke-linejoin="round"/>
      <path d="M65 28 55 56 28 70 39 43Z" stroke="white" stroke-width="3" stroke-linejoin="round"/>
      <circle cx="46" cy="49" r="5" fill="black"/>''',
    "menu_taste_v3": '''<g fill="none" stroke="white" stroke-width="6.5" stroke-linecap="round">
      <path d="M11 48C11-2 89-2 89 48"/>
      <path d="M14 66Q24 61 24 48C24 14 76 14 76 48Q76 72 68 86"/>
      <path d="M23 80Q37 70 37 48C37 31 63 31 63 48Q63 76 51 93"/>
      <path d="M50 48Q50 76 37 89"/></g>''',
    "menu_settings": GEAR + '<circle cx="50" cy="50" r="34"/><circle cx="50" cy="50" r="16" fill="black"/>',
    "menu_privacy": '''<path d="M50 7 85 20V49C85 70 70 84 50 94C30 84 15 70 15 49V20Z"
      fill="none" stroke="white" stroke-width="7" stroke-linejoin="round"/>
      <path d="M50 7V94C30 84 15 70 15 49V20Z"/>''',
    "menu_refresh": '''<g fill="none" stroke="white" stroke-width="8" stroke-linecap="round">
      <path d="M17 40C25 7 74 7 84 38M83 60C75 93 26 93 16 62"/></g>
      <path d="M70 36 88 48 95 27ZM30 64 12 52 5 73Z" stroke="white" stroke-width="2" stroke-linejoin="round"/>''',
    "menu_quick": '''<path d="M45 9H74L52 43H73L29 92 38 58H16Z" stroke="white" stroke-width="2" stroke-linejoin="round"/>''',
    "menu_templates": '''<path d="M24 10H76Q83 10 83 17V89Q83 93 79 90L50 70 21 90Q17 93 17 89V17Q17 10 24 10Z"/>''',
    "menu_all": stack((54, 70)),
    "menu_fresh": '''<path d="M25 24C51 0 91 20 90 53C90 93 31 108 12 64"
      fill="none" stroke="white" stroke-width="7" stroke-linecap="round"/>
      <path d="M15 13 36 32 12 36Z" stroke="white" stroke-width="2" stroke-linejoin="round"/>
      <path d="M52 30V55L68 68" fill="none" stroke="white" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>''',
    "menu_random": '''<path d="M50 66 23 55 7 66 36 78ZM50 66 77 55 93 66 64 78Z"/>
      <path d="M50 63 24 52 10 43 32 36 50 47 68 36 90 43 76 52Z"/>
      <path d="M22 75 36 82 47 75V96L22 84ZM78 75 64 82 53 75V96L78 84Z"/>
      <path d="M40 17C40 4 63 4 63 18C63 26 51 28 51 35" fill="none" stroke="white" stroke-width="7" stroke-linecap="round"/>
      <circle cx="51" cy="45" r="4"/>''',
    "menu_hidden": '''<path d="M8 50Q50 7 92 50Q50 93 8 50Z" fill="none" stroke="white" stroke-width="7" stroke-linejoin="round"/>
      <circle cx="50" cy="50" r="13"/><path d="M19 18 81 82" stroke="black" stroke-width="15" stroke-linecap="round"/>
      <path d="M19 18 81 82" stroke="white" stroke-width="7" stroke-linecap="round"/>''',
    "menu_branching_v2": '''<g fill="none" stroke="white" stroke-width="7" stroke-linecap="round" stroke-linejoin="round">
      <path d="M10 50H33C50 50 48 22 65 22H84M33 50C50 50 48 78 65 78H84M74 11 85 22 74 33M74 67 85 78 74 89"/></g>''',
}


def build(destination):
    for style in ("square", "landscape"):
        (destination / style).mkdir(parents=True, exist_ok=True)
    for key in ICONS:
        shape = fitted_mask(glyph_mask(key, ICONS), 340)
        for style, size in (("square", (512, 512)), ("landscape", (960, 540))):
            image = Image.new("RGBA", size, (255, 255, 255, 0))
            alpha = Image.new("L", size)
            alpha.paste(shape, ((size[0] - shape.width) // 2, (size[1] - shape.height) // 2))
            image.putalpha(alpha)
            image.save(destination / style / (key + ".png"), optimize=True)
    print("Built %d menu images in %s" % (len(ICONS) * 2, destination))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "resources/media/menu" / MENU_BUNDLE)
    build(parser.parse_args().output)
