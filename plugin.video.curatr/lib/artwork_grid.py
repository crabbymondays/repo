import xbmcgui

from .ui_theme import bold, skin_name


_ACTION_PARENT_DIR = 9
_ACTION_PREVIOUS_MENU = 10
_ACTION_NAV_BACK = 92
_BACK_ACTIONS = {_ACTION_PARENT_DIR, _ACTION_PREVIOUS_MENU, _ACTION_NAV_BACK}


class ArtworkGridWindow(xbmcgui.WindowXMLDialog):
    """Small skin-independent artwork picker backed by Kodi panel controls."""

    ICON_PANEL_ID = 100
    FANART_PANEL_ID = 200

    def __new__(cls, addon_path, heading, entries, layout="icon", selection_handler=None):
        return super().__new__(
            cls, "curatr-artwork-grid.xml", addon_path, skin_name(), "1080i"
        )

    def __init__(self, addon_path, heading, entries, layout="icon", selection_handler=None):
        self.heading = str(heading or "Choose artwork")
        self.entries = list(entries or [])
        self.layout = "fanart" if layout == "fanart" else "icon"
        self.selected_index = -1
        self.selected_entry = None
        self.selection_handler = selection_handler

    def onInit(self):
        panel_id = self.FANART_PANEL_ID if self.layout == "fanart" else self.ICON_PANEL_ID
        other_id = self.ICON_PANEL_ID if panel_id == self.FANART_PANEL_ID else self.FANART_PANEL_ID
        try:
            self.getControl(10).setLabel(bold(self.heading))
            self.getControl(other_id).setVisible(False)
            panel = self.getControl(panel_id)
            panel.setVisible(True)
            items = []
            for entry in self.entries:
                label = str(entry.get("label") or "Artwork")
                item = xbmcgui.ListItem(label=label, offscreen=True)
                source = str(entry.get("preview_source") or entry.get("source") or "")
                if source:
                    item.setArt({"icon": source, "thumb": source})
                item.setProperty("CuratrSelected", "true" if entry.get("selected") else "false")
                items.append(item)
            panel.reset()
            panel.addItems(items)
            panel.selectItem(next((i for i, entry in enumerate(self.entries) if entry.get("selected")), 0))
            self.setFocus(panel)
        except Exception:
            self.close()

    def onClick(self, control_id):
        if control_id not in (self.ICON_PANEL_ID, self.FANART_PANEL_ID):
            return
        position = self.getControl(control_id).getSelectedPosition()
        if 0 <= position < len(self.entries):
            entry = dict(self.entries[position])
            if self.selection_handler is not None:
                entry = self.selection_handler(entry)
                if entry is None:
                    return
            self.selected_entry = entry
            self.selected_index = position
            self.close()

    def onAction(self, action):
        if action.getId() in _BACK_ACTIONS:
            self.close()


def choose_artwork(addon_path, heading, entries, layout="icon", selection_handler=None):
    entries = list(entries or [])
    if not entries:
        return None
    window = ArtworkGridWindow(addon_path, heading, entries, layout, selection_handler)
    try:
        window.doModal()
        if 0 <= window.selected_index < len(entries):
            return window.selected_entry or entries[window.selected_index]
        return None
    finally:
        window.close()
