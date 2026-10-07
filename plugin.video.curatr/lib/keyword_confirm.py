"""Remote-friendly confirmation and structured filter editing for Keyword Matching."""

import os
import unicodedata
from copy import deepcopy

import xbmc
import xbmcgui

from .keyword_matcher import confirmation_confidence, confirmation_parts, format_rules, parse_prompt, validate_filters
from .list_request import HISTORY_KEYS, parse_history, parse_request
from .ui_theme import bold, skin_name, move_finished_button, resize_finished_panel


_BACK_ACTIONS = {9, 10, 92}
_PALETTE = {
    "genre": ("0x704B2632", "0xFFFFD2D9"),
    "person": ("0x70304B62", "0xFFD5EEFF"),
    "film": ("0x70493664", "0xFFE8DCFF"),
    "number": ("0x70584A26", "0xFFFFECB5"),
    "year": ("0x705C392E", "0xFFFFDAC9"),
    "runtime": ("0x70383F66", "0xFFDDE2FF"),
    "place": ("0x702A554B", "0xFFD2F4EA"),
}



class KeywordConfirmWindow(xbmcgui.WindowXMLDialog):
    PROMPT_EDIT_ID = 100
    CREATE_ID = 101
    FILTER_EDIT_ID = 102
    FILTER_ICON_ID = 103

    def __new__(cls, addon_path, prompt, rules, footer, edit_existing=False,
                confirm_label=None, start_editing=False):
        return super().__new__(cls, "curatr-keyword-confirm.xml", addon_path, skin_name(), "1080i")

    def __init__(self, addon_path, prompt, rules, footer, edit_existing=False,
                 confirm_label=None, start_editing=False):
        self.addon_path = addon_path
        self.prompt = str(prompt or "")
        self.rules = deepcopy(rules or {})
        self.footer = str(footer or "")
        self.edit_existing = bool(edit_existing)
        self.result = None
        self.edit_mode = bool(start_editing)
        self.confirm_label = confirm_label or ("Save" if edit_existing else "Create List")
        self.dynamic_controls = []
        self.filter_groups = []
        self.control_actions = {}
        self.action_controls = {}
        self.neutral_background = "0x404C4D55"
        self.neutral_focus = "0xFF505159"
        colour = xbmc.getInfoLabel("Window(Home).Property(CuratrSheenColour)")
        self.sheen_colour = "0x" + colour if len(colour) == 8 else "0xFFFFFFFF"

    @staticmethod
    def _text_width(text, chip=False):
        text = str(text or "")
        if chip:
            return max(120, min(520, len(text) * 13 + 70))
        # Reserve the visible connector width, without chip padding or a second
        # trailing gap. Narrow letters otherwise leave 'similar to' too far away.
        widths = {" ": 7, "i": 6, "l": 6, "j": 6, "I": 7, "t": 9,
                  "f": 9, "r": 10, "s": 12, "m": 22, "w": 20, "M": 23, "W": 25}
        return sum(widths.get(char, 26 if unicodedata.east_asian_width(char) in ("W", "F") else (17 if char.isupper() else 14)) for char in text)

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

    def _refresh_edit_toggle(self):
        self.getControl(self.FILTER_EDIT_ID).setLabel("")
        icon = "check" if self.edit_mode else "pen-to-square"
        image = self.getControl(self.FILTER_ICON_ID)
        image.setImage(
            os.path.join(self.addon_path, "resources", "media", "action_icons", "v1", icon + ".png")
        )
        image.setPosition(10, 4 if self.edit_mode else 6)

    def _add_label(self, x, y, width, text, colour=""):
        colour = colour or "0xFFD7D3DF"
        control = xbmcgui.ControlLabel(x, y, width, 50, str(text or ""), font="font13", textColor=colour, alignment=4)
        self.addControl(control); self.dynamic_controls.append(control)

    def _chip_images(self, x, y, width, background, height=60, bordered=True):
        media = os.path.join(self.addon_path, "resources", "media")
        pixel = os.path.join(media, "chip_middle_finish_v1.png")
        left = os.path.join(media, "chip_left_finish_v1.png")
        right = os.path.join(media, "chip_right_finish_v1.png")
        controls = [
            xbmcgui.ControlImage(x, y, 14, height, left, colorDiffuse=background),
            xbmcgui.ControlImage(x + 14, y, max(1, width - 28), height, pixel, colorDiffuse=background),
            xbmcgui.ControlImage(x + width - 14, y, 14, height, right, colorDiffuse=background),
        ]
        if bordered:
            edge_colour = "0x72FFFFFF" if background.startswith("0xFF") else "0x28FFFFFF"
            for dx, cw, name in ((0, 14, "left"), (14, max(1, width - 28), "middle"), (width - 14, 14, "right")):
                controls.append(xbmcgui.ControlImage(x + dx, y, cw, height,
                                os.path.join(media, "chip_%s_edge_v1.png" % name), colorDiffuse=edge_colour))
            controls.append(xbmcgui.ControlImage(x, y, width, height,
                            os.path.join(media, "chip_sheen_v1.png"), colorDiffuse=self.sheen_colour))
        self.addControls(controls); self.dynamic_controls.extend(controls)
        return controls

    def _filter_button(self, x, y, width, height, label, colour, focus_colour,
                       font="font13", alignment=6, text_offset=0, focused_text="0xFFFFFFFF", bordered=True):
        """Use tag or neutral focus colours with the same shared shapes."""
        focus_images = self._chip_images(
            x, y, width, focus_colour, height, bordered=bordered,
        )
        clear = os.path.join(self.addon_path, "resources", "media", "control_clear_v2.png")
        control = xbmcgui.ControlButton(
            x, y, width, height, label, font=font, textColor=colour,
            focusedColor=focused_text, alignment=alignment, textOffsetX=text_offset,
            focusTexture=clear, noFocusTexture=clear,
        )
        self.addControl(control)
        self.dynamic_controls.append(control)
        for image in focus_images:
            image.setVisibleCondition("Control.HasFocus(%d)" % control.getId())
        return control

    def _add_chip(self, x, y, width, part, index):
        kind = str(part.get("kind") or "genre")
        background, foreground = _PALETTE.get(kind, _PALETTE["genre"])
        self._chip_images(x, y, width, background)
        if not self.edit_mode:
            label = xbmcgui.ControlLabel(x + 18, y + 10, width - 36, 40, "[B]%s[/B]" % part.get("text", ""), font="font13", textColor=foreground, alignment=6)
            self.addControl(label); self.dynamic_controls.append(label)
            return

        minus = self._filter_button(
            x + 6, y + 7, 36, 46, "−", foreground,
            "0xFF" + background[-6:], font="font30", bordered=False,
        )
        inset = 46
        tag_text = self._filter_button(
            x + inset, y + 7, max(48, width - inset - 6), 46,
            "[B]%s[/B]" % part.get("text", ""), foreground,
            "0xFF" + background[-6:], alignment=4, text_offset=2,
            bordered=False,
        )
        text_id = tag_text.getId()
        self.control_actions[minus.getId()] = ("remove", index)
        self.action_controls[minus.getId()] = minus
        self.control_actions[text_id] = ("edit", index)
        self.action_controls[text_id] = tag_text
        controls = (minus, tag_text)
        self.filter_groups.append({"ids": tuple(control.getId() for control in controls), "controls": controls})

    def _build_flow(self):
        self._clear_flow()
        left, right, x, y = 360, 1560, 360, 492
        parts = confirmation_parts(self.rules)
        for index, part in enumerate(parts):
            connector = str(part.get("connector") or "").strip()
            chip_text = str(part.get("text") or "").strip()
            connector_width = self._text_width(connector) if connector else 0
            chip_width = self._text_width(chip_text, chip=True)
            required = connector_width + (14 if connector else 0) + chip_width + 14
            if x > left and x + required > right:
                x, y = left, y + 72
            if connector:
                self._add_label(x, y + 6, connector_width, connector)
                x += connector_width + 14
            self._add_chip(x, y, chip_width, part, index)
            x += chip_width + 14
        if self.edit_mode:
            if x + 64 > right:
                x, y = left, y + 72
            self._chip_images(x, y, 60, self.neutral_background)
            plus_text = "0xFFDAD6E0"
            plus = self._filter_button(
                x, y, 60, 60, "", plus_text, self.neutral_focus,
                focused_text=plus_text,
            )
            plus_icon = xbmcgui.ControlImage(
                x + 14, y + 14, 32, 32,
                os.path.join(self.addon_path, "resources", "media", "action_icons", "v1", "plus.png"),
                aspectRatio=2, colorDiffuse=plus_text,
            )
            self.addControl(plus_icon)
            self.dynamic_controls.append(plus_icon)
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
        panel_bottom = max(740, exclusion_y + 80)
        button_y = panel_bottom + 40
        if button_y + 82 + 12 > 1068:
            raise RuntimeError("Keyword confirmation content is too tall")
        self.getControl(12).setPosition(360, footer_y)
        self.getControl(13).setPosition(360, exclusion_y)
        move_finished_button(self, self.PROMPT_EDIT_ID, 545, button_y)
        move_finished_button(self, self.CREATE_ID, 985, button_y)
        resize_finished_panel(self, 14, panel_bottom - 140)

    def _refresh_flow(self, focus_id=None, focus_first_action=False):
        self._refresh_exclusion()
        self._position_summary(self._build_flow())
        if focus_first_action and self.control_actions:
            focus_id = next(iter(self.control_actions))
        if focus_id:
            try:
                control = self.action_controls.get(focus_id) or self.getControl(focus_id)
                self.setFocus(control)
            except Exception: pass

    def _refresh_exclusion(self):
        request = self.rules.get("request_rules") or {}
        if request.get("content_type") == "episodes":
            request["hide_watched"] = self.rules.get("exclude_watched", True)
            exclusion = "Watched episodes hidden" if request.get("hide_watched", True) else "Watched episodes included"
            exclusion += "  •  Airing dates from Trakt  •  No AI will be used"
            defaults = []
            if not request.get("source"): defaults.append("all shows")
            if not request.get("window"): defaults.append("next 30 days")
            footer = "Default calendar: " + "  •  ".join(defaults) if defaults else ""
            self.getControl(12).setLabel("  •  ".join(value for value in (self.footer, footer) if value))
        elif not self.rules.get("exclude_watched", True):
            exclusion = "Watched titles allowed  •  Hidden items excluded  •  No AI will be used"
        else:
            exclusion = "Watched, rated and hidden items will be excluded  •  No AI will be used"
        self.getControl(13).setLabel(exclusion)

    def onInit(self):
        try:
            self.getControl(11).setText(self.prompt or "Add filters or edit your request.")
            self.getControl(12).setLabel(self.footer)
            self.getControl(self.PROMPT_EDIT_ID).setLabel(bold("Edit Request" if self.edit_existing else "Edit Prompt"))
            self._refresh_edit_toggle()
            self.getControl(self.CREATE_ID).setLabel(bold(self.confirm_label))
            self._refresh_flow(focus_first_action=self.edit_mode)
            if not self.edit_mode:
                self.setFocus(self.getControl(self.CREATE_ID))
        except Exception as exc:
            xbmc.log("curatr: keyword editor initialisation failed: %s" % exc, xbmc.LOGWARNING)
            self.result = "fallback"; self.close()

    def _remove_part(self, part):
        field, index = part.get("field"), int(part.get("index") or 0)
        clear = {
            "collection": ("collection_query", "collection_name"), "country": ("country", "country_label"),
            "language": ("language", "language_label"), "year": ("year_min", "year_max"),
            "rating": ("rating_min",), "runtime": ("runtime_min", "runtime_max"),
            "external": ("external_source", "external_source_label", "external_chart_limit", "external_rating_min"),
            "history": ("history_mode", "history_days", "history_plays", "history_comparison"),
            "excluded_genres": ("excluded_genres", "excluded_genre_labels"),
        }
        if field == "person":
            self.rules["people"] = [row for i, row in enumerate(self.rules.get("people") or []) if i != index]
            if not self.rules["people"]: self.rules["strategy"] = "similar_films" if self.rules.get("reference_movies") else "filtered_discover"
        elif field == "reference":
            self.rules["reference_movies"] = [row for i, row in enumerate(self.rules.get("reference_movies") or []) if i != index]
            if not self.rules["reference_movies"]:
                self.rules["strategy"] = "exact_people" if self.rules.get("people") else "filtered_discover"
        elif field == "genres":
            for key in ("genres", "genre_labels", "themes", "theme_labels"): self.rules[key] = []
            self.rules["genre_match"] = "all"
        elif field == "mainstream": self.rules.update({"avoid_mainstream": False, "prefer_blockbusters": False})
        elif field == "sort": self.rules["sort"] = "balanced"
        else:
            if field in ("episode_source", "airing_window"):
                request = self.rules.setdefault("request_rules", {})
                if field == "episode_source": request["source"] = ""
                else: request.update(window="", window_days=0)
                return
            for key in clear.get(field, ()): self.rules[key] = 0 if key.endswith(("_min", "_max", "_days", "_plays", "_limit")) else ""
            if field == "excluded_genres":
                self.rules.update(excluded_genres=[], excluded_genre_labels=[])
            if field == "collection": self.rules["strategy"] = "filtered_discover"
            if field == "history": self.rules["exclude_watched"] = True

    def _edit_part(self, part):
        field, index = part.get("field"), int(part.get("index") or 0)
        if field == "episode_source":
            request = self.rules.setdefault("request_rules", {})
            sources = ("trakt", "kodi", "all")
            request["source"] = sources[(sources.index(request.get("source") or "trakt") + 1) % len(sources)]
            return
        current = str(part.get("text") or "")
        if field == "airing_window":
            request = self.rules.get("request_rules") or {}
            current = {"next_calendar_month": "next calendar month", "this_calendar_month": "this calendar month"}.get(request.get("window")) or "%s %d days" % ("past" if request.get("window") == "past_days" else "next", request.get("window_days") or 30)
        value = xbmcgui.Dialog().input("Edit %s" % (part.get("connector") or field or "filter"), defaultt=current)
        if value is None or not str(value).strip(): return
        value = str(value).strip()
        if field == "person": self.rules["people"][index]["query"] = value; self.rules["people"][index].pop("name", None)
        elif field == "reference": self.rules["reference_movies"][index]["title"] = value
        elif field == "collection": self.rules.update({"collection_query": value, "collection_name": value, "strategy": "collection"})
        else:
            if field == "airing_window":
                self._set_airing_window(value)
                return
            parsed = parse_prompt("%s %s" % (part.get("connector") or "", value))
            if field == "history":
                parsed = parse_history("%s %s" % (part.get("connector") or "", value))
                if not parsed["history_mode"]:
                    self._history_help()
                    return
            keys = {
                "genres": ("genres", "genre_labels", "themes", "theme_labels", "genre_match"), "year": ("year_min", "year_max"),
                "rating": ("rating_min",), "runtime": ("runtime_min", "runtime_max"),
                "language": ("language", "language_label"), "country": ("country", "country_label"),
                "history": HISTORY_KEYS,
                "excluded_genres": ("excluded_genres", "excluded_genre_labels"),
                "sort": ("sort",),
            }.get(field, ())
            if not keys: return
            if (field == "sort" and parsed.get("sort") == "balanced") or not any(parsed.get(key) for key in keys if key not in ("genre_match", "exclude_watched")):
                xbmcgui.Dialog().ok("Keyword Matching", "That filter wasn't recognised. Your existing filter has been kept. Use the minus button to remove it.")
                return
            for key in keys: self.rules[key] = parsed.get(key, self.rules.get(key))

    def _add_filter(self):
        options = []
        request = self.rules.get("request_rules") or {}
        episodes = request.get("content_type") == "episodes"
        if episodes and not request.get("source"): options.append(("Episode source", "episode_source"))
        if not request.get("window"): options.append(("Airing window" if episodes else "Release window", "airing_window"))
        if len(self.rules.get("genres") or []) + len(self.rules.get("themes") or []) < 4: options.append(("Genre" if episodes else "Genre or theme", "genres"))
        if not (self.rules.get("year_min") or self.rules.get("year_max")): options.append(("Year or decade", "year"))
        if not self.rules.get("rating_min"): options.append(("Rating", "rating"))
        if not (self.rules.get("runtime_min") or self.rules.get("runtime_max")): options.append(("Runtime", "runtime"))
        if not episodes:
            if not self.rules.get("collection_query") and len(self.rules.get("people") or []) < 3: options.extend((("Actor", "actor"), ("Director", "director")))
            if not self.rules.get("collection_query") and len(self.rules.get("reference_movies") or []) < 3: options.append(("Reference", "reference"))
            if not self.rules.get("collection_query") and not self.rules.get("people") and not self.rules.get("reference_movies"): options.append(("Collection", "collection"))
        if not self.rules.get("language"): options.append(("Language", "language"))
        if not self.rules.get("country"): options.append(("Country", "country"))
        if not self.rules.get("history_mode"): options.append(("Viewing history", "history"))
        if self.rules.get("sort") == "balanced": options.append(("Ordering", "sort"))
        if not options:
            xbmcgui.Dialog().ok("Keyword Matching", "No more filter types are available.")
            return
        choice = xbmcgui.Dialog().select("Add a filter", [row[0] for row in options])
        if choice < 0: return
        label, kind = options[choice]
        if kind == "episode_source":
            self.rules.setdefault("request_rules", {})["source"] = "trakt"
            return
        value = xbmcgui.Dialog().input("Viewing history (e.g. unwatched, seen twice, not watched in 2 years)" if kind == "history" else "Add %s" % label.lower())
        if not value or not value.strip(): return
        value = value.strip()
        if kind == "airing_window":
            self._set_airing_window(value)
        elif kind in ("actor", "director"):
            people = list(self.rules.get("people") or [])
            people.append({"query": value, "role": "cast" if kind == "actor" else "director"})
            self.rules["people"], self.rules["strategy"] = people, "reference_people" if self.rules.get("reference_movies") else "exact_people"
        elif kind == "reference":
            refs = list(self.rules.get("reference_movies") or [])
            refs.append({"title": value, "year": 0}); self.rules["reference_movies"] = refs
            self.rules["strategy"] = "reference_people" if self.rules.get("people") else "similar_films"
        elif kind == "collection":
            self.rules.update({"collection_query": value, "collection_name": value, "strategy": "collection"})
        else:
            prefixes = {"genres": "", "year": "from ", "rating": "rated ", "runtime": "under ", "language": "in ", "country": "", "history": "", "sort": "sorted by "}
            parsed = parse_prompt(prefixes.get(kind, "") + value)
            if kind == "sort" and parsed.get("sort") == "balanced":
                xbmcgui.Dialog().ok("Keyword Matching", "Use release date, popularity or rating. No ordering filter was added.")
                return
            if kind == "history":
                parsed = parse_history(value)
                if not parsed["history_mode"]:
                    self._history_help()
                    return
                self.rules.update(parsed)
                return
            key_groups = {
                "genres": ("genres", "genre_labels", "themes", "theme_labels"), "year": ("year_min", "year_max"),
                "rating": ("rating_min",), "runtime": ("runtime_min", "runtime_max"),
                "language": ("language", "language_label"), "country": ("country", "country_label"),
                "history": HISTORY_KEYS,
                "sort": ("sort",),
            }
            for key in key_groups.get(kind, ()):
                incoming = parsed.get(key)
                if isinstance(incoming, list):
                    self.rules[key] = list(dict.fromkeys(list(self.rules.get(key) or []) + incoming))
                elif isinstance(incoming, bool) or incoming not in (None, "", 0): self.rules[key] = incoming
            if kind == "genres" and parsed.get("genres"):
                self.rules["genre_match"] = parsed["genre_match"]

    def _set_airing_window(self, value):
        parsed = parse_request(value)
        if not parsed.get("window"):
            xbmcgui.Dialog().ok("Keyword Matching", "That filter wasn't recognised. Try next 30 days, past 7 days, this calendar month or next calendar month.")
            return
        self.rules.setdefault("request_rules", {}).update({key: parsed[key] for key in ("window", "window_days")})

    @staticmethod
    def _history_help():
        xbmcgui.Dialog().ok("Viewing history", "That history filter wasn't recognised. Try unwatched, watched before, seen twice, watched at least 3 times, or not watched in the last 2 years.")

    def onClick(self, control_id):
        if control_id == self.PROMPT_EDIT_ID:
            self.result = "edit"; self.close()
        elif control_id == self.CREATE_ID:
            if not confirmation_confidence(self.rules):
                xbmcgui.Dialog().ok("Keyword Matching", "Add at least one filter before saving the list.")
                return
            try:
                validate_filters(self.rules)
            except ValueError as exc:
                xbmcgui.Dialog().ok("Keyword Matching", str(exc))
                return
            self.result = "create"; self.close()
        elif control_id == self.FILTER_EDIT_ID:
            self.edit_mode = not self.edit_mode
            self._refresh_edit_toggle()
            self._refresh_flow(self.CREATE_ID if not self.edit_mode else None, focus_first_action=self.edit_mode)
        elif control_id in self.control_actions:
            action, index = self.control_actions[control_id]
            parts = confirmation_parts(self.rules)
            if action == "remove" and 0 <= index < len(parts): self._remove_part(parts[index])
            elif action == "edit" and 0 <= index < len(parts): self._edit_part(parts[index])
            elif action == "add": self._add_filter()
            self.rules["display_parts"] = confirmation_parts(self.rules)
            self.rules["confidence"] = confirmation_confidence(self.rules)
            self._refresh_flow(focus_first_action=True)

    def onAction(self, action):
        if action.getId() in _BACK_ACTIONS:
            if self.edit_mode:
                self.edit_mode = False
                self._refresh_edit_toggle()
                self._refresh_flow(self.CREATE_ID)
            else: self.close()


def confirm_keyword_rules(addon_path, prompt, rules, footer="", edit_existing=False,
                          confirm_label=None, start_editing=False):
    window = None
    try:
        window = KeywordConfirmWindow(addon_path, prompt, rules, footer, edit_existing,
                                      confirm_label, start_editing)
        window.doModal(); result = window.result
        if result in ("create", "edit") and isinstance(rules, dict) and isinstance(window.rules, dict):
            rules.clear(); rules.update(deepcopy(window.rules))
    except Exception as exc:
        xbmc.log("curatr: keyword editor unavailable: %s" % exc, xbmc.LOGWARNING)
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
        save_label = confirm_label or ("Save" if edit_existing else "Create List")
        choice = xbmcgui.Dialog().yesnocustom("Keyword Matching", message, edit_label, nolabel="Cancel", yeslabel=save_label)
        return "edit" if choice == 2 else ("create" if choice == 1 else None)
    except (AttributeError, TypeError):
        save_label = confirm_label or ("Save" if edit_existing else "Create List")
        return "create" if xbmcgui.Dialog().yesno("Keyword Matching", message, nolabel="Cancel", yeslabel=save_label) else None
