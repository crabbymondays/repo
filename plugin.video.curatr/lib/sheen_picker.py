"""Custom RGB entry for the sheen swatches inside Customise Theme."""

import re

import xbmcgui

from .ui_theme import normalise_sheen_colour


def enter_custom_colour(current):
    current = normalise_sheen_colour(current)
    dialog = xbmcgui.Dialog()
    initial = current if current.startswith("#") else "#FFFFFF"
    while True:
        entered = dialog.input("Custom colour (e.g. #B8DFFF)", defaultt=initial).strip()
        if not entered:
            return None
        if re.fullmatch(r"#?[0-9a-fA-F]{6}", entered):
            return normalise_sheen_colour(entered)
        dialog.ok("Sheen colour", "Enter six colour digits, for example #B8DFFF.")
        initial = entered
