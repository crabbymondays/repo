import sys
from urllib.parse import urlencode

import xbmc
import xbmcaddon
import xbmcgui

from lib.core import Curator


ADDON = xbmcaddon.Addon()
NAME = ADDON.getAddonInfo("name") or "curatr"


def _call(obj, method, default="", *args):
    try:
        value = getattr(obj, method)(*args)
        return default if value in (None, "") else value
    except Exception:
        return default


def _selected_item():
    item = getattr(sys, "listitem", None)
    if item is None:
        raise RuntimeError("Kodi did not provide the selected item.")
    tag = _call(item, "getVideoInfoTag", None)
    media_type = str(_call(tag, "getMediaType", "") if tag else "").lower()
    if media_type == "tvshow":
        media_type = "show"
    elif media_type != "movie":
        media_type = ""
    title = str(_call(tag, "getTitle", "") if tag else "").strip()
    if not title:
        title = str(_call(item, "getLabel", "")).strip()
    year = _call(tag, "getYear", 0) if tag else 0
    try:
        year = int(year or 0)
    except (TypeError, ValueError):
        year = 0
    ids = {}
    if tag:
        for key in ("tmdb", "imdb", "tvdb"):
            value = _call(tag, "getUniqueID", "", key)
            if value:
                ids[key] = value
    path = str(_call(item, "getPath", "")).strip()
    is_folder = bool(_call(item, "isFolder", False))
    art = {}
    for key in ("poster", "thumb", "fanart", "landscape", "icon"):
        value = str(_call(item, "getArt", "", key)).strip()
        if value:
            art[key] = value
    stored_images = {}
    poster = art.get("poster") or art.get("thumb") or art.get("icon")
    fanart = art.get("fanart") or art.get("landscape")
    if poster:
        stored_images["poster"] = {"full": poster}
    if fanart:
        stored_images["fanart"] = {"full": fanart}
    return {
        "title": title,
        "year": year,
        "media_type": media_type,
        "ids": ids,
        "overview": str(_call(tag, "getPlot", "") if tag else ""),
        "rating": _call(tag, "getRating", 0.0) if tag else 0.0,
        "genres": list(_call(tag, "getGenres", []) if tag else []),
        "images": stored_images,
        "art": art,
        "path": path,
        "is_folder": is_folder,
    }


def _open_preview(selected):
    if not selected.get("media_type") or not selected.get("title"):
        raise RuntimeError("curatr could not identify that item as a movie or TV show.")
    ids = selected.get("ids") or {}
    params = {
        "action": "similar_preview",
        "title": selected.get("title") or "",
        "year": str(selected.get("year") or ""),
        "media_type": selected.get("media_type") or "movie",
        "tmdb_id": str(ids.get("tmdb") or ""),
        "imdb_id": str(ids.get("imdb") or ""),
        "tvdb_id": str(ids.get("tvdb") or ""),
    }
    url = "plugin://plugin.video.curatr/?" + urlencode(params)
    xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % url.replace('"', '%22'))


def main():
    command = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    selected = _selected_item()
    if command == "find_similar":
        _open_preview(selected)
        return
    curator = Curator(ADDON)
    if command == "add_to_list":
        if not selected.get("media_type"):
            raise RuntimeError("Select a movie or TV show to add to a curatr list.")
        curator.add_media_to_list_interactive(selected)
    elif command == "add_to_folder":
        if not selected.get("is_folder") or not selected.get("path"):
            raise RuntimeError("Select an add-on folder to add to a curatr folder.")
        curator.add_external_shortcut_interactive(
            selected.get("path"), selected.get("title"),
            (selected.get("art") or {}).get("thumb") or (selected.get("art") or {}).get("icon"),
        )
    else:
        raise RuntimeError("Unknown curatr context action.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        xbmc.log("curatr context action failed: %s" % exc, xbmc.LOGERROR)
        xbmcgui.Dialog().ok(NAME, str(exc))
