"""Remote-friendly confirmation and structured filter editing for Keyword Matching."""

import os

import xbmcgui

from .keyword_matcher import confirmation_parts, format_rules, parse_prompt


_BACK_ACTIONS = {9, 10, 92}
_NAV_ACTIONS = {1, 2, 3, 4}
_PALETTE = {
    "genre": ("0x704B2632", "0xFFFFD2D9", "0xA84B2632"),
    "person": ("0x70304B62", "0xFFD5EEFF", "0xA8304B62"),
    "film": ("0x70493664", "0xFFE8DCFF", "0xA8493664"),
    "number": ("0x70584A26", "0xFFFFECB5", "0xA8584A26"),
    "year": ("0x705C392E", "0xFFFFDAC9", "0xA85C392E"),
    "runtime": ("0x70383F66", "0xFFDDE2FF", "0xA8383F66"),
    "place": ("0x702A554B", "0xFFD2F4EA", "0xA82A554B"),
}


def _control_texture(addon_path, kind, control, state):
    safe_kind = kind if kind in _PALETTE else "genre"
    return os.path.join(
        addon_path, "resources", "media", "keyword_controls_v5",
        "%s_%s_%s.png" % (safe_kind, control, state),
    )


class KeywordConfirmWindow(xbmcgui.WindowXMLDialog):
    PROMPT_EDIT_ID = 100
    CREATE_ID = 101
    FILTER_EDIT_ID = 102
    PLUS_ID = 1900

    def __new__(cls, addon_path, prompt, rules, footer, edit_existing=False):
        return super().__new__(cls, "curatr-keyword-confirm.xml", addon_path, "Default", "1080i")

    def __init__(self, addon_path, prompt, rules, footer, edit_existing=False):
        self.addon_path = addon_path
        self.prompt = str(prompt or "")
        self.rules = dict(rules or {})
        self.footer = str(footer or "")
        self.edit_existing = bool(edit_existing)
        self.result = None
        self.edit_mode = False
        self.dynamic_controls = []
        self.filter_groups = []
        self.control_actions = {}
        self.action_controls = {}

    @staticmethod
    def _text_width(text, chip=False):
        unit, padding = (13, 70) if chip else (11, 20)
        return max(120 if chip else 46, min(520 if chip else 330, len(str(text or "")) * unit + padding))

    def _clear_flow(self):
        if self.dynamic_controls:
            try:
                self.removeControls(self.dynamic_controls)
            except Exception:
                for control in self.dynamic_controls:
                    try: self.removeControl(control)
                    except Exception: pass
        self.dynamic_controls = []
        self.filter_groups = []
        self.control_actions = {}
        self.action_controls = {}

    def _add_label(self, x, y, width, text, colour="0xFFD7D3DF"):
        control = xbmcgui.ControlLabel(x, y, width, 50, str(text or ""), font="font13", textColor=colour, alignment=4)
        self.addControl(control); self.dynamic_controls.append(control)

    def _chip_images(self, x, y, width, background):
        media = os.path.join(self.addon_path, "resources", "media")
        pixel = os.path.join(media, "pixel.png")
        left = os.path.join(media, "chip_round_left.png")
        right = os.path.join(media, "chip_round_right.png")
        controls = [
            xbmcgui.ControlImage(x, y, 14, 60, left, colorDiffuse=background),
            xbmcgui.ControlImage(x + 14, y, max(1, width - 28), 60, pixel, colorDiffuse=background),
            xbmcgui.ControlImage(x + width - 14, y, 14, 60, right, colorDiffuse=background),
        ]
        self.addControls(controls); self.dynamic_controls.extend(controls)
        return controls

    def _add_chip(self, x, y, width, part, index):
        kind = str(part.get("kind") or "genre")
        background, foreground, focused = _PALETTE.get(kind, _PALETTE["genre"])
        images = self._chip_images(x, y, width, background)
        if not self.edit_mode:
            label = xbmcgui.ControlLabel(x + 18, y + 10, width - 36, 40, "[B]%s[/B]" % part.get("text", ""), font="font13", textColor=foreground, alignment=6)
            self.addControl(label); self.dynamic_controls.append(label)
            return

        minus = xbmcgui.ControlButton(
            x + 7, y + 7, 48, 46, "−", font="font30", textColor=foreground,
            focusedColor="0xFFFFFFFF", alignment=6,
            focusTexture=_control_texture(self.addon_path, kind, "minus", "focus"),
            noFocusTexture=_control_texture(self.addon_path, kind, "minus", "normal"),
        )
        tag_text = xbmcgui.ControlButton(
            x + 56, y + 7, max(48, width - 63), 46,
            "[B]%s[/B]" % part.get("text", ""), font="font13",
            textColor=foreground, focusedColor="0xFFFFFFFF", alignment=6,
            focusTexture=_control_texture(self.addon_path, kind, "text", "focus"),
            noFocusTexture=_control_texture(self.addon_path, kind, "text", "normal"),
        )
        self.addControls([minus, tag_text]); self.dynamic_controls.extend([minus, tag_text])
        minus_id, text_id = minus.getId(), tag_text.getId()
        self.control_actions[minus_id] = ("remove", index)
        self.control_actions[text_id] = ("edit", index)
        self.action_controls[minus_id] = minus
        self.action_controls[text_id] = tag_text
        self.filter_groups.append({"ids": (minus_id, text_id), "controls": (minus, tag_text), "images": images, "normal": background, "focused": focused})

    def _build_flow(self):
        self._clear_flow()
        left, right, x, y = 360, 1560, 360, 468
        parts = confirmation_parts(self.rules)
        for index, part in enumerate(parts):
            connector = str(part.get("connector") or "").strip()
            chip_text = str(part.get("text") or "").strip()
            connector_width = self._text_width(connector) if connector else 0
            chip_width = self._text_width(chip_text, chip=True) + (42 if self.edit_mode else 0)
            required = connector_width + (12 if connector else 0) + chip_width + 14
            if x > left and x + required > right:
                x, y = left, y + 72
            if connector:
                self._add_label(x, y + 6, connector_width, connector)
                x += connector_width + 12
            self._add_chip(x, y, chip_width, part, index)
            x += chip_width + 14
        if self.edit_mode:
            if x + 64 > right:
                x, y = left, y + 72
            plus = xbmcgui.ControlButton(
                x, y, 60, 60, "[B]+[/B]", font="font35",
                textColor="0xFFDAD6E0", focusedColor="0xFFFFFFFF", alignment=6,
                focusTexture=os.path.join(self.addon_path, "resources", "media", "keyword_controls_v5", "plus_focus.png"),
                noFocusTexture=os.path.join(self.addon_path, "resources", "media", "keyword_controls_v5", "plus_normal.png"),
            )
            self.addControl(plus); self.dynamic_controls.append(plus)
            plus_id = plus.getId()
            self.control_actions[plus_id] = ("add", -1)
            self.action_controls[plus_id] = plus
            focusable = [control for group in self.filter_groups for control in group["controls"]] + [plus]
            header = self.getControl(self.FILTER_EDIT_ID)
            prompt = self.getControl(self.PROMPT_EDIT_ID)
            create = self.getControl(self.CREATE_ID)
            for position, control in enumerate(focusable):
                control.setNavigation(
                    header, create,
                    focusable[position - 1], focusable[(position + 1) % len(focusable)],
                )
            header.setNavigation(header, focusable[0], header, header)
            prompt.setNavigation(focusable[-1], prompt, prompt, create)
            create.setNavigation(focusable[-1], create, prompt, create)
        else:
            header = self.getControl(self.FILTER_EDIT_ID)
            prompt = self.getControl(self.PROMPT_EDIT_ID)
            create = self.getControl(self.CREATE_ID)
            header.setNavigation(header, prompt, header, header)
            prompt.setNavigation(header, prompt, prompt, create)
            create.setNavigation(header, create, prompt, create)
        return y + 60

    def _position_summary(self, flow_bottom):
        footer_y = max(610, int(flow_bottom) + 34)
        exclusion_y = footer_y + 50
        button_y = max(780, exclusion_y + 84)
        if button_y + 82 > 1030:
            raise RuntimeError("Keyword confirmation content is too tall")
        self.getControl(12).setPosition(360, footer_y)
        self.getControl(13).setPosition(360, exclusion_y)
        self.getControl(self.PROMPT_EDIT_ID).setPosition(545, button_y)
        self.getControl(self.CREATE_ID).setPosition(985, button_y)
        self.getControl(14).setHeight(max(740, exclusion_y + 80) - 140)

    def _refresh_flow(self, focus_id=None, focus_first_action=False):
        self._position_summary(self._build_flow())
        if focus_first_action and self.control_actions:
            focus_id = next(iter(self.control_actions))
        if focus_id:
            try:
                control = self.action_controls.get(focus_id) or self.getControl(focus_id)
                self.setFocus(control)
            except Exception: pass
        self._sync_focus_visuals()

    def _sync_focus_visuals(self):
        try: focused_id = self.getFocusId()
        except Exception: focused_id = -1
        for group in self.filter_groups:
            colour = group["focused"] if focused_id in group["ids"] else group["normal"]
            for image in group["images"]:
                try: image.setColorDiffuse(colour)
                except Exception: pass

    def onInit(self):
        try:
            self.getControl(11).setText(self.prompt)
            self.getControl(12).setLabel(self.footer)
            if self.rules.get("history_mode") in ("stale", "plays"):
                exclusion = "Rated and hidden items will be excluded  •  Viewing history filter active  •  No AI will be used"
            elif self.rules.get("history_mode") == "never":
                exclusion = "Previously watched, rated and hidden items will be excluded  •  No AI will be used"
            else:
                exclusion = "Watched, rated and hidden items will be excluded  •  No AI will be used"
            self.getControl(13).setLabel(exclusion)
            self.getControl(self.PROMPT_EDIT_ID).setLabel("EDIT REQUEST" if self.edit_existing else "EDIT PROMPT")
            self.getControl(self.FILTER_EDIT_ID).setLabel("EDIT FILTERS")
            self.getControl(self.CREATE_ID).setLabel("SAVE CHANGES" if self.edit_existing else "CREATE LIST")
            self._refresh_flow()
            self.setFocus(self.getControl(self.CREATE_ID))
        except Exception:
            self.result = "fallback"; self.close()

    def _remove_part(self, part):
        field, index = part.get("field"), int(part.get("index") or 0)
        clear = {
            "collection": ("collection_query", "collection_name"), "country": ("country", "country_label"),
            "language": ("language", "language_label"), "year": ("year_min", "year_max"),
            "rating": ("rating_min",), "runtime": ("runtime_min", "runtime_max"),
            "external": ("external_source", "external_source_label", "external_chart_limit", "external_rating_min"),
            "history": ("history_mode", "history_days", "history_plays", "history_comparison"),
        }
        if field == "person":
            self.rules["people"] = [row for i, row in enumerate(self.rules.get("people") or []) if i != index]
            if not self.rules["people"]: self.rules["strategy"] = "similar_films" if self.rules.get("reference_movies") else "filtered_discover"
        elif field == "reference":
            self.rules["reference_movies"] = [row for i, row in enumerate(self.rules.get("reference_movies") or []) if i != index]
            if not self.rules["reference_movies"] and not self.rules.get("people"): self.rules["strategy"] = "filtered_discover"
        elif field == "genres":
            for key in ("genres", "genre_labels", "themes", "theme_labels"): self.rules[key] = []
        elif field == "mainstream": self.rules.update({"avoid_mainstream": False, "prefer_blockbusters": False})
        else:
            for key in clear.get(field, ()): self.rules[key] = 0 if key.endswith(("_min", "_max", "_days", "_plays", "_limit")) else ""
            if field == "collection": self.rules["strategy"] = "filtered_discover"
            if field == "history": self.rules["exclude_watched"] = True

    def _edit_part(self, part):
        field, index = part.get("field"), int(part.get("index") or 0)
        current = str(part.get("text") or "")
        value = xbmcgui.Dialog().input("Edit %s" % (part.get("connector") or field or "filter"), defaultt=current)
        if value is None or not str(value).strip(): return
        value = str(value).strip()
        if field == "person": self.rules["people"][index]["query"] = value; self.rules["people"][index].pop("name", None)
        elif field == "reference": self.rules["reference_movies"][index]["title"] = value
        elif field == "collection": self.rules.update({"collection_query": value, "collection_name": value, "strategy": "collection"})
        else:
            parsed = parse_prompt("%s %s" % (part.get("connector") or "", value))
            keys = {
                "genres": ("genres", "genre_labels", "themes", "theme_labels"), "year": ("year_min", "year_max"),
                "rating": ("rating_min",), "runtime": ("runtime_min", "runtime_max"),
                "language": ("language", "language_label"), "country": ("country", "country_label"),
                "history": ("history_mode", "history_days", "history_plays", "history_comparison", "exclude_watched"),
            }.get(field, ())
            if not keys: return
            for key in keys: self.rules[key] = parsed.get(key, self.rules.get(key))

    def _add_filter(self):
        options = []
        if len(self.rules.get("genres") or []) + len(self.rules.get("themes") or []) < 4: options.append(("Genre or theme", "genres"))
        if not (self.rules.get("year_min") or self.rules.get("year_max")): options.append(("Year or decade", "year"))
        if not self.rules.get("rating_min"): options.append(("Rating", "rating"))
        if not (self.rules.get("runtime_min") or self.rules.get("runtime_max")): options.append(("Runtime", "runtime"))
        if not self.rules.get("collection_query") and len(self.rules.get("people") or []) < 3: options.extend((("Actor", "actor"), ("Director", "director")))
        if not self.rules.get("collection_query") and len(self.rules.get("reference_movies") or []) < 3: options.append(("Reference", "reference"))
        if not self.rules.get("collection_query") and not self.rules.get("people") and not self.rules.get("reference_movies"): options.append(("Collection", "collection"))
        if not self.rules.get("language"): options.append(("Language", "language"))
        if not self.rules.get("country"): options.append(("Country", "country"))
        if not self.rules.get("history_mode"): options.append(("Viewing history", "history"))
        if not options:
            xbmcgui.Dialog().ok("Keyword Matching", "No more filter types are available.")
            return
        choice = xbmcgui.Dialog().select("Add a filter", [row[0] for row in options])
        if choice < 0: return
        label, kind = options[choice]
        value = xbmcgui.Dialog().input("Add %s" % label.lower())
        if not value or not value.strip(): return
        value = value.strip()
        if kind in ("actor", "director"):
            people = list(self.rules.get("people") or [])
            people.append({"query": value, "role": "cast" if kind == "actor" else "director"})
            self.rules["people"], self.rules["strategy"] = people, "exact_people"
        elif kind == "reference":
            refs = list(self.rules.get("reference_movies") or [])
            refs.append({"title": value, "year": 0}); self.rules["reference_movies"] = refs
            if not self.rules.get("people"): self.rules["strategy"] = "similar_films"
        elif kind == "collection":
            self.rules.update({"collection_query": value, "collection_name": value, "strategy": "collection"})
        else:
            prefixes = {"genres": "", "year": "from ", "rating": "rated ", "runtime": "under ", "language": "in ", "country": "", "history": ""}
            parsed = parse_prompt(prefixes.get(kind, "") + value)
            key_groups = {
                "genres": ("genres", "genre_labels", "themes", "theme_labels"), "year": ("year_min", "year_max"),
                "rating": ("rating_min",), "runtime": ("runtime_min", "runtime_max"),
                "language": ("language", "language_label"), "country": ("country", "country_label"),
                "history": ("history_mode", "history_days", "history_plays", "history_comparison", "exclude_watched"),
            }
            for key in key_groups.get(kind, ()):
                incoming = parsed.get(key)
                if isinstance(incoming, list):
                    self.rules[key] = list(dict.fromkeys(list(self.rules.get(key) or []) + incoming))
                elif incoming not in (None, "", 0): self.rules[key] = incoming

    def onClick(self, control_id):
        if control_id == self.PROMPT_EDIT_ID:
            self.result = "edit"; self.close()
        elif control_id == self.CREATE_ID:
            if not confirmation_parts(self.rules):
                xbmcgui.Dialog().ok("Keyword Matching", "Add at least one filter before saving the list.")
                return
            self.result = "create"; self.close()
        elif control_id == self.FILTER_EDIT_ID:
            self.edit_mode = not self.edit_mode
            self.getControl(self.FILTER_EDIT_ID).setLabel("DONE" if self.edit_mode else "EDIT FILTERS")
            self._refresh_flow(self.CREATE_ID if not self.edit_mode else None, focus_first_action=self.edit_mode)
        elif control_id in self.control_actions:
            action, index = self.control_actions[control_id]
            parts = confirmation_parts(self.rules)
            if action == "remove" and 0 <= index < len(parts): self._remove_part(parts[index])
            elif action == "edit" and 0 <= index < len(parts): self._edit_part(parts[index])
            elif action == "add": self._add_filter()
            self.rules["display_parts"] = confirmation_parts(self.rules)
            self.rules["confidence"] = min(1.0, len(self.rules["display_parts"]) / 3.0)
            self._refresh_flow(focus_first_action=True)

    def onAction(self, action):
        if action.getId() in _BACK_ACTIONS:
            if self.edit_mode:
                self.edit_mode = False
                self.getControl(self.FILTER_EDIT_ID).setLabel("EDIT FILTERS")
                self._refresh_flow(self.CREATE_ID)
            else: self.close()
        elif action.getId() in _NAV_ACTIONS:
            self._sync_focus_visuals()


def confirm_keyword_rules(addon_path, prompt, rules, footer="", edit_existing=False):
    window = None
    try:
        window = KeywordConfirmWindow(addon_path, prompt, rules, footer, edit_existing)
        window.doModal(); result = window.result
        if isinstance(rules, dict) and isinstance(window.rules, dict):
            rules.clear(); rules.update(window.rules)
    except Exception:
        result = "fallback"
    finally:
        if window is not None:
            try: window.close()
            except Exception: pass
    if result != "fallback": return result
    message = format_rules(rules)
    if footer: message = "%s\n\n%s" % (message, footer)
    try:
        edit_label = "Edit Request" if edit_existing else "Edit Prompt"
        save_label = "Save Changes" if edit_existing else "Create List"
        choice = xbmcgui.Dialog().yesnocustom("Keyword Matching", message, edit_label, nolabel="Cancel", yeslabel=save_label)
        return "edit" if choice == 2 else ("create" if choice == 1 else None)
    except (AttributeError, TypeError):
        save_label = "Save Changes" if edit_existing else "Create List"
        return "create" if xbmcgui.Dialog().yesno("Keyword Matching", message, nolabel="Cancel", yeslabel=save_label) else None
