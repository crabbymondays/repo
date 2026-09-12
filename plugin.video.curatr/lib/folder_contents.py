import xbmcgui

from .ui_theme import bold, skin_name


_BACK_ACTIONS = {9, 10, 92}
_ADD_KEY = "__curatr_add_folder_item__"


class FolderContentsWindow(xbmcgui.WindowXMLDialog):
    GRID_ID = 100
    ACTION_LIST_ID = 200
    ACTIVE_LABEL_ID = 20
    ACTIVE_HELP_ID = 21
    CLOSE_ID = 300

    def __new__(
        cls, addon_path, heading, description, entry_provider, action_provider,
        action_handler, add_handler, add_art,
    ):
        return super().__new__(
            cls, "curatr-folder-contents.xml", addon_path, skin_name(), "1080i"
        )

    def __init__(
        self, addon_path, heading, description, entry_provider, action_provider,
        action_handler, add_handler, add_art,
    ):
        self.heading = str(heading or "Folder Contents")
        self.description = str(description or "Choose an item to manage or add something new.")
        self.entry_provider = entry_provider
        self.action_provider = action_provider
        self.action_handler = action_handler
        self.add_handler = add_handler
        self.add_art = str(add_art or "")
        self.entries = []
        self.actions = []
        self.active_key = ""
        self.failed = False

    @staticmethod
    def _grid_item(row):
        item = xbmcgui.ListItem(label=str(row.get("label") or "Item"), offscreen=True)
        item.setProperty("CuratrDetail", str(row.get("detail") or ""))
        art = row.get("art") if isinstance(row.get("art"), dict) else {}
        if art:
            item.setArt(art)
        return item

    @staticmethod
    def _action_item(row):
        item = xbmcgui.ListItem(label=str(row.get("label") or "Action"), offscreen=True)
        item.setProperty("CuratrDetail", str(row.get("detail") or ""))
        item.setProperty("CuratrDisabled", "true" if not row.get("enabled", True) else "false")
        return item

    def onInit(self):
        try:
            self.getControl(10).setLabel(bold(self.heading))
            self.getControl(11).setLabel(self.description)
            self._refresh_entries()
            self.getControl(self.GRID_ID).selectItem(0)
            self._sync_actions()
            self.setFocus(self.getControl(self.GRID_ID))
        except Exception:
            self.failed = True
            self.close()

    def _source_entries(self):
        return [
            dict(row) for row in (self.entry_provider() or [])
            if isinstance(row, dict) and str(row.get("key") or "")
        ]

    def _refresh_entries(self, preferred_key=""):
        previous_position = 0
        try:
            previous_position = max(0, self.getControl(self.GRID_ID).getSelectedPosition())
        except Exception:
            pass
        self.entries = self._source_entries()
        self.entries.append({
            "key": _ADD_KEY,
            "label": "Add Item",
            "detail": "curatr, Trakt or a path",
            "kind": "add",
            "art": {"icon": self.add_art, "thumb": self.add_art},
        })
        grid = self.getControl(self.GRID_ID)
        grid.reset()
        grid.addItems([self._grid_item(row) for row in self.entries])
        position = min(previous_position, len(self.entries) - 1)
        wanted = str(preferred_key or "")
        if wanted:
            position = next((
                index for index, row in enumerate(self.entries)
                if str(row.get("key") or "") == wanted
            ), position)
        grid.selectItem(position)

    def _selected_entry(self):
        position = self.getControl(self.GRID_ID).getSelectedPosition()
        if 0 <= position < len(self.entries):
            return self.entries[position]
        return None

    def _open_actions(self, entry, preferred_action="", set_focus=True):
        self.active_key = str(entry.get("key") or "")
        self.actions = [
            dict(row) for row in (self.action_provider(dict(entry)) or [])
            if isinstance(row, dict) and str(row.get("key") or "")
        ]
        if not self.actions:
            self._leave_actions(set_focus=False)
            return
        control = self.getControl(self.ACTION_LIST_ID)
        control.reset()
        control.addItems([self._action_item(row) for row in self.actions])
        control.setVisible(True)
        control.setEnabled(True)
        self.getControl(self.ACTIVE_LABEL_ID).setLabel(bold("Choose an action"))
        self.getControl(self.ACTIVE_HELP_ID).setLabel("")
        position = 0
        wanted = str(preferred_action or "")
        if wanted:
            position = next((
                index for index, row in enumerate(self.actions)
                if str(row.get("key") or "") == wanted
            ), 0)
        control.selectItem(position)
        if set_focus:
            self.setFocus(control)

    def _sync_actions(self):
        entry = self._selected_entry()
        if not entry or entry.get("key") == _ADD_KEY:
            self._leave_actions(set_focus=False)
        elif str(entry.get("key") or "") != self.active_key:
            self._open_actions(entry, set_focus=False)

    def _leave_actions(self, set_focus=True):
        control = self.getControl(self.ACTION_LIST_ID)
        control.setVisible(False)
        control.setEnabled(False)
        self.actions = []
        self.active_key = ""
        self.getControl(self.ACTIVE_LABEL_ID).setLabel(bold("Select an item"))
        self.getControl(self.ACTIVE_HELP_ID).setLabel("")
        if set_focus:
            self.setFocus(self.getControl(self.GRID_ID))

    def _add_item(self):
        before = {str(row.get("key") or "") for row in self.entries if row.get("key") != _ADD_KEY}
        try:
            self.add_handler()
        except Exception as exc:
            xbmcgui.Dialog().ok("curatr", str(exc))
            return
        current = self._source_entries()
        new_key = next((
            str(row.get("key") or "") for row in current
            if str(row.get("key") or "") not in before
        ), "")
        self._refresh_entries(new_key or _ADD_KEY)
        self._sync_actions()

    def _run_action(self):
        position = self.getControl(self.ACTION_LIST_ID).getSelectedPosition()
        if not (0 <= position < len(self.actions)):
            return
        action = self.actions[position]
        action_key = str(action.get("key") or "")
        if not action.get("enabled", True):
            xbmcgui.Dialog().notification(
                self.heading, str(action.get("detail") or "That action is not available"),
                xbmcgui.NOTIFICATION_INFO, 2200,
            )
            return
        wanted = self.active_key
        try:
            self.action_handler(wanted, action_key)
        except Exception as exc:
            xbmcgui.Dialog().ok("curatr", str(exc))
            return
        self._refresh_entries(wanted)
        entry = next((
            row for row in self.entries if str(row.get("key") or "") == wanted
        ), None)
        if entry:
            self._open_actions(entry, action_key)
        else:
            self._sync_actions()
            self.setFocus(self.getControl(self.GRID_ID))

    def onClick(self, control_id):
        if control_id == self.GRID_ID:
            entry = self._selected_entry()
            if not entry:
                return
            if str(entry.get("key") or "") == _ADD_KEY:
                self._add_item()
            else:
                self._open_actions(entry, set_focus=False)
        elif control_id == self.ACTION_LIST_ID:
            self._run_action()
        elif control_id == self.CLOSE_ID:
            self.close()

    def onFocus(self, control_id):
        if control_id == self.GRID_ID:
            self._sync_actions()

    def onAction(self, action):
        focus = self.getFocusId()
        if action.getId() not in _BACK_ACTIONS:
            if focus == self.GRID_ID:
                self._sync_actions()
            return
        if focus == self.ACTION_LIST_ID:
            self.setFocus(self.getControl(self.GRID_ID))
            return
        self.close()


def manage_folder_contents(
    addon_path, heading, description, entry_provider, action_provider,
    action_handler, add_handler, add_art,
):
    window = None
    try:
        window = FolderContentsWindow(
            addon_path, heading, description, entry_provider, action_provider,
            action_handler, add_handler, add_art,
        )
        window.doModal()
        return "fallback" if window.failed else None
    except Exception:
        return "fallback"
    finally:
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
