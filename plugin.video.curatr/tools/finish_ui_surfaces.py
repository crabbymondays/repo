"""Apply a decorative finish to the existing Kodi layouts without moving controls."""

from pathlib import Path
import xml.etree.ElementTree as ET

from build_ui_surface import build


ROOT = Path(__file__).resolve().parents[1]
SKIN = ROOT / "resources/skins/Default/1080i"
MEDIA = "special://home/addons/plugin.video.curatr/resources/media/"
MARKER = "Curatr surface finish"
ORIGINALS = {
    "rounded_surface_v2.png": "rounded_surface_v3.png",
    "rounded_control_v3.png": "rounded_surface_v3.png",
    "artwork_card_square_v2.png": "artwork_surface_square_v3.png",
    "artwork_card_landscape_v4.png": "artwork_surface_landscape_v3.png",
}
MOVING_BUTTONS = {
    "curatr-list-settings.xml": {300, 301, 302},
    "curatr-collection-manager.xml": {300},
    "curatr-collection-dialog.xml": {300},
    "curatr-keyword-confirm.xml": {100, 101},
}


def decoration(original, texture_name, colour, border, visible=None, padding=0):
    result = ET.Element("control", type="image")
    ET.SubElement(result, "description").text = MARKER
    for tag, delta in (("left", -padding), ("top", -padding), ("width", 2 * padding), ("height", 2 * padding)):
        ET.SubElement(result, tag).text = str(int(original.findtext(tag)) + delta)
    attributes = {"border": str(border), "infill": "false"}
    if colour:
        attributes["colordiffuse"] = colour
    ET.SubElement(result, "texture", attributes).text = MEDIA + texture_name
    conditions = [original.findtext("visible")]
    if original.get("id"):
        conditions.append("Control.IsVisible(%s)" % original.get("id"))
    conditions.append(visible)
    conditions = ["[%s]" % c for c in conditions if c]
    if conditions:
        ET.SubElement(result, "visible").text = " + ".join(conditions)
    return result


def finish(path):
    tree = ET.parse(path)
    root = tree.getroot()
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "control" and child.findtext("description") == MARKER:
                parent.remove(child)

    for parent in list(root.iter()):
        children = list(parent)
        for child in children:
            if child.tag != "control":
                continue
            kind = child.get("type")
            for texture in child:
                if "texture" not in texture.tag:
                    continue
                name = (texture.text or "").removeprefix(MEDIA)
                if name in ORIGINALS:
                    target = "rounded_control_v3.png" if kind == "button" else ORIGINALS[name]
                    texture.text = MEDIA + target

            texture = child.find("texture")
            card = (parent.tag == "itemlayout" and texture is not None
                    and int(child.findtext("width", "0")) > 250
                    and int(child.findtext("height", "0")) > 150
                    and "CuratrListCard" in texture.get("colordiffuse", ""))
            surface = kind == "image" and texture is not None and (texture.text == MEDIA + "rounded_surface_v3.png" or card)
            button = kind == "button" and parent.tag == "controls"
            if not (surface or button):
                continue
            owner = int(child.get("id", "0"))
            moving = button and owner in MOVING_BUTTONS.get(path.name, set())
            changing_panel = path.name == "curatr-keyword-confirm.xml" and owner == 14
            button_background = surface and owner >= 1000
            has_background = button and any(c.get("id") == str(1000 + owner) for c in children)
            border = int((texture if surface else child.find("texturefocus")).get("border", "18"))
            visible = None
            if button and not has_background:
                normal = child.find("texturenofocus")
                if normal is not None and (normal.get("colordiffuse", "").startswith("00") or normal.text == MEDIA + "pixel.png"):
                    visible = "Control.HasFocus(%d)" % owner

            room = True
            if parent.tag in ("itemlayout", "focusedlayout"):
                x, y, width, height = (int(child.findtext(tag)) for tag in ("left", "top", "width", "height"))
                room = x >= 12 and y >= 12 and x + width + 12 <= int(parent.get("width")) and y + height + 12 <= int(parent.get("height"))
            if not has_background and room:
                shadow = decoration(child, "rounded_shadow_v3.png", None, border + 12, visible, padding=12)
                if moving or changing_panel:
                    shadow.set("id", str(9000 + owner))
                parent.insert(list(parent).index(child), shadow)
            if button_background:
                continue

            index = list(parent).index(child) + 1
            if button:
                for focused, colour in ((False, "16FFFFFF"), (True, "80FFFFFF")):
                    if moving and not focused:
                        continue
                    condition = ("" if focused else "!") + "Control.HasFocus(%d)" % owner
                    edge = decoration(child, "rounded_focus_outline_v1.png" if focused else "rounded_outline_v3.png", colour, border, condition)
                    if moving:
                        edge.set("id", str(8000 + owner))
                    parent.insert(index, edge)
                    index += 1
            else:
                edge = decoration(child, "rounded_outline_v3.png", "12FFFFFF", border)
                if changing_panel:
                    edge.set("id", str(8000 + owner))
                parent.insert(index, edge)

    ET.indent(root, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    build()
    for path in sorted(SKIN.glob("*.xml")):
        finish(path)
    print("Applied surface gradients, shadows and light borders to the existing layouts.")
