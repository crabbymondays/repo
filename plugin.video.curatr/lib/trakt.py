import time
from urllib.parse import quote

import requests

BASE = "https://api.trakt.tv"
AUTH_BASE = "https://auth.trakt.tv"

# curatr's registered Trakt application credentials.
# These identify the application; individual users still authenticate their own
# Trakt account via the Device Code flow and receive their own OAuth tokens.
TRAKT_CLIENT_ID = "umfIvh4qFQIL8GppluNNNSziVFSvbXb0my7VV0QeRqE"
TRAKT_CLIENT_SECRET = "8a-D7GzBGvF49Xjw8t-BVgw1NaeK8IUHJIGGIP4AjQk"
TRAKT_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


class TraktError(Exception):
    def __init__(self, message, status_code=None, path=None, payload=None, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.path = path
        self.payload = payload
        self.retry_after = retry_after


class TraktClient:
    def __init__(self, client_id, client_secret, token_store, redirect_uri="",
                 token_callback=None, session=None, user_agent=None):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.redirect_uri = (redirect_uri or "").strip()
        self.token_store = token_store
        self.token_callback = token_callback
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent or "curatr"})

    def _headers(self, auth=True):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "trakt-api-key": self.client_id,
            "trakt-api-version": "2",
        }
        if auth and self.token_store.get("access_token"):
            headers["Authorization"] = "Bearer " + self.token_store["access_token"]
        return headers

    @staticmethod
    def _response_payload(response):
        if not response.text:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    @classmethod
    def _error_from_response(cls, response, path):
        payload = cls._response_payload(response)
        message = "Trakt request failed"
        if isinstance(payload, dict):
            message = (payload.get("error_description") or payload.get("message") or
                       payload.get("error") or message)
        elif isinstance(payload, str) and payload.strip():
            message = payload.strip()[:500]

        retry_after = None
        if response.status_code == 401:
            message = "Trakt authorization expired or was revoked. Re-link Trakt if refresh cannot recover it."
        elif response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            message = "Trakt rate limit reached"
            if retry_after:
                message += "; retry after %s seconds" % retry_after

        return TraktError(
            "%s (HTTP %s)" % (message, response.status_code),
            status_code=response.status_code,
            path=path,
            payload=payload,
            retry_after=retry_after,
        )

    def _request_raw(self, method, url, path_label, **kwargs):
        attempts = 3 if str(method).upper() == "GET" else 1
        for attempt in range(attempts):
            try:
                return self.session.request(method, url, timeout=30, **kwargs)
            except requests.RequestException as exc:
                if attempt + 1 >= attempts:
                    raise TraktError("Could not contact Trakt: %s" % exc, path=path_label)
                time.sleep(0.35 * (2 ** attempt))

    def _token_expiring(self):
        access_token = self.token_store.get("access_token")
        if not access_token:
            return True
        try:
            created_at = int(self.token_store.get("created_at") or 0)
            expires_in = int(self.token_store.get("expires_in") or 0)
        except (TypeError, ValueError):
            return False
        if not created_at or not expires_in:
            return False
        return time.time() >= (created_at + expires_in - 300)

    def _store_token_payload(self, token):
        if not isinstance(token, dict) or not token.get("access_token"):
            raise TraktError("Trakt returned an invalid token response.")
        self.token_store.update(token)
        if self.token_callback:
            self.token_callback(token)
        return token

    def ensure_access_token(self):
        if not self.token_store.get("access_token"):
            raise TraktError("Trakt is not linked. Use Link / Re-link Trakt first.", status_code=401)
        if self._token_expiring() and self.token_store.get("refresh_token"):
            self.refresh_access_token()

    def refresh_access_token(self):
        refresh_token = self.token_store.get("refresh_token")
        if not refresh_token:
            raise TraktError("No Trakt refresh token is available. Re-link Trakt.", status_code=401)
        if not self.client_id or not self.client_secret:
            raise TraktError("Trakt Client ID and Client Secret are not configured.")

        body = {
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
        }
        if self.redirect_uri:
            body["redirect_uri"] = self.redirect_uri

        response = self._request_raw(
            "POST", AUTH_BASE + "/oauth/token", "/oauth/token",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=body,
        )
        if response.status_code >= 400:
            error = self._error_from_response(response, "/oauth/token")
            if error.status_code == 400 and not self.redirect_uri:
                raise TraktError(
                    "Trakt could not refresh the login. If your Trakt application has a Redirect URI, "
                    "enter that exact URI in addon settings, then re-link Trakt. (%s)" % error,
                    status_code=400,
                    path="/oauth/token",
                    payload=error.payload,
                )
            raise error
        return self._store_token_payload(self._response_payload(response) or {})

    def request(self, method, path, auth=True, retry_auth=True, **kwargs):
        if not self.client_id:
            raise TraktError("Trakt Client ID is not configured.")
        if auth:
            self.ensure_access_token()

        response = self._request_raw(
            method, BASE + path, path, headers=self._headers(auth), **kwargs)

        if response.status_code == 401 and auth and retry_auth and self.token_store.get("refresh_token"):
            self.refresh_access_token()
            return self.request(method, path, auth=auth, retry_auth=False, **kwargs)

        if response.status_code >= 400:
            raise self._error_from_response(response, path)
        return self._response_payload(response) or {}

    # ---------- OAuth / account ----------

    def device_code(self):
        if not self.client_id:
            raise TraktError("Trakt Client ID is not configured.")
        response = self._request_raw(
            "POST", AUTH_BASE + "/oauth/device/code", "/oauth/device/code",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={"client_id": self.client_id},
        )
        if response.status_code >= 400:
            raise self._error_from_response(response, "/oauth/device/code")
        payload = self._response_payload(response) or {}
        if not isinstance(payload, dict) or not payload.get("device_code") or not payload.get("user_code"):
            raise TraktError("Trakt returned an invalid device-code response.")
        return payload

    def device_token(self, device_code):
        response = self._request_raw(
            "POST", AUTH_BASE + "/oauth/device/token", "/oauth/device/token",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={
                "code": device_code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        if response.status_code >= 400:
            raise self._error_from_response(response, "/oauth/device/token")
        return self._store_token_payload(self._response_payload(response) or {})

    def profile(self):
        return self.request("GET", "/users/me")


    # ---------- Taste/profile reads ----------


    def ratings_movies(self, limit=300):
        # Minimal/default metadata is sufficient for taste analysis and saves bandwidth.
        return self._paged("/users/me/ratings/movies", limit, extended=False, auth=True)

    def ratings_shows(self, limit=300):
        return self._paged("/users/me/ratings/shows", limit, extended=False, auth=True)

    def watched_movies(self, limit=300):
        return self._paged("/users/me/watched/movies", limit, extended=False, auth=True)

    def watched_shows(self, limit=300):
        return self._paged("/users/me/watched/shows", limit, extended=False, auth=True)

    def ratings_movies_for_user(self, username, limit=300):
        path = "/users/%s/ratings/movies" % quote(str(username), safe="")
        return self._paged(path, limit, extended=False, auth=False)

    def ratings_shows_for_user(self, username, limit=300):
        path = "/users/%s/ratings/shows" % quote(str(username), safe="")
        return self._paged(path, limit, extended=False, auth=False)

    def watched_movies_for_user(self, username, limit=300):
        path = "/users/%s/watched/movies" % quote(str(username), safe="")
        return self._paged(path, limit, extended=False, auth=False)

    def watched_shows_for_user(self, username, limit=300):
        path = "/users/%s/watched/shows" % quote(str(username), safe="")
        return self._paged(path, limit, extended=False, auth=False)

    def movie_people(self, trakt_id):
        return self.request("GET", "/movies/%s/people" % int(trakt_id), auth=False)

    def search_movies(self, query, year=None):
        # extended=full gives the plugin enough metadata/artwork to cache the result
        # locally without an additional movie-summary request for every recommendation.
        params = {"query": query, "extended": "full"}
        if year:
            params["years"] = int(year)
        return self.request("GET", "/search/movie", auth=False, params=params)

    def search_shows(self, query, year=None):
        params = {"query": query, "extended": "full"}
        if year:
            params["years"] = int(year)
        return self.request("GET", "/search/show", auth=False, params=params)

    def search_tmdb(self, tmdb_id, media_type="movie"):
        kind = "show" if media_type == "show" else "movie"
        data = self.request(
            "GET", "/search/tmdb/%s" % quote(str(tmdb_id), safe=""),
            auth=False, params={"type": kind},
        )
        return data if isinstance(data, list) else []

    def movie_summary(self, trakt_id):
        return self.request(
            "GET", "/movies/%s" % quote(str(trakt_id), safe=""),
            auth=False, params={"extended": "full"}
        )

    def show_summary(self, trakt_id):
        return self.request(
            "GET", "/shows/%s" % quote(str(trakt_id), safe=""),
            auth=False, params={"extended": "full"}
        )

    # ---------- Personal list writes (OAuth required) ----------

    def lists(self):
        data = self.request("GET", "/users/me/lists")
        return data if isinstance(data, list) else []

    def create_list(self, name, description):
        return self.request("POST", "/users/me/lists", json={
            "name": name,
            "description": description,
            "privacy": "private",
            "display_numbers": False,
            "allow_comments": False,
        })

    def update_list(self, list_id, name=None, description=None):
        payload = {}
        if name is not None:
            payload["name"] = str(name)
        if description is not None:
            payload["description"] = str(description)
        if not payload:
            return {}
        return self.request(
            "PUT",
            "/users/me/lists/%s" % quote(str(list_id), safe=""),
            json=payload,
        )

    def delete_list(self, list_id):
        """Permanently delete one of the authenticated user's Trakt lists."""
        return self.request(
            "DELETE",
            "/users/me/lists/%s" % quote(str(list_id), safe=""),
        )

    def list_items(self, list_id, limit=5000, extended=False, media_type="all"):
        item_type = media_type if media_type in ("movies", "shows") else "all"
        path = "/users/me/lists/%s/items/%s" % (quote(str(list_id), safe=""), item_type)
        return self._paged(path, limit, extended=extended, auth=True)

    def add_movies(self, list_id, trakt_ids):
        ids = self._unique_int_ids(trakt_ids)
        if not ids:
            return {}
        return self.request(
            "POST",
            "/users/me/lists/%s/items" % quote(str(list_id), safe=""),
            json={"movies": [{"ids": {"trakt": item_id}} for item_id in ids]},
        )

    def remove_movies(self, list_id, trakt_ids):
        ids = self._unique_int_ids(trakt_ids)
        if not ids:
            return {}
        return self.request(
            "POST",
            "/users/me/lists/%s/items/remove" % quote(str(list_id), safe=""),
            json={"movies": [{"ids": {"trakt": item_id}} for item_id in ids]},
        )

    def add_shows(self, list_id, trakt_ids):
        ids = self._unique_int_ids(trakt_ids)
        if not ids:
            return {}
        return self.request(
            "POST", "/users/me/lists/%s/items" % quote(str(list_id), safe=""),
            json={"shows": [{"ids": {"trakt": item_id}} for item_id in ids]},
        )

    def remove_shows(self, list_id, trakt_ids):
        ids = self._unique_int_ids(trakt_ids)
        if not ids:
            return {}
        return self.request(
            "POST", "/users/me/lists/%s/items/remove" % quote(str(list_id), safe=""),
            json={"shows": [{"ids": {"trakt": item_id}} for item_id in ids]},
        )

    @staticmethod
    def _unique_int_ids(values):
        result = []
        seen = set()
        for value in values:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def _paged(self, path, limit, extended=True, auth=True):
        page = 1
        items = []
        limit = max(1, int(limit))
        while len(items) < limit:
            page_size = min(100, limit - len(items))
            params = {"page": page, "limit": page_size}
            if extended:
                params["extended"] = "full"
            data = self.request("GET", path, auth=auth, params=params)
            if not isinstance(data, list) or not data:
                break
            items.extend(data)
            if len(data) < page_size:
                break
            page += 1
        return items[:limit]
