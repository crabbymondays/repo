"""Build the single, theme-neutral surface texture (Pillow is build-time only)."""

from pathlib import Path

from PIL import Image, ImageDraw


def build():
    media = Path(__file__).resolve().parents[1] / "resources" / "media"
    # Keep the existing antialiased corner geometry exactly. Kodi stretches the
    # centre using the same nine-slice border and supplies each theme's colour.
    mask = Image.open(media / "rounded_rect_v1.png").convert("LA")
    width, height = mask.size
    shade = Image.new("L", mask.size)
    # Nine-slicing keeps these short edge bands at a fixed height, including on
    # large panels. The centre stays level instead of stretching a full fade.
    def luminance(y):
        top = max(0.0, 1.0 - y / 12.0) ** 2
        bottom = max(0.0, 1.0 - (height - 1 - y) / 14.0) ** 2
        return round(244 + 11 * top - 68 * bottom)

    shade.putdata([luminance(y) for y in range(height) for _ in range(width)])
    shade.putalpha(mask.getchannel("A"))
    target = media / "rounded_surface_v2.png"
    shade.save(target, optimize=True)
    preview = Image.new("RGBA", (768, 432), (255, 255, 255, 0))
    ImageDraw.Draw(preview).rounded_rectangle((0, 0, 767, 431), radius=40, fill="white")
    preview.resize((192, 108), Image.Resampling.LANCZOS).save(media / "preview_mask_v1.png", optimize=True)
    return target


if __name__ == "__main__":
    print(build())
