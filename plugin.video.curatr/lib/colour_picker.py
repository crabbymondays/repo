"""Theme colours and the menu background, edited together in one swatch window."""

import xbmc
import xbmcgui
import xbmcvfs

from .colours import COLOURS, colour_label
from .menu_background import background_source, current_choice, import_custom_background
from .ui_theme import bold, save_theme, skin_name, theme_config, theme_palette, _publish_palette


class ColourPickerWindow(xbmcgui.WindowXMLDialog):
    FIELDS = {20: "base", 22: "primary", 23: "secondary", 24: "tint", 25: "background"}

    def __new__(cls, addon, state=None, background=False):
        return super().__new__(cls, "curatr-colour-picker.xml",
                               xbmcvfs.translatePath(addon.getAddonInfo("path")), skin_name(), "1080i")

    def __init__(self, addon, state=None, background=False):
        self.addon = addon
        self.state = dict(state or {})
        self.config = theme_config(addon)
        self.active_field = "background" if background else "base"
        self.choice = current_choice(addon, self.state)
        self.keys = list(COLOURS)
        self.result = None

    def onInit(self):
        self.getControl(10).setLabel(bold("Customise Theme"))
        self.getControl(30).setVisible(False)
        self._refresh()
        self.setFocus(self.getControl(100))

    def _refresh(self):
        background = self.active_field == "background"
        palette = theme_palette(self.addon, self.config)
        _publish_palette(palette)
        labels = {20: "Theme", 22: "Highlight", 23: "Secondary highlight", 24: "Background tint", 25: "Menu background"}
        background_label = {"theme": "Match Theme", "custom": "Custom Image"}.get(self.choice, colour_label(self.choice))
        for cid, field in self.FIELDS.items():
            value = background_label if field == "background" else colour_label(self.config[field])
            label = "%s · %s" % (labels[cid], value)
            selected = field == self.active_field
            self.getControl(1000 + cid).setColorDiffuse("0x" + palette["CuratrPrimary" if selected else "CuratrButtonFaint"])
            self.getControl(cid).setLabel(bold(label) if selected else label)
            self.getControl(cid).setEnabled(cid in (20, 25) or self.config["custom"])
        self.getControl(21).setLabel("Custom colours · %s" % ("On" if self.config["custom"] else "Off"))
        for cid in (26, 27, 1026, 1027, 31):
            self.getControl(cid).setVisible(background)
        for cid in (26, 27):
            self.getControl(cid).setEnabled(background)
        for cid in (32, 33):
            self.getControl(cid).setVisible(not background)
        for cid, key, label in ((26, "theme", "Match Theme"), (27, "custom", "Custom Image")):
            selected = self.choice == key
            self.getControl(1000 + cid).setColorDiffuse("0x" + palette["CuratrPrimary" if selected else "CuratrButtonFaint"])
            self.getControl(cid).setLabel(bold(label) if selected else label)
        if background:
            choice = self.config["tint"] if self.choice == "theme" else self.choice
            self.getControl(31).setImage(background_source(self.addon, self.state, choice))
            self.getControl(34).setLabel(background_label)
        selected = self.choice if background else self.config[self.active_field]
        items = []
        for key in self.keys:
            item = xbmcgui.ListItem(label=colour_label(key), offscreen=True)
            item.setProperty("CuratrSwatch", "FF" + COLOURS[key][1].lstrip("#"))
            item.setProperty("CuratrSelected", "true" if key == selected else "false")
            items.append(item)
        panel = self.getControl(100)
        panel.setPosition(880, 300 if background else 230)
        panel.setHeight(550)
        panel.setNavigation(self.getControl(26 if background else 20), self.getControl(300),
                            self.getControl(300), self.getControl(300))
        panel.reset()
        panel.addItems(items)
        panel.selectItem(self.keys.index(selected) if selected in self.keys else 0)

    def onClick(self, control_id):
        if control_id in (300, 301):
            if control_id == 300:
                save_theme(self.addon, self.config)
                self.result = {"theme": dict(self.config), "background": {
                    "key": self.choice, "source": self.state.get("menu_background_source", "")}}
            self.close()
            return
        if control_id == 100:
            position = self.getControl(100).getSelectedPosition()
            if not 0 <= position < len(self.keys):
                return
            key = self.keys[position]
            if self.active_field == "background":
                self.choice = key
            elif self.active_field == "base":
                self.config.update(base=key, primary=key, secondary=key, tint=key, custom=False)
            else:
                self.config[self.active_field] = key
            self._refresh()
        elif control_id == 26 and self.active_field == "background":
            self.choice = "theme"
            self._refresh()
        elif control_id == 27 and self.active_field == "background":
            source = xbmcgui.Dialog().browseSingle(2, "Choose a background image", "files", ".png|.jpg|.jpeg|.webp",
                                                   defaultt=self.state.get("menu_background_source", ""))
            if source:
                try:
                    self.state["menu_background_source"] = import_custom_background(self.addon, source)
                    self.choice = "custom"
                    self._refresh()
                except (OSError, ValueError, RuntimeError) as exc:
                    xbmcgui.Dialog().ok("Menu Background", str(exc))
        elif control_id == 21:
            self.config["custom"] = not self.config["custom"]
            if not self.config["custom"]:
                base = self.config["base"]
                self.config.update(primary=base, secondary=base, tint=base)
                if self.active_field not in ("base", "background"):
                    self.active_field = "base"
            self._refresh()
        elif control_id in self.FIELDS and (control_id in (20, 25) or self.config["custom"]):
            self.active_field = self.FIELDS[control_id]
            self._refresh()
            self.setFocus(self.getControl(100))

    def onAction(self, action):
        if action.getId() in (9, 10, 92):
            self.close()


def choose_colours(addon, state=None, background=False):
    window = ColourPickerWindow(addon, state, background)
    try:
        window.doModal()
        return window.result
    except Exception as exc:
        xbmc.log("curatr colour picker: %s" % exc, xbmc.LOGERROR)
        raise
    finally:
        window.close()
        _publish_palette(theme_palette(addon))
