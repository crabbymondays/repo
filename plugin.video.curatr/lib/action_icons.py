"""Icons for the right-hand action lists; editor tabs remain text-only."""


_PREFIX = "special://home/addons/plugin.video.curatr/resources/media/action_icons/v1/"
_ICONS = {
    "move_up": "caret-up", "move_front": "caret-up",
    "move_down": "caret-down", "move_back": "caret-down",
    "contents": "filter", "artwork": "image", "details": "circle-info",
    "info": "circle-info", "reference": "plus", "folder": "folder-plus",
    "template": "bookmark", "refresh": "arrows-rotate", "sync": "arrows-rotate",
}


def action_icon(row):
    label = str(row.get("label") or "").lower()
    if "remove" in label:
        name = "xmark"
    elif "delete" in label:
        name = "trash"
    elif "settings" in label:
        name = "gear"
    elif "refresh" in label or "sync" in label:
        name = "arrows-rotate"
    else:
        name = _ICONS.get(str(row.get("key") or ""))
    return _PREFIX + name + ".png" if name else ""


def decorate_action(item, row):
    item.setProperty("CuratrActionIcon", action_icon(row))
    item.setProperty("CuratrActionTint", "FFFFFFFF" if row.get("enabled", True) else "FF77747D")
    return item
