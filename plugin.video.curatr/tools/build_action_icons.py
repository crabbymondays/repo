"""Render the selected Font Awesome menu and keyword-control icons."""

from pathlib import Path
from PIL import Image
from build_menu_artwork import vector_mask


ROOT = Path(__file__).resolve().parents[1]
ICONS = ("caret-up", "caret-down", "xmark", "trash", "gear", "filter", "image", "circle-info", "plus", "bookmark", "arrows-rotate", "folder-plus", "pen-to-square", "check")


def build():
    output = ROOT / "resources/media/action_icons/v1"
    output.mkdir(parents=True, exist_ok=True)
    for name in ICONS:
        version = "7.0.1" if name in ("arrows-rotate", "folder-plus") else "6.7.2"
        svg = (ROOT / "tools/icon_sources" / ("fontawesome-" + version) / (name + ".svg")).read_text()
        mask = vector_mask(svg)
        scale = 50 / max(mask.size)
        mask = mask.resize((round(mask.width * scale), round(mask.height * scale)), Image.Resampling.LANCZOS)
        alpha = Image.new("L", (64, 64))
        alpha.paste(mask, ((64 - mask.width) // 2, (64 - mask.height) // 2))
        image = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
        image.putalpha(alpha)
        image.save(output / (name + ".png"), optimize=True)
    print("Built %d UI icons." % len(ICONS))


if __name__ == "__main__":
    build()
