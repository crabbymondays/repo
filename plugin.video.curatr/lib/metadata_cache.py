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
        return {
            "cast": cast, "directors": directors, "writers": writers, "studios": studios,
            "countries": countries, "trailer": trailer, "runtime": runtime,
            "certification": cls._certification(details, media_type),
            "tagline": str(details.get("tagline") or ""), "released": str(date or ""),
            "status": str(details.get("status") or ""),
            "original_title": str(details.get("original_name") or details.get("original_title") or ""),
        }

    def enrich(self, movies, tmdb, workers=4):
        if not tmdb or not getattr(tmdb, "api_key", ""):
            return movies
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
                movie.update(cached)
            else:
                wanted[key] = (media_type, int(tmdb_id))
        if not wanted:
            return movies

        fetched = {}
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
                    movie.update(metadata)
        return movies
