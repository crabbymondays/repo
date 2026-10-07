"""Prepare native Kodi controls before rebuilding decorative layers."""

from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SKIN = ROOT / "resources/skins/Default/1080i"
MARKERS = {"Curatr surface finish", "Curatr corner finish"}


def prepare_dialog_copies():
    """Nested editors use separate native dialog identities with shared layouts."""
    for original, target in (("curatr-list-settings.xml", "curatr-dynamic-settings.xml"),
                             ("curatr-collection-manager.xml", "curatr-collection-dialog.xml")):
        tree = ET.parse(SKIN / original)
        root = tree.getroot()
        for parent in root.iter():
            for child in list(parent):
                if child.tag == "control" and child.findtext("description") in MARKERS:
                    parent.remove(child)
        tree.write(SKIN / target, encoding="utf-8", xml_declaration=True)


def set_text(element, tag, text):
    child = element.find(tag)
    if child is None:
        child = ET.SubElement(element, tag)
    child.text = str(text)


def prepare_keyword_header(root, controls):
    """Let Kodi place the edit icon after the skin's actual heading width."""
    heading = next(c for c in root.iter("control")
                   if c.get("type") == "label" and c.findtext("label") == "[B]Looking for[/B]")
    toggle = root.find(".//control[@id='102']")
    icon = root.find(".//control[@id='103']")
    if icon is None:
        icon = ET.Element("control", type="image", id="103")
    header = root.find(".//control[@id='104']")
    if header is None:
        header = ET.Element("control", type="grouplist", id="104")
        controls.insert(list(controls).index(heading), header)
    target = root.find(".//control[@id='105']")
    if target is None:
        target = ET.Element("control", type="group", id="105")

    parents = {child: parent for parent in root.iter() for child in parent}
    for child in (heading, toggle, icon, target):
        if child in parents:
            parents[child].remove(child)
    set_text(header, "description", "Curatr keyword edit header")
    for tag, value in (("left", 360), ("top", 396), ("width", 600), ("height", 80),
                       ("orientation", "horizontal"), ("itemgap", 0),
                       ("usecontrolcoords", "true"), ("align", "left"),
                       ("defaultcontrol", 102), ("onleft", 102), ("onright", 102),
                       ("onup", 102), ("ondown", 100)):
        set_text(header, tag, value)
    set_text(heading, "description", "Curatr keyword filter heading")
    for tag, value in (("left", 0), ("top", 12), ("width", "auto"), ("height", 56),
                       ("align", "left"), ("aligny", "center")):
        set_text(heading, tag, value)
    heading.find("width").attrib.update(min="1", max="480")
    set_text(target, "description", "Curatr keyword edit target")
    for tag, value in (("left", 0), ("top", 12), ("width", 56), ("height", 56),
                       ("defaultcontrol", 102)):
        set_text(target, tag, value)
    for tag, value in (("left", 0), ("top", 0), ("width", 56), ("height", 56),
                       ("label", ""), ("align", "center"), ("aligny", "center"),
                       ("textoffsetx", 0)):
        set_text(toggle, tag, value)
    for tag in ("texturefocus", "texturenofocus"):
        texture = toggle.find(tag)
        texture.text = "special://home/addons/plugin.video.curatr/resources/media/control_clear_v2.png"
        texture.attrib.clear()
    set_text(icon, "description", "Curatr keyword edit icon")
    for tag, value in (("left", 10), ("top", 6), ("width", 36), ("height", 36)):
        set_text(icon, tag, value)
    set_text(icon, "texture", "special://home/addons/plugin.video.curatr/resources/media/action_icons/v1/pen-to-square.png")
    set_text(icon, "aspectratio", "keep")
    set_text(icon, "colordiffuse", "FFFFFFFF")
    set_text(icon, "visible", "Control.IsVisible(102)")
    for animation in list(icon.findall("animation")):
        icon.remove(animation)
    ET.SubElement(icon, "animation", effect="zoom", start="100", end="112",
                  center="auto", time="120", condition="Control.HasFocus(102)",
                  reversible="true").text = "Conditional"
    header.append(heading)
    header.append(target)
    target.append(toggle)
    target.append(icon)


def prepare_keyword_spacing(root, controls):
    """Balance the divider using Kodi's wrapped text height and justified gaps."""
    request = root.find(".//control[@id='11']")
    header = root.find(".//control[@id='104']")
    divider = root.find(".//control[@id='107']")
    if divider is None:
        divider = next(c for c in controls if c.get("type") == "image"
                       and c.findtext("width") == "1200" and c.findtext("height") == "2")
        divider.set("id", "107")
    body = root.find(".//control[@id='106']")
    if body is None:
        body = ET.Element("control", type="grouplist", id="106")
        controls.insert(list(controls).index(request), body)
    parents = {child: parent for parent in root.iter() for child in parent}
    for child in (request, divider, header):
        parents[child].remove(child)
    set_text(body, "description", "Curatr keyword request spacing")
    for tag, value in (("left", 360), ("top", 232), ("width", 1200), ("height", 244),
                       ("orientation", "vertical"), ("align", "justify"),
                       ("itemgap", 0), ("usecontrolcoords", "true"),
                       ("defaultcontrol", 102), ("onup", 102), ("ondown", 100),
                       ("onleft", 102), ("onright", 102)):
        set_text(body, tag, value)
    for tag, value in (("left", 0), ("top", 0), ("height", "auto")):
        set_text(request, tag, value)
    request.find("height").attrib.update(min="1", max="128")
    set_text(divider, "description", "Curatr keyword request divider")
    set_text(divider, "left", 0)
    # This inset matches the heading's inset, so the divider is centred between
    # the request's rendered bottom and the heading label's top.
    set_text(divider, "top", 12)
    set_text(header, "left", 0)
    set_text(header, "top", 0)
    for child in (request, divider, header):
        body.append(child)


def prepare(path):
    tree = ET.parse(path)
    root = tree.getroot()
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "control" and child.findtext("description") in MARKERS:
                parent.remove(child)
    controls = root.find("controls")
    for button in controls.findall("control[@type='button']"):
        normal = button.find("texturenofocus")
        if int(button.findtext("top")) >= 740 and "rounded_control" in (normal.text or ""):
            normal.set("colordiffuse", "$INFO[Window(Home).Property(CuratrFooterButton)]")
    if path.name == "curatr-colour-picker.xml":
        if root.find(".//control[@id='28']") is None:
            for source_id, target_id in ((1020, 1028), (20, 28)):
                new = deepcopy(root.find(".//control[@id='%d']" % source_id))
                new.set("id", str(target_id))
                controls.insert(list(controls).index(root.find(".//control[@id='1026']")), new)
        for index, cid in enumerate((20, 21, 22, 23, 24, 25, 28)):
            control = root.find(".//control[@id='%d']" % cid)
            set_text(control, "top", 205 + index * 70)
            set_text(control, "onup", (20, 21, 22, 23, 24, 25, 28)[index - 1] if index else 300)
            set_text(control, "ondown", (20, 21, 22, 23, 24, 25, 28)[index + 1] if index < 6 else 300)
            backing = root.find(".//control[@id='%d']" % (1000 + cid))
            if backing is not None:
                set_text(backing, "top", 205 + index * 70)
        for cid, top in ((32, 710), (33, 776)):
            control = root.find(".//control[@id='%d']" % cid)
            set_text(control, "top", top)
            set_text(control, "height", 54)
        for c in controls:
            if c.get("type") == "label" and c.findtext("label") in ("[B]Highlight[/B]", "[B]Secondary highlight[/B]"):
                set_text(c, "top", 710 if "Secondary" not in c.findtext("label") else 776)
                set_text(c, "height", 54)
            if c.get("type") == "image" and c.findtext("visible") == "Control.IsVisible(31)":
                set_text(c, "top", 710); set_text(c, "height", 120)
            if c.get("type") == "label" and c.findtext("label") == "Background preview":
                set_text(c, "top", 727)
        preview = root.find(".//control[@id='31']")
        for tag, value in (("left", 231), ("top", 726), ("width", 160), ("height", 90)):
            set_text(preview, tag, value)
        set_text(root.find(".//control[@id='34']"), "top", 768)

    for cid in ((200,) if path.name in ("curatr-collection-manager.xml", "curatr-folder-contents.xml") else (410,) if path.name == "curatr-folder-settings.xml" else ()):
        menu = root.find(".//control[@id='%d']" % cid)
        for layout in (menu.find("itemlayout"), menu.find("focusedlayout")):
            if layout.find("control[description='Curatr action icon']") is None:
                icon = ET.SubElement(layout, "control", type="image")
                ET.SubElement(icon, "description").text = "Curatr action icon"
                for tag, value in (("left", 18), ("top", 16), ("width", 30), ("height", 30)):
                    set_text(icon, tag, value)
                set_text(icon, "texture", "$INFO[ListItem.Property(CuratrActionIcon)]")
                set_text(icon, "aspectratio", "keep")
            icon = layout.find("control[description='Curatr action icon']")
            set_text(icon, "colordiffuse", "$INFO[ListItem.Property(CuratrActionTint)]")
            for label in layout.findall("control[@type='label']"):
                set_text(label, "left", 64)
                set_text(label, "width", int(layout.get("width")) - 82)
                set_text(label, "textcolor", "FFFFFFFF")
                if layout.tag == "focusedlayout":
                    set_text(label, "scroll", "true")

    if path.name in ("curatr-collection-manager.xml", "curatr-folder-contents.xml", "curatr-folder-settings.xml"):
        grid = root.find(".//control[@id='%d']" % (400 if path.name == "curatr-folder-settings.xml" else 100))
        for layout in (grid.find("itemlayout"), grid.find("focusedlayout")):
            for c in layout.findall("control[@type='image']"):
                texture = c.find("texture")
                diffuse = texture.get("colordiffuse", "")
                card = int(c.findtext("width")) > (700 if path.name == "curatr-collection-manager.xml" else 250) and int(c.findtext("height")) > 150
                description = c.findtext("description", "")
                if "artwork_surface_square" in (texture.text or ""):
                    description = "Curatr artwork surround"
                    set_text(c, "description", description)
                elif "artwork_mask_square" in (texture.text or ""):
                    description = "Curatr artwork corner mask"
                    set_text(c, "description", description)
                surround = description in ("Curatr artwork surround", "Curatr artwork corner mask")
                if (card or surround) and any(key in diffuse for key in ("CuratrRow", "CuratrCard", "CuratrListCard")):
                    colour = "CuratrListCardFocus" if layout.tag == "focusedlayout" else "CuratrListCard"
                    texture.set("colordiffuse", "$INFO[Window(Home).Property(%s)]" % colour)
                    if card:
                        texture.text = "special://home/addons/plugin.video.curatr/resources/media/rounded_rect_v1.png"
                        texture.set("border", "18")
                    elif description == "Curatr artwork surround":
                        texture.text = "special://home/addons/plugin.video.curatr/resources/media/list_artwork_frame_flat_v2.png"
                        texture.set("border", "16")
                    else:
                        texture.text = "special://home/addons/plugin.video.curatr/resources/media/artwork_mask_square_v2.png"
                        texture.set("border", "16")
            if path.name == "curatr-collection-manager.xml":
                labels = layout.findall("control[@type='label']")
                for c in labels:
                    set_text(c, "visible", "!String.IsEqual(ListItem.Property(CuratrCompact),true)")
                if layout.find("control[description='Curatr centred source title']") is None:
                    for original, top, height, name in ((labels[0], 50, 38, "title"), (labels[1], 92, 32, "description")):
                        new = deepcopy(original)
                        set_text(new, "description", "Curatr centred source " + name)
                        set_text(new, "top", top); set_text(new, "height", height)
                        set_text(new, "aligny", "center")
                        set_text(new, "visible", "String.IsEqual(ListItem.Property(CuratrCompact),true)")
                        layout.append(new)
                else:
                    for c in layout.findall("control"):
                        if c.findtext("description", "").startswith("Curatr centred source"):
                            set_text(c, "visible", "String.IsEqual(ListItem.Property(CuratrCompact),true)")
                            set_text(c, "top", 50 if c.findtext("description").endswith("title") else 92)
            else:
                small = path.name == "curatr-folder-settings.xml"
                for c in layout.findall("control[@type='image']"):
                    width = int(c.findtext("width"))
                    if (small and width == 140) or (not small and width in (202, 222, 190, 208)):
                        size = 116 if small else 202 if width in (202, 222) else 190
                        set_text(c, "width", size); set_text(c, "height", size)
                        set_text(c, "left", (int(layout.get("width")) - size) // 2)
                        set_text(c, "top", 20 if small else 30 if size == 202 else 36)
                for c in layout.findall("control[@type='label']"):
                    title = "ListItem.Label" in c.findtext("label", "")
                    set_text(c, "top", (150 if title else 194) if small else (246 if title else 288))
                    set_text(c, "height", 36 if title else 32)
                    set_text(c, "aligny", "center")

    if path.name == "curatr-keyword-confirm.xml":
        root.find(".//control[@id='14']/texture").text = "special://home/addons/plugin.video.curatr/resources/media/rounded_surface_v3.png"
        controls[0].find("texture").set("colordiffuse", "$INFO[Window(Home).Property(CuratrKeywordBackdrop)]")
        prepare_keyword_header(root, controls)
        prepare_keyword_spacing(root, controls)
    ET.indent(root, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def prepare_settings():
    path = ROOT / "resources/settings.xml"
    tree = ET.parse(path)
    for parent in tree.iter():
        for child in list(parent):
            if child.tag == "setting" and child.get("id") == "choose_sheen_colour":
                parent.remove(child)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    for path in sorted(SKIN.glob("*.xml")):
        prepare(path)
    prepare_settings()
