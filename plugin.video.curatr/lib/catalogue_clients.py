from urllib.parse import urlparse

import requests


class CatalogueError(Exception):
    pass


class TMDBClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key, region="GB", session=None, user_agent=None):
        self.api_key = str(api_key or "").strip()
        self.region = (str(region or "GB").strip().upper() or "GB")[:2]
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent or "curatr"})

    def _get(self, path, params=None):
        if not self.api_key:
            raise CatalogueError("Add your TMDB API key under Metadata in Settings first.")
        query = dict(params or {})
        headers = {"Accept": "application/json"}
        if self.api_key.startswith("eyJ"):
            headers["Authorization"] = "Bearer " + self.api_key
        else:
            query["api_key"] = self.api_key
        try:
            response = self.session.get(
                self.BASE_URL + path, params=query, headers=headers, timeout=25
            )
        except requests.RequestException as exc:
            raise CatalogueError("Could not contact TMDB: %s" % exc)
        if response.status_code >= 400:
            if response.status_code in (401, 403):
                raise CatalogueError("TMDB rejected the API credential.")
            if response.status_code == 429:
                raise CatalogueError("TMDB request limit reached. curatr will use its normal recommendation path.")
            raise CatalogueError("TMDB request failed (HTTP %s)." % response.status_code)
        try:
            return response.json()
        except ValueError:
            raise CatalogueError("TMDB returned an unreadable response.")

    def test(self):
        data = self._get("/configuration")
        return bool(isinstance(data, dict) and data.get("images"))

    def search_movie(self, title, year=0):
        params = {"query": str(title or ""), "include_adult": "false", "region": self.region}
        if year:
            params["year"] = int(year)
        data = self._get("/search/movie", params)
        rows = data.get("results") or [] if isinstance(data, dict) else []
        if not rows and year:
            params.pop("year", None)
            rows = (self._get("/search/movie", params).get("results") or [])
        return rows[0] if rows and isinstance(rows[0], dict) else None

    def search_people(self, query, limit=20):
        data = self._get("/search/person", {
            "query": str(query or "").strip(), "include_adult": "false",
        })
        rows = data.get("results") or [] if isinstance(data, dict) else []
        return [row for row in rows if isinstance(row, dict) and row.get("name")][:max(1, min(20, int(limit)))]

    @staticmethod
    def image_url(path, size="w1280"):
        value = str(path or "").strip()
        if not value:
            return ""
        return "https://image.tmdb.org/t/p/%s/%s" % (str(size or "w1280").strip("/"), value.lstrip("/"))

    def recommendation_pool(self, reference_movies, limit=60):
        collected = []
        seen = set()
        for reference in (reference_movies or [])[:3]:
            if not isinstance(reference, dict) or not reference.get("title"):
                continue
            match = self.search_movie(reference.get("title"), reference.get("year"))
            movie_id = (match or {}).get("id")
            if not movie_id:
                continue
            data = self._get("/movie/%s/recommendations" % int(movie_id), {
                "region": self.region, "page": 1,
            })
            for row in data.get("results") or []:
                compact = self._compact(row)
                marker = (compact.get("title", "").casefold(), compact.get("year"))
                if not compact.get("title") or marker in seen:
                    continue
                seen.add(marker)
                collected.append(compact)
                if len(collected) >= limit:
                    return collected
        return collected

    @staticmethod
    def _compact(row):
        date = str((row or {}).get("release_date") or "")
        try:
            year = int(date[:4])
        except (TypeError, ValueError):
            year = 0
        return {
            "title": str((row or {}).get("title") or (row or {}).get("original_title") or ""),
            "year": year,
            "tmdb_id": (row or {}).get("id"),
            "rating": (row or {}).get("vote_average"),
            "votes": (row or {}).get("vote_count"),
            "overview": str((row or {}).get("overview") or "")[:160],
        }


class MDBListClient:
    API_URL = "https://api.mdblist.com"
    def __init__(self, api_key="", session=None, user_agent=None):
        self.api_key = str(api_key or "").strip()
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent or "curatr"})

    @staticmethod
    def _normalise_url(value):
        url = str(value or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() not in ("mdblist.com", "www.mdblist.com", "api.mdblist.com"):
            raise CatalogueError("Enter a complete HTTPS MDBList list URL.")
        if parsed.netloc.lower() in ("mdblist.com", "www.mdblist.com") and not parsed.path.rstrip("/").endswith("/json"):
            url = url.rstrip("/") + "/json/"
        return url

    def fetch_list(self, list_url, limit=100):
        url = self._normalise_url(list_url)
        params = {"apikey": self.api_key} if self.api_key else None
        try:
            response = self.session.get(url, params=params, headers={"Accept": "application/json"}, timeout=30)
        except requests.RequestException as exc:
            raise CatalogueError("Could not contact MDBList: %s" % exc)
        if response.status_code >= 400:
            if response.status_code in (401, 403):
                raise CatalogueError("MDBList rejected the API key or the list is private.")
            if response.status_code == 429:
                raise CatalogueError("MDBList request limit reached. curatr will use its normal recommendation path.")
            raise CatalogueError("MDBList request failed (HTTP %s)." % response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise CatalogueError("MDBList returned an unreadable response.")
        rows = []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("movies") or data.get("items") or data.get("results") or []
        output = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("name") or "").strip()
            year = row.get("release_year") or row.get("year") or 0
            try:
                year = int(year)
            except (TypeError, ValueError):
                year = 0
            marker = (title.casefold(), year)
            mediatype = str(row.get("mediatype") or row.get("media_type") or "movie").lower()
            if not title or marker in seen or mediatype not in ("movie", "movies"):
                continue
            seen.add(marker)
            output.append({"title": title, "year": year, "imdb_id": row.get("imdb_id"), "rank": row.get("rank")})
            if len(output) >= limit:
                break
        if not output:
            raise CatalogueError("No movie entries were found in that MDBList list.")
        return output

    def _api_get(self, path):
        if not self.api_key:
            raise CatalogueError("Enter your MDBList API key to use lists from your account.")
        try:
            response = self.session.get(
                self.API_URL + path,
                params={"apikey": self.api_key},
                headers={"Accept": "application/json"}, timeout=30,
            )
        except requests.RequestException as exc:
            raise CatalogueError("Could not contact MDBList: %s" % exc)
        if response.status_code >= 400:
            if response.status_code in (401, 403):
                raise CatalogueError("MDBList rejected the API key.")
            if response.status_code == 429:
                raise CatalogueError("MDBList request limit reached. Try again later.")
            raise CatalogueError("MDBList request failed (HTTP %s)." % response.status_code)
        try:
            return response.json()
        except ValueError:
            raise CatalogueError("MDBList returned an unreadable response.")

    def user_lists(self):
        data = self._api_get("/lists/user")
        rows = data if isinstance(data, list) else (data.get("lists") or [] if isinstance(data, dict) else [])
        output = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            list_id = row.get("id") or row.get("list_id")
            name = str(row.get("name") or row.get("title") or "").strip()
            mediatype = str(row.get("mediatype") or row.get("media_type") or "movie").lower()
            if list_id in (None, "") or not name or mediatype not in ("movie", "movies"):
                continue
            output.append({
                "id": str(list_id), "name": name,
                "items": row.get("items"), "dynamic": bool(row.get("dynamic")),
            })
        output.sort(key=lambda row: row["name"].casefold())
        return output

    def fetch_list_id(self, list_id, limit=100):
        safe_id = str(list_id or "").strip()
        if not safe_id.isdigit():
            raise CatalogueError("That MDBList list ID is not valid.")
        data = self._api_get("/lists/%s/items" % safe_id)
        rows = data if isinstance(data, list) else (data.get("movies") or data.get("items") or [] if isinstance(data, dict) else [])
        # Reuse the same normaliser without another network call.
        output = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("name") or "").strip()
            year = row.get("release_year") or row.get("year") or 0
            try:
                year = int(year)
            except (TypeError, ValueError):
                year = 0
            marker = (title.casefold(), year)
            mediatype = str(row.get("mediatype") or row.get("media_type") or "movie").lower()
            if not title or marker in seen or mediatype not in ("movie", "movies"):
                continue
            seen.add(marker)
            output.append({"title": title, "year": year, "imdb_id": row.get("imdb_id"), "rank": row.get("rank")})
            if len(output) >= limit:
                break
        return output

    def test(self, list_url):
        return len(self.fetch_list(list_url, limit=1)) == 1
