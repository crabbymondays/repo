import os
import random
import sys
import time
from urllib.parse import parse_qs, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from lib.art_cache import ArtworkCache
from lib.core import Curator
from lib.list_art import resolved_sources as resolved_list_art
from lib.trakt import TraktError
from lib.view_refresh import list_signature, refresh_if_changed


ADDON = xbmcaddon.Addon()
NAME = ADDON.getAddonInfo("name") or "curatr"
BASE_URL = sys.argv[0]
HANDLE = int(sys.argv[1])
ADDON_ID = ADDON.getAddonInfo("id") or "plugin.video.curatr"
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
MEDIA_PATH = os.path.join(ADDON_PATH, "resources", "media")


def _loc(string_id, fallback):
    try:
        value = str(ADDON.getLocalizedString(int(string_id)) or "").strip()
        if value:
            return value
    except Exception:
        pass
    return str(fallback)


def _params():
    query = sys.argv[2][1:] if len(sys.argv) > 2 and sys.argv[2].startswith("?") else ""
    parsed = parse_qs(query, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _url(**params):
    return BASE_URL + ("?" + urlencode(params) if params else "")


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _existing_art(filename):
    if not filename:
        return ""
    path = os.path.join(MEDIA_PATH, filename)
    return path if xbmcvfs.exists(path) else ""


def _apply_menu_art(item, icon_name="", fanart_name="", custom_art=None):
    # Keep navigation glyphs as item icons while every menu entry uses the
    # same restrained global background. Never promote an icon or per-menu
    # image to fanart: many TV skins display that artwork full-screen.
    icon = _existing_art(icon_name)
    # Versioned filename prevents Kodi's texture cache from retaining the old
    # unbranded menu background after the 0.15.1 artwork change.
    fanart = _existing_art("fanart_menu_clean_v2.jpg")

    if not icon:
        root_icon = os.path.join(ADDON_PATH, "icon.png")
        if xbmcvfs.exists(root_icon):
            icon = root_icon

    art = dict(custom_art or {})
    if icon:
        art.setdefault("icon", icon)
        art.setdefault("thumb", icon)
    if fanart:
        art.setdefault("fanart", fanart)
        art.setdefault("landscape", fanart)
    if art:
        try:
            item.setArt(art)
        except Exception:
            pass


def _add_folder(label, action, plot="", context_items=None, icon_name="", fanart_name="", art=None, **params):
    item = xbmcgui.ListItem(label=label, offscreen=True)
    try:
        item.setInfo("video", {"title": label, "plot": plot or ""})
    except Exception:
        pass
    try:
        tag = item.getVideoInfoTag()
        tag.setTitle(label)
        if plot:
            tag.setPlot(plot)
    except Exception:
        pass
    _apply_menu_art(item, icon_name, fanart_name, custom_art=art)
    if context_items:
        try:
            item.addContextMenuItems(context_items)
        except Exception as exc:
            xbmc.log("curatr folder context menu skipped: %s" % exc, xbmc.LOGDEBUG)
    xbmcplugin.addDirectoryItem(HANDLE, _url(action=action, **params), item, isFolder=True)


def _add_action(label, command, plot="", icon_name="", fanart_name=""):
    item = xbmcgui.ListItem(label=label, offscreen=True)
    try:
        item.setInfo("video", {"title": label, "plot": plot or ""})
    except Exception:
        pass
    try:
        tag = item.getVideoInfoTag()
        tag.setTitle(label)
        if plot:
            tag.setPlot(plot)
    except Exception:
        pass
    _apply_menu_art(item, icon_name, fanart_name)
    xbmcplugin.addDirectoryItem(HANDLE, _url(action="run", command=command), item, isFolder=False)


def _managed_records(curator):
    records = [row for row in curator.state.get("ai_lists", []) if isinstance(row, dict)]
    records.sort(key=lambda row: _safe_int(row.get("updated_at"), 0), reverse=True)
    return records


def _record_art(curator, record):
    """Resolve both square and landscape list art without making widgets fragile."""
    try:
        art = resolved_list_art(ADDON, record)
        cache = ArtworkCache(ADDON, workers=2)
        resolved = {}
        for key, source in art.items():
            source = str(source or "").strip()
            if not source:
                continue
            if source.startswith("https://") or source.startswith("http://"):
                source = cache._download(source)
            if source and (source.startswith("special://") or xbmcvfs.exists(source)):
                resolved[key] = source
        return resolved
    except Exception as exc:
        xbmc.log("curatr list artwork skipped: %s" % exc, xbmc.LOGWARNING)
        return {}


def _root(curator):
    xbmcplugin.setPluginCategory(HANDLE, NAME)
    _add_folder(
        _loc(32410, "My Lists"), "my",
        _loc(32414, "Create, browse and manage your personalised movie lists."),
        icon_name="menu_my_lists.png", fanart_name="fanart_my_lists.jpg",
    )
    _add_folder(
        _loc(32411, "Find Something to Watch"), "explore",
        _loc(32415, "Quick picks, saved prompts and more ways to browse recommendations."),
        icon_name="menu_explore.png", fanart_name="fanart_explore.jpg",
    )
    _add_folder(
        _loc(32412, "Preferences & Activity"), "taste_activity",
        _loc(32416, "Your preferences, Trakt connection, AI usage and recent activity."),
        icon_name="menu_taste.png", fanart_name="fanart_taste.jpg",
    )
    _add_action(
        _loc(32413, "Settings"), "settings",
        _loc(32417, "Choose your AI provider, Trakt setup, defaults and notifications."),
        icon_name="menu_settings.png", fanart_name="fanart_settings.jpg",
    )
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def _my(curator):
    xbmcplugin.setPluginCategory(HANDLE, _loc(32410, "My Lists"))
    _add_folder(_loc(32418, "Browse My Lists"), "lists", _loc(32419, "Open and browse your saved lists."), icon_name="menu_my_lists.png", fanart_name="fanart_my_lists.jpg")
    _add_action(_loc(32420, "Create a New List"), "create", _loc(32424, "Describe what you want to watch and create a personalised list."), icon_name="menu_create.png", fanart_name="fanart_create.jpg")
    _add_action(_loc(32421, "Manage My Lists"), "manage", _loc(32425, "Change list names, prompts, artwork and refresh settings."), icon_name="menu_manage.png", fanart_name="fanart_my_lists.jpg")
    _add_action(_loc(32422, "Refresh All Lists"), "update", _loc(32426, "Find fresh recommendations for all your saved lists."), icon_name="menu_refresh.png", fanart_name="fanart_my_lists.jpg")
    _add_action(_loc(32423, "Backup & Restore"), "backup", _loc(32427, "Save or restore a backup of your lists, prompts and hidden movies."), icon_name="menu_backup.png", fanart_name="fanart_settings.jpg")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def _explore(curator):
    xbmcplugin.setPluginCategory(HANDLE, _loc(32411, "Find Something to Watch"))
    _add_action(_loc(32430, "Quick Pick"), "quick", _loc(32433, "Choose a mood and quickly create a personalised list."), icon_name="menu_quick.png", fanart_name="fanart_explore.jpg")
    _add_action(_loc(32431, "Saved Prompts"), "templates", _loc(32434, "Reuse and manage prompts you have saved for later."), icon_name="menu_templates.png", fanart_name="fanart_explore.jpg")
    _add_folder("All Picks", "all", "Browse recommendations from all your current lists.", icon_name="menu_all.png", fanart_name="fanart_explore.jpg")
    _add_folder("Latest Picks", "fresh", "See recommendations from the list refreshed most recently.", icon_name="menu_fresh.png", fanart_name="fanart_explore.jpg")
    _add_folder("Surprise Me", "random", "Browse a different selection from your current lists.", icon_name="menu_random.png", fanart_name="fanart_explore.jpg", limit="10")
    _add_action(_loc(32432, "Hidden Movies"), "hidden", _loc(32435, "Review movies you have asked curatr not to recommend."), icon_name="menu_hidden.png", fanart_name="fanart_explore.jpg")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def _taste_activity(curator):
    xbmcplugin.setPluginCategory(HANDLE, _loc(32412, "Preferences & Activity"))
    _add_action(_loc(32440, "Refresh Preferences from Trakt"), "sync", _loc(32446, "Update the ratings and watch history curatr uses."), icon_name="menu_sync.png", fanart_name="fanart_taste.jpg")
    _add_action(_loc(32441, "View My Preferences"), "taste", _loc(32447, "See the information curatr uses when choosing recommendations."), icon_name="menu_taste.png", fanart_name="fanart_taste.jpg")
    _add_action(_loc(32442, "AI Usage"), "usage", _loc(32448, "See request and token totals reported by your AI provider."), icon_name="menu_usage.png", fanart_name="fanart_info.jpg")
    _add_action(_loc(32443, "Recent Activity"), "activity", _loc(32449, "See recent list refreshes, Trakt updates and errors."), icon_name="menu_activity.png", fanart_name="fanart_info.jpg")
    _add_action(_loc(32444, "Check Trakt Connection"), "status", _loc(32450, "Check the Trakt account or public profile currently in use."), icon_name="menu_trakt.png", fanart_name="fanart_trakt.jpg")
    _add_action(_loc(32445, "Connect / Reconnect Trakt"), "auth", _loc(32451, "Link curatr to Trakt when you want it to write Trakt lists directly."), icon_name="menu_trakt.png", fanart_name="fanart_trakt.jpg")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def _lists(curator):
    xbmcplugin.setPluginCategory(HANDLE, _loc(32410, "My Lists"))
    records = _managed_records(curator)
    if not records:
        _add_action("Create your first list", "create", "You do not have any saved lists yet.")
    else:
        for record in records:
            local_id = curator._record_key(record)
            if not local_id:
                continue
            name = str(record.get("name") or "curatr list")
            plot = str(record.get("description") or "").strip()
            if not plot:
                plot = "A personalised curatr list. Add a description in List settings."
            refresh_url = _url(action="refresh_list", list_id=local_id)
            edit_url = _url(action="edit_list", list_id=local_id)
            artwork_url = _url(action="artwork_list", list_id=local_id)
            sync_url = _url(action="sync_list", list_id=local_id)
            delete_url = _url(action="delete_list", list_id=local_id)
            context = [
                ("Refresh this list", "RunPlugin(%s)" % refresh_url),
                ("List settings", "RunPlugin(%s)" % edit_url),
                ("Artwork", "RunPlugin(%s)" % artwork_url),
                ("Delete this list", "RunPlugin(%s)" % delete_url),
            ]
            if record.get("sync_to_trakt"):
                context.append(("Update Trakt copy now", "RunPlugin(%s)" % sync_url))
            context.append(("Refresh all lists", "RunScript(%s,update)" % ADDON_ID))
            _add_folder(
                name, "list", plot=plot, context_items=context,
                icon_name="menu_list.png", fanart_name="fanart_my_lists.jpg",
                art=_record_art(curator, record),
                list_id=local_id, name=name
            )
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def _fresh_movie(curator, trakt_id):
    # Prefer the locally saved recommendation metadata so opening a widget or
    # info panel does not create another Trakt request.
    wanted = str(trakt_id)
    for record in _managed_records(curator):
        for movie in record.get("movies") or []:
            if not isinstance(movie, dict):
                continue
            if str((movie.get("ids") or {}).get("trakt") or "") == wanted:
                return movie
    try:
        if curator.trakt.client_id:
            return curator.trakt.movie_summary(trakt_id)
    except Exception:
        pass
    return {}


def _movie_rows_for_list(curator, list_id):
    record = curator._managed_record_by_id(list_id)
    if not record:
        return []
    movies = [m for m in (record.get("movies") or []) if isinstance(m, dict)]
    if movies:
        return [({}, movie) for movie in movies]

    # Migration fallback for a v0.6 record that has never yet been refreshed
    # under local-first mode. If OAuth still works, import its current contents
    # once; otherwise the user can simply refresh the list to build it locally.
    remote_id = record.get("trakt_id")
    if remote_id and curator._has_oauth():
        try:
            rows = curator.trakt.list_items(remote_id, limit=100, extended=True)
            result = []
            local_movies = []
            for row in rows if isinstance(rows, list) else []:
                movie = row.get("movie", {}) if isinstance(row, dict) else {}
                ids = movie.get("ids") or {} if isinstance(movie, dict) else {}
                if isinstance(movie, dict) and isinstance(ids, dict) and ids.get("trakt"):
                    result.append((row, movie))
                    local_movies.append(movie)
            if local_movies:
                updated = dict(record)
                updated["movies"] = local_movies
                updated["last_result_count"] = len(local_movies)
                curator._store_managed_record(updated, record)
                curator._save_state()
            return result
        except Exception as exc:
            xbmc.log("curatr legacy remote-list import skipped: %s" % exc, xbmc.LOGWARNING)
    return []


def _movie_rows_all(curator):
    seen = set()
    combined = []
    for record in _managed_records(curator):
        list_name = str(record.get("name") or "curatr list")
        list_id = curator._record_key(record)
        try:
            rows = _movie_rows_for_list(curator, list_id)
        except Exception as exc:
            xbmc.log("curatr widget skipped list %s: %s" % (list_name, exc), xbmc.LOGWARNING)
            continue
        for row, movie in rows:
            if curator.is_movie_hidden(movie):
                continue
            trakt_id = _safe_int((movie.get("ids") or {}).get("trakt"), 0)
            marker = trakt_id or (str(movie.get("title") or "").casefold(), _safe_int(movie.get("year"), 0))
            if not marker or marker in seen:
                continue
            seen.add(marker)
            combined.append((row, movie, list_name, list_id))
    return combined


def _movie_rows_fresh(curator):
    records = _managed_records(curator)
    if not records:
        return []
    record = records[0]
    list_id = curator._record_key(record)
    name = str(record.get("name") or "Latest List")
    return [(row, movie, name, list_id) for row, movie in _movie_rows_for_list(curator, list_id) if not curator.is_movie_hidden(movie)]


def _movie_rows_random(curator, limit=10):
    rows = list(_movie_rows_all(curator))
    if not rows:
        return []
    # Daily seed avoids widgets jumping around on every Kodi container refresh.
    seed = "%s:%s" % (time.strftime("%Y-%m-%d", time.localtime()), ADDON.getAddonInfo("id"))
    rng = random.Random(seed)
    rng.shuffle(rows)
    return rows[:max(1, min(50, _safe_int(limit, 10)))]


def _safe_tag_call(tag, method, *args):
    try:
        function = getattr(tag, method, None)
        if function:
            function(*args)
            return True
    except Exception as exc:
        xbmc.log("curatr metadata %s skipped: %s" % (method, exc), xbmc.LOGDEBUG)
    return False


def _set_movie_info(item, movie):
    """Populate a movie ListItem without letting one metadata field break a widget.

    Kodi 21 supports InfoTagVideo, but this deliberately uses best-effort setters
    and a legacy setInfo fallback so unusual platform/skin builds still render.
    """
    if not isinstance(movie, dict):
        movie = {}
    title = str(movie.get("title") or "Unknown movie")
    year = _safe_int(movie.get("year"), 0)
    overview = str(movie.get("overview") or movie.get("ai_reason") or "")
    tagline = str(movie.get("tagline") or "")
    genres = movie.get("genres") or []
    if not isinstance(genres, list):
        genres = [genres] if genres else []
    runtime = _safe_int(movie.get("runtime"), 0)
    released = str(movie.get("released") or "")
    certification = str(movie.get("certification") or "")
    rating = movie.get("rating")
    try:
        rating = float(rating)
    except (TypeError, ValueError):
        rating = 0.0
    votes = _safe_int(movie.get("votes"), 0)
    ids = movie.get("ids") or {}
    if not isinstance(ids, dict):
        ids = {}

    # Kodi still supports setInfo in 21.x. Use it as a compatibility baseline,
    # then enrich with the modern InfoTagVideo API where available.
    try:
        legacy = {"title": title, "mediatype": "movie"}
        if year:
            legacy["year"] = year
        if overview:
            legacy["plot"] = overview
        if tagline:
            legacy["tagline"] = tagline
        if genres:
            legacy["genre"] = [str(value) for value in genres if value]
        if runtime:
            legacy["duration"] = runtime * 60
        if released:
            legacy["premiered"] = released
        if certification:
            legacy["mpaa"] = certification
        if rating:
            legacy["rating"] = rating
        if votes:
            legacy["votes"] = votes
        item.setInfo("video", legacy)
    except Exception as exc:
        xbmc.log("curatr legacy metadata fallback skipped: %s" % exc, xbmc.LOGDEBUG)

    try:
        tag = item.getVideoInfoTag()
    except Exception as exc:
        xbmc.log("curatr InfoTagVideo unavailable: %s" % exc, xbmc.LOGDEBUG)
        return

    _safe_tag_call(tag, "setTitle", title)
    if year:
        _safe_tag_call(tag, "setYear", year)
    if overview:
        _safe_tag_call(tag, "setPlot", overview)
    if tagline:
        _safe_tag_call(tag, "setTagLine", tagline)
    if genres:
        _safe_tag_call(tag, "setGenres", [str(value) for value in genres if value])
    if runtime:
        _safe_tag_call(tag, "setDuration", runtime * 60)
    if released:
        _safe_tag_call(tag, "setPremiered", released)
    if certification:
        _safe_tag_call(tag, "setMpaa", certification)
    if rating:
        _safe_tag_call(tag, "setRating", rating, votes, "trakt", True)

    unique_ids = {}
    for key in ("trakt", "tmdb", "imdb"):
        value = ids.get(key)
        if value not in (None, ""):
            unique_ids[key] = str(value)
    if unique_ids:
        default_id = "tmdb" if "tmdb" in unique_ids else ("imdb" if "imdb" in unique_ids else "trakt")
        _safe_tag_call(tag, "setUniqueIDs", unique_ids, default_id)
    _safe_tag_call(tag, "setMediaType", "movie")

def _redlight_play_url(movie):
    ids = movie.get("ids") or {}
    tmdb_id = ids.get("tmdb")
    if not tmdb_id:
        return ""
    try:
        installed = bool(xbmc.getCondVisibility("System.HasAddon(plugin.video.redlight)"))
    except Exception:
        installed = False
    if not installed:
        return ""
    return "plugin://plugin.video.redlight/?" + urlencode({
        "mode": "playback.media",
        "media_type": "movie",
        "tmdb_id": str(tmdb_id),
        "media": "media",
    })


def _add_movie(movie, artwork, list_name="", list_id=""):
    if not isinstance(movie, dict):
        return False
    title = str(movie.get("title") or "Unknown movie")
    year = _safe_int(movie.get("year"), 0)
    label = "%s (%d)" % (title, year) if year else title
    item = xbmcgui.ListItem(label=label, offscreen=True)
    _set_movie_info(item, movie)
    if artwork:
        try:
            item.setArt(artwork)
        except Exception as exc:
            xbmc.log("curatr artwork assignment skipped: %s" % exc, xbmc.LOGDEBUG)
    ids = movie.get("ids") or {}
    if not isinstance(ids, dict):
        ids = {}
    trakt_id = ids.get("trakt") or ""
    info_url = _url(action="info", trakt_id=str(trakt_id))
    why_url = _url(action="why", trakt_id=str(trakt_id), list_id=str(list_id), title=title, year=str(year or ""))
    hide_url = _url(action="hide", trakt_id=str(trakt_id), title=title, year=str(year or ""))
    if list_name:
        try:
            item.setProperty("CuratrList", list_name)
        except Exception:
            pass

    play_url = _redlight_play_url(movie)
    try:
        context = [
            ("Why this pick?", "RunPlugin(%s)" % why_url),
            ("Never recommend this", "RunPlugin(%s)" % hide_url),
        ]
        if list_id:
            refresh_url = _url(action="refresh_list", list_id=str(list_id))
            context.append(("Refresh this list", "RunPlugin(%s)" % refresh_url))
        context.extend([
            ("Movie details", "RunPlugin(%s)" % info_url),
            ("curatr settings", "RunScript(%s,settings)" % ADDON_ID),
        ])
        item.addContextMenuItems(context)
    except Exception as exc:
        xbmc.log("curatr context menu skipped: %s" % exc, xbmc.LOGDEBUG)

    if play_url:
        try:
            item.setProperty("IsPlayable", "true")
        except Exception:
            pass
        target_url = play_url
        is_folder = False
    else:
        try:
            item.setProperty("IsPlayable", "false")
        except Exception:
            pass
        target_url = info_url
        is_folder = True

    return bool(xbmcplugin.addDirectoryItem(HANDLE, target_url, item, isFolder=is_folder))


def _render_movies(curator, rows, category):
    xbmcplugin.setPluginCategory(HANDLE, category)
    xbmcplugin.setContent(HANDLE, "movies")
    rows = list(rows or [])
    art_cache = ArtworkCache(ADDON)

    # Artwork or one malformed movie must never make the entire skin widget fail.
    try:
        art_cache.prefetch_movies([entry[1] for entry in rows if len(entry) > 1 and isinstance(entry[1], dict)])
    except Exception as exc:
        xbmc.log("curatr artwork prefetch skipped: %s" % exc, xbmc.LOGWARNING)

    added = 0
    skipped = 0
    for entry in rows:
        try:
            if len(entry) < 2 or not isinstance(entry[1], dict):
                skipped += 1
                continue
            movie = entry[1]
            list_name = entry[2] if len(entry) > 2 else category
            list_id = entry[3] if len(entry) > 3 else ""
            try:
                artwork = art_cache.art_for_movie(movie)
            except Exception as exc:
                artwork = {}
                xbmc.log("curatr artwork skipped for %s: %s" % (movie.get("title"), exc), xbmc.LOGDEBUG)
            if _add_movie(movie, artwork, list_name=list_name, list_id=list_id):
                added += 1
            else:
                skipped += 1
        except Exception as exc:
            skipped += 1
            xbmc.log("curatr movie row skipped: %s" % exc, xbmc.LOGWARNING)

    if not rows:
        _add_action("No picks are currently in this view", "update", "Refresh your lists with AI, then refresh this folder.")
    elif not added:
        _add_action("Picks could not be displayed", "update", "The local list exists. Refresh it, or check Recent Activity for details.")

    # Sort constants vary slightly between Kodi builds/platforms.  In
    # particular, some Kodi 21 builds expose SORT_METHOD_VIDEO_YEAR but not
    # the older SORT_METHOD_YEAR alias.  Resolve each name defensively before
    # calling addSortMethod so a missing constant can never abort the listing.
    sort_names = (
        ("SORT_METHOD_UNSORTED",),
        ("SORT_METHOD_TITLE_IGNORE_THE", "SORT_METHOD_TITLE"),
        ("SORT_METHOD_VIDEO_YEAR", "SORT_METHOD_YEAR"),
        ("SORT_METHOD_VIDEO_RATING",),
    )
    seen_sorts = set()
    for names in sort_names:
        method = None
        for name in names:
            method = getattr(xbmcplugin, name, None)
            if method is not None:
                break
        if method is None or method in seen_sorts:
            continue
        seen_sorts.add(method)
        try:
            xbmcplugin.addSortMethod(HANDLE, method)
        except Exception as exc:
            xbmc.log("curatr sort method skipped: %s" % exc, xbmc.LOGDEBUG)
    if skipped:
        xbmc.log("curatr rendered %d movie(s), skipped %d" % (added, skipped), xbmc.LOGWARNING)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)

def _single_list(curator, params):
    list_id = params.get("list_id") or params.get("trakt_id")
    if not list_id:
        raise RuntimeError("No curatr list ID was supplied.")
    name = params.get("name") or "curatr Recommendations"
    rows = [(row, movie, name, list_id) for row, movie in _movie_rows_for_list(curator, list_id) if not curator.is_movie_hidden(movie)]
    _render_movies(curator, rows, name)


def _all(curator):
    _render_movies(curator, _movie_rows_all(curator), "All Picks")


def _fresh(curator):
    _render_movies(curator, _movie_rows_fresh(curator), "Latest Picks")


def _random_picks(curator, params):
    _render_movies(curator, _movie_rows_random(curator, params.get("limit") or 10), "Surprise Me")


def _run_command(curator, command):
    actions = {
        "auth": curator.authenticate_trakt,
        "create": curator.create_list_interactive,
        "quick": curator.quick_pick_interactive,
        "templates": curator.prompt_templates_interactive,
        "hidden": curator.manage_hidden_interactive,
        "backup": curator.backup_menu_interactive,
        "update": curator.update_all,
        "manage": curator.manage_lists_interactive,
        "sync": curator.sync_profile,
        "taste": curator.view_taste_fingerprint,
        "usage": curator.show_ai_usage,
        "activity": curator.show_activity,
        "settings": curator.open_settings,
        "status": lambda: curator.refresh_trakt_status(silent=False),
    }
    function = actions.get(command)
    if not function:
        raise RuntimeError("Unknown addon action: %s" % command)
    before = list_signature(curator.state)
    function()
    refresh_if_changed(before, curator.state)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def _info(curator, params):
    trakt_id = params.get("trakt_id")
    if not trakt_id:
        return
    movie = _fresh_movie(curator, trakt_id)
    if not movie:
        raise RuntimeError("Could not load movie details from Trakt.")
    title = str(movie.get("title") or "Movie")
    year = movie.get("year")
    heading = "%s (%s)" % (title, year) if year else title
    pieces = []
    tagline = str(movie.get("tagline") or "").strip()
    overview = str(movie.get("overview") or movie.get("ai_reason") or "").strip()
    if tagline:
        pieces.append(tagline)
    if overview:
        pieces.append(overview)
    ids = movie.get("ids") or {}
    pieces.append(
        "Trakt: %s\nTMDb: %s\nIMDb: %s"
        % (ids.get("trakt") or "—", ids.get("tmdb") or "—", ids.get("imdb") or "—")
    )
    xbmcgui.Dialog().textviewer(heading, "\n\n".join(pieces))
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def main():
    params = _params()
    action = params.get("action") or "root"
    curator = None
    try:
        curator = Curator(ADDON)
        before = list_signature(curator.state)
        if action == "root":
            curator.maybe_show_first_run()
            _root(curator)
        elif action == "my":
            _my(curator)
        elif action == "explore":
            _explore(curator)
        elif action == "taste_activity":
            _taste_activity(curator)
        elif action == "lists":
            _lists(curator)
        elif action == "list":
            _single_list(curator, params)
        elif action == "all":
            _all(curator)
        elif action == "fresh":
            _fresh(curator)
        elif action == "random":
            _random_picks(curator, params)
        elif action == "run":
            _run_command(curator, params.get("command") or "")
        elif action == "refresh_list":
            list_id = params.get("list_id") or params.get("trakt_id") or ""
            curator.refresh_list(list_id, silent=False)
            refresh_if_changed(before, curator.state)
            xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        elif action == "edit_list":
            list_id = params.get("list_id") or params.get("trakt_id") or ""
            curator.edit_list_interactive(list_id)
            refresh_if_changed(before, curator.state)
            xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        elif action == "artwork_list":
            list_id = params.get("list_id") or params.get("trakt_id") or ""
            curator.list_artwork_interactive(list_id)
            refresh_if_changed(before, curator.state)
            xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        elif action == "sync_list":
            list_id = params.get("list_id") or params.get("trakt_id") or ""
            curator.sync_list_to_trakt(list_id, silent=False)
            refresh_if_changed(before, curator.state)
            xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        elif action == "delete_list":
            list_id = params.get("list_id") or params.get("trakt_id") or ""
            curator.delete_list_interactive(list_id)
            refresh_if_changed(before, curator.state)
            xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        elif action == "why":
            curator.why_recommended(params.get("list_id") or "", params.get("trakt_id") or "", params.get("title") or "", _safe_int(params.get("year"), 0))
            xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        elif action == "hide":
            curator.hide_movie(params.get("trakt_id") or "", params.get("title") or "", _safe_int(params.get("year"), 0), confirm=True)
            refresh_if_changed(before, curator.state)
            xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        elif action == "info":
            _info(curator, params)
        else:
            raise RuntimeError("Unknown plugin route: %s" % action)
    except (TraktError, RuntimeError) as exc:
        xbmc.log("curatr plugin error: %s" % exc, xbmc.LOGERROR)
        if curator is not None:
            try:
                curator.report_error("Plugin request failed", detail=str(exc))
            except Exception:
                pass
        xbmcgui.Dialog().ok(NAME, str(exc))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
    except Exception as exc:
        xbmc.log("curatr plugin unexpected error: %s" % exc, xbmc.LOGERROR)
        if curator is not None:
            try:
                curator.report_error("Recommendations view hit an unexpected error", detail=str(exc))
            except Exception:
                pass
        xbmcgui.Dialog().ok(NAME, "%s: %s" % (_loc(32490, "Error"), exc))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)


if __name__ == "__main__":
    main()
