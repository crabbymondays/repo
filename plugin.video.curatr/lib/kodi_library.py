"""Read compact, preference-relevant metadata from Kodi's local movie library."""

import json

import xbmc


class KodiLibraryError(RuntimeError):
    pass


class KodiLibraryReader:
    """Read-only JSON-RPC adapter; it never changes Kodi library records."""

    def __init__(self, limit=1000):
        self.limit = max(50, min(2000, int(limit or 1000)))

    @staticmethod
    def _text_list(values, maximum=20):
        output = []
        seen = set()
        for value in values or []:
            text = str(value or "").strip()
            marker = text.casefold()
            if text and marker not in seen:
                seen.add(marker)
                output.append(text)
            if len(output) >= maximum:
                break
        return output

    @staticmethod
    def _safe_int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _normalise_ids(row):
        unique = row.get("uniqueid") or {}
        unique = unique if isinstance(unique, dict) else {}
        imdb = unique.get("imdb") or row.get("imdbnumber")
        tmdb = unique.get("tmdb") or unique.get("themoviedb")
        result = {}
        if imdb:
            result["imdb"] = str(imdb).strip()
        if tmdb not in (None, ""):
            result["tmdb"] = str(tmdb).strip()
        return result

    def movies(self):
        request = {
            "jsonrpc": "2.0",
            "id": "curatr-kodi-library",
            "method": "VideoLibrary.GetMovies",
            "params": {
                "properties": [
                    "title", "year", "userrating", "playcount", "lastplayed",
                    "genre", "director", "uniqueid", "imdbnumber",
                ],
                "limits": {"start": 0, "end": self.limit},
                "sort": {"method": "dateadded", "order": "descending"},
            },
        }
        try:
            raw = xbmc.executeJSONRPC(json.dumps(request, separators=(",", ":")))
            response = json.loads(raw or "{}")
        except Exception as exc:
            raise KodiLibraryError("Kodi could not read its movie library: %s" % exc)
        if not isinstance(response, dict) or response.get("error"):
            detail = (response.get("error") or {}).get("message") if isinstance(response, dict) else ""
            detail = str(detail or "").strip()
            raise KodiLibraryError("Kodi could not read its movie library%s." % ((": " + detail) if detail else ""))

        result = response.get("result") or {}
        rows = (result.get("movies") or []) if isinstance(result, dict) else []
        movies = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("label") or "").strip()
            year = self._safe_int(row.get("year"), 0)
            if not title:
                continue
            userrating = self._safe_int(row.get("userrating"), 0)
            movies.append({
                "title": title,
                "year": year,
                "kodi_id": self._safe_int(row.get("movieid"), 0),
                "rating": max(1, min(10, userrating)) if userrating > 0 else None,
                "playcount": max(0, self._safe_int(row.get("playcount"), 0)),
                "last_watched_at": str(row.get("lastplayed") or "").strip(),
                "genres": self._text_list(row.get("genre"), 12),
                "directors": self._text_list(row.get("director"), 12),
                "ids": self._normalise_ids(row),
                "source": "kodi",
            })

        limits = (result.get("limits") or {}) if isinstance(result, dict) else {}
        total = self._safe_int(limits.get("total"), len(movies)) if isinstance(limits, dict) else len(movies)
        return {"movies": movies, "total": max(total, len(movies))}
