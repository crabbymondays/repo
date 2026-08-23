import os
import re

import xbmcvfs

from .art_cache import ArtworkCache


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
        "icon_label": "",
        "icon_style": "white",
        "fanart_mode": "auto",
        "fanart_key": "",
        "fanart_source": "",
        "fanart_label": "",
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
    if result["icon_style"] not in ("white", "colour"):
        result["icon_style"] = "white"
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
        folder = "icons_colour_v1" if state["icon_style"] == "colour" else "icons_v3"
        icon = _media(addon, "list_art", folder, key + ".png")

    fanart = ""
    if state["fanart_mode"] == "default":
        fanart = _media(addon, "fanart_menu_clean_v2.jpg")
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
    if state["icon_mode"] == "auto":
        icon = "Automatic (%s)" % label(automatic)
    elif state["icon_mode"] == "bundled":
        icon = label(state["icon_key"])
    elif state["icon_mode"] == "custom" and state["icon_label"]:
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
        # Older saved lists predate fanart_label. Recover the film name by
        # matching the stored source against artwork already in the record.
        fanart = "Film artwork"
        for movie in record.get("movies", []) if isinstance(record.get("movies"), list) else []:
            if not isinstance(movie, dict):
                continue
            if ArtworkCache._first_image(movie, "fanart") != state["fanart_source"]:
                continue
            fanart = "%s%s" % (
                movie.get("title") or "Film artwork",
                " (%s)" % movie.get("year") if movie.get("year") else "",
            )
            break
    elif state["fanart_mode"] == "person":
        fanart = "Person artwork"
    else:
        fanart = state["fanart_mode"].replace("_", " ").title()
    return icon, fanart, state["fanart_style"].title()
