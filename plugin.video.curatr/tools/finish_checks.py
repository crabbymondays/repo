"""Check the configurable finish without live Kodi or provider connections."""

import copy
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

from PIL import Image, ImageStat

from release_checks import ROOT, FakeAddon, FakeControl, install_kodi_stubs

install_kodi_stubs("")
from lib import sheen_picker, ui_theme, menu_background, colour_picker, action_icons, core
from lib.collection_manager import CollectionManagerWindow
from lib.folder_contents import FolderContentsWindow
from lib.folder_settings import FolderSettingsWindow
from lib.keyword_confirm import KeywordConfirmWindow
from lib.list_settings import ListSettingsWindow
from lib.dynamic_settings import PreviewWindow
from finish_ui_accents import MARKER, SURFACE_MARKER, PREFIX, geometry


def theme_window(addon):
    obj = object.__new__(colour_picker.ColourPickerWindow)
    colour_picker.ColourPickerWindow.__init__(obj, addon)
    xml = ET.parse(ROOT / "resources/skins/Default/1080i/curatr-colour-picker.xml")
    controls = {int(c.get("id")): FakeControl() for c in xml.iter("control") if c.get("id")}
    obj.getControl = controls.__getitem__
    obj.setFocus = Mock()
    obj.close = Mock()
    obj.onInit()
    return obj, controls


class ColourChecks(unittest.TestCase):
    def test_white_default_custom_rgb_and_invalid_saved_values(self):
        addon = FakeAddon("")
        self.assertEqual(ui_theme.theme_palette(addon)["CuratrSheenColour"], "FFFFFFFF")
        for value, expected in (("#b8dfff", "FFB8DFFF"), ("AABBCC", "FFAABBCC"),
                                ("#000000", "FF000000"), ("not-a-colour", "FFFFFFFF"),
                                ("#123", "FFFFFFFF"), ("#11223344", "FFFFFFFF")):
            addon.settings["interface_sheen_colour"] = value
            self.assertEqual(ui_theme.theme_palette(addon)["CuratrSheenColour"], expected)

    def test_colour_is_independent_and_survives_theme_changes(self):
        addon = FakeAddon("", {"interface_sheen_colour": "#B8DFFF"})
        old = ui_theme.theme_palette(addon)
        config = ui_theme.theme_config(addon)
        config.update(base="red", primary="red", secondary="red", tint="red", custom=False)
        ui_theme.save_theme(addon, config)
        self.assertEqual(ui_theme.theme_palette(addon)["CuratrSheenColour"], old["CuratrSheenColour"])
        self.assertNotEqual(ui_theme.theme_palette(addon)["CuratrPrimary"], old["CuratrPrimary"])
        theme_before = {k: v for k, v in ui_theme.theme_palette(addon).items() if k != "CuratrSheenColour"}
        addon.settings["interface_sheen_colour"] = "rose"
        self.assertEqual({k: v for k, v in ui_theme.theme_palette(addon).items() if k != "CuratrSheenColour"}, theme_before)

    def test_sheen_swatches_preview_without_saving_and_cancel_restores_palette(self):
        addon = FakeAddon("", {"interface_sheen_colour": "rose", "interface_theme": "ocean"})
        before = copy.deepcopy(addon.settings)
        with patch.object(colour_picker, "_publish_palette") as publish:
            obj, controls = theme_window(addon)
            obj.onClick(28)
            self.assertEqual(obj.active_field, "sheen")
            self.assertEqual(len(controls[100].items), 27)
            self.assertTrue(controls[28].enabled)
            controls[100].selectItem(obj.keys.index("deep_blue"))
            obj.onClick(100)
            self.assertEqual(publish.call_args.args[0]["CuratrSheenColour"], "FF" + colour_picker.COLOURS["deep_blue"][1])
            self.assertEqual(controls[100].items[obj.keys.index("deep_blue")].properties["CuratrSwatch"], publish.call_args.args[0]["CuratrSheenColour"])
            self.assertEqual(addon.settings, before)
            obj.doModal = lambda: obj.onClick(301)
            with patch.object(colour_picker, "ColourPickerWindow", return_value=obj):
                self.assertIsNone(colour_picker.choose_colours(addon))
            self.assertEqual(publish.call_args.args[0], ui_theme.theme_palette(addon))
        self.assertEqual(addon.settings, before)

    def test_custom_entry_validation_save_and_theme_change_preserve_sheen(self):
        addon = FakeAddon("", {"interface_theme": "ocean"})
        with patch.object(colour_picker, "_publish_palette") as publish, patch.object(sheen_picker.xbmcgui, "Dialog") as dialog:
            obj, controls = theme_window(addon)
            obj.onClick(28)
            controls[100].selectItem(obj.keys.index("custom"))
            dialog.return_value.input.side_effect = ["invalid", "", "b8dfff"]
            obj.onClick(100)
            self.assertEqual(obj.config["sheen"], "white")
            self.assertNotIn("interface_sheen_colour", addon.settings)
            dialog.return_value.ok.assert_called_once()
            obj.onClick(100)
            self.assertEqual(obj.config["sheen"], "#B8DFFF")
            self.assertEqual(publish.call_args.args[0]["CuratrSheenColour"], "FFB8DFFF")
            obj.onClick(20)
            controls[100].selectItem(obj.keys.index("red"))
            obj.onClick(100)
            self.assertEqual(obj.config["sheen"], "#B8DFFF")
            self.assertFalse(obj.config["custom"])
            self.assertTrue(controls[28].enabled)
            obj.onClick(300)
            self.assertEqual(addon.getSetting("interface_sheen_colour"), "#B8DFFF")
            self.assertEqual(ui_theme.theme_palette(addon)["CuratrSheenColour"], "FFB8DFFF")

    def test_sheen_action_is_inside_theme_editor_and_legacy_command_opens_it(self):
        settings = ET.parse(ROOT / "resources/settings.xml")
        self.assertIsNone(settings.find(".//setting[@id='choose_sheen_colour']"))
        self.assertIsNotNone(settings.find(".//setting[@id='interface_sheen_colour']"))
        curator = object.__new__(core.Curator)
        curator.customise_theme_interactive = Mock(return_value={"theme": {"sheen": "white"}})
        self.assertEqual(curator.choose_sheen_colour_interactive(), {"theme": {"sheen": "white"}})
        curator.customise_theme_interactive.assert_called_once_with(sheen=True)

    def test_changes_refresh_fixed_and_custom_background_views(self):
        addon = FakeAddon("", {"menu_background_style": "amber"})
        before = menu_background.appearance_signature(addon)
        addon.settings["interface_sheen_colour"] = "#B8DFFF"
        self.assertNotEqual(menu_background.appearance_signature(addon), before)
        state = {"menu_background_style": "custom", "menu_background_source": str(ROOT / "icon_v4.png")}
        before = menu_background.appearance_signature(addon, state)
        addon.settings["interface_sheen_colour"] = "white"
        self.assertNotEqual(menu_background.appearance_signature(addon, state), before)


class SurfaceChecks(unittest.TestCase):
    def test_footer_buttons_are_opaque_and_legible_in_each_theme(self):
        for colour in colour_picker.COLOURS:
            palette = ui_theme.theme_palette(FakeAddon("", {"interface_base_colour": colour}))
            footer = palette["CuratrFooterButton"]
            self.assertTrue(footer.startswith("FF"), colour)
            contrast = 1.05 / (ui_theme._relative_luminance(ui_theme._rgb(footer)) + 0.05)
            self.assertGreaterEqual(contrast, 4.5, colour)
        footer_count = 0
        for path in (ROOT / "resources/skins/Default/1080i").glob("*.xml"):
            for button in ET.parse(path).findall("controls/control[@type='button']"):
                normal = button.find("texturenofocus")
                if int(button.findtext("top")) >= 740:
                    footer_count += 1
                    self.assertEqual(normal.get("colordiffuse"), "$INFO[Window(Home).Property(CuratrFooterButton)]", (path.name, button.get("id")))
                    self.assertIn("rounded_control_v3.png", normal.text)
                else:
                    self.assertNotIn("CuratrFooterButton", normal.get("colordiffuse", ""))
        self.assertEqual(footer_count, 20)

    def test_dots_fill_unused_list_space_and_stay_clear_of_other_controls(self):
        list_views = {"curatr-collection-manager.xml": "100", "curatr-collection-dialog.xml": "100", "curatr-folder-contents.xml": "100", "curatr-folder-settings.xml": "400"}
        for path in (ROOT / "resources/skins/Default/1080i").glob("*.xml"):
            controls = ET.parse(path).getroot().find("controls")
            dots = next(c for c in controls if "corner_dots_" in c.findtext("texture", ""))
            bx, by, width, height = geometry(dots)
            background_index = list(controls).index(dots) - 1
            background = list(controls)[background_index]
            with Image.open(ROOT / "resources/media" / dots.findtext("texture").removeprefix(PREFIX)) as image:
                alpha = image.getchannel("A")
                self.assertGreater(alpha.getextrema()[1], 0, path.name)
                self.assertEqual(alpha.size, (width, height))
                for control in controls:
                    if control is background or control is controls[0] or control.findtext("description") in (MARKER, SURFACE_MARKER):
                        continue
                    x, y, cw, ch = geometry(control)
                    rectangle = max(0, x - bx), max(0, y - by), min(width, x + cw - bx), min(height, y + ch - by)
                    if rectangle[2] > rectangle[0] and rectangle[3] > rectangle[1]:
                        crop = alpha.crop(rectangle)
                        if control.get("id") == list_views.get(path.name, "never"):
                            self.assertGreater(crop.getextrema()[1], 0, path.name)
                            self.assertGreater(ImageStat.Stat(crop.crop((0, 0, crop.width // 2, crop.height))).mean[0], ImageStat.Stat(crop.crop((crop.width // 2, 0, crop.width, crop.height))).mean[0])
                        else:
                            self.assertEqual(crop.getextrema(), (0, 0), (path.name, control.get("id")))
        self.assertTrue(all(ui_theme.theme_palette(FakeAddon(""))[name].startswith("FF") for name in ("CuratrListCard", "CuratrListCardFocus", "CuratrCard")))

    def test_selected_cards_are_lighter_across_the_palette_without_changing_artwork(self):
        for colour in colour_picker.COLOURS:
            palette = ui_theme.theme_palette(FakeAddon("", {"interface_base_colour": colour}))
            normal, selected = (palette[key] for key in ("CuratrListCard", "CuratrListCardFocus"))
            self.assertTrue(normal.startswith("FF") and selected.startswith("FF"), colour)
            normal_light, selected_light = (ui_theme._relative_luminance(ui_theme._rgb(value)) for value in (normal, selected))
            self.assertGreater(selected_light, normal_light, colour)
            self.assertGreaterEqual(1.05 / (selected_light + 0.05), 4.5, colour)
            original_artwork = ui_theme._argb("FF", ui_theme._mix(ui_theme._rgb(colour_picker.COLOURS[colour][2]), (0, 0, 0), 0.52))
            self.assertEqual(palette["CuratrCard"], original_artwork, colour)
        for name, ident in (("collection-manager", 100), ("collection-dialog", 100), ("folder-contents", 100), ("folder-settings", 400)):
            root = ET.parse(ROOT / ("resources/skins/Default/1080i/curatr-%s.xml" % name))
            for kind, property_name in (("itemlayout", "CuratrListCard"), ("focusedlayout", "CuratrListCardFocus")):
                layout = root.find(".//control[@id='%d']/%s" % (ident, kind))
                cards = [c for c in layout.findall("control[@type='image']") if geometry(c)[2] > 250 and geometry(c)[3] > 150 and "CuratrListCard" in (c.find("texture").get("colordiffuse", "") if c.find("texture") is not None else "")]
                self.assertEqual(len(cards), 1, (name, kind))
                self.assertEqual(cards[0].find("texture").get("colordiffuse"), "$INFO[Window(Home).Property(%s)]" % property_name)

    def test_sheen_is_a_smooth_unstretched_mask_with_original_corners(self):
        with Image.open(ROOT / "resources/media/button_sheen_1000x82_b18_v2.png") as image:
            alpha = image.getchannel("A")
            self.assertEqual(alpha.size, (1000, 82))
            self.assertEqual(alpha.getpixel((0, 0)), 0)
            self.assertGreater(alpha.getpixel((975, 66)), alpha.getpixel((700, 20)))
            self.assertLessEqual(alpha.getextrema()[1], 46)
            self.assertGreaterEqual(alpha.getextrema()[1], 40)
            self.assertGreater(ImageStat.Stat(alpha).mean[0], 6)
            self.assertEqual(alpha.getpixel((999, 81)), 0)
            values = [alpha.getpixel((x, 41)) for x in range(18, 982)]
            self.assertEqual(values, sorted(values))
            self.assertLessEqual(max(b - a for a, b in zip(values, values[1:])), 1)

    def test_decorations_cannot_receive_focus_or_add_click_actions(self):
        for path in (ROOT / "resources/skins/Default/1080i").glob("*.xml"):
            for control in ET.parse(path).iter("control"):
                if control.findtext("description") != MARKER:
                    continue
                self.assertEqual(control.get("type"), "image")
                for tag in ("onclick", "onleft", "onright", "onup", "ondown"):
                    self.assertIsNone(control.find(tag))

    def test_list_edit_buttons_move_with_their_finish_and_cancel_stays_hidden(self):
        obj = object.__new__(ListSettingsWindow)
        ListSettingsWindow.__init__(obj, str(ROOT), {}, lambda _f, d: d, lambda f, _d: f, existing=True)
        xml = ET.parse(ROOT / "resources/skins/Default/1080i/curatr-list-settings.xml")
        controls = {int(c.get("id")): FakeControl() for c in xml.iter("control") if c.get("id")}
        obj.getControl = controls.__getitem__
        obj.setFocus = Mock()
        obj.onInit()
        for ident, expected in ((300, (610, 750)), (301, (970, 750))):
            self.assertEqual(controls[ident].xy, expected)
            self.assertEqual(controls[7000 + ident].xy, expected)
            self.assertEqual(controls[8000 + ident].xy, expected)
            self.assertEqual(controls[9000 + ident].xy, (expected[0] - 12, expected[1] - 12))
        self.assertFalse(controls[302].visible)
        sheen = next(c for c in xml.iter("control") if "Control.IsVisible(302)" in c.findtext("visible", "") and c.findtext("description") == MARKER)
        self.assertIn("button_sheen_", sheen.findtext("texture"))

    def test_keyword_footer_repositions_its_finish_without_touching_artwork_controls(self):
        obj = object.__new__(KeywordConfirmWindow)
        xml = ET.parse(ROOT / "resources/skins/Default/1080i/curatr-keyword-confirm.xml")
        controls = {int(c.get("id")): FakeControl() for c in xml.iter("control") if c.get("id")}
        obj.getControl = controls.__getitem__
        obj._position_summary(640)
        for ident, x in ((100, 545), (101, 985)):
            self.assertEqual(controls[ident].xy, (x, 844))
            self.assertEqual(controls[7000 + ident].xy, controls[ident].xy)
            self.assertEqual(controls[8000 + ident].xy, controls[ident].xy)
            self.assertEqual(controls[9000 + ident].xy, (x - 12, 832))
        self.assertEqual(controls[14].height, controls[8014].height)
        self.assertEqual(controls[14].height, controls[7014].height)
        self.assertEqual(controls[9014].height, controls[14].height + 24)
        # Appearance uses existing Kodi images; the picker modules are untouched.
        for ident in (7100, 7101):
            self.assertEqual(xml.find(".//control[@id='%d']" % ident).get("type"), "image")

    def test_wrapped_keyword_tags_keep_a_gap_above_footer_buttons_and_shrink_back(self):
        obj = object.__new__(KeywordConfirmWindow)
        xml = ET.parse(ROOT / "resources/skins/Default/1080i/curatr-keyword-confirm.xml")
        controls = {int(c.get("id")): FakeControl() for c in xml.iter("control") if c.get("id")}
        obj.getControl = controls.__getitem__
        for flow_bottom in (552, 624, 696, 768, 552):
            obj._position_summary(flow_bottom)
            panel_bottom = 140 + controls[14].height
            for ident in (100, 101):
                self.assertEqual(controls[ident].xy[1] - panel_bottom, 40)
                self.assertEqual(controls[7000 + ident].xy, controls[ident].xy)
                self.assertEqual(controls[8000 + ident].xy, controls[ident].xy)
                self.assertLessEqual(controls[ident].xy[1] + 82 + 12, 1080)
            self.assertEqual(controls[7014].height, controls[14].height)
            self.assertEqual(controls[8014].height, controls[14].height)
            self.assertEqual(controls[9014].height, controls[14].height + 24)

    def test_dynamic_preview_keeps_its_single_close_button_aligned(self):
        obj = object.__new__(PreviewWindow)
        PreviewWindow.__init__(obj, str(ROOT), "Preview", "", "Close", lambda: [], lambda _row: [], Mock(), Mock())
        xml = ET.parse(ROOT / "resources/skins/Default/1080i/curatr-collection-dialog.xml")
        controls = {int(c.get("id")): FakeControl() for c in xml.iter("control") if c.get("id")}
        obj.getControl = controls.__getitem__
        obj.setFocus = Mock()
        obj.close = Mock()
        obj.onInit()
        self.assertFalse(obj.failed)
        self.assertEqual(controls[300].xy, (740, 965))
        self.assertEqual(controls[7300].xy, controls[300].xy)
        self.assertEqual(controls[8300].xy, controls[300].xy)
        self.assertEqual(controls[9300].xy, (728, 953))
        self.assertFalse(controls[301].visible)
        obj.close.assert_not_called()

    def test_sheen_control_binding_and_artwork_preview_flair(self):
        for path in (ROOT / "resources/skins/Default/1080i").glob("*.xml"):
            for c in ET.parse(path).iter("control"):
                if c.findtext("description") == MARKER and "rounded_focus_outline" not in c.findtext("texture", ""):
                    self.assertEqual(c.findtext("colordiffuse"), "$INFO[Window(Home).Property(CuratrSheenColour)]")
                    self.assertIsNone(c.find("texture").get("colordiffuse"))
        artwork = ET.parse(ROOT / "resources/skins/Default/1080i/curatr-artwork-editor.xml")
        flair = [c for c in artwork.iter("control") if c.findtext("description") == MARKER and geometry(c) == (105, 155, 550, 735)]
        self.assertEqual(len(flair), 1)
        self.assertIn("button_sheen_", flair[0].findtext("texture"))

    def test_list_card_dotted_sheen_is_faint_and_above_artwork_but_below_text(self):
        for name, ident in (("collection-manager", 100), ("collection-dialog", 100), ("folder-contents", 100), ("folder-settings", 400)):
            xml = ET.parse(ROOT / ("resources/skins/Default/1080i/curatr-%s.xml" % name))
            for kind in ("itemlayout", "focusedlayout"):
                layout = xml.find(".//control[@id='%d']/%s" % (ident, kind))
                lines = [c for c in layout.findall("control") if "list_sheen_" in c.findtext("texture", "")]
                self.assertEqual(len(lines), 1, (name, kind))
                with Image.open(ROOT / "resources/media" / lines[0].findtext("texture").removeprefix(PREFIX)) as im:
                    alpha = im.getchannel("A")
                    self.assertGreater(alpha.crop((0, 0, alpha.width, 36)).getextrema()[1], 0)
                    self.assertGreater(alpha.crop((0, alpha.height - 36, alpha.width, alpha.height)).getextrema()[1], 0)
                    self.assertEqual(alpha.crop((0, alpha.height // 2 - 8, alpha.width, alpha.height // 2 + 8)).getextrema(), (0, 0))
                    self.assertLessEqual(alpha.getextrema()[1], 14)
                    self.assertLess(ImageStat.Stat(alpha.crop((20, 0, alpha.width - 20, 36))).mean[0], 3)
                    row = [alpha.getpixel((x, 8)) for x in range(30, alpha.width - 30)]
                    self.assertGreater(max(row), min(row) + 4)
                    self.assertTrue(all(row[x] == row[x + 18] for x in range(len(row) - 18)))
                self.assertTrue(lines[0].findtext("texture").endswith("_v2.png"))
                index = list(layout).index(lines[0])
                self.assertTrue(all(list(layout).index(c) < index for c in layout.findall("control[@type='image']")
                                    if c is not lines[0]))
                self.assertTrue(all(list(layout).index(c) > index for c in layout.findall("control[@type='label']")))
            rims = [c for c in xml.find(".//control[@id='%d']/focusedlayout" % ident).findall("control") if "rounded_focus_outline" in c.findtext("texture", "")]
            self.assertEqual(len(rims), 1, name)
            self.assertIn("Control.HasFocus(%d)" % ident, rims[0].findtext("visible"))


class ActionAndLayoutChecks(unittest.TestCase):
    def test_all_requested_action_icons_and_disabled_state(self):
        cases = [("move_up", "Move Up", "caret-up"), ("move_front", "Move to Front", "caret-up"),
                 ("move_down", "Move Down", "caret-down"), ("move_back", "Move to Back", "caret-down"),
                 ("remove", "Remove Source", "xmark"), ("delete", "Delete Folder", "trash"),
                 ("settings", "Linked List Settings", "gear"), ("contents", "Manage Contents", "filter"),
                 ("artwork", "Artwork", "image"), ("details", "List Details", "circle-info"),
                 ("refresh", "Refresh Linked Items", "arrows-rotate"), ("sync", "Sync to Trakt", "arrows-rotate"),
                 ("reference", "Create Similar List", "plus"), ("folder", "Add to Folder", "folder-plus"),
                 ("template", "Save Request as Template", "bookmark")]
        for key, label, expected in cases:
            row = {"key": key, "label": label, "enabled": False}
            self.assertTrue(action_icons.action_icon(row).endswith("/" + expected + ".png"))
            for builder in (lambda r: CollectionManagerWindow._list_item(r, action=True), FolderContentsWindow._action_item, FolderSettingsWindow._action_item):
                item = builder(row)
                self.assertTrue(item.properties["CuratrActionIcon"].endswith(expected + ".png"))
                self.assertEqual(item.properties["CuratrActionTint"], "FF77747D")
        self.assertEqual(action_icons.action_icon({"key": "unknown", "label": "Unknown"}), "")
        labels = [row["label"] for row in core.Curator._managed_list_actions({})]
        self.assertIn("List Details", labels)
        self.assertNotIn("View List Details", labels)

    def test_icons_only_in_right_hand_action_lists_and_have_no_hit_targets(self):
        owners = {"curatr-collection-manager.xml": "200", "curatr-collection-dialog.xml": "200", "curatr-folder-contents.xml": "200", "curatr-folder-settings.xml": "410"}
        for path in (ROOT / "resources/skins/Default/1080i").glob("*.xml"):
            root = ET.parse(path).getroot()
            parents = {child: parent for parent in root.iter() for child in parent}
            icons = [c for c in root.iter("control") if c.findtext("description") == "Curatr action icon"]
            self.assertEqual(len(icons), 2 if path.name in owners else 0, path.name)
            for icon in icons:
                self.assertEqual(icon.get("type"), "image")
                self.assertIsNone(icon.get("id"))
                self.assertEqual(parents[parents[icon]].get("id"), owners[path.name])
                self.assertIsNone(icon.find("onclick"))
                layout = parents[icon]
                for label in layout.findall("control[@type='label']"):
                    self.assertEqual(label.findtext("textcolor"), "FFFFFFFF")

    def test_artwork_surrounds_match_their_card_fill_in_each_selection_state(self):
        for name in ("collection-manager", "folder-contents"):
            root = ET.parse(ROOT / ("resources/skins/Default/1080i/curatr-%s.xml" % name))
            for kind, property_name in (("itemlayout", "CuratrListCard"), ("focusedlayout", "CuratrListCardFocus")):
                layout = root.find(".//control[@id='100']/" + kind)
                surrounds = [c for c in layout.findall("control[@type='image']") if c.findtext("description") in ("Curatr artwork surround", "Curatr artwork corner mask")]
                self.assertEqual(len(surrounds), 2, (name, kind))
                card = next(c for c in layout.findall("control[@type='image']")
                            if geometry(c)[2] > 250 and geometry(c)[3] > 150
                            and "CuratrListCard" in c.find("texture").get("colordiffuse", ""))
                self.assertTrue(card.findtext("texture").endswith("/rounded_rect_v1.png"))
                sheen = next(c for c in layout.findall("control") if "list_sheen_" in c.findtext("texture", ""))
                for control in surrounds:
                    self.assertEqual(control.find("texture").get("colordiffuse"), "$INFO[Window(Home).Property(%s)]" % property_name)
                    self.assertEqual(control.find("texture").get("border"), "16")
                    with Image.open(ROOT / "resources/media" / control.findtext("texture").removeprefix(PREFIX)) as image:
                        base = image.convert("LA")
                    self.assertEqual(base.getchannel("L").getextrema(), (255, 255))
                    self.assertLess(list(layout).index(control), list(layout).index(sheen))
                    if control.findtext("description") == "Curatr artwork corner mask":
                        self.assertEqual(base.getchannel("A").getpixel((base.width // 2, base.height // 2)), 0)
                        self.assertGreater(base.getchannel("A").getpixel((0, 0)), 0)
                self.assertEqual(len([c for c in layout.findall("control[@type='image']")
                                      if c.findtext("texture") == "$INFO[ListItem.Art(thumb)]"]), 1)

    def test_compact_sources_are_centred_without_changing_full_list_rows(self):
        source = CollectionManagerWindow._list_item({"label": "My Picks", "detail": "Curatr List"})
        normal = CollectionManagerWindow._list_item({"label": "My Picks", "detail": "20 items", "status": "Manual refresh", "summary": "Request"})
        self.assertEqual(source.properties["CuratrCompact"], "true")
        self.assertEqual(normal.properties["CuratrCompact"], "false")
        self.assertNotIn("CuratrActionIcon", source.properties)
        root = ET.parse(ROOT / "resources/skins/Default/1080i/curatr-collection-manager.xml")
        for kind in ("itemlayout", "focusedlayout"):
            layout = root.find(".//control[@id='100']/" + kind)
            labels = [c for c in layout.findall("control") if c.findtext("description", "").startswith("Curatr centred source")]
            self.assertEqual(len(labels), 2)
            start, end = min(geometry(c)[1] for c in labels), max(geometry(c)[1] + geometry(c)[3] for c in labels)
            card = next(c for c in layout.findall("control") if "CuratrListCard" in (c.find("texture").get("colordiffuse", "") if c.find("texture") is not None else "") or "CuratrCard" in (c.find("texture").get("colordiffuse", "") if c.find("texture") is not None else ""))
            self.assertEqual((start + end) / 2, geometry(card)[1] + geometry(card)[3] / 2)
            for label in labels:
                self.assertEqual(label.findtext("aligny"), "center")

    def test_contents_labels_have_space_above_between_and_below(self):
        for name, ident in (("folder-contents", 100), ("folder-settings", 400)):
            root = ET.parse(ROOT / ("resources/skins/Default/1080i/curatr-%s.xml" % name))
            for kind in ("itemlayout", "focusedlayout"):
                layout = root.find(".//control[@id='%d']/%s" % (ident, kind))
                art = next(c for c in layout.findall("control") if c.findtext("texture") == "$INFO[ListItem.Art(thumb)]")
                title, description = layout.findall("control[@type='label']")
                card = next(c for c in layout.findall("control") if geometry(c)[2] > 250 and geometry(c)[3] > 150 and "Curatr" in (c.find("texture").get("colordiffuse", "") if c.find("texture") is not None else "") and "Primary" not in c.find("texture").get("colordiffuse", ""))
                self.assertGreaterEqual(geometry(title)[1] - geometry(art)[1] - geometry(art)[3], 14)
                self.assertGreaterEqual(geometry(description)[1] - geometry(title)[1] - geometry(title)[3], 6)
                self.assertGreaterEqual(geometry(card)[1] + geometry(card)[3] - geometry(description)[1] - geometry(description)[3], 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
