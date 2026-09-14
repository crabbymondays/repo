"""Shared artwork and rating enrichment for Dynamic List previews and directories."""

import xbmc

from .art_cache import ArtworkCache
from .metadata_cache import MetadataCache
from .dynamic_lists import media_type


def native_art(data):
    art = {key: value for key, value in (data.get("art") or {}).items()
           if isinstance(value, str) and value} if isinstance(data.get("art"), dict) else {}
    for source, target in (("thumbnail", "thumb"), ("fanart", "fanart")):
        if data.get(source):
            art.setdefault(target, data[source])
    for target, candidates in (("poster", ("tvshow.poster",)), ("fanart", ("tvshow.fanart",)),
                               ("thumb", ("poster", "icon", "landscape", "fanart")),
                               ("icon", ("thumb",)), ("landscape", ("fanart", "thumb"))):
        if not art.get(target):
            value = next((art.get(key) for key in candidates if art.get(key)), "")
            if value:
                art[target] = value
    return art


def prepare_items(curator, items):
    """Work on copies: a display lookup must not rewrite a source or its cache."""
    rows, movies, targets = [], [], []
    for original in items:
        row = dict(original, data=dict(original["data"]))
        rows.append(row)
        data = row["data"]
        if row["kind"] == "curatr":
            movie = data
        else:
            kind = media_type(data)
            # An episode's TMDB identifier may belong to its series. Do not look
            # it up as a movie or replace the episode's metadata with show data.
            if kind not in ("movie", "tvshow"):
                row["art"] = native_art(data)
                continue
            ids = dict(data.get("uniqueid") or {}) if isinstance(data.get("uniqueid"), dict) else {}
            if data.get("imdbnumber"):
                ids.setdefault("imdb", data["imdbnumber"])
            movie = {"ids": ids, "media_type": "show" if kind == "tvshow" else "movie"}
        movies.append(movie)
        targets.append(row)
    try:
        MetadataCache(curator.addon).enrich(movies, getattr(curator, "tmdb", None),
                                          getattr(curator, "mdblist", None), include_artwork=True)
    except Exception as exc:
        xbmc.log("curatr Dynamic List metadata skipped: %s" % type(exc).__name__, xbmc.LOGDEBUG)
    cache = ArtworkCache(curator.addon)
    try:
        cache.prefetch_movies(movies)
    except Exception as exc:
        xbmc.log("curatr Dynamic List artwork skipped: %s" % type(exc).__name__, xbmc.LOGDEBUG)
    for row, movie in zip(targets, movies):
        try:
            art = cache.art_for_movie(movie)
        except Exception:
            art = {}
        if row["kind"] == "native":
            # Existing source artwork wins, including local and image:// paths.
            art.update(native_art(row["data"]))
            if movie.get("ratings"):
                existing = row["data"].get("ratings")
                ratings = dict(movie["ratings"])
                if isinstance(existing, dict):
                    ratings.update(existing)
                row["data"]["ratings"] = ratings
        else:
            art.update(native_art(movie))
        row["art"] = native_art({"art": art})
    return rows
