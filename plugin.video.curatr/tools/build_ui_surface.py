"""Build theme-neutral surface finishes; Pillow is used only when building."""

from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageDraw


def _rim(mask):
    inner = Image.new("L", mask.size)
    inner.paste(mask.resize((mask.width - 2, mask.height - 2), Image.Resampling.LANCZOS), (1, 1))
    return ImageChops.subtract(mask, inner)


def _surface(mask, target, edge=False):
    height = mask.height
    shade = Image.new("L", mask.size)
    def luminance(y):
        top = max(0.0, 1.0 - y / 10.0) ** 2
        bottom = max(0.0, 1.0 - (height - 1 - y) / 12.0) ** 2
        return round(246 + 9 * top - 34 * bottom)
    shade.putdata([luminance(y) for y in range(height) for _ in range(mask.width)])
    if edge:
        shade = Image.composite(Image.new("L", mask.size, 255), shade, _rim(mask))
    shade.putalpha(mask)
    shade.save(target, optimize=True)


def build():
    root = Path(__file__).resolve().parents[1]
    media = root / "resources" / "media"
    with Image.open(media / "rounded_rect_v1.png") as original:
        mask = original.convert("RGBA").getchannel("A")
    target = media / "rounded_surface_v3.png"
    _surface(mask, target)
    _surface(mask, media / "rounded_control_v3.png", edge=True)

    outline = Image.new("LA", mask.size, (255, 0))
    outline.putalpha(_rim(mask))
    outline.save(media / "rounded_outline_v3.png", optimize=True)
    inner = Image.new("L", mask.size)
    inner.paste(mask.resize((mask.width - 4, mask.height - 4), Image.Resampling.LANCZOS), (2, 2))
    focus = Image.new("LA", mask.size, (255, 0))
    focus.putalpha(ImageChops.subtract(mask, inner))
    focus.save(media / "rounded_focus_outline_v1.png", optimize=True)

    padding = 12
    size = (mask.width + 2 * padding, mask.height + 2 * padding)
    body = Image.new("L", size)
    body.paste(mask, (padding, padding))
    shifted = Image.new("L", size)
    shifted.paste(mask, (padding, padding + 3))
    shadow_alpha = ImageChops.subtract(shifted.filter(ImageFilter.GaussianBlur(3)), body)
    shadow_alpha = shadow_alpha.point(lambda value: round(value * 0.46))
    shadow = Image.new("LA", size, (0, 0))
    shadow.putalpha(shadow_alpha)
    shadow.save(media / "rounded_shadow_v3.png", optimize=True)

    for source, name in (("artwork_card_square_v2.png", "artwork_surface_square_v3.png"),
                         ("artwork_card_landscape_v4.png", "artwork_surface_landscape_v3.png")):
        with Image.open(media / source) as original:
            _surface(original.getchannel("A"), media / name, edge=True)
    with Image.open(media / "artwork_card_square_v2.png") as source:
        flat = Image.new("LA", source.size, (255, 0))
        flat.putalpha(source.getchannel("A"))
        flat.save(media / "list_artwork_frame_flat_v2.png", optimize=True)
    for name in ("left", "right", "middle"):
        if name == "middle":
            chip_mask = Image.new("L", (32, 60), 255)
        else:
            with Image.open(root / "tools" / "ui_sources" / ("chip_round_%s.png" % name)) as source:
                chip_mask = source.convert("RGBA").getchannel("A").resize((14, 60), Image.Resampling.LANCZOS)
        _surface(chip_mask, media / ("chip_%s_finish_v1.png" % name))
        inner = Image.new("L", chip_mask.size)
        if name == "left":
            inner.paste(chip_mask.resize((chip_mask.width, chip_mask.height - 2), Image.Resampling.LANCZOS), (1, 1))
        elif name == "right":
            inner.paste(chip_mask.resize((chip_mask.width, chip_mask.height - 2), Image.Resampling.LANCZOS), (-1, 1))
        else:
            inner.paste(Image.new("L", (chip_mask.width, chip_mask.height - 2), 255), (0, 1))
        edge = Image.new("LA", chip_mask.size, (255, 0))
        edge.putalpha(ImageChops.subtract(chip_mask, inner))
        edge.save(media / ("chip_%s_edge_v1.png" % name), optimize=True)
    chip = Image.new("LA", (160, 60), (255, 0))
    chip_alpha = Image.new("L", chip.size)
    chip_alpha.putdata([round(13 * max(0, (0.7 * x / 159 + 0.3 * y / 59 - 0.3) / 0.7) ** 1.6)
                       for y in range(60) for x in range(160)])
    clip = Image.new("L", chip.size)
    ImageDraw.Draw(clip).rounded_rectangle((0, 0, 159, 59), radius=14, fill=255)
    chip.putalpha(ImageChops.multiply(chip_alpha, clip))
    chip.save(media / "chip_sheen_v1.png", optimize=True)
    return target


if __name__ == "__main__":
    print(build())
