"""Episode calendars with stable identities and shared watch-state snapshots."""

import hashlib
import json
from datetime import datetime, time, timedelta, timezone

from .dynamic_lists import SourceCache
from .list_request import date_window


def _genre(value):
    value = str(value).replace("-", " ").casefold()
    return {"sci fi": "science fiction"}.get(value, value)


def show_tokens(show):
    ids = show.get("ids") or show.get("uniqueid") or {}
    tokens = {(key, str(ids[key]).casefold()) for key in ("trakt", "tmdb", "imdb", "tvdb") if ids.get(key) not in (None, "", 0, "0")}
    title = str(show.get("title") or show.get("showtitle") or "").strip().casefold()
    if title:
        tokens.add(("title", title, str(show.get("year") or show.get("showyear") or "")))
    return tokens


def _snapshot(curator, key, fetch):
    key = hashlib.sha256(("episodes:" + key).encode("utf-8")).hexdigest()
    rows, stale = SourceCache(curator.profile_dir, max_items=20000).get(key, fetch)
    if stale:
        raise RuntimeError("Episode data is unavailable. Try again shortly; the saved list has been kept.")
    return rows


def _kodi_rows(curator, method, key, properties):
    result = curator._kodi_json_rpc(method, {"properties": properties, "limits": {"start": 0, "end": 20000}})
    rows = result.get(key) if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Kodi library history could not be read.")
    if int((result.get("limits") or {}).get("total") or 0) > 20000:
        raise RuntimeError("The Kodi library is too large for this episode query.")
    return rows


def kodi_shows(curator):
    return _kodi_rows(curator, "VideoLibrary.GetTVShows", "tvshows", ["title", "year", "uniqueid"])


def watch_history(curator, include_trakt=True):
    cached = getattr(curator, "_episode_watch_history", None)
    if isinstance(cached, dict) and include_trakt:
        return cached
    result = {}
    def put(tokens, season, episode, plays, stamp=""):
        if season is None or episode is None:
            return
        for token in tokens:
            key = (token, int(season), int(episode))
            old = result.get(key, (0, ""))
            result[key] = (max(old[0], int(plays or 0)), max(old[1], str(stamp or "")))
    mode = curator._preference_history_mode()
    if mode in ("kodi", "both"):
        shows = kodi_shows(curator)
        by_id = {row.get("tvshowid"): show_tokens(row) for row in shows if isinstance(row, dict)}
        rows = _kodi_rows(curator, "VideoLibrary.GetEpisodes", "episodes", ["title", "showtitle", "season", "episode", "playcount", "lastplayed", "tvshowid"])
        for row in rows:
            if isinstance(row, dict):
                put(by_id.get(row.get("tvshowid"), set()), row.get("season"), row.get("episode"), row.get("playcount"), row.get("lastplayed"))
    if include_trakt and mode in ("trakt", "both"):
        username = curator._public_username()
        if mode == "trakt" and not curator._has_oauth() and not username:
            raise RuntimeError("Trakt watch history is not connected. Link Trakt, set a public username, or choose Kodi history in Preferences & Activity.")
        if curator._has_oauth() or username:
            identity = str(curator.state.get("trakt_username") or username or "me") + ":" + str((curator.trakt.token_store or {}).get("created_at") or 0)
            rows = _snapshot(curator, "watched:" + identity, lambda: curator.trakt.watched_shows(20000) if curator._has_oauth() else curator.trakt.watched_shows_for_user(username, 20000))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                tokens = show_tokens(row.get("show") or {})
                for season in row.get("seasons") or []:
                    if not isinstance(season, dict):
                        continue
                    for episode in season.get("episodes") or []:
                        if isinstance(episode, dict):
                            put(tokens, season.get("number"), episode.get("number"), episode.get("plays"), episode.get("last_watched_at"))
    if include_trakt:
        curator._episode_watch_history = result
    return result


def watched_state(item, history):
    show = {"ids": item.get("show_ids") or {}, "title": item.get("showtitle"), "year": item.get("showyear")}
    values = [history.get((token, int(item.get("season") or 0), int(item.get("episode") or 0)), (0, "")) for token in show_tokens(show)]
    return max((value[0] for value in values), default=0), max((value[1] for value in values), default="")


def history_matches(plays, stamp, rules, now=None):
    mode = rules.get("history_mode") or ""
    if mode == "plays":
        wanted = int(rules.get("history_plays") or 0)
        return {"exact": plays == wanted, "gt": plays > wanted, "lt": plays < wanted,
                "lte": plays <= wanted, "gte": plays >= wanted}.get(rules.get("history_comparison") or "gte", False)
    if mode == "watched":
        return plays > 0
    if mode == "stale":
        if not plays or not stamp:
            return False
        try:
            watched = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            watched = watched.replace(tzinfo=timezone.utc) if watched.tzinfo is None else watched
            current = now or datetime.now(timezone.utc)
            return current - watched >= timedelta(days=int(rules.get("history_days") or 0))
        except (ValueError, TypeError, OverflowError):
            return False
    return True


def generate(curator, request, filters, count):
    source = request.get("source") or "all"
    if source == "trakt" and not curator._has_oauth():
        raise RuntimeError("Link Trakt to create an episode list from your watched/watchlisted shows, or choose Kodi library shows.")
    library_tokens = set()
    if source == "kodi":
        for show in kodi_shows(curator):
            if isinstance(show, dict):
                library_tokens.update(show_tokens(show))
        if not library_tokens:
            raise RuntimeError("No identifiable TV shows were found in your Kodi library.")
    start, end = date_window(request)
    start_local = datetime.combine(start, time.min).astimezone()
    end_local = datetime.combine(end, time.min).astimezone()
    cursor = start_local.astimezone(timezone.utc).date()
    last = end_local.astimezone(timezone.utc).date() + timedelta(days=1)
    rows = []
    while cursor < last:
        days = min(33, (last - cursor).days)
        identity = (str(curator.state.get("trakt_username") or "me") + ":" + str((curator.trakt.token_store or {}).get("created_at") or 0)) if source == "trakt" else "all"
        key = "calendar:%s:%s:%s:%d" % (source, identity, cursor.isoformat(), days)
        rows.extend(_snapshot(curator, key, lambda cursor=cursor, days=days: curator.trakt.calendar_shows(cursor.isoformat(), days, personal=source == "trakt")))
        cursor += timedelta(days=days)
    history = watch_history(curator) if request.get("hide_watched", True) or filters.get("history_mode") in ("stale", "plays", "watched") else {}
    hidden = {str(row.get("trakt_id")) for row in curator.state.get("hidden_movies", []) if isinstance(row, dict) and row.get("media_type") == "episode"}
    hidden_shows = {str(row.get("trakt_id")) for row in curator.state.get("hidden_movies", []) if isinstance(row, dict) and row.get("media_type") == "show"}
    items, seen = [], set()
    wanted = {_genre(value) for value in filters.get("genre_labels") or []}
    excluded = {_genre(value) for value in filters.get("excluded_genre_labels") or []}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("episode"), dict) or not isinstance(row.get("show"), dict):
            continue
        episode, show = row["episode"], row["show"]
        if source == "kodi" and not library_tokens.intersection(show_tokens(show)):
            continue
        try:
            aired = datetime.fromisoformat(str(row.get("first_aired") or episode.get("first_aired") or "").replace("Z", "+00:00"))
            aired = aired.replace(tzinfo=timezone.utc) if aired.tzinfo is None else aired
            season, number = int(episode["season"]), int(episode["number"])
        except (KeyError, ValueError, TypeError):
            continue
        if not start_local <= aired < end_local or season < 0 or number < 1:
            continue
        ids, show_ids = episode.get("ids") or {}, show.get("ids") or {}
        marker = (json.dumps(show_ids, sort_keys=True), season, number)
        if marker in seen or str(ids.get("trakt")) in hidden or str(show_ids.get("trakt")) in hidden_shows:
            continue
        genres = {_genre(value) for value in show.get("genres") or []}
        if wanted and not (bool(wanted.intersection(genres)) if filters.get("genre_match") == "any" else wanted.issubset(genres)):
            continue
        if excluded.intersection(genres):
            continue
        item = {"title": episode.get("title") or "Episode %d" % number, "year": aired.year,
                "media_type": "episode", "showtitle": show.get("title") or "TV Show", "showyear": show.get("year"),
                "show_ids": show_ids, "ids": ids, "season": season, "episode": number,
                "first_aired": aired.isoformat(), "released": aired.date().isoformat(),
                "overview": episode.get("overview") or "", "runtime": episode.get("runtime") or show.get("runtime") or 0,
                "rating": episode.get("rating") or 0, "votes": episode.get("votes") or 0, "genres": list(show.get("genres") or [])}
        if filters.get("rating_min") and float(item["rating"]) < float(filters["rating_min"]): continue
        if filters.get("runtime_min") and int(item["runtime"]) < int(filters["runtime_min"]): continue
        if filters.get("runtime_max") and int(item["runtime"]) > int(filters["runtime_max"]): continue
        if filters.get("year_min") and aired.year < int(filters["year_min"]): continue
        if filters.get("year_max") and aired.year > int(filters["year_max"]): continue
        if filters.get("language") and show.get("language") != filters["language"]: continue
        if filters.get("country") and str(show.get("country") or "").upper() != filters["country"]: continue
        plays, stamp = watched_state(item, history)
        if (request.get("hide_watched", True) and plays) or not history_matches(plays, stamp, filters):
            continue
        item.update(playcount=plays, last_watched_at=stamp)
        items.append(item)
        seen.add(marker)
    items.sort(key=lambda item: (item["first_aired"], item["showtitle"].casefold(), item["season"], item["episode"]))
    return items[:max(1, min(50, int(count)))], {"start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(), "source": source}


def enrich_artwork(curator, items):
    from .metadata_cache import MetadataCache
    shows = {}
    for item in items:
        if item.get("media_type") == "episode" and (item.get("show_ids") or {}).get("tmdb"):
            tmdb = item["show_ids"]["tmdb"]
            shows.setdefault(tmdb, {"title": item.get("showtitle"), "ids": item["show_ids"], "media_type": "show"})
    if shows:
        MetadataCache(curator.addon).enrich(list(shows.values()), getattr(curator, "tmdb", None), include_artwork=True)
        for item in items:
            show = shows.get((item.get("show_ids") or {}).get("tmdb"))
            if show and show.get("images"):
                item["images"] = show["images"]
