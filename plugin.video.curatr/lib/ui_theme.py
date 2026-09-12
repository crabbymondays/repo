"""Palette handling for Curatr's custom Kodi windows."""

from .colours import COLOURS, BACKGROUND_COLOURS, normalise_colour


_PRESETS = {
    "violet": {"primary": "deep_violet", "secondary": "violet", "tint": "deep_violet"},
    "ocean": {"primary": "deep_blue", "secondary": "cyan", "tint": "deep_blue"},
    "emerald": {"primary": "green", "secondary": "sage", "tint": "green"},
    "amber": {"primary": "amber", "secondary": "gold", "tint": "amber"},
}

def _setting(addon, setting_id, default=""):
    try:
        value = addon.getSetting(setting_id)
    except Exception:
        value = ""
    return str(value or default).strip().lower()


def _enabled(addon, setting_id):
    return _setting(addon, setting_id) in ("true", "1", "yes", "on")


def _rgb(value):
    value = str(value or "000000").lstrip("#")[-6:]
    try:
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
    except (TypeError, ValueError):
        return (0, 0, 0)


def _mix(first, second, amount):
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(
        int(round(left + ((right - left) * amount)))
        for left, right in zip(first, second)
    )


def _hex(rgb):
    return "".join("%02X" % max(0, min(255, int(channel))) for channel in rgb)


def _argb(alpha, rgb):
    return str(alpha).upper() + _hex(rgb)


def _relative_luminance(rgb):
    channels = []
    for value in rgb:
        value = value / 255.0
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return (0.2126 * channels[0]) + (0.7152 * channels[1]) + (0.0722 * channels[2])


def _for_white_text(rgb):
    """Keep focused-button labels legible for every preset and custom choice."""
    adjusted = tuple(rgb)
    while (1.05 / (_relative_luminance(adjusted) + 0.05)) < 4.5:
        adjusted = _mix(adjusted, (0, 0, 0), 0.06)
    return adjusted


def _selected_colour(addon, setting_id, fallback):
    key = normalise_colour(_setting(addon, setting_id, "theme"))
    return _rgb(COLOURS[key if key != "default" else fallback][1])


def background_colour(addon, colour="theme"):
    """Resolve a menu/theme choice to the same palette key used by list artwork."""
    key = normalise_colour(colour)
    if key != "default":
        return key
    preset = _PRESETS.get(_setting(addon, "interface_theme", "violet"), _PRESETS["violet"])
    if _enabled(addon, "interface_custom_colours"):
        key = normalise_colour(_setting(addon, "interface_background_colour", "theme"))
        if key != "default":
            return key
    return preset["tint"]


def background_palette(addon, colour="theme"):
    return COLOURS[background_colour(addon, colour)]


def theme_palette(addon=None):
    """Return the shared accent colours and darker surfaces for custom windows."""
    if addon is None:
        try:
            import xbmcaddon
            addon = xbmcaddon.Addon("plugin.video.curatr")
        except Exception:
            addon = None
    preset = _PRESETS.get(_setting(addon, "interface_theme", "violet"), _PRESETS["violet"])
    primary, secondary = (_rgb(COLOURS[preset[key]][1]) for key in ("primary", "secondary"))
    if _enabled(addon, "interface_custom_colours"):
        primary = _selected_colour(addon, "interface_primary_colour", preset["primary"])
        secondary = _selected_colour(addon, "interface_secondary_colour", preset["secondary"])
    tint = _rgb(background_palette(addon)[2])
    surface = _mix(tint, (0, 0, 0), 0.52)
    row = _mix(tint, (255, 255, 255), 0.12)
    return {
        "CuratrPrimary": _argb("FF", _for_white_text(primary)),
        "CuratrSecondary": _argb("FF", _for_white_text(secondary)),
        "CuratrDanger": _argb("FF", _for_white_text(_rgb(COLOURS["red"][1]))),
        "CuratrBackdrop": _argb("ED", _mix(tint, (0, 0, 0), 0.78)),
        "CuratrBackdropAlt": _argb("E8", _mix(tint, (0, 0, 0), 0.78)),
        "CuratrPanel": _argb("E5", _mix(tint, (0, 0, 0), 0.64)),
        "CuratrPanelMedium": _argb("AA", surface),
        "CuratrPanelSoft": _argb("66", surface),
        "CuratrKeywordPanel": "FF25262B",
        "CuratrButton": _argb("66", row),
        "CuratrButtonFaint": _argb("44", row),
        "CuratrRow": _argb("55", row),
        "CuratrCard": _argb("FF", surface),
    }


def _publish_palette(palette):
    try:
        import xbmcgui

        window = xbmcgui.Window(10000)
        for name, value in palette.items():
            window.setProperty(name, value)
    except Exception:
        pass


def skin_name():
    """Publish the active palette for the custom windows."""
    try:
        import xbmcaddon

        addon = xbmcaddon.Addon("plugin.video.curatr")
    except Exception:
        addon = None
    _publish_palette(theme_palette(addon))
    return "Default"


def style_tab(window, control_id, label, selected):
    """Keep an active selector coloured independently of keyboard/touch focus."""
    palette = getattr(window, "_tab_palette", None)
    if palette is None:
        palette = window._tab_palette = theme_palette()
        window._tab_text = "FFE2DEE8"
    # A control-owned colour survives native dialogs opening above this window.
    window.getControl(1000 + control_id).setColorDiffuse(
        "0x" + palette["CuratrPrimary" if selected else "CuratrButtonFaint"]
    )
    window.getControl(control_id).setLabel(
        "[B]%s[/B]" % label if selected else label,
        textColor="0xFFFFFFFF" if selected else "0x" + window._tab_text,
    )


def show_tab(window, control_id, visible):
    window.getControl(1000 + control_id).setVisible(visible)
    control = window.getControl(control_id)
    control.setVisible(visible)
    control.setEnabled(visible)


def bold(label):
    return "[B]%s[/B]" % label
