import os
import re

import xbmcvfs

from .art_cache import ArtworkCache
from .menu_art import ADDON_ICON, current_menu_source
from .menu_background import background_source
from .bundled_art import (
    BUNDLE as ARTWORK_BUNDLE, CHOICES, colour_label, components, normalise_colour, rendered_source,
)


LABELS = dict(CHOICES)
_LEGACY_FOLDERS = {
    "icons_v2": ("icon", "white"),
    "icons_v3": ("icon", "white"),
    "icons_colour_v3": ("icon", "genre_colours"),
    "icons_colour_v4": ("icon", "genre_colours"),
    "fanart_v2": ("fanart", "colour"),
    "fanart_mono_v2": ("fanart", "monochrome"),
}
KEYWORDS = (
    ("director", ("director", "directed by", "filmmaker", "films by")),
    ("actor", ("actor", "actress", "starring", "films with", "performer")),
    ("mind_bending", ("mind-bending", "mind bending", "surreal", "reality-bending", "dreamlike")),
    ("sci_fi", ("sci-fi", "science fiction", "space", "cyberpunk", "alien")),
    ("superhero", ("superhero", "comic book", "super-powered")),
    ("documentary", ("documentary", "documentaries", "non-fiction", "nonfiction")),
    ("animation", ("animation", "animated", "anime")),
    ("western", ("western", "cowboy", "frontier")),
    ("romance", ("romance", "romantic", "love story")),
    ("horror", ("horror", "scary", "terrifying", "slasher", "haunted")),
    ("mystery", ("mystery", "whodunit", "detective", "puzzle")),
    ("thriller", ("thriller", "suspense", "tense", "conspiracy")),
    ("crime", ("crime", "gangster", "mafia", "heist", "serial killer", "noir")),
    ("fantasy", ("fantasy", "magic", "mythical", "fairy tale")),
    ("comedy", ("comedy", "funny", "hilarious", "laugh")),
    ("drama", ("drama", "dramatic", "character study")),
    ("action", ("action", "explosive", "adrenaline", "martial arts")),
)


def suggest_key(name="", prompt=""):
    text = " ".join((str(name or ""), str(prompt or ""))).casefold()
    text = re.sub(r"\s+", " ", text)
    for key, words in KEYWORDS:
        if any(word in text for word in words):
            return key
    return "drama"


def label(key):
    return LABELS.get(str(key or ""), "Default")


def default_state():
    return {
        "icon_mode": "auto",
        "icon_key": "",
        "icon_source": "",
        "icon_label": "",
        "icon_style": "white",
        "icon_colour": "default",
        "fanart_mode": "auto",
        "fanart_key": "",
        "fanart_source": "",
        "fanart_label": "",
        "fanart_style": "colour",
        "fanart_colour": "default",
    }


def normalise_state(value):
    result = default_state()
    if isinstance(value, dict):
        for key in result:
            if value.get(key) not in (None,):
                result[key] = str(value.get(key) or "")
    if result["icon_mode"] not in ("auto", "bundled", "person", "custom", "default"):
        result["icon_mode"] = "auto"
    if result["icon_style"] not in ("white", "genre_colours"):
        result["icon_style"] = "white"
    if result["fanart_mode"] not in ("auto", "bundled", "item", "person", "custom", "default"):
        result["fanart_mode"] = "auto"
    if result["fanart_style"] not in ("colour", "monochrome"):
        result["fanart_style"] = "colour"
    for kind in ("icon", "fanart"):
        result[kind + "_colour"] = normalise_colour(result[kind + "_colour"])
    return result


def bundled_source(addon, key, kind, style, colour="default"):
    """Resolve one bundled artwork choice from its stable key and style."""
    root = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    try:
        return rendered_source(root, xbmcvfs.translatePath(addon.getAddonInfo("profile")),
                               key, kind, style, colour)
    except (OSError, ValueError):
        symbol, background, _palette = components(root, key, kind, style, colour)
        return (symbol or background) if kind == "icon" else background


def _current_source(addon, source):
    """Keep local shortcuts to retired bundled files usable after an upgrade."""
    source = current_menu_source(xbmcvfs.translatePath(addon.getAddonInfo("path")), source)
    path = str(source or "").replace("\\", "/")
    if "://" in path and not path.startswith("special://"):
        return source
    root = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    if path.endswith(("/plugin.video.curatr/icon.png", "/plugin.video.curatr/icon_addon_v2.png")):
        return os.path.join(root, ADDON_ICON)
    if path.endswith(("/plugin.video.curatr/fanart.jpg", "/plugin.video.curatr/fanart_addon_v2.jpg")):
        return background_source(addon)
    for filename, colour in (("fanart_menu_clean_v4.jpg", "theme"), ("background_1_v4.jpg", "deep_blue"),
                             ("background_2_v4.jpg", "deep_violet"), ("background_3_v4.jpg", "slate")):
        if path.endswith("/plugin.video.curatr/resources/media/" + filename):
            return background_source(addon, choice=colour)
    parts = path.rsplit("/plugin.video.curatr/resources/media/list_art/", 1)
    if len(parts) != 2:
        return source
    relative = parts[1].split("/")
    if len(relative) == 4 and relative[0] == "v6" and relative[1] == "backgrounds":
        kind = "icon" if relative[2] == "icon" else "fanart"
        return bundled_source(addon, "blank", kind, "colour", os.path.splitext(relative[3])[0])
    if len(relative) == 3 and relative[0] in ("v5", "v6"):
        previous = {"white": ("icon", "white"), "colour": ("icon", "genre_colours"),
                    "fanart": ("fanart", "colour"), "monochrome": ("fanart", "monochrome"),
                    "landscape": ("fanart", "colour")}
        key = os.path.splitext(relative[2])[0]
        if relative[1] in previous and key in LABELS:
            return bundled_source(addon, key, *previous[relative[1]])
    if len(relative) != 2 or relative[0] not in _LEGACY_FOLDERS:
        return source
    key, extension = os.path.splitext(relative[1])
    kind, style = _LEGACY_FOLDERS[relative[0]]
    if key not in LABELS or extension != (".png" if kind == "icon" else ".jpg"):
        return source
    return bundled_source(addon, key, kind, style)


def resolved_sources(addon, record):
    record = record if isinstance(record, dict) else {}
    state = normalise_state(record.get("artwork"))
    automatic = suggest_key(record.get("name"), record.get("prompt"))

    icon = ""
    if state["icon_mode"] == "default":
        icon = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo("path")), ADDON_ICON)
    elif state["icon_mode"] in ("person", "custom"):
        icon = _current_source(addon, state["icon_source"])
    else:
        key = automatic if state["icon_mode"] == "auto" else state["icon_key"]
        if key not in LABELS:
            key = automatic
        # List artwork is independent from the global menu theme. Versioned
        # folders also prevent Kodi retaining an older texture-cache result.
        icon_style = state["icon_style"]
        if state["icon_mode"] == "auto":
            icon_style = "genre_colours" if state["fanart_style"] == "colour" else "white"
        icon = bundled_source(addon, key, "icon", icon_style, state["icon_colour"])

    fanart = ""
    if state["fanart_mode"] == "default":
        fanart = background_source(addon)
    elif state["fanart_mode"] in ("item", "person", "custom"):
        fanart = _current_source(addon, state["fanart_source"])
    else:
        key = automatic if state["fanart_mode"] == "auto" else state["fanart_key"]
        if key not in LABELS:
            key = automatic
        fanart = bundled_source(addon, key, "fanart", state["fanart_style"], state["fanart_colour"])
    return {"icon": icon, "thumb": icon, "fanart": fanart, "landscape": fanart}


def summary(record):
    record = record if isinstance(record, dict) else {}
    state = normalise_state(record.get("artwork"))
    automatic = suggest_key(record.get("name"), record.get("prompt"))
    if state["icon_mode"] == "auto":
        style = "Colours" if state["fanart_style"] == "colour" else "White"
        icon = "Automatic (%s: %s)" % (label(automatic), style)
    elif state["icon_mode"] == "bundled":
        style = "Colours" if state["icon_style"] == "genre_colours" else "White"
        icon = "%s: %s" % (label(state["icon_key"]), style)
    elif state["icon_mode"] in ("person", "custom") and state["icon_label"]:
        icon = state["icon_label"]
    else:
        icon = state["icon_mode"].title()
    if state["fanart_mode"] == "auto":
        fanart = "Automatic (%s)" % label(automatic)
    elif state["fanart_mode"] == "bundled":
        fanart = label(state["fanart_key"])
    elif state["fanart_mode"] in ("item", "person", "custom") and state["fanart_label"]:
        fanart = state["fanart_label"]
    elif state["fanart_mode"] == "item":
        # Older saved lists predate fanart_label. Recover the item name by
        # matching the stored source against artwork already in the record.
        fanart = "Item artwork"
        for item in record.get("movies", []) if isinstance(record.get("movies"), list) else []:
            if not isinstance(item, dict):
                continue
            if ArtworkCache._first_image(item, "fanart") != state["fanart_source"]:
                continue
            fanart = "%s%s" % (
                item.get("title") or "Item artwork",
                " (%s)" % item.get("year") if item.get("year") else "",
            )
            break
    elif state["fanart_mode"] == "person":
        fanart = "Person artwork"
    else:
        fanart = state["fanart_mode"].replace("_", " ").title()
    if state["icon_mode"] == "bundled" and state["icon_style"] == "genre_colours" and state["icon_colour"] != "default":
        icon = "%s: %s" % (label(state["icon_key"]), colour_label(state["icon_colour"]))
    if state["fanart_mode"] == "bundled" and state["fanart_style"] == "colour" and state["fanart_colour"] != "default":
        fanart = "%s: %s" % (label(state["fanart_key"]), colour_label(state["fanart_colour"]))
    return icon, fanart, state["fanart_style"].title()
