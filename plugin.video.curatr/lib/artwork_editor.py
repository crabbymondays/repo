import xbmcgui

from .ui_theme import skin_name, style_tab


_BACK_ACTIONS = {9, 10, 92}


class ArtworkEditorWindow(xbmcgui.WindowXMLDialog):
    TAB_IDS = {100: "icon", 101: "fanart"}
    SOURCE_IDS = (200, 201, 202, 203, 204)
    STYLE_IDS = (300, 301)
    GRID_IDS = {"icon": 400, "fanart": 401}
    SAVE_ID = 500
    RESET_ID = 501
    CANCEL_ID = 502

    def __new__(
        cls, addon_path, heading, artwork, reset_artwork, preview_provider,
        choice_provider, custom_provider, has_contents=False,
        person_available=False,
    ):
        return super().__new__(
            cls, "curatr-artwork-editor.xml", addon_path, skin_name(), "1080i"
        )

    def __init__(
        self, addon_path, heading, artwork, reset_artwork, preview_provider,
        choice_provider, custom_provider, has_contents=False,
        person_available=False,
    ):
        self.heading = str(heading or "Artwork")
        self.original = dict(artwork or {})
        self.draft = dict(artwork or {})
        self.reset_artwork = dict(reset_artwork or {})
        self.preview_provider = preview_provider
        self.choice_provider = choice_provider
        self.custom_provider = custom_provider
        self.has_contents = bool(has_contents)
        self.person_available = bool(person_available)
        self.tab = "icon"
        self.sources = []
        self.source_by_tab = {"icon": "curatr", "fanart": "curatr"}
        self.style_by_tab = {
            "icon": str(self.draft.get("icon_style") or "white"),
            "fanart": str(self.draft.get("fanart_style") or "colour"),
        }
        self.grid_entries = []
        self.grid_source = ""
        self.in_grid = False
        self.result = "cancel"
        self.failed = False

    def _available_sources(self):
        rows = [
            ("curatr", "Curatr"),
            ("match", "Match Fanart" if self.tab == "icon" else "Match Icon"),
        ]
        if self.tab == "fanart" and self.has_contents:
            rows.append(("contents", "Contents"))
        if self.person_available or self.draft.get(self.tab + "_mode") == "person":
            rows.append(("person", "Person"))
        rows.append(("custom", "Custom"))
        return rows

    def _style_rows(self):
        if self.tab == "icon":
            return (("white", "White"), ("genre_colours", "Colours"))
        return (("colour", "Colour"), ("monochrome", "Monochrome"))

    @staticmethod
    def _grid_item(row, selected=False):
        item = xbmcgui.ListItem(label=str(row.get("label") or "Artwork"), offscreen=True)
        source = str(row.get("preview_source") or row.get("source") or "")
        if source:
            item.setArt({"icon": source, "thumb": source})
        item.setProperty("CuratrSelected", "true" if selected else "false")
        return item

    def onInit(self):
        try:
            self.getControl(10).setLabel(self.heading)
            self._show_tab("icon", load_saved=True)
            self.setFocus(self.getControl(100))
        except Exception:
            self.failed = True
            self.close()

    def _update_preview(self):
        preview = self.preview_provider(dict(self.draft)) or {}
        self.getControl(20).setImage(str(preview.get("fanart") or ""))
        self.getControl(21).setImage(str(preview.get("icon") or ""))
        self.getControl(22).setLabel("Icon  •  %s" % (preview.get("icon_label") or "Automatic"))
        self.getControl(23).setLabel("Fanart  •  %s" % (preview.get("fanart_label") or "Automatic"))

    def _show_tab(self, tab, load_saved=False):
        self.tab = "fanart" if tab == "fanart" else "icon"
        self.in_grid = False
        for control_id, name in self.TAB_IDS.items():
            style_tab(self, control_id, name.title(), name == self.tab)
        source = self.source_by_tab.get(self.tab) or "curatr"
        self.sources = self._available_sources()
        available_keys = {key for key, _label in self.sources}
        if source not in available_keys:
            source = "curatr"
            self.source_by_tab[self.tab] = source
        for index, control_id in enumerate(self.SOURCE_IDS):
            control = self.getControl(control_id)
            visible = index < len(self.sources)
            control.setVisible(visible)
            control.setEnabled(visible)
            if visible:
                key, label = self.sources[index]
                style_tab(self, control_id, label, key == source)
        show_style = source == "curatr"
        style_rows = self._style_rows()
        active_style = self.style_by_tab[self.tab]
        for index, control_id in enumerate(self.STYLE_IDS):
            control = self.getControl(control_id)
            control.setVisible(show_style)
            control.setEnabled(show_style)
            key, label = style_rows[index]
            style_tab(self, control_id, label, key == active_style)
        if load_saved and source == "curatr":
            self._load_grid("curatr", focus=False)
        else:
            self._display_grid()
        self._update_preview()
        self._wire_navigation()

    def _source_control(self, source=None):
        wanted = str(source or self.source_by_tab.get(self.tab) or "")
        index = next((
            index for index, row in enumerate(self.sources) if row[0] == wanted
        ), 0)
        return self.getControl(self.SOURCE_IDS[index])

    def _active_grid(self):
        return self.getControl(self.GRID_IDS[self.tab])

    def _display_grid(self, preferred_source=""):
        active_id = self.GRID_IDS[self.tab]
        other_id = self.GRID_IDS["fanart" if self.tab == "icon" else "icon"]
        other = self.getControl(other_id)
        other.setVisible(False)
        other.setEnabled(False)
        grid = self.getControl(active_id)
        visible = bool(self.grid_entries and self.grid_source == self.source_by_tab.get(self.tab))
        grid.setVisible(visible)
        grid.setEnabled(visible)
        if not visible:
            return
        grid.reset()
        selected_position = 0
        items = []
        for index, row in enumerate(self.grid_entries):
            selected = self._entry_selected(row)
            if selected:
                selected_position = index
            items.append(self._grid_item(row, selected=selected))
        grid.addItems(items)
        wanted = str(preferred_source or "")
        if wanted:
            selected_position = next((
                index for index, row in enumerate(self.grid_entries)
                if str(row.get("source") or "") == wanted
            ), selected_position)
        grid.selectItem(selected_position)

    def _entry_selected(self, row):
        source = self.source_by_tab.get(self.tab)
        mode = str(self.draft.get(self.tab + "_mode") or "auto")
        if source == "curatr":
            key = str(self.draft.get(self.tab + "_key") or "")
            style = str(self.draft.get(self.tab + "_style") or "")
            return mode == "bundled" and str(row.get("key") or "") == key and style == self.style_by_tab[self.tab]
        return str(row.get("source") or "") == str(self.draft.get(self.tab + "_source") or "")

    def _load_grid(self, source, focus=True):
        try:
            rows = self.choice_provider(
                self.tab, source, self.style_by_tab[self.tab]
            ) or []
        except Exception as exc:
            xbmcgui.Dialog().ok("curatr", str(exc))
            return False
        rows = [dict(row) for row in rows if isinstance(row, dict) and row.get("source")]
        if not rows:
            return False
        self.grid_source = source
        self.grid_entries = rows
        self._display_grid()
        self._wire_navigation()
        if focus:
            self.in_grid = True
            self.setFocus(self._active_grid())
        return True

    def _set_icon(self, mode, key="", source="", label="", style=None):
        self.draft.update({
            "icon_mode": mode,
            "icon_key": str(key or ""),
            "icon_source": str(source or ""),
            "icon_label": str(label or ""),
        })
        if style:
            self.draft["icon_style"] = style
            self.style_by_tab["icon"] = style

    def _set_fanart(self, mode, key="", source="", label="", style=None):
        self.draft.update({
            "fanart_mode": mode,
            "fanart_key": str(key or ""),
            "fanart_source": str(source or ""),
            "fanart_label": str(label or ""),
        })
        if style:
            self.draft["fanart_style"] = style
            self.style_by_tab["fanart"] = style

    def _match_other(self):
        if self.tab == "icon":
            mode = str(self.draft.get("fanart_mode") or "auto")
            if mode == "auto":
                self._set_icon("auto", style="white")
            elif mode == "bundled":
                style = "genre_colours" if self.draft.get("fanart_style") == "colour" else "white"
                self._set_icon("bundled", key=self.draft.get("fanart_key"), style=style)
            elif mode in ("item", "person", "custom") and self.draft.get("fanart_source"):
                self._set_icon(
                    "person" if mode == "person" else "custom",
                    source=self.draft.get("fanart_source"),
                    label=self.draft.get("fanart_label") or "Custom",
                )
            elif mode == "default":
                self._set_icon("default")
            else:
                return False
            return True
        mode = str(self.draft.get("icon_mode") or "auto")
        if mode == "auto":
            self._set_fanart("auto")
        elif mode == "bundled":
            style = "colour" if self.draft.get("icon_style") == "genre_colours" else "monochrome"
            self._set_fanart("bundled", key=self.draft.get("icon_key"), style=style)
        elif mode in ("person", "custom") and self.draft.get("icon_source"):
            self._set_fanart(
                "person" if mode == "person" else "custom",
                source=self.draft.get("icon_source"),
                label=self.draft.get("icon_label") or "Custom",
            )
        elif mode == "default":
            self._set_fanart("default")
        else:
            return False
        return True

    def _choose_source(self, control_id):
        index = self.SOURCE_IDS.index(control_id)
        if index >= len(self.sources):
            return
        previous = self.source_by_tab.get(self.tab) or "curatr"
        source = self.sources[index][0]
        self.source_by_tab[self.tab] = source
        if source in ("curatr", "contents", "person"):
            if not self._load_grid(source):
                self.source_by_tab[self.tab] = previous
                self._show_tab(self.tab, load_saved=previous == "curatr")
            else:
                self._show_source_state()
            return
        if source == "match":
            if not self._match_other():
                xbmcgui.Dialog().ok("curatr", "The other artwork cannot be matched yet.")
                self.source_by_tab[self.tab] = previous
                self._show_tab(self.tab, load_saved=previous == "curatr")
                self.setFocus(self._source_control())
                return
        elif source == "custom":
            selected = self.custom_provider(self.tab)
            if not selected:
                self.source_by_tab[self.tab] = previous
                self._show_tab(self.tab, load_saved=previous == "curatr")
                self.setFocus(self._source_control())
                return
            elif self.tab == "icon":
                self._set_icon("custom", source=selected.get("source"), label=selected.get("label") or "Custom")
            else:
                self._set_fanart("custom", source=selected.get("source"), label=selected.get("label") or "Custom")
        self.grid_entries = []
        self.grid_source = ""
        self._show_tab(self.tab)
        self.setFocus(self._source_control())

    def _show_source_state(self):
        source = self.source_by_tab.get(self.tab)
        for index, control_id in enumerate(self.SOURCE_IDS):
            if index >= len(self.sources):
                continue
            key, label = self.sources[index]
            style_tab(self, control_id, label, key == source)
        show_style = source == "curatr"
        for control_id in self.STYLE_IDS:
            self.getControl(control_id).setVisible(show_style)
            self.getControl(control_id).setEnabled(show_style)
        self._update_preview()
        self._wire_navigation()

    def _choose_style(self, control_id):
        index = self.STYLE_IDS.index(control_id)
        style, _label = self._style_rows()[index]
        self.style_by_tab[self.tab] = style
        if self.tab == "icon" and self.draft.get("icon_mode") == "bundled":
            self.draft["icon_style"] = style
        elif self.tab == "fanart" and self.draft.get("fanart_mode") == "bundled":
            self.draft["fanart_style"] = style
        self._load_grid("curatr", focus=False)
        self._show_tab(self.tab)
        self.setFocus(self.getControl(control_id))

    def _choose_grid_entry(self):
        grid = self._active_grid()
        position = grid.getSelectedPosition()
        if not (0 <= position < len(self.grid_entries)):
            return
        row = self.grid_entries[position]
        source = self.source_by_tab.get(self.tab)
        image = str(row.get("source") or "")
        label = str(row.get("label") or "Artwork")
        if source == "curatr":
            if self.tab == "icon":
                self._set_icon(
                    "bundled", key=row.get("key"),
                    style=self.style_by_tab["icon"],
                )
            else:
                self._set_fanart(
                    "bundled", key=row.get("key"),
                    style=self.style_by_tab["fanart"],
                )
        elif source == "contents":
            self._set_fanart(
                str(row.get("mode") or "item"), source=image, label=label,
            )
        elif source == "person":
            if self.tab == "icon":
                self._set_icon("person", source=image, label=label)
            else:
                self._set_fanart("person", source=image, label=label)
        self._update_preview()
        self._display_grid(preferred_source=image)
        self.in_grid = True
        self.setFocus(grid)

    def _wire_navigation(self):
        tab_controls = [self.getControl(control_id) for control_id in self.TAB_IDS]
        first_source = self._source_control()
        tab_controls[0].setNavigation(
            self.getControl(self.SAVE_ID), first_source,
            tab_controls[1], tab_controls[1],
        )
        tab_controls[1].setNavigation(
            self.getControl(self.CANCEL_ID), first_source,
            tab_controls[0], tab_controls[0],
        )

        source_controls = [self.getControl(self.SOURCE_IDS[index]) for index in range(len(self.sources))]
        grid_visible = bool(self.grid_entries and self.grid_source == self.source_by_tab.get(self.tab))
        style_visible = self.source_by_tab.get(self.tab) == "curatr"
        below_source = self.getControl(self.STYLE_IDS[0]) if style_visible else (
            self._active_grid() if grid_visible else self.getControl(self.SAVE_ID)
        )
        active_tab = self.getControl(100 if self.tab == "icon" else 101)
        for index, control in enumerate(source_controls):
            control.setNavigation(
                active_tab, below_source,
                source_controls[index - 1], source_controls[(index + 1) % len(source_controls)],
            )

        style_controls = [self.getControl(control_id) for control_id in self.STYLE_IDS]
        below_style = self._active_grid() if grid_visible else self.getControl(self.SAVE_ID)
        for index, control in enumerate(style_controls):
            control.setNavigation(
                first_source, below_style,
                style_controls[index - 1], style_controls[(index + 1) % len(style_controls)],
            )

        if grid_visible:
            self._active_grid().setNavigation(
                self.getControl(self.STYLE_IDS[0]) if style_visible else first_source,
                self.getControl(self.SAVE_ID),
                self.getControl(self.SAVE_ID), self.getControl(self.SAVE_ID),
            )

        actions = [self.getControl(control_id) for control_id in (self.SAVE_ID, self.RESET_ID, self.CANCEL_ID)]
        above = self._active_grid() if grid_visible else (self.getControl(self.STYLE_IDS[0]) if style_visible else first_source)
        for index, control in enumerate(actions):
            control.setNavigation(
                above, active_tab,
                actions[index - 1], actions[(index + 1) % len(actions)],
            )

    def onClick(self, control_id):
        if control_id in self.TAB_IDS:
            self._show_tab(self.TAB_IDS[control_id], load_saved=True)
            self.setFocus(self._source_control())
        elif control_id in self.SOURCE_IDS:
            self._choose_source(control_id)
        elif control_id in self.STYLE_IDS:
            self._choose_style(control_id)
        elif control_id in self.GRID_IDS.values():
            self._choose_grid_entry()
        elif control_id == self.SAVE_ID:
            self.result = "save"
            self.close()
        elif control_id == self.RESET_ID:
            self.draft = dict(self.reset_artwork)
            self.source_by_tab = {"icon": "curatr", "fanart": "curatr"}
            self.style_by_tab = {
                "icon": str(self.draft.get("icon_style") or "white"),
                "fanart": str(self.draft.get("fanart_style") or "colour"),
            }
            self.grid_entries = []
            self.grid_source = ""
            self._show_tab(self.tab, load_saved=True)
            self.setFocus(self.getControl(self.RESET_ID))
        elif control_id == self.CANCEL_ID:
            self.result = "cancel"
            self.close()

    def onFocus(self, control_id):
        self.in_grid = control_id in self.GRID_IDS.values()

    def onAction(self, action):
        if action.getId() not in _BACK_ACTIONS:
            return
        if self.in_grid:
            self.in_grid = False
            self.setFocus(self._source_control(self.grid_source))
            return
        self.result = "cancel"
        self.close()


def edit_artwork(
    addon_path, heading, artwork, reset_artwork, preview_provider,
    choice_provider, custom_provider, has_contents=False,
    person_available=False,
):
    window = None
    try:
        window = ArtworkEditorWindow(
            addon_path, heading, artwork, reset_artwork, preview_provider,
            choice_provider, custom_provider, has_contents=has_contents,
            person_available=person_available,
        )
        window.doModal()
        if window.failed:
            return "fallback", dict(artwork or {})
        return window.result, dict(window.draft)
    except Exception:
        return "fallback", dict(artwork or {})
    finally:
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
