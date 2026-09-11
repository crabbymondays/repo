import xbmcgui

from .ui_theme import skin_name, style_tab


_BACK_ACTIONS = {9, 10, 92}


class ListSettingsWindow(xbmcgui.WindowXMLDialog):
    TAB_IDS = {100: "appearance", 101: "content", 102: "behaviour"}
    ROW_IDS = (200, 201, 202, 203)
    ACTION_IDS = {300: "preview", 301: "create", 302: "cancel"}
    FIELDS = {
        "appearance": ("name", "description", "artwork"),
        "content": ("prompt", "generation_method", "content_type", "count"),
        "behaviour": ("regeneration_enabled", "regeneration_interval_hours", "sync_to_trakt", "trakt_refresh_schedule"),
    }

    def __new__(cls, addon_path, draft, editor, formatter, existing=False):
        return super().__new__(cls, "curatr-list-settings.xml", addon_path, skin_name(), "1080i")

    def __init__(self, addon_path, draft, editor, formatter, existing=False):
        self.draft = dict(draft or {})
        self.editor = editor
        self.formatter = formatter
        self.tab = "appearance"
        self.result = "cancel"
        self.existing = bool(existing)

    def onInit(self):
        if self.existing:
            save, cancel, hidden = self.getControl(300), self.getControl(301), self.getControl(302)
            save.setLabel("Save Changes")
            cancel.setLabel("Cancel")
            save.setPosition(610, 750)
            cancel.setPosition(970, 750)
            hidden.setVisible(False)
            hidden.setEnabled(False)
            self.getControl(20).setLabel("Review the saved settings above, then save or cancel your changes.")
        self._show_tab("appearance")

    def _show_tab(self, tab):
        self.tab = tab
        for control_id, name in self.TAB_IDS.items():
            style_tab(self, control_id, name.title(), name == tab)
        fields = self.FIELDS[tab]
        visible_rows = []
        for index, control_id in enumerate(self.ROW_IDS):
            control = self.getControl(control_id)
            visible = index < len(fields)
            control.setVisible(visible)
            control.setEnabled(visible)
            if visible:
                control.setLabel(self.formatter(fields[index], self.draft))
                visible_rows.append(control)
        tab_control = self.getControl(next(control_id for control_id, name in self.TAB_IDS.items() if name == tab))
        preview = self.getControl(300)
        for index, control in enumerate(visible_rows):
            above = visible_rows[index - 1] if index else preview
            below = visible_rows[index + 1] if index + 1 < len(visible_rows) else preview
            control.setNavigation(above, below, tab_control, control)
        action_ids = (300, 301) if self.existing else (300, 301, 302)
        actions = [self.getControl(control_id) for control_id in action_ids]
        for index, control in enumerate(actions):
            control.setNavigation(
                visible_rows[-1], control,
                actions[index - 1], actions[(index + 1) % len(actions)],
            )
        try:
            self.setFocus(self.getControl(self.ROW_IDS[0]))
        except Exception:
            pass

    def onClick(self, control_id):
        if control_id in self.TAB_IDS:
            self._show_tab(self.TAB_IDS[control_id])
            return
        if control_id in self.ACTION_IDS:
            if self.existing:
                self.result = "save" if control_id == 300 else "cancel"
            else:
                self.result = self.ACTION_IDS[control_id]
            self.close()
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
        self.setFocus(self.getControl(control_id))

    def onAction(self, action):
        if action.getId() in _BACK_ACTIONS:
            self.result = "cancel"
            self.close()


def edit_list_settings(addon_path, draft, editor, formatter, existing=False):
    window = ListSettingsWindow(addon_path, draft, editor, formatter, existing=existing)
    try:
        window.doModal()
        return window.result, window.draft
    finally:
        window.close()
