"""Persistent, bounded TMDB metadata used to populate Kodi list items."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import xbmcvfs


CACHE_VERSION = 1
TTL_SECONDS = 30 * 24 * 60 * 60
MAX_ITEMS = 1200


class MetadataCache:
    def __init__(self, addon):
        profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
        if not xbmcvfs.exists(profile):
            xbmcvfs.mkdirs(profile)
        self.path = os.path.join(profile, "tmdb_metadata_cache.json")
        self._data = None

    def _load(self):
        if self._data is not None:
            return self._data
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if raw.get("version") != CACHE_VERSION or not isinstance(raw.get("items"), dict):
                raise ValueError("unsupported cache")
            self._data = raw
        except (OSError, ValueError, TypeError, AttributeError):
            self._data = {"version": CACHE_VERSION, "items": {}}
        return self._data

    @staticmethod
    def _key(media_type, tmdb_id):
        return "%s:%s" % ("show" if media_type == "show" else "movie", int(tmdb_id))

    def get(self, media_type, tmdb_id):
        try:
            row = self._load()["items"].get(self._key(media_type, tmdb_id))
        except (TypeError, ValueError):
            return None
        if not isinstance(row, dict) or time.time() - int(row.get("cached_at") or 0) > TTL_SECONDS:
            return None
        value = row.get("metadata")
        return dict(value) if isinstance(value, dict) else None

    def _save(self):
        items = self._load()["items"]
        if len(items) > MAX_ITEMS:
            ordered = sorted(items.items(), key=lambda item: int((item[1] or {}).get("cached_at") or 0), reverse=True)
            self._data["items"] = dict(ordered[:MAX_ITEMS])
        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(self._data, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp, self.path)

    @staticmethod
    def _certification(details, media_type):
        if media_type == "show":
            rows = (details.get("content_ratings") or {}).get("results") or []
            chosen = next((row for row in rows if row.get("iso_3166_1") == "GB"), None)
            chosen = chosen or next((row for row in rows if row.get("iso_3166_1") == "US"), None)
            return str((chosen or {}).get("rating") or "")
        rows = (details.get("release_dates") or {}).get("results") or []
        chosen = next((row for row in rows if row.get("iso_3166_1") == "GB"), None)
        chosen = chosen or next((row for row in rows if row.get("iso_3166_1") == "US"), None)
        releases = (chosen or {}).get("release_dates") or []
        return next((str(row.get("certification") or "") for row in releases if row.get("certification")), "")

    @classmethod
    def _compact(cls, tmdb, details, media_type):
        credits = details.get("credits") or {}
        cast = []
        for order, person in enumerate(credits.get("cast") or []):
            name = str(person.get("name") or "").strip()
            if not name:
                continue
            cast.append({
                "name": name,
                "role": str(person.get("character") or ""),
                "order": int(person.get("order") if person.get("order") is not None else order),
                "thumbnail": tmdb.image_url(person.get("profile_path"), "h632"),
            })
            if len(cast) >= 60:
                break
        crew = credits.get("crew") or []
        directors = list(dict.fromkeys(str(row.get("name")) for row in crew if str(row.get("job") or "").lower() == "director" and row.get("name")))
        writing_jobs = {"writer", "screenplay", "teleplay", "story", "characters"}
        writers = list(dict.fromkeys(str(row.get("name")) for row in crew if str(row.get("job") or "").lower() in writing_jobs and row.get("name")))
        organisations = details.get("networks") if media_type == "show" else details.get("production_companies")
        studios = list(dict.fromkeys(str(row.get("name")) for row in organisations or [] if row.get("name")))
        countries = [str(row.get("name")) for row in details.get("production_countries") or [] if row.get("name")]
        videos = (details.get("videos") or {}).get("results") or []
        youtube = [row for row in videos if str(row.get("site") or "").lower() == "youtube" and row.get("key")]
        trailers = [row for row in youtube if str(row.get("type") or "").lower() == "trailer"] or youtube
        trailer = "plugin://plugin.video.youtube/play/?video_id=%s" % trailers[0]["key"] if trailers else ""
        runtime = details.get("episode_run_time") if media_type == "show" else [details.get("runtime")]
        runtime = next((int(value) for value in runtime or [] if value), 0)
        date = details.get("first_air_date") if media_type == "show" else details.get("release_date")
        external = details.get("external_ids") or {}
        ids = {}
        for source, field in (("imdb", "imdb_id"), ("tvdb", "tvdb_id")):
            value = external.get(field)
            if value not in (None, ""):
                ids[source] = value
        ratings = {}
        images = {}
        for kind, field, size in (("fanart", "backdrop_path", "w1280"), ("poster", "poster_path", "w500")):
            url = tmdb.image_url(details.get(field), size)
            if url:
                images[kind] = {"full": url}
        try:
            tmdb_rating = float(details.get("vote_average") or 0)
            tmdb_votes = int(details.get("vote_count") or 0)
        except (TypeError, ValueError):
            tmdb_rating, tmdb_votes = 0.0, 0
        if tmdb_rating > 0:
            ratings["tmdb"] = {
                "rating": round(tmdb_rating, 2), "votes": max(0, tmdb_votes),
                "percent": max(0, min(100, int(round(tmdb_rating * 10)))),
            }
        return {
            "cast": cast, "directors": directors, "writers": writers, "studios": studios,
            "countries": countries, "trailer": trailer, "runtime": runtime,
            "certification": cls._certification(details, media_type),
            "tagline": str(details.get("tagline") or ""), "released": str(date or ""),
            "status": str(details.get("status") or ""),
            "original_title": str(details.get("original_name") or details.get("original_title") or ""),
            "ids": ids, "ratings": ratings, "images": images,
        }

    @staticmethod
    def _apply(movie, metadata):
        for key, value in metadata.items():
            if key in ("ids", "ratings") and isinstance(value, dict):
                merged = dict(movie.get(key) or {})
                merged.update(value)
                movie[key] = merged
            elif key == "images" and isinstance(value, dict):
                merged = dict(value)
                existing = movie.get("images")
                if isinstance(existing, dict):
                    merged.update({kind: source for kind, source in existing.items() if source})
                movie[key] = merged
            elif key != "mdblist_ratings_checked":
                movie[key] = value

    def _enrich_external_ratings(self, movies, mdblist):
        if not mdblist or not getattr(mdblist, "api_key", ""):
            return False
        pending = {"movie": {}, "show": {}}
        for movie in movies or []:
            if not isinstance(movie, dict):
                continue
            media_type = "show" if movie.get("media_type") == "show" else "movie"
            tmdb_id = (movie.get("ids") or {}).get("tmdb")
            try:
                key = self._key(media_type, tmdb_id)
            except (TypeError, ValueError):
                continue
            cached = self.get(media_type, tmdb_id) or {}
            if not cached.get("mdblist_ratings_checked"):
                pending[media_type][int(tmdb_id)] = key
        changed = False
        for media_type, items in pending.items():
            if not items:
                continue
            fetched = mdblist.ratings_for_ids(media_type, items)
            now = int(time.time())
            for tmdb_id, key in items.items():
                metadata = self.get(media_type, tmdb_id) or {}
                ratings = dict(metadata.get("ratings") or {})
                ratings.update(fetched.get(tmdb_id) or {})
                metadata["ratings"] = ratings
                metadata["mdblist_ratings_checked"] = True
                self._load()["items"][key] = {"cached_at": now, "metadata": metadata}
                changed = True
        if changed:
            self._save()
            for movie in movies or []:
                if not isinstance(movie, dict):
                    continue
                media_type = "show" if movie.get("media_type") == "show" else "movie"
                tmdb_id = (movie.get("ids") or {}).get("tmdb")
                cached = self.get(media_type, tmdb_id) if tmdb_id not in (None, "") else None
                if cached:
                    self._apply(movie, cached)
        return changed

    def enrich(self, movies, tmdb, mdblist=None, workers=4, include_artwork=False):
        tmdb_available = bool(tmdb and getattr(tmdb, "api_key", ""))
        wanted = {}
        for movie in movies or []:
            if not isinstance(movie, dict):
                continue
            media_type = "show" if movie.get("media_type") == "show" else "movie"
            tmdb_id = (movie.get("ids") or {}).get("tmdb")
            try:
                key = self._key(media_type, tmdb_id)
            except (TypeError, ValueError):
                continue
            cached = self.get(media_type, tmdb_id)
            if cached:
                self._apply(movie, cached)
            if tmdb_available and (not cached or (include_artwork and "images" not in cached)):
                wanted[key] = (media_type, int(tmdb_id))

        fetched = {}
        if wanted:
            with ThreadPoolExecutor(max_workers=max(1, min(4, int(workers)))) as executor:
                futures = {
                    executor.submit(tmdb.list_item_details, tmdb_id, media_type): (key, media_type)
                    for key, (media_type, tmdb_id) in wanted.items()
                }
                for future in as_completed(futures):
                    key, media_type = futures[future]
                    try:
                        fetched[key] = self._compact(tmdb, future.result(), media_type)
                    except Exception:
                        continue
        if fetched:
            now = int(time.time())
            for key, metadata in fetched.items():
                self._load()["items"][key] = {"cached_at": now, "metadata": metadata}
            try:
                self._save()
            except OSError:
                pass
            for movie in movies or []:
                media_type = "show" if movie.get("media_type") == "show" else "movie"
                try:
                    metadata = fetched.get(self._key(media_type, (movie.get("ids") or {}).get("tmdb")))
                except (TypeError, ValueError):
                    metadata = None
                if metadata:
                    self._apply(movie, metadata)
        try:
            self._enrich_external_ratings(movies, mdblist)
        except Exception:
            pass
        return movies
