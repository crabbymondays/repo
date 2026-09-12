import xbmcgui

from .ui_theme import bold, skin_name


_BACK_ACTIONS = {9, 10, 92}


class CollectionManagerWindow(xbmcgui.WindowXMLDialog):
    ENTRY_LIST_ID = 100
    ACTION_LIST_ID = 200
    EMPTY_LABEL_ID = 20
    ACTIVE_LABEL_ID = 21
    ACTIVE_HELP_ID = 22
    CREATE_ID = 300
    CLOSE_ID = 301

    def __new__(
        cls, addon_path, heading, description, create_label, entry_provider,
        action_provider, action_handler, create_handler,
    ):
        return super().__new__(
            cls, "curatr-collection-manager.xml", addon_path, skin_name(), "1080i"
        )

    def __init__(
        self, addon_path, heading, description, create_label, entry_provider,
        action_provider, action_handler, create_handler,
    ):
        self.heading = str(heading or "Manage")
        self.description = str(description or "Select an item to manage.")
        self.create_label = str(create_label or "Create")
        self.entry_provider = entry_provider
        self.action_provider = action_provider
        self.action_handler = action_handler
        self.create_handler = create_handler
        self.entries = []
        self.actions = []
        self.active_key = ""
        self.result = None
        self.failed = False

    @staticmethod
    def _list_item(row, action=False):
        item = xbmcgui.ListItem(label=str(row.get("label") or "Item"), offscreen=True)
        if action:
            item.setProperty("CuratrDetail", str(row.get("detail") or ""))
            item.setProperty("CuratrDisabled", "true" if not row.get("enabled", True) else "false")
            return item
        item.setProperty("CuratrDetail", str(row.get("detail") or ""))
        item.setProperty("CuratrStatus", str(row.get("status") or ""))
        item.setProperty("CuratrSummary", str(row.get("summary") or ""))
        art = row.get("art") if isinstance(row.get("art"), dict) else {}
        if art:
            item.setArt(art)
        return item

    def onInit(self):
        try:
            self.getControl(10).setLabel(bold(self.heading))
            self.getControl(11).setLabel(self.description)
            self.getControl(self.CREATE_ID).setLabel(bold(self.create_label))
            self._refresh_entries()
            if self.entries:
                self._open_actions(set_focus=False)
            else:
                self._leave_actions(set_focus=False)
            target = self.ENTRY_LIST_ID if self.entries else self.CREATE_ID
            self.setFocus(self.getControl(target))
        except Exception:
            self.failed = True
            self.close()

    def _selected_entry(self):
        if not self.entries:
            return None
        position = self.getControl(self.ENTRY_LIST_ID).getSelectedPosition()
        if 0 <= position < len(self.entries):
            return self.entries[position]
        return None

    def _refresh_entries(self, preferred_key=""):
        previous_position = 0
        try:
            previous_position = max(0, self.getControl(self.ENTRY_LIST_ID).getSelectedPosition())
        except Exception:
            pass
        self.entries = [
            dict(row) for row in (self.entry_provider() or [])
            if isinstance(row, dict) and str(row.get("key") or "")
        ]
        control = self.getControl(self.ENTRY_LIST_ID)
        control.reset()
        control.addItems([self._list_item(row) for row in self.entries])
        has_entries = bool(self.entries)
        control.setVisible(has_entries)
        self.getControl(self.EMPTY_LABEL_ID).setVisible(not has_entries)
        if not has_entries:
            return
        position = min(previous_position, len(self.entries) - 1)
        wanted = str(preferred_key or "")
        if wanted:
            position = next((
                index for index, row in enumerate(self.entries)
                if str(row.get("key") or "") == wanted
            ), position)
        control.selectItem(position)

    def _open_actions(self, preferred_action="", set_focus=True):
        entry = self._selected_entry()
        if not entry:
            self._leave_actions(set_focus=False)
            return
        self.active_key = str(entry.get("key") or "")
        self.actions = [
            dict(row) for row in (self.action_provider(dict(entry)) or [])
            if isinstance(row, dict) and str(row.get("key") or "")
        ]
        if not self.actions:
            self._leave_actions(set_focus=False)
            return
        actions = self.getControl(self.ACTION_LIST_ID)
        actions.reset()
        actions.addItems([self._list_item(row, action=True) for row in self.actions])
        actions.setVisible(True)
        actions.setEnabled(True)
        self.getControl(self.ACTIVE_LABEL_ID).setLabel(bold("Choose an action"))
        self.getControl(self.ACTIVE_HELP_ID).setLabel("")
        position = 0
        wanted = str(preferred_action or "")
        if wanted:
            position = next((
                index for index, row in enumerate(self.actions)
                if str(row.get("key") or "") == wanted
            ), 0)
        actions.selectItem(position)
        if set_focus:
            self.setFocus(actions)

    def _sync_actions(self):
        entry = self._selected_entry()
        selected_key = str(entry.get("key") or "") if entry else ""
        if selected_key != self.active_key:
            self._open_actions(set_focus=False)

    def _leave_actions(self, set_focus=True):
        actions = self.getControl(self.ACTION_LIST_ID)
        actions.setVisible(False)
        actions.setEnabled(False)
        self.actions = []
        self.active_key = ""
        self.getControl(self.ACTIVE_LABEL_ID).setLabel(bold("Select an item"))
        self.getControl(self.ACTIVE_HELP_ID).setLabel("")
        if set_focus:
            target = self.ENTRY_LIST_ID if self.entries else self.CREATE_ID
            self.setFocus(self.getControl(target))

    @staticmethod
    def _result_key(result):
        if not isinstance(result, dict):
            return ""
        return str(result.get("local_id") or result.get("id") or result.get("trakt_id") or "")

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
        try:
            result = self.action_handler(self.active_key, action_key)
        except Exception as exc:
            xbmcgui.Dialog().ok("curatr", str(exc))
            return
        if isinstance(result, dict) and result.get("kind") == "list_preview":
            self.result = result
            self.close()
            return
        wanted = self.active_key
        self._refresh_entries(wanted)
        if any(str(row.get("key") or "") == wanted for row in self.entries):
            self._open_actions(action_key, set_focus=True)
        else:
            if self.entries:
                self._open_actions(set_focus=False)
                self.setFocus(self.getControl(self.ENTRY_LIST_ID))
            else:
                self._leave_actions()

    def _create(self):
        try:
            result = self.create_handler()
        except Exception as exc:
            xbmcgui.Dialog().ok("curatr", str(exc))
            return
        if isinstance(result, dict) and result.get("kind") == "list_preview":
            self.result = result
            self.close()
            return
        self._refresh_entries(self._result_key(result))
        if self.entries:
            self._open_actions(set_focus=False)
            self.setFocus(self.getControl(self.ENTRY_LIST_ID))
        else:
            self._leave_actions()

    def onClick(self, control_id):
        if control_id == self.ENTRY_LIST_ID:
            self._open_actions(set_focus=False)
        elif control_id == self.ACTION_LIST_ID:
            self._run_action()
        elif control_id == self.CREATE_ID:
            self._create()
        elif control_id == self.CLOSE_ID:
            self.close()

    def onFocus(self, control_id):
        if control_id == self.ENTRY_LIST_ID:
            self._sync_actions()

    def onAction(self, action):
        if action.getId() not in _BACK_ACTIONS:
            try:
                if self.getFocusId() == self.ENTRY_LIST_ID:
                    self._sync_actions()
            except Exception:
                pass
            return
        try:
            focus_id = self.getFocusId()
        except Exception:
            focus_id = self.ENTRY_LIST_ID
        if focus_id == self.ACTION_LIST_ID and self.entries:
            self.setFocus(self.getControl(self.ENTRY_LIST_ID))
            return
        self.close()


def manage_collection(
    addon_path, heading, description, create_label, entry_provider,
    action_provider, action_handler, create_handler,
):
    window = None
    try:
        window = CollectionManagerWindow(
            addon_path, heading, description, create_label, entry_provider,
            action_provider, action_handler, create_handler,
        )
        window.doModal()
        return "fallback" if window.failed else window.result
    except Exception:
        return "fallback"
    finally:
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
