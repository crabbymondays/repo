"""Palette handling for Curatr's custom Kodi windows."""

_PRESETS = {
    "violet": {
        "primary": "7653B9",
        "secondary": "A365D1",
        "tint": "39294F",
    },
    "ocean": {
        "primary": "356FC2",
        "secondary": "169CB4",
        "tint": "203A55",
    },
    "emerald": {
        "primary": "2E8B69",
        "secondary": "58A884",
        "tint": "25483D",
    },
    "amber": {
        "primary": "B66B22",
        "secondary": "D49A35",
        "tint": "51381F",
    },
}

_COLOURS = {
    "violet": "7653B9",
    "lilac": "A365D1",
    "blue": "356FC2",
    "cyan": "169CB4",
    "teal": "278F8A",
    "green": "2E8B69",
    "amber": "B66B22",
    "gold": "D49A35",
    "red": "B64B59",
    "pink": "BE558C",
    "neutral": "626775",
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
    selected = _setting(addon, setting_id, "theme")
    return _rgb(_COLOURS.get(selected, fallback))


def is_light_mode(addon=None):
    if addon is None:
        try:
            import xbmcaddon

            addon = xbmcaddon.Addon("plugin.video.curatr")
        except Exception:
            addon = None
    return bool(addon and _enabled(addon, "interface_light_mode"))


def theme_palette(addon=None):
    """Return the complete colour palette used by the custom window XML."""
    if addon is None:
        try:
            import xbmcaddon

            addon = xbmcaddon.Addon("plugin.video.curatr")
        except Exception:
            addon = None

    preset_name = _setting(addon, "interface_theme", "violet") if addon else "violet"
    preset = _PRESETS.get(preset_name, _PRESETS["violet"])
    custom = bool(addon and _enabled(addon, "interface_custom_colours"))
    light = is_light_mode(addon)

    primary = _rgb(preset["primary"])
    secondary = _rgb(preset["secondary"])
    tint = _rgb(preset["tint"])
    if custom:
        primary = _selected_colour(addon, "interface_primary_colour", preset["primary"])
        secondary = _selected_colour(addon, "interface_secondary_colour", preset["secondary"])
        tint_choice = _setting(addon, "interface_background_colour", "theme")
        if tint_choice != "theme":
            tint = _rgb(_COLOURS.get(tint_choice, preset["tint"]))

    danger = _rgb(_COLOURS["red"])
    if light:
        primary = _mix(primary, (0, 0, 0), 0.12)
        secondary = _mix(secondary, (0, 0, 0), 0.15)
        danger = _mix(danger, (0, 0, 0), 0.10)
        backdrop = _mix(tint, (255, 255, 255), 0.86)
        panel = _mix(tint, (255, 255, 255), 0.80)
        surface = _mix(tint, (255, 255, 255), 0.74)
        row = _mix(tint, (255, 255, 255), 0.68)
    else:
        backdrop = _mix(tint, (0, 0, 0), 0.78)
        panel = _mix(tint, (0, 0, 0), 0.64)
        surface = _mix(tint, (0, 0, 0), 0.52)
        row = _mix(tint, (255, 255, 255), 0.12)

    primary = _for_white_text(primary)
    secondary = _for_white_text(secondary)
    danger = _for_white_text(danger)

    return {
        "CuratrPrimary": _argb("FF", primary),
        "CuratrSecondary": _argb("FF", secondary),
        "CuratrDanger": _argb("FF", danger),
        "CuratrBackdrop": _argb("ED", backdrop),
        "CuratrBackdropAlt": _argb("E8", backdrop),
        "CuratrPanel": _argb("E5", panel),
        "CuratrPanelMedium": _argb("AA", surface),
        "CuratrPanelSoft": _argb("66", surface),
        "CuratrKeywordPanel": _argb("B9", panel),
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
    """Publish the active palette and select the dark or light text layout."""
    try:
        import xbmcaddon

        addon = xbmcaddon.Addon("plugin.video.curatr")
    except Exception:
        addon = None
    _publish_palette(theme_palette(addon))
    return "Light" if is_light_mode(addon) else "Default"
