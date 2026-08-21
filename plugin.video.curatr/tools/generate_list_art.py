"""Generate curatr's deterministic list-art pack.

The shipped PNG files are rendered from simple geometry so every genre uses the
same proportions, visual weight and safe area.  This script is a maintainer
tool; Kodi only reads the generated files under resources/media/list_art.
"""

import os
from pathlib import Path
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "resources" / "media" / "list_art"

ART = {
    "action": ("Action", (184, 72, 38), (239, 139, 67)),
    "comedy": ("Comedy", (11, 82, 91), (225, 126, 81)),
    "crime": ("Crime", (23, 48, 78), (94, 135, 185)),
    "drama": ("Drama", (72, 38, 78), (158, 93, 150)),
    "horror": ("Horror", (61, 20, 31), (158, 47, 67)),
    "romance": ("Romance", (92, 36, 55), (213, 98, 130)),
    "sci_fi": ("Sci-Fi", (35, 31, 88), (108, 92, 201)),
    "fantasy": ("Fantasy", (27, 70, 63), (104, 177, 137)),
    "thriller": ("Thriller", (50, 43, 25), (201, 151, 53)),
    "mystery": ("Mystery", (20, 33, 65), (72, 92, 148)),
    "western": ("Western", (82, 52, 27), (191, 132, 66)),
    "documentary": ("Documentary", (45, 55, 66), (118, 141, 157)),
    "animation": ("Animation", (24, 67, 91), (72, 156, 200)),
    "mind_bending": ("Mind-Bending", (39, 30, 81), (58, 177, 186)),
    "superhero": ("Superhero", (28, 52, 94), (177, 58, 68)),
    "director": ("Director", (43, 43, 48), (180, 145, 75)),
    "actor": ("Actor", (31, 43, 61), (139, 151, 170)),
}


def save_png(image, target):
    """Write atomically so an interrupted build can never ship a zero-byte asset."""
    target = Path(target)
    temp = target.with_suffix(target.suffix + ".tmp")
    image.save(temp, format="PNG", compress_level=9)
    if not temp.exists() or temp.stat().st_size < 100:
        raise RuntimeError("Artwork render failed: %s" % target)
    os.replace(str(temp), str(target))


def line(draw, points, width, fill="white", joint="curve"):
    draw.line(points, fill=fill, width=width, joint=joint)


def glyph(draw, key, box, fill="white"):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    sw = max(8, int(min(w, h) * 0.065))

    if key == "crime":
        for inset in (0.08, 0.19, 0.30, 0.41):
            dx, dy = int(w * inset), int(h * inset)
            draw.arc((x0 + dx, y0 + dy, x1 - dx, y1 - dy // 2), 195, 520, fill=fill, width=sw)
        line(draw, [(cx, cy), (cx, y1 - int(h * .12))], sw, fill)
    elif key == "mystery":
        r = int(min(w, h) * .22)
        draw.ellipse((cx-r, y0+int(h*.15), cx+r, y0+int(h*.15)+2*r), fill=fill)
        draw.polygon([(cx-int(w*.15), cy), (cx+int(w*.15), cy), (cx+int(w*.24), y1-int(h*.12)), (cx-int(w*.24), y1-int(h*.12))], fill=fill)
    elif key == "sci_fi":
        r = int(min(w, h) * .27)
        draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=fill)
        draw.arc((x0+int(w*.04), cy-int(h*.16), x1-int(w*.04), cy+int(h*.16)), 170, 370, fill=fill, width=sw)
    elif key in ("comedy", "drama"):
        draw.rounded_rectangle((x0+int(w*.15), y0+int(h*.10), x1-int(w*.15), y1-int(h*.08)), radius=int(w*.12), outline=fill, width=sw)
        ey = y0 + int(h*.42)
        line(draw, [(cx-int(w*.18), ey), (cx-int(w*.08), ey)], sw, fill)
        line(draw, [(cx+int(w*.08), ey), (cx+int(w*.18), ey)], sw, fill)
        mouth = (x0+int(w*.30), y0+int(h*.48), x1-int(w*.30), y1-int(h*.17))
        draw.arc(mouth, 5 if key == "comedy" else 185, 175 if key == "comedy" else 355, fill=fill, width=sw)
    elif key == "horror":
        # Angular eye with a narrow vertical pupil.
        line(draw, [(x0+int(w*.08), cy), (cx, y0+int(h*.24)), (x1-int(w*.08), cy), (cx, y1-int(h*.24)), (x0+int(w*.08), cy)], sw, fill)
        draw.rounded_rectangle((cx-sw//2, cy-int(h*.18), cx+sw//2, cy+int(h*.18)), radius=sw//2, fill=fill)
    elif key == "romance":
        r = int(min(w, h) * .22)
        top = y0 + int(h * .18)
        draw.ellipse((cx-2*r, top, cx, top+2*r), fill=fill)
        draw.ellipse((cx, top, cx+2*r, top+2*r), fill=fill)
        draw.polygon([(cx-2*r, top+r), (cx+2*r, top+r), (cx, y1-int(h*.07))], fill=fill)
    elif key == "action":
        draw.polygon([(cx+int(w*.05), y0+int(h*.04)), (x0+int(w*.22), cy+int(h*.03)), (cx-int(w*.02), cy+int(h*.03)), (cx-int(w*.12), y1-int(h*.04)), (x1-int(w*.18), cy-int(h*.08)), (cx+int(w*.08), cy-int(h*.08))], fill=fill)
    elif key == "fantasy":
        draw.polygon([(cx, y0+int(h*.04)), (cx+int(w*.10), cy-int(h*.11)), (x1-int(w*.04), cy), (cx+int(w*.10), cy+int(h*.11)), (cx, y1-int(h*.04)), (cx-int(w*.10), cy+int(h*.11)), (x0+int(w*.04), cy), (cx-int(w*.10), cy-int(h*.11))], fill=fill)
    elif key == "thriller":
        r = int(min(w, h)*.31)
        draw.ellipse((cx-r, cy-r, cx+r, cy+r), outline=fill, width=sw)
        draw.ellipse((cx-sw, cy-sw, cx+sw, cy+sw), fill=fill)
        line(draw, [(cx, y0), (cx, cy-r//2)], sw, fill); line(draw, [(cx, cy+r//2), (cx, y1)], sw, fill)
        line(draw, [(x0, cy), (cx-r//2, cy)], sw, fill); line(draw, [(cx+r//2, cy), (x1, cy)], sw, fill)
    elif key == "western":
        draw.ellipse((x0+int(w*.05), cy+int(h*.12), x1-int(w*.05), cy+int(h*.34)), fill=fill)
        draw.polygon([(x0+int(w*.25), cy+int(h*.16)), (x0+int(w*.32), y0+int(h*.20)), (x1-int(w*.32), y0+int(h*.20)), (x1-int(w*.25), cy+int(h*.16))], fill=fill)
    elif key == "documentary":
        r = int(min(w,h)*.34)
        draw.ellipse((cx-r, cy-r, cx+r, cy+r), outline=fill, width=sw)
        for angle in range(0, 360, 60):
            import math
            a = math.radians(angle)
            b = math.radians(angle+38)
            draw.polygon([(cx,cy),(cx+int(r*.88*math.cos(a)),cy+int(r*.88*math.sin(a))),(cx+int(r*.88*math.cos(b)),cy+int(r*.88*math.sin(b)))], fill=fill)
        draw.ellipse((cx-int(r*.22), cy-int(r*.22), cx+int(r*.22), cy+int(r*.22)), fill=(0,0,0,0))
    elif key == "animation":
        line(draw, [(x0+int(w*.18), y1-int(h*.15)), (x1-int(w*.18), y0+int(h*.15))], sw*2, fill)
        draw.polygon([(x1-int(w*.18), y0+int(h*.15)), (x1-int(w*.05), y0+int(h*.05)), (x1-int(w*.12), y0+int(h*.24))], fill=fill)
        draw.arc((x0+int(w*.05), y0+int(h*.20), x1-int(w*.15), y1-int(h*.02)), 45, 155, fill=fill, width=sw)
    elif key == "mind_bending":
        # Clean interlocking impossible-loop mark.
        pts = [(x0+int(w*.14), cy), (cx-int(w*.04), y0+int(h*.15)), (x1-int(w*.14), cy), (cx+int(w*.04), y1-int(h*.15)), (x0+int(w*.14), cy)]
        line(draw, pts, sw*2, fill)
        line(draw, [(cx-int(w*.04), y0+int(h*.15)), (cx+int(w*.04), y1-int(h*.15))], sw, fill)
    elif key == "superhero":
        shield = [(cx, y0+int(h*.06)), (x1-int(w*.14), y0+int(h*.20)), (x1-int(w*.22), cy+int(h*.24)), (cx, y1-int(h*.05)), (x0+int(w*.22), cy+int(h*.24)), (x0+int(w*.14), y0+int(h*.20)), (cx, y0+int(h*.06))]
        line(draw, shield, sw*2, fill)
        line(draw, [(cx, y0+int(h*.16)), (cx, y1-int(h*.17))], sw, fill)
    elif key == "director":
        line(draw, [(x0+int(w*.22), y0+int(h*.17)), (x1-int(w*.22), y1-int(h*.17))], sw, fill)
        line(draw, [(x1-int(w*.22), y0+int(h*.17)), (x0+int(w*.22), y1-int(h*.17))], sw, fill)
        draw.rectangle((x0+int(w*.15), y0+int(h*.12), x1-int(w*.15), cy), outline=fill, width=sw)
        draw.rectangle((x0+int(w*.18), cy, x1-int(w*.18), cy+int(h*.18)), fill=fill)
    elif key == "actor":
        r = int(min(w,h)*.18)
        draw.ellipse((cx-r, y0+int(h*.10), cx+r, y0+int(h*.10)+2*r), fill=fill)
        draw.pieslice((x0+int(w*.16), cy-int(h*.02), x1-int(w*.16), y1+int(h*.30)), 180, 360, fill=fill)


def background(base, accent):
    img = Image.new("RGB", (1920, 1080), base)
    d = ImageDraw.Draw(img)
    blends = [tuple(int(base[i]*(1-t)+accent[i]*t) for i in range(3)) for t in (.22,.38,.55,.74)]
    # Broad layered curves inspired by curatr's abstract fanart, kept flat.
    for index, colour in enumerate(blends):
        inset = index * 95
        d.ellipse((1120+inset, -820+inset, 2600-inset//2, 650+inset//3), fill=colour)
        d.ellipse((-900+inset//2, 580+inset//3, 650+inset, 1900-inset//2), fill=colour)
    return img


def render():
    icons = OUT / "icons"
    fanart = OUT / "fanart"
    fanart_mono = OUT / "fanart_mono"
    icons.mkdir(parents=True, exist_ok=True)
    fanart.mkdir(parents=True, exist_ok=True)
    fanart_mono.mkdir(parents=True, exist_ok=True)
    for key, (_label, base, accent) in ART.items():
        icon = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        glyph(ImageDraw.Draw(icon), key, (76, 76, 436, 436))
        save_png(icon, icons / (key + ".png"))

        wide = background(base, accent)
        d = ImageDraw.Draw(wide)
        # A few faint, uniform orbit/evidence points add theme without clutter.
        faint = tuple(int(base[i]*.25 + accent[i]*.75) for i in range(3))
        for px, py, radius in ((1280,310,6),(1570,780,8),(1740,250,5)):
            d.ellipse((px-radius,py-radius,px+radius,py+radius), fill=faint)
        glyph(d, key, (1320, 300, 1740, 720))
        save_png(wide, fanart / (key + ".png"))

        mono = background((19, 21, 27), (116, 122, 136))
        md = ImageDraw.Draw(mono)
        for px, py, radius in ((1280,310,6),(1570,780,8),(1740,250,5)):
            md.ellipse((px-radius,py-radius,px+radius,py+radius), fill=(128,132,141))
        glyph(md, key, (1320, 300, 1740, 720))
        save_png(mono, fanart_mono / (key + ".png"))


if __name__ == "__main__":
    render()
