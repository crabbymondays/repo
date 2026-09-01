import re
from urllib.parse import urlparse

import requests


class CatalogueError(Exception):
    pass


class TMDBClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key, region="GB", session=None, user_agent=None):
        self.api_key = self._normalise_credential(api_key)
        self.region = (str(region or "GB").strip().upper() or "GB")[:2]
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent or "curatr"})

    @staticmethod
    def _normalise_credential(value):
        """Accept copied v3 keys and v4 read tokens without storing auth syntax."""
        credential = str(value or "").strip().strip('"\'')
        if credential.lower().startswith("bearer "):
            credential = credential[7:].strip()
        # Copying from a wrapped browser field can introduce harmless line breaks.
        return "".join(credential.split())

    def _get(self, path, params=None):
        if not self.api_key:
            raise CatalogueError("Add your TMDB API key under Metadata in Settings first.")
        query = dict(params or {})
        headers = {"Accept": "application/json"}
        if self.api_key.startswith("eyJ") or self.api_key.count(".") == 2 or len(self.api_key) > 80:
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
                raise CatalogueError(
                    "TMDB rejected the credential. Enter either one v3 API key or one API Read Access Token, "
                    "press OK to save Settings, then try again."
                )
            if response.status_code == 429:
                raise CatalogueError("TMDB request limit reached. Try again later; cached curatr data will be reused when available.")
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

    def search_show(self, title, year=0):
        params = {"query": str(title or ""), "include_adult": "false"}
        if year:
            params["first_air_date_year"] = int(year)
        data = self._get("/search/tv", params)
        rows = data.get("results") or [] if isinstance(data, dict) else []
        if not rows and year:
            params.pop("first_air_date_year", None)
            rows = (self._get("/search/tv", params).get("results") or [])
        return rows[0] if rows and isinstance(rows[0], dict) else None

    def search_collections(self, query, limit=10):
        data = self._get("/search/collection", {
            "query": str(query or "").strip(), "include_adult": "false",
            "language": "en-GB", "region": self.region,
        })
        rows = data.get("results") or [] if isinstance(data, dict) else []
        return [row for row in rows if isinstance(row, dict) and row.get("id") and row.get("name")][:max(1, min(20, int(limit)))]

    def collection_movies(self, query, limit=100):
        rows = self.search_collections(query, limit=8)
        if not rows:
            return [], None
        wanted = re.sub(r"\b(?:collection|franchise|saga|series|films?|movies?)\b", "", str(query or ""), flags=re.I).strip()
        exact = [row for row in rows if re.sub(r"\bcollection\b", "", str(row.get("name") or ""), flags=re.I).strip().casefold() == wanted.casefold()]
        chosen = (exact or rows)[0]
        data = self._get("/collection/%s" % int(chosen["id"]), {"language": "en-GB"})
        parts = [self._compact(row) for row in data.get("parts") or [] if isinstance(row, dict)]
        parts = [row for row in parts if row.get("title")]
        parts.sort(key=lambda row: (int(row.get("year") or 9999), row.get("title") or ""))
        return parts[:max(1, min(250, int(limit)))], chosen

    def search_people(self, query, limit=20):
        data = self._get("/search/person", {
            "query": str(query or "").strip(), "include_adult": "false",
        })
        rows = data.get("results") or [] if isinstance(data, dict) else []
        return [row for row in rows if isinstance(row, dict) and row.get("name")][:max(1, min(20, int(limit)))]

    def resolve_people(self, references, maximum=3):
        """Resolve natural person references, including generic sibling collectives."""
        output = []
        seen = set()
        for reference in references or []:
            if not isinstance(reference, dict):
                continue
            query = str(reference.get("query") or "").strip()
            role = str(reference.get("role") or "").strip().lower()
            if not query:
                continue
            collective = bool(re.search(r"\b(?:brothers|sisters|siblings)\b", query, re.I))
            search_query = re.sub(r"\b(?:the|brothers|sisters|siblings)\b", " ", query, flags=re.I)
            search_query = " ".join(search_query.split()) if collective else query
            rows = self.search_people(search_query, limit=8 if collective else 3)
            if role == "director":
                directed = [row for row in rows if str(row.get("known_for_department") or "").lower() in ("directing", "writing")]
                rows = directed or rows
            take = 2 if collective else 1
            for row in rows[:take]:
                try:
                    person_id = int(row.get("id"))
                except (TypeError, ValueError):
                    continue
                if person_id in seen:
                    continue
                seen.add(person_id)
                output.append({
                    "id": person_id, "name": str(row.get("name") or query), "role": role,
                    "department": str(row.get("known_for_department") or ""),
                })
                if len(output) >= max(1, min(6, int(maximum))):
                    return output
        return output

    def movie_details(self, movie_id):
        return self._get("/movie/%s" % int(movie_id), {
            "append_to_response": "keywords,credits", "language": "en-GB",
        })

    def list_item_details(self, tmdb_id, media_type="movie"):
        """Fetch only metadata Kodi can display directly on a title list item."""
        media = "tv" if str(media_type) == "show" else "movie"
        extras = ["credits", "videos", "content_ratings" if media == "tv" else "release_dates"]
        return self._get("/%s/%s" % (media, int(tmdb_id)), {
            "append_to_response": ",".join(extras), "language": "en-GB",
            "include_video_language": "en-GB,en,null",
        })

    def analyse_people(self, references, film_limit=15, detail_limit=4):
        """Build a bounded metadata profile without any AI interpretation."""
        resolved = self.resolve_people(references, maximum=3)
        genre_weights, keyword_weights, company_weights, cast_weights = {}, {}, {}, {}
        source_movies = []
        seen_movies = set()
        for person in resolved:
            credits = self._get("/person/%s/movie_credits" % person["id"], {"language": "en-GB"})
            if person.get("role") == "director":
                rows = [row for row in credits.get("crew") or [] if str(row.get("job") or "").lower() == "director"]
            elif person.get("role") == "crew":
                rows = credits.get("crew") or []
            else:
                rows = credits.get("cast") or []
            rows = [row for row in rows if isinstance(row, dict) and row.get("id") and row.get("title")]
            rows.sort(key=lambda row: (float(row.get("vote_average") or 0), int(row.get("vote_count") or 0), float(row.get("popularity") or 0)), reverse=True)
            representative = []
            for row in rows:
                movie_id = int(row["id"])
                if movie_id not in seen_movies:
                    seen_movies.add(movie_id)
                    compact = self._compact(row)
                    source_movies.append(compact)
                    representative.append(row)
                for genre_id in row.get("genre_ids") or []:
                    key = str(genre_id); genre_weights[key] = genre_weights.get(key, 0) + 2
                if len(representative) >= max(5, min(20, int(film_limit))):
                    break
            for row in representative[:max(1, min(6, int(detail_limit)))]:
                details = self.movie_details(row["id"])
                for genre in details.get("genres") or []:
                    if genre.get("id"):
                        key = str(genre["id"]); genre_weights[key] = genre_weights.get(key, 0) + 3
                keyword_rows = ((details.get("keywords") or {}).get("keywords") or (details.get("keywords") or {}).get("results") or [])
                for keyword in keyword_rows[:20]:
                    if keyword.get("id"):
                        key = str(keyword["id"]); keyword_weights[key] = keyword_weights.get(key, 0) + 1
                for company in details.get("production_companies") or []:
                    if company.get("id"):
                        key = str(company["id"]); company_weights[key] = company_weights.get(key, 0) + 1
                for cast in ((details.get("credits") or {}).get("cast") or [])[:8]:
                    if cast.get("id"):
                        key = str(cast["id"]); cast_weights[key] = cast_weights.get(key, 0) + 1

        def strongest(values, limit, minimum=1):
            ordered = sorted(values.items(), key=lambda row: (row[1], row[0]), reverse=True)
            return [int(key) for key, weight in ordered if weight >= minimum][:limit]

        return {
            "resolved_people": resolved,
            "genre_weights": genre_weights,
            "genre_ids": strongest(genre_weights, 4, 2),
            "keyword_ids": strongest(keyword_weights, 8, 2),
            "company_ids": strongest(company_weights, 5, 2),
            "cast_ids": strongest(cast_weights, 8, 2),
            "source_movies": source_movies[:45],
        }

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

    def similar_titles(self, reference, limit=60):
        """Return TMDB recommendations for one movie or whole TV show."""
        reference = reference if isinstance(reference, dict) else {}
        media_type = "show" if str(reference.get("media_type") or "movie") == "show" else "movie"
        tmdb_id = (reference.get("ids") or {}).get("tmdb") or reference.get("tmdb_id")
        if not tmdb_id:
            match = (
                self.search_show(reference.get("title"), reference.get("year"))
                if media_type == "show" else self.search_movie(reference.get("title"), reference.get("year"))
            )
            tmdb_id = (match or {}).get("id")
        if not tmdb_id:
            return []
        endpoint = "tv" if media_type == "show" else "movie"
        wanted = max(5, min(100, int(limit or 20)))
        output, seen = [], set()
        for page in range(1, min(5, (wanted + 19) // 20) + 1):
            data = self._get("/%s/%s/recommendations" % (endpoint, int(tmdb_id)), {
                "region": self.region, "page": page,
            })
            for row in data.get("results") or [] if isinstance(data, dict) else []:
                compact = self._compact_show(row) if media_type == "show" else self._compact(row)
                marker = (str(compact.get("title") or "").casefold(), compact.get("year"))
                if not compact.get("title") or marker in seen:
                    continue
                seen.add(marker)
                compact["media_type"] = media_type
                output.append(compact)
                if len(output) >= wanted:
                    return output
        return output

    def person_credits(self, query, role="", limit=80):
        people = self.search_people(query, limit=5)
        if not people:
            return []
        person_id = people[0].get("id")
        if not person_id:
            return []
        data = self._get("/person/%s/movie_credits" % int(person_id), {
            "language": "en-GB",
        })
        rows = []
        role = str(role or "").lower()
        if role == "director":
            rows = [row for row in data.get("crew") or [] if str(row.get("job") or "").lower() == "director"]
        elif role == "cast":
            rows = data.get("cast") or []
        else:
            rows = (data.get("cast") or []) + (data.get("crew") or [])
        output = []
        seen = set()
        for row in rows:
            compact = self._compact(row)
            marker = compact.get("tmdb_id")
            if marker and marker not in seen and compact.get("title"):
                seen.add(marker)
                output.append(compact)
            if len(output) >= max(1, min(100, int(limit))):
                break
        return output

    def discover_movies(self, rules, limit=100, extra_params=None):
        """Return a compact candidate pool using one or two TMDB discover pages."""
        rules = rules if isinstance(rules, dict) else {}
        sort_map = {
            "recent": "primary_release_date.desc",
            "popular": "popularity.desc",
            "rated": "vote_average.desc",
            "less_mainstream": "vote_average.desc",
            "balanced": "popularity.desc",
        }
        params = {
            "include_adult": "false", "include_video": "false", "region": self.region,
            "sort_by": sort_map.get(str(rules.get("sort") or "balanced"), "popularity.desc"),
        }
        if rules.get("genres"):
            params["with_genres"] = ",".join(str(value) for value in rules["genres"])
        if rules.get("year_min"):
            params["primary_release_date.gte"] = "%d-01-01" % int(rules["year_min"])
        if rules.get("year_max"):
            params["primary_release_date.lte"] = "%d-12-31" % int(rules["year_max"])
        if rules.get("runtime_min"):
            params["with_runtime.gte"] = int(rules["runtime_min"])
        if rules.get("runtime_max"):
            params["with_runtime.lte"] = int(rules["runtime_max"])
        if rules.get("rating_min"):
            params["vote_average.gte"] = float(rules["rating_min"])
            params["vote_count.gte"] = 40
        if rules.get("language"):
            params["with_original_language"] = str(rules["language"])
        if rules.get("country"):
            params["with_origin_country"] = str(rules["country"])
        people = rules.get("resolved_people") or []
        if not people and rules.get("people"):
            people = self.resolve_people(rules.get("people"), maximum=3)
        if people:
            role_ids = {"director": [], "crew": [], "cast": [], "other": []}
            for person in people:
                if not person.get("id"):
                    continue
                role = str(person.get("role") or rules.get("person_role") or "").lower()
                bucket = role if role in ("director", "crew", "cast") else "other"
                role_ids[bucket].append(str(person["id"]))
            if not any(role_ids.values()):
                return []
            crew_ids = role_ids["director"] + role_ids["crew"]
            if crew_ids:
                params["with_crew"] = "|".join(crew_ids)
            if role_ids["cast"]:
                params["with_cast"] = "|".join(role_ids["cast"])
            if role_ids["other"]:
                params["with_people"] = "|".join(role_ids["other"])
        params.update({key: value for key, value in (extra_params or {}).items() if value not in (None, "", [])})

        wanted = max(20, min(100, int(limit)))
        output = []
        seen = set()
        pages = min(5, (wanted + 19) // 20)
        for page in range(1, pages + 1):
            params["page"] = page
            data = self._get("/discover/movie", params)
            rows = data.get("results") or [] if isinstance(data, dict) else []
            if not rows:
                break
            for row in rows:
                compact = self._compact(row)
                marker = compact.get("tmdb_id")
                if marker and marker not in seen and compact.get("title"):
                    seen.add(marker)
                    output.append(compact)
                if len(output) >= wanted:
                    return output
        return output

    def discover_shows(self, rules, limit=100):
        """Return whole-show candidates from TMDB; seasons and episodes are never queried."""
        rules = rules if isinstance(rules, dict) else {}
        movie_to_tv_genres = {
            28: 10759, 12: 10759, 16: 16, 35: 35, 80: 80, 99: 99,
            18: 18, 10751: 10751, 14: 10765, 36: 36, 27: 9648,
            10402: 10764, 9648: 9648, 10749: 18, 878: 10765,
            53: 9648, 10752: 10768, 37: 37,
        }
        sort_map = {
            "recent": "first_air_date.desc", "popular": "popularity.desc",
            "rated": "vote_average.desc", "less_mainstream": "vote_average.desc",
            "balanced": "popularity.desc",
        }
        params = {
            "include_adult": "false",
            "sort_by": sort_map.get(str(rules.get("sort") or "balanced"), "popularity.desc"),
        }
        genres = [movie_to_tv_genres.get(int(value), int(value)) for value in rules.get("genres") or []]
        if genres:
            params["with_genres"] = ",".join(str(value) for value in genres)
        if rules.get("year_min"):
            params["first_air_date.gte"] = "%d-01-01" % int(rules["year_min"])
        if rules.get("year_max"):
            params["first_air_date.lte"] = "%d-12-31" % int(rules["year_max"])
        if rules.get("rating_min"):
            params["vote_average.gte"] = float(rules["rating_min"])
            params["vote_count.gte"] = 20
        if rules.get("language"):
            params["with_original_language"] = str(rules["language"])
        if rules.get("country"):
            params["with_origin_country"] = str(rules["country"])

        wanted = max(20, min(100, int(limit)))
        output, seen = [], set()
        for page in range(1, min(5, (wanted + 19) // 20) + 1):
            params["page"] = page
            data = self._get("/discover/tv", params)
            for row in data.get("results") or [] if isinstance(data, dict) else []:
                compact = self._compact_show(row)
                marker = compact.get("tmdb_id")
                if marker and marker not in seen and compact.get("title"):
                    seen.add(marker)
                    output.append(compact)
                if len(output) >= wanted:
                    return output
        return output

    def enriched_discovery_pool(self, rules, analysis, limit=100):
        """Discover across several loose metadata signals, then deduplicate."""
        rules = dict(rules or {})
        analysis = analysis or {}
        # Similarity means films sharing metadata with the references, not the
        # references' own filmographies. Exact-person mode keeps its people.
        if rules.get("strategy") in ("similar_people", "recurring_collaborators"):
            rules["people"] = []
            rules["resolved_people"] = []
            rules["person_query"] = ""
        variants = []
        genre_ids = analysis.get("genre_ids") or []
        keyword_ids = analysis.get("keyword_ids") or []
        company_ids = analysis.get("company_ids") or []
        cast_ids = analysis.get("cast_ids") or []
        if rules.get("genres"):
            variants.append({})
        elif genre_ids:
            variants.append({"with_genres": "|".join(str(value) for value in genre_ids[:4])})
        if keyword_ids:
            variants.append({"with_keywords": "|".join(str(value) for value in keyword_ids[:8])})
        if company_ids:
            variants.append({"with_companies": "|".join(str(value) for value in company_ids[:5])})
        if cast_ids:
            variants.append({"with_cast": "|".join(str(value) for value in cast_ids[:8])})
        if not variants:
            variants.append({})
        collected, seen = [], set()
        per_variant = max(20, min(60, int(limit) // max(1, len(variants)) + 10))
        source_ids = {row.get("tmdb_id") for row in analysis.get("source_movies") or [] if row.get("tmdb_id")}
        for params in variants[:4]:
            for row in self.discover_movies(rules, limit=per_variant, extra_params=params):
                marker = row.get("tmdb_id")
                if not marker or marker in seen or marker in source_ids:
                    continue
                seen.add(marker); collected.append(row)
                if len(collected) >= max(20, min(120, int(limit))):
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
            "popularity": (row or {}).get("popularity"),
            "genre_ids": list((row or {}).get("genre_ids") or []),
            "poster_path": str((row or {}).get("poster_path") or ""),
            "backdrop_path": str((row or {}).get("backdrop_path") or ""),
            "original_language": str((row or {}).get("original_language") or ""),
            "origin_country": list((row or {}).get("origin_country") or []),
        }

    @staticmethod
    def _compact_show(row):
        date = str((row or {}).get("first_air_date") or "")
        try:
            year = int(date[:4])
        except (TypeError, ValueError):
            year = 0
        return {
            "title": str((row or {}).get("name") or (row or {}).get("original_name") or ""),
            "year": year, "tmdb_id": (row or {}).get("id"),
            "rating": (row or {}).get("vote_average"), "votes": (row or {}).get("vote_count"),
            "overview": str((row or {}).get("overview") or "")[:160],
            "popularity": (row or {}).get("popularity"),
            "genre_ids": list((row or {}).get("genre_ids") or []),
            "poster_path": str((row or {}).get("poster_path") or ""),
            "backdrop_path": str((row or {}).get("backdrop_path") or ""),
            "original_language": str((row or {}).get("original_language") or ""),
            "origin_country": list((row or {}).get("origin_country") or []),
            "media_type": "show",
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
            ids = {
                "imdb": row.get("imdb_id") or row.get("imdbid"),
                "tmdb": row.get("tmdb_id") or row.get("tmdbid"),
                "trakt": row.get("trakt_id") or row.get("traktid"),
            }
            ids = {key: value for key, value in ids.items() if value not in (None, "")}
            output.append({
                "title": title, "year": year, "ids": ids,
                "imdb_id": ids.get("imdb"), "rank": row.get("rank"),
                "rating": row.get("score") or row.get("rating"),
                "overview": str(row.get("description") or row.get("overview") or "")[:500],
            })
            if len(output) >= limit:
                break
        if not output:
            raise CatalogueError("No movie entries were found in that MDBList list.")
        return output

    def _api_get(self, path, params=None):
        if not self.api_key:
            raise CatalogueError("Enter your MDBList API key to use lists from your account.")
        try:
            query = dict(params or {})
            query["apikey"] = self.api_key
            response = self.session.get(
                self.API_URL + path,
                params=query,
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
            ids = {
                "imdb": row.get("imdb_id") or row.get("imdbid"),
                "tmdb": row.get("tmdb_id") or row.get("tmdbid"),
                "trakt": row.get("trakt_id") or row.get("traktid"),
            }
            ids = {key: value for key, value in ids.items() if value not in (None, "")}
            output.append({
                "title": title, "year": year, "ids": ids,
                "imdb_id": ids.get("imdb"), "rank": row.get("rank"),
                "rating": row.get("score") or row.get("rating"),
                "overview": str(row.get("description") or row.get("overview") or "")[:500],
            })
            if len(output) >= limit:
                break
        return output

    def catalog_movies(self, source="mdblist", limit=100):
        """Return a compact, source-sorted MDBList catalogue snapshot."""
        source = str(source or "mdblist").lower()
        sort_fields = {
            "imdb": "imdbrating", "tomatoes": "tomatoes",
            "popcorn": "popcorn", "mdblist": "score",
        }
        rating_keys = {
            "imdb": ("imdb",), "tomatoes": ("tomato", "tomatoes", "tomatometer"),
            "popcorn": ("popcorn", "popcornmeter"), "mdblist": ("score",),
        }
        if source not in sort_fields:
            raise CatalogueError("That MDBList rating source is not supported.")
        limit = max(1, min(250, int(limit or 100)))
        output, seen = [], set()
        for page in range(min(6, (limit + 47) // 48)):
            data = self._api_get("/catalog/movie", {
                "sort": sort_fields[source], "sort_order": "asc", "page": page,
            })
            rows = data if isinstance(data, list) else (data.get("movies") or data.get("items") or data.get("results") or [] if isinstance(data, dict) else [])
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("title") or row.get("name") or "").strip()
                year = row.get("release_year") or row.get("year") or 0
                try:
                    year = int(year)
                except (TypeError, ValueError):
                    year = 0
                title = re.sub(r"\s*\(%s\)\s*$" % year, "", title).strip() if year else title
                marker = (title.casefold(), year)
                if not title or marker in seen:
                    continue
                seen.add(marker)
                ratings = row.get("ratings") or {}
                source_rating = row.get("score") if source == "mdblist" else None
                if isinstance(ratings, dict):
                    for key in rating_keys[source]:
                        value = ratings.get(key)
                        if isinstance(value, dict):
                            value = value.get("rating")
                        if value not in (None, ""):
                            source_rating = value
                            break
                ids = {
                    "imdb": row.get("imdb_id") or row.get("imdbid"),
                    "tmdb": row.get("tmdb_id") or row.get("tmdbid"),
                    "trakt": row.get("trakt_id") or row.get("traktid"),
                }
                ids = {key: value for key, value in ids.items() if value not in (None, "")}
                try:
                    numeric_rating = float(source_rating or 0)
                except (TypeError, ValueError):
                    numeric_rating = 0.0
                output.append({
                    "title": title, "year": year, "ids": ids,
                    "imdb_id": ids.get("imdb"), "external_rating": numeric_rating,
                    "rating": numeric_rating if source in ("imdb", "mdblist") else 0,
                    "overview": str(row.get("description") or row.get("overview") or "")[:500],
                    "source_ratings": ratings if isinstance(ratings, dict) else {},
                })
                if len(output) >= limit:
                    return output
            if len(rows) < 48:
                break
        if not output:
            raise CatalogueError("MDBList returned no films for that rating source.")
        return output

    def test(self, list_url):
        return len(self.fetch_list(list_url, limit=1)) == 1
