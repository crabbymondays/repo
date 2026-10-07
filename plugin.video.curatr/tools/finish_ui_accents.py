"""Build theme-bound highlights and background patterns for Kodi controls.

Pillow is a build dependency only. Full-size alpha masks keep the sheen diagonal
across wide buttons, rather than stretching a nine-sliced corner gradient.
"""

from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageDraw

from build_ui_surface import build
from finish_ui_surfaces import finish as finish_surfaces, MARKER as SURFACE_MARKER, decoration
from prepare_ui_layouts import prepare, prepare_settings, prepare_dialog_copies


ROOT = Path(__file__).resolve().parents[1]
SKIN = ROOT / "resources/skins/Default/1080i"
MEDIA = ROOT / "resources/media"
PREFIX = "special://home/addons/plugin.video.curatr/resources/media/"
MARKER = "Curatr corner finish"
COLOUR = "$INFO[Window(Home).Property(CuratrSheenColour)]"
MOVING = {
    "curatr-list-settings.xml": {300, 301},
    "curatr-collection-manager.xml": {300},
    "curatr-collection-dialog.xml": {300},
    "curatr-keyword-confirm.xml": {100, 101},
}


def geometry(control):
    return tuple(int(control.findtext(tag)) for tag in ("left", "top", "width", "height"))


def rounded_mask(size, border=18):
    """Use the same nine-sliced corner alpha as the original native control."""
    with Image.open(MEDIA / "rounded_rect_v1.png") as source:
        original = source.convert("RGBA").getchannel("A")
    width, height = size
    border = min(border, width // 2, height // 2)
    sx = (0, border, original.width - border, original.width)
    sy = (0, border, original.height - border, original.height)
    tx = (0, border, width - border, width)
    ty = (0, border, height - border, height)
    result = Image.new("L", size)
    for row in range(3):
        for column in range(3):
            target_size = tx[column + 1] - tx[column], ty[row + 1] - ty[row]
            if min(target_size) <= 0:
                continue
            piece = original.crop((sx[column], sy[row], sx[column + 1], sy[row + 1]))
            result.paste(piece.resize(target_size, Image.Resampling.BILINEAR), (tx[column], ty[row]))
    return result


def sheen_mask(size, border=18):
    width, height = size
    alpha = Image.new("L", size)
    alpha.putdata([
        round(46 * max(0.0, min(1.0, (0.67 * x / max(1, width - 1)
                                    + 0.33 * y / max(1, height - 1) - 0.38) / 0.62)) ** 1.5)
        for y in range(height) for x in range(width)
    ])
    return ImageChops.multiply(alpha, rounded_mask(size, border))


def list_sheen_mask(size, border=18):
    """Faint dotted light at both edges, with no solid bright bands."""
    width, height = size
    tile = Image.new("L", (54, 54))
    ImageDraw.Draw(tile).ellipse((22.9, 22.9, 31.1, 31.1), fill=255)
    tile = tile.resize((18, 18), Image.Resampling.LANCZOS)
    pattern = Image.new("L", size)
    for y in range(0, height, 18):
        for x in range(0, width, 18):
            pattern.paste(tile, (x, y))
    fade, glow = Image.new("L", size), Image.new("L", size)
    dots_draw, glow_draw = ImageDraw.Draw(fade), ImageDraw.Draw(glow)
    band = min(54, height * 0.30)
    for y in range(height):
        t = max(0.0, 1.0 - min(y, height - 1 - y) / band)
        strength = t * t * (3 - 2 * t)
        dots_draw.line((0, y, width - 1, y), fill=round(12 * strength))
        glow_draw.line((0, y, width - 1, y), fill=round(2 * strength))
    alpha = ImageChops.add(ImageChops.multiply(pattern, fade), glow)
    return ImageChops.multiply(alpha, rounded_mask(size, border))


def dots_mask(size, left=False):
    """Small, antialiased dots fading from the left or bottom-right corner."""
    tile = Image.new("L", (54, 54))
    ImageDraw.Draw(tile).ellipse((22.9, 22.9, 31.1, 31.1), fill=255)
    tile = tile.resize((18, 18), Image.Resampling.LANCZOS)
    width, height = size
    pattern = Image.new("L", size)
    for y in range(0, height, 18):
        for x in range(0, width, 18):
            pattern.paste(tile, (x, y))
    horizontal = [max(0.0, 1 - x / max(1, width * 0.84)) ** 1.25 if left
                  else max(0.0, (x / max(1, width - 1) - 0.52) / 0.48) ** 1.6 for x in range(width)]
    vertical = [max(0.0, (y / max(1, height - 1) - (0.12 if left else 0.42)) / (0.88 if left else 0.58)) ** 1.3 for y in range(height)]
    fade = Image.new("L", size)
    fade.putdata([round((55 if left else 38) * fx * fy) for fy in vertical for fx in horizontal])
    return ImageChops.multiply(pattern, fade)


def save_alpha(alpha, name):
    image = Image.new("LA", alpha.size, (255, 0))
    image.putalpha(alpha)
    image.save(MEDIA / name, optimize=True)


def layer(owner, name, condition=None, control_id=None):
    attrs = {"type": "image"}
    if control_id is not None:
        attrs["id"] = str(control_id)
    image = ET.Element("control", attrs)
    ET.SubElement(image, "description").text = MARKER
    for tag in ("left", "top", "width", "height"):
        ET.SubElement(image, tag).text = owner.findtext(tag)
    ET.SubElement(image, "texture").text = PREFIX + name
    ET.SubElement(image, "colordiffuse").text = COLOUR
    conditions = [owner.findtext("visible")]
    if owner.get("id"):
        conditions.append("Control.IsVisible(%s)" % owner.get("id"))
    conditions.append(condition)
    if any(conditions):
        ET.SubElement(image, "visible").text = " + ".join("[%s]" % value for value in conditions if value)
    return image


def finish(path):
    tree = ET.parse(path)
    root = tree.getroot()
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "control" and child.findtext("description") == MARKER:
                parent.remove(child)
    controls = root.find("controls")
    originals = [c for c in controls if c.tag == "control" and c.findtext("description") != SURFACE_MARKER]
    backgrounds = [c for c in originals if c.get("type") == "image"
                   and c.findtext("texture") == PREFIX + "rounded_surface_v3.png"
                   and geometry(c)[2] > 1000 and geometry(c)[3] > 500]
    if path.name == "curatr-keyword-confirm.xml":
        backgrounds = []
    background = backgrounds[0] if backgrounds else originals[0]
    bx, by, width, height = geometry(background)
    list_view = path.name in ("curatr-collection-manager.xml", "curatr-collection-dialog.xml", "curatr-folder-contents.xml", "curatr-folder-settings.xml")
    alpha = dots_mask((width, height), left=list_view)
    if backgrounds:
        border = int(background.find("texture").get("border", "18"))
        alpha = ImageChops.multiply(alpha, rounded_mask((width, height), border))

    # Remove every foreground footprint, including translucent surfaces and
    # all possible positions of the few original runtime-repositioned buttons.
    clear = ImageDraw.Draw(alpha)
    for control in originals:
        if control is background or control is originals[0]:
            continue
        x, y, cw, ch = geometry(control)
        rectangles = [(x, y, x + cw - 1, y + ch - 1)]
        ident = control.get("id")
        if list_view and ident == ("400" if path.name == "curatr-folder-settings.xml" else "100"):
            continue
        if path.name in ("curatr-list-settings.xml", "curatr-dynamic-settings.xml") and ident in ("300", "301"):
            alternate_x = 610 if ident == "300" else 970
            rectangles.append((alternate_x, 750, alternate_x + cw - 1, 750 + ch - 1))
        if path.name in ("curatr-collection-manager.xml", "curatr-collection-dialog.xml") and ident == "300":
            rectangles.append((740, 965, 740 + cw - 1, 965 + ch - 1))
        if path.name == "curatr-keyword-confirm.xml" and ident in ("100", "101", "14"):
            rectangles.append((x, y, x + cw - 1, 1030))
        for x1, y1, x2, y2 in rectangles:
            clear.rectangle((x1 - bx, y1 - by, x2 - bx, y2 - by), fill=0)
    name = "corner_dots_%s_v2.png" % path.stem.removeprefix("curatr-").replace("-", "_")
    save_alpha(alpha, name)
    controls.insert(list(controls).index(background) + 1, layer(background, name))

    parents = {child: parent for parent in root.iter() for child in parent}
    for parent in list(root.iter()):
        children = list(parent)
        for child in children:
            if child.tag != "control" or child.findtext("description") in (MARKER, SURFACE_MARKER):
                continue
            button = child.get("type") == "button" and parent is controls
            texture = child.find("texture")
            diffuse = texture.get("colordiffuse", "") if texture is not None else ""
            swatch = child.get("type") == "image" and any(key in diffuse for key in ("CuratrSwatch", "CuratrColour"))
            preview = parent is controls and child.get("id") in ("32", "33") and path.name == "curatr-colour-picker.xml"
            flair = path.name == "curatr-artwork-editor.xml" and parent is controls and child.get("type") == "image" and geometry(child) == (105, 155, 550, 735)
            keyword_panel = path.name == "curatr-keyword-confirm.xml" and child.get("id") == "14"
            card = parent.tag in ("itemlayout", "focusedlayout") and list_view and child.get("type") == "image" and int(child.findtext("width")) > 250 and int(child.findtext("height")) > 150 and any(key in diffuse for key in ("CuratrListCard", "CuratrCard"))
            if parent.tag == "focusedlayout" and child.get("type") == "image" and "CuratrPrimary" in diffuse and texture is not None and "rounded_rect" in texture.text:
                border = int(texture.get("border", "18"))
                menu_owner = parents[parent].get("id")
                edge = decoration(child, "rounded_focus_outline_v1.png", "80FFFFFF", border,
                                  "Control.HasFocus(%s)" % menu_owner)
                edge.find("description").text = MARKER
                parent.insert(list(parent).index(child) + 1, edge)
            if not (button or swatch or preview or flair or card or keyword_panel):
                continue
            source_texture = child.find("texturefocus") if button else texture
            border = int(source_texture.get("border", "18"))
            _, _, cw, ch = geometry(child)
            name = "button_sheen_%dx%d_b%d_v2.png" % (cw, ch, border)
            if card:
                name = "list_sheen_%dx%d_b%d_v2.png" % (cw, ch, border)
                save_alpha(list_sheen_mask((cw, ch), border), name)
            if keyword_panel:
                name = "button_sheen_keyword_panel_v1.png"
                save_alpha(sheen_mask((cw, ch), border).point(lambda value: round(value * 0.48)), name)
            if not (MEDIA / name).exists():
                save_alpha(sheen_mask((cw, ch), border), name)
            condition = None
            overlay_id = None
            if keyword_panel:
                overlay_id = 7014
            if button:
                ident = int(child.get("id"))
                has_background = any(c.get("id") == str(1000 + ident) for c in children)
                normal = child.find("texturenofocus")
                if not has_background and normal.get("colordiffuse", "").startswith("00"):
                    condition = "Control.HasFocus(%d)" % ident
                if ident in MOVING.get(path.name, set()):
                    overlay_id = 7000 + ident
            index = next((i for i, c in enumerate(parent) if c.get("type") == "label"), len(parent)) if card else list(parent).index(child) + 1
            parent.insert(index, layer(child, name, condition, overlay_id))

    ET.indent(root, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def prune_unused_finishes():
    """Keep only decorative masks referenced by the current native layouts."""
    referenced = set()
    for path in SKIN.glob("*.xml"):
        for node in ET.parse(path).iter():
            value = node.text or ""
            if value.startswith(PREFIX):
                referenced.add(value.removeprefix(PREFIX))
    removed = 0
    for pattern in ("button_sheen_*.png", "corner_dots_*.png", "list_sheen_*.png",
                    "list_lines_*.png", "list_artwork_*.png"):
        for path in MEDIA.glob(pattern):
            if path.name not in referenced:
                path.unlink()
                removed += 1
    return removed


if __name__ == "__main__":
    build()
    prepare_dialog_copies()
    for path in sorted(SKIN.glob("*.xml")):
        prepare(path)
        finish_surfaces(path)
        finish(path)
    prepare_settings()
    removed = prune_unused_finishes()
    print("Applied button sheen, faint dotted cards and background dots; removed %d unused masks." % removed)
