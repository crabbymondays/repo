import xbmc
import xbmcgui

from .ui_theme import bold, skin_name, style_tab


_BACK_ACTIONS = {9, 10, 92}
_ADD_KEY = "__curatr_add_folder_item__"


class FolderSettingsWindow(xbmcgui.WindowXMLDialog):
    TAB_IDS = {100: "appearance", 101: "contents"}
    ROW_IDS = (200, 201, 202)
    GRID_ID = 400
    ACTION_LIST_ID = 410
    SCROLLBAR_ID = 420
    SIDE_PANEL_ID = 29
    SIDE_LABEL_ID = 30
    CREATE_ID = 300
    CANCEL_ID = 301
    FIELDS = {"appearance": ("name", "description", "artwork"), "contents": ()}

    def __new__(
        cls, addon_path, draft, editor, formatter, existing=False,
        content_rows=None, content_actions=None, content_handler=None,
        content_add_handler=None, add_art="",
    ):
        return super().__new__(
            cls, "curatr-folder-settings.xml", addon_path, skin_name(), "1080i"
        )

    def __init__(
        self, addon_path, draft, editor, formatter, existing=False,
        content_rows=None, content_actions=None, content_handler=None,
        content_add_handler=None, add_art="",
    ):
        self.draft = dict(draft or {})
        self.editor = editor
        self.formatter = formatter
        self.content_rows = content_rows
        self.content_actions = content_actions
        self.content_handler = content_handler
        self.content_add_handler = content_add_handler
        self.add_art = str(add_art or "")
        self.tab = "appearance"
        self.result = "cancel"
        self.existing = bool(existing)
        self.entries = []
        self.actions = []
        self.active_key = ""
        self.failed = False
        if self.existing:
            self.FIELDS = dict(self.FIELDS)
            self.FIELDS["contents"] = ("manage_contents",)

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
        item.setProperty(
            "CuratrDisabled", "true" if not row.get("enabled", True) else "false"
        )
        return item

    def onInit(self):
        try:
            if self.existing:
                self.getControl(self.CREATE_ID).setLabel(bold("Save Changes"))
                self.getControl(20).setLabel("Save when you're ready.")
            self._show_tab("appearance")
        except Exception as exc:
            xbmc.log("curatr: could not initialise Folder Settings: %s" % exc, xbmc.LOGERROR)
            try:
                self._show_basic_appearance()
            except Exception as fallback_exc:
                self.failed = True
                xbmc.log(
                    "curatr: Folder Settings recovery failed: %s" % fallback_exc,
                    xbmc.LOGERROR,
                )
                self.close()

    def _show_basic_appearance(self):
        """Recover the core custom window if an optional control is unavailable."""
        self.tab = "appearance"
        self._set_view("appearance")
        for control_id, name in self.TAB_IDS.items():
            style_tab(self, control_id, name.title(), name == "appearance")
        for index, control_id in enumerate(self.ROW_IDS):
            control = self.getControl(control_id)
            control.setVisible(True)
            control.setLabel(
                self.formatter(self.FIELDS["appearance"][index], self.draft)
            )
        self.getControl(20).setLabel(
            "Save when you're ready."
            if self.existing else "Folders can be created empty."
        )
        self._set_focus(self.getControl(self.ROW_IDS[0]))

    @staticmethod
    def _set_navigation(control, up, down, left, right):
        """Keep a platform-specific navigation failure from closing the dialog."""
        try:
            control.setNavigation(up, down, left, right)
        except Exception:
            pass

    def _set_focus(self, control):
        try:
            self.setFocus(control)
        except Exception:
            pass

    def _set_view(self, view):
        try:
            self.setProperty("CuratrFolderView", str(view or "appearance"))
        except Exception:
            pass

    def _set_visible(self, control_id, visible):
        try:
            self.getControl(control_id).setVisible(visible)
        except Exception as exc:
            xbmc.log(
                "curatr: Folder Settings control %s visibility unavailable: %s"
                % (control_id, exc),
                xbmc.LOGDEBUG,
            )

    def _set_contents_visible(self, visible):
        self._set_visible(self.GRID_ID, visible)
        self._set_visible(self.ACTION_LIST_ID, visible)
        for control_id in (self.SCROLLBAR_ID, self.SIDE_PANEL_ID, self.SIDE_LABEL_ID):
            self._set_visible(control_id, visible)

    def _show_tab(self, tab):
        self.tab = tab
        self._set_view(
            "contents" if tab == "contents" and not self.existing else "appearance"
        )
        for control_id, name in self.TAB_IDS.items():
            style_tab(self, control_id, name.title(), name == tab)
        if tab == "contents" and not self.existing:
            self._show_draft_contents()
            return

        self._set_contents_visible(False)
        self.actions = []
        self.active_key = ""
        fields = self.FIELDS[tab]
        visible_rows = []
        for index, control_id in enumerate(self.ROW_IDS):
            control = self.getControl(control_id)
            visible = index < len(fields)
            control.setVisible(visible)
            if visible:
                control.setLabel(self.formatter(fields[index], self.draft))
                visible_rows.append(control)
        if not visible_rows:
            return
        self.getControl(20).setLabel(
            "Save when you're ready." if self.existing else "Folders can be created empty."
        )
        tab_control = self.getControl(
            next(control_id for control_id, name in self.TAB_IDS.items() if name == tab)
        )
        create = self.getControl(self.CREATE_ID)
        cancel = self.getControl(self.CANCEL_ID)
        for index, control in enumerate(visible_rows):
            above = visible_rows[index - 1] if index else create
            below = visible_rows[index + 1] if index + 1 < len(visible_rows) else create
            self._set_navigation(control, above, below, tab_control, control)
        self._set_navigation(create, visible_rows[-1], create, cancel, cancel)
        self._set_navigation(cancel, visible_rows[-1], cancel, create, create)
        self._set_navigation(self.getControl(100),
            self.getControl(101), self.getControl(101), self.getControl(100),
            visible_rows[0] if tab == "appearance" else self.getControl(100),
        )
        self._set_navigation(self.getControl(101),
            self.getControl(100), self.getControl(100), self.getControl(101),
            visible_rows[0] if tab == "contents" else self.getControl(101),
        )
        self._set_focus(visible_rows[0])

    def _source_entries(self):
        if not self.content_rows:
            return []
        return [
            dict(row) for row in (self.content_rows(dict(self.draft)) or [])
            if isinstance(row, dict) and str(row.get("key") or "")
        ]

    def _refresh_contents(self, preferred_key=""):
        previous_position = 0
        try:
            previous_position = max(
                0, self.getControl(self.GRID_ID).getSelectedPosition()
            )
        except Exception:
            pass
        self.entries = self._source_entries()
        self.entries.append({
            "key": _ADD_KEY, "label": "Add Item", "detail": "", "kind": "add",
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

    def _show_draft_contents(self):
        for control_id in self.ROW_IDS:
            self._set_visible(control_id, False)
        self._set_visible(self.GRID_ID, True)
        self._set_visible(self.SCROLLBAR_ID, True)
        self._set_visible(self.SIDE_PANEL_ID, True)
        self._set_visible(self.SIDE_LABEL_ID, True)
        self.getControl(self.SIDE_LABEL_ID).setLabel(bold("Select an item"))
        self._set_visible(self.ACTION_LIST_ID, False)
        self.getControl(20).setLabel("Folders can be created empty.")
        self.actions = []
        self.active_key = ""
        self._refresh_contents()
        grid = self.getControl(self.GRID_ID)
        create = self.getControl(self.CREATE_ID)
        cancel = self.getControl(self.CANCEL_ID)
        self._set_navigation(grid, grid, create, self.getControl(101), self.getControl(self.ACTION_LIST_ID))
        self._set_navigation(create, grid, create, cancel, cancel)
        self._set_navigation(cancel, grid, cancel, create, create)
        self._set_navigation(self.getControl(100),
            self.getControl(101), self.getControl(101), self.getControl(100),
            self.getControl(200),
        )
        self._set_navigation(self.getControl(101),
            self.getControl(100), self.getControl(100), self.getControl(101), grid,
        )
        self._set_focus(grid)

    def _selected_entry(self):
        position = self.getControl(self.GRID_ID).getSelectedPosition()
        if 0 <= position < len(self.entries):
            return self.entries[position]
        return None

    def _open_actions(self, entry, preferred_action=""):
        if not self.content_actions:
            return
        self.active_key = str(entry.get("key") or "")
        self.actions = [
            dict(row) for row in (self.content_actions(dict(entry)) or [])
            if isinstance(row, dict) and str(row.get("key") or "")
        ]
        if not self.actions:
            self.active_key = ""
            return
        control = self.getControl(self.ACTION_LIST_ID)
        control.reset()
        control.addItems([self._action_item(row) for row in self.actions])
        control.setVisible(True)
        self.getControl(self.SIDE_LABEL_ID).setLabel(bold("Choose an action"))
        self._set_navigation(self.getControl(self.GRID_ID), self.getControl(self.GRID_ID), self.getControl(self.CREATE_ID), self.getControl(101), control)
        self._set_navigation(control, control, control, self.getControl(self.GRID_ID), control)
        position = 0
        wanted = str(preferred_action or "")
        if wanted:
            position = next((
                index for index, row in enumerate(self.actions)
                if str(row.get("key") or "") == wanted
            ), 0)
        control.selectItem(position)
        self._set_focus(control)

    def _leave_actions(self):
        control = self.getControl(self.ACTION_LIST_ID)
        control.setVisible(False)
        self.actions = []
        self.active_key = ""
        self.getControl(self.SIDE_LABEL_ID).setLabel(bold("Select an item"))
        self._set_focus(self.getControl(self.GRID_ID))

    def _add_item(self):
        if not self.content_add_handler:
            return
        before = {
            str(row.get("key") or "") for row in self.entries
            if row.get("key") != _ADD_KEY
        }
        updated = self.content_add_handler(dict(self.draft))
        if isinstance(updated, dict):
            self.draft = updated
        current = self._source_entries()
        new_key = next((
            str(row.get("key") or "") for row in current
            if str(row.get("key") or "") not in before
        ), "")
        self._refresh_contents(new_key or _ADD_KEY)

    def _run_action(self):
        if not self.content_handler:
            return
        position = self.getControl(self.ACTION_LIST_ID).getSelectedPosition()
        if not (0 <= position < len(self.actions)):
            return
        action = self.actions[position]
        if not action.get("enabled", True):
            return
        wanted = self.active_key
        updated = self.content_handler(
            dict(self.draft), wanted, str(action.get("key") or "")
        )
        if isinstance(updated, dict):
            self.draft = updated
        self._refresh_contents(wanted)
        entry = next((
            row for row in self.entries if str(row.get("key") or "") == wanted
        ), None)
        if entry:
            self._open_actions(entry, str(action.get("key") or ""))
        else:
            self._leave_actions()

    def onClick(self, control_id):
        if control_id in self.TAB_IDS:
            self._show_tab(self.TAB_IDS[control_id])
            return
        if control_id in (self.CREATE_ID, self.CANCEL_ID):
            self.result = "create" if control_id == self.CREATE_ID else "cancel"
            self.close()
            return
        if control_id == self.GRID_ID and self.tab == "contents" and not self.existing:
            entry = self._selected_entry()
            if not entry:
                return
            if str(entry.get("key") or "") == _ADD_KEY:
                self._add_item()
            else:
                self._open_actions(entry)
            return
        if control_id == self.ACTION_LIST_ID:
            self._run_action()
            return
        if control_id not in self.ROW_IDS:
            return
        index = self.ROW_IDS.index(control_id)
        fields = self.FIELDS[self.tab]
        if index >= len(fields):
            return
        updated = self.editor(fields[index], dict(self.draft))
        if isinstance(updated, dict):
            self.draft = updated
        self._show_tab(self.tab)
        self._set_focus(self.getControl(control_id))

    def onAction(self, action):
        if action.getId() not in _BACK_ACTIONS:
            return
        if self.active_key:
            self._leave_actions()
            return
        self.result = "cancel"
        self.close()


def edit_folder_settings(
    addon_path, draft, editor, formatter, existing=False,
    content_rows=None, content_actions=None, content_handler=None,
    content_add_handler=None, add_art="",
):
    window = None
    try:
        window = FolderSettingsWindow(
            addon_path, draft, editor, formatter, existing=existing,
            content_rows=content_rows, content_actions=content_actions,
            content_handler=content_handler,
            content_add_handler=content_add_handler, add_art=add_art,
        )
        window.doModal()
        if window.failed:
            return "failed", dict(draft or {})
        return window.result, window.draft
    except Exception as exc:
        xbmc.log("curatr: Folder Settings unavailable: %s" % exc, xbmc.LOGERROR)
        return "failed", dict(draft or {})
    finally:
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
