import os
import re

import xbmcvfs


CHOICES = (
    ("action", "Action"), ("comedy", "Comedy"), ("crime", "Crime"),
    ("drama", "Drama"), ("horror", "Horror"), ("romance", "Romance"),
    ("sci_fi", "Sci-Fi"), ("fantasy", "Fantasy"), ("thriller", "Thriller"),
    ("mystery", "Mystery"), ("western", "Western"),
    ("documentary", "Documentary"), ("animation", "Animation"),
    ("mind_bending", "Mind-Bending"), ("superhero", "Superhero"),
    ("director", "Director"), ("actor", "Actor"),
)

LABELS = dict(CHOICES)
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
        "fanart_mode": "auto",
        "fanart_key": "",
        "fanart_source": "",
        "fanart_style": "colour",
    }


def normalise_state(value):
    result = default_state()
    if isinstance(value, dict):
        for key in result:
            if value.get(key) not in (None,):
                result[key] = str(value.get(key) or "")
    if result["icon_mode"] not in ("auto", "bundled", "custom", "default"):
        result["icon_mode"] = "auto"
    if result["fanart_mode"] not in ("auto", "bundled", "item", "person", "custom", "default"):
        result["fanart_mode"] = "auto"
    if result["fanart_style"] not in ("colour", "monochrome"):
        result["fanart_style"] = "colour"
    return result


def _media(addon, *parts):
    root = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    return os.path.join(root, "resources", "media", *parts)


def resolved_sources(addon, record):
    record = record if isinstance(record, dict) else {}
    state = normalise_state(record.get("artwork"))
    automatic = suggest_key(record.get("name"), record.get("prompt"))

    icon = ""
    if state["icon_mode"] == "default":
        icon = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo("path")), "icon.png")
    elif state["icon_mode"] == "custom":
        icon = state["icon_source"]
    else:
        key = automatic if state["icon_mode"] == "auto" else state["icon_key"]
        if key not in LABELS:
            key = automatic
        # Versioned path avoids Kodi reusing the earlier drawn icon cache.
        icon = _media(addon, "list_art", "icons_v2", key + ".png")

    fanart = ""
    if state["fanart_mode"] == "default":
        fanart = _media(addon, "fanart_global.jpg")
    elif state["fanart_mode"] in ("item", "person", "custom"):
        fanart = state["fanart_source"]
    else:
        key = automatic if state["fanart_mode"] == "auto" else state["fanart_key"]
        if key not in LABELS:
            key = automatic
        folder = "fanart_mono_v2" if state["fanart_style"] == "monochrome" else "fanart_v2"
        fanart = _media(addon, "list_art", folder, key + ".jpg")
    return {"icon": icon, "thumb": icon, "fanart": fanart, "landscape": fanart}


def summary(record):
    record = record if isinstance(record, dict) else {}
    state = normalise_state(record.get("artwork"))
    automatic = suggest_key(record.get("name"), record.get("prompt"))
    icon = "Automatic (%s)" % label(automatic) if state["icon_mode"] == "auto" else (
        label(state["icon_key"]) if state["icon_mode"] == "bundled" else state["icon_mode"].title()
    )
    fanart = "Automatic (%s)" % label(automatic) if state["fanart_mode"] == "auto" else (
        label(state["fanart_key"]) if state["fanart_mode"] == "bundled" else state["fanart_mode"].replace("_", " ").title()
    )
    return icon, fanart, state["fanart_style"].title()
