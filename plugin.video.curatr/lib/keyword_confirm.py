"""Lightweight, skin-independent confirmation window for Keyword Matching."""

import os

import xbmcgui

from .keyword_matcher import confirmation_parts, format_rules


_BACK_ACTIONS = {9, 10, 92}
_PALETTE = {
    "genre": ("0x594B2632", "0xFFFFD2D9"),
    "person": ("0x59304B62", "0xFFD5EEFF"),
    "film": ("0x59493664", "0xFFE8DCFF"),
    "number": ("0x59584A26", "0xFFFFECB5"),
    "year": ("0x595C392E", "0xFFFFDAC9"),
    "runtime": ("0x59383F66", "0xFFDDE2FF"),
    "place": ("0x592A554B", "0xFFD2F4EA"),
}


class KeywordConfirmWindow(xbmcgui.WindowXMLDialog):
    EDIT_ID = 100
    CREATE_ID = 101

    def __new__(cls, addon_path, prompt, rules, footer):
        return super().__new__(cls, "curatr-keyword-confirm.xml", addon_path, "Default", "1080i")

    def __init__(self, addon_path, prompt, rules, footer):
        self.addon_path = addon_path
        self.prompt = str(prompt or "")
        self.rules = dict(rules or {})
        self.footer = str(footer or "")
        self.result = None
        self.dynamic_controls = []

    @staticmethod
    def _text_width(text, chip=False):
        # Bold chip labels are slightly wider than connector text. Keep the
        # estimate conservative so Kodi fonts do not touch the rounded ends.
        # Kodi's bold font needs more room than a plain character count
        # suggests. Keep chips compact, but never squeeze common values into
        # an ellipsis (for example "Mystery" or "2000 onwards").
        unit = 13 if chip else 11
        padding = 52 if chip else 20
        minimum = 120 if chip else 46
        maximum = 500 if chip else 330
        return max(minimum, min(maximum, len(str(text or "")) * unit + padding))

    def _add_label(self, x, y, width, text, colour="0xFFD7D3DF"):
        control = xbmcgui.ControlLabel(
            x, y, width, 50, str(text or ""), font="font13", textColor=colour,
            alignment=4,
        )
        self.addControl(control); self.dynamic_controls.append(control)

    def _add_chip(self, x, y, width, text, kind):
        background, foreground = _PALETTE.get(kind, _PALETTE["genre"])
        media = os.path.join(self.addon_path, "resources", "media")
        pixel = os.path.join(media, "pixel.png")
        # Use a raster cap here. Some Kodi/Android builds flatten dynamically
        # created SVG ControlImages, making the chip appear square even though
        # the source SVG is rounded.
        rounded_left = os.path.join(media, "chip_round_left.png")
        rounded_right = os.path.join(media, "chip_round_right.png")
        height, corner = 60, 14
        controls = [
            xbmcgui.ControlImage(x, y, corner, height, rounded_left, colorDiffuse=background),
            xbmcgui.ControlImage(x + corner, y, max(1, width - corner * 2), height, pixel, colorDiffuse=background),
            xbmcgui.ControlImage(x + width - corner, y, corner, height, rounded_right, colorDiffuse=background),
            xbmcgui.ControlLabel(
                x + 18, y + 10, width - 36, 40, "[B]%s[/B]" % str(text or ""),
                font="font13", textColor=foreground, alignment=6,
            ),
        ]
        self.addControls(controls); self.dynamic_controls.extend(controls)

    def _build_flow(self):
        left, right, x, y = 360, 1560, 360, 468
        line_height = 72
        for part in confirmation_parts(self.rules):
            connector = str(part.get("connector") or "").strip()
            chip_text = str(part.get("text") or "").strip()
            connector_width = self._text_width(connector) if connector else 0
            chip_width = self._text_width(chip_text, chip=True)
            required = connector_width + (12 if connector else 0) + chip_width + 14
            if x > left and x + required > right:
                x, y = left, y + line_height
            if connector:
                self._add_label(x, y + 6, connector_width, connector)
                x += connector_width + 12
            self._add_chip(x, y, chip_width, chip_text, str(part.get("kind") or "genre"))
            x += chip_width + 14
        return y + 60

    def _position_summary(self, flow_bottom):
        """Keep short prompts compact while allowing wrapped chips to expand safely."""
        footer_y = max(610, int(flow_bottom) + 34)
        exclusion_y = footer_y + 50
        button_y = max(780, exclusion_y + 84)
        if button_y + 82 > 1030:
            raise RuntimeError("Keyword confirmation content is too tall for the custom window")
        self.getControl(12).setPosition(360, footer_y)
        self.getControl(13).setPosition(360, exclusion_y)
        self.getControl(self.EDIT_ID).setPosition(545, button_y)
        self.getControl(self.CREATE_ID).setPosition(985, button_y)
        panel_bottom = max(740, exclusion_y + 80)
        self.getControl(14).setHeight(panel_bottom - 140)

    def onInit(self):
        try:
            self.getControl(11).setText(self.prompt)
            self.getControl(12).setLabel(self.footer)
            self._position_summary(self._build_flow())
            self.setFocus(self.getControl(self.CREATE_ID))
        except Exception:
            self.result = "fallback"
            self.close()

    def onClick(self, control_id):
        if control_id == self.EDIT_ID:
            self.result = "edit"; self.close()
        elif control_id == self.CREATE_ID:
            self.result = "create"; self.close()

    def onAction(self, action):
        if action.getId() in _BACK_ACTIONS:
            self.close()


def confirm_keyword_rules(addon_path, prompt, rules, footer=""):
    window = None
    try:
        window = KeywordConfirmWindow(addon_path, prompt, rules, footer)
        window.doModal()
        result = window.result
    except Exception:
        result = "fallback"
    finally:
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
    if result != "fallback":
        return result
    message = format_rules(rules)
    if footer:
        message = "%s\n\n%s" % (message, footer)
    try:
        choice = xbmcgui.Dialog().yesnocustom(
            "Keyword Matching", message, "Edit Prompt", nolabel="Cancel", yeslabel="Create List",
        )
        return "edit" if choice == 2 else ("create" if choice == 1 else None)
    except (AttributeError, TypeError):
        return "create" if xbmcgui.Dialog().yesno("Keyword Matching", message, nolabel="Cancel", yeslabel="Create List") else None
