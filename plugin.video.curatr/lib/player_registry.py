"""Validated direct-player definitions without an intermediary playback add-on."""

import io
import json
import os
import re
import time
import zipfile
from urllib.parse import quote_plus, urlparse

import requests
import xbmc
import xbmcgui
import xbmcvfs


BUILTIN_PLAYERS = ({
    "id": "redlight",
    "name": "Redlight",
    "plugin": "plugin.video.redlight",
    "priority": 1000,
    "is_resolvable": True,
    "play_movie": (
        "plugin://plugin.video.redlight/?mode=playback.media&media_type=movie"
        "&tmdb_id={tmdb_id}&media=media"
    ),
    "open_show": (
        "plugin://plugin.video.redlight/?mode=playback.media&media_type=tvshow"
        "&tmdb_id={tmdb_id}&media=media"
    ),
},)

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_DEFINITION_BYTES = 256 * 1024
MAX_DEFINITIONS = 200
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_COMMUNITY_URL = "https://codeload.github.com/slrwin/tmdbh.players/zip/refs/heads/master"
COMMUNITY_FALLBACK_URLS = (
    DEFAULT_COMMUNITY_URL,
    "https://github.com/slrwin/tmdbh.players/archive/refs/heads/master.zip",
    "https://bit.ly/gplayers",
)
OBSOLETE_COMMUNITY_URLS = {
    "https://github.com/mrgsi/tmdbh.players/raw/master/tmdbh-players.zip",
    "https://raw.githubusercontent.com/mrgsi/tmdbh.players/master/tmdbh-players.zip",
    "https://raw.githubusercontent.com/slrwin/tmdbh.players/master/tmdbh-players.zip",
    "https://github.com/slrwin/tmdbh.players/raw/master/tmdbh-players.zip",
}


class PlayerDefinitionError(RuntimeError):
    pass


class _Values(dict):
    def __missing__(self, _key):
        return ""


class PlayerRegistry:
    def __init__(self, addon):
        self.addon = addon
        profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
        self.directory = os.path.join(profile, "players")
        self.preferences_path = os.path.join(profile, "player_preferences.json")
        if not xbmcvfs.exists(self.directory):
            xbmcvfs.mkdirs(self.directory)

    def _preferences(self):
        try:
            with open(self.preferences_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _set_preference(self, key, value):
        data = self._preferences()
        data[str(key)] = str(value)
        temp = self.preferences_path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp, self.preferences_path)

    @staticmethod
    def _clean_label(value):
        return re.sub(r"\[/?(?:COLOR|B|I)(?: [^]]+)?\]", "", str(value or ""), flags=re.I).strip()

    @classmethod
    def _normalise(cls, data, fallback_id=""):
        if not isinstance(data, dict):
            return None
        plugin = str(data.get("plugin") or "").strip()
        if not plugin or not SAFE_ID.fullmatch(plugin):
            return None
        player_id = str(data.get("id") or fallback_id or plugin).strip().lower()
        if not player_id or not SAFE_ID.fullmatch(player_id):
            return None
        movie = data.get("play_movie")
        show = data.get("open_show")
        episode = data.get("play_episode")
        # TMDb Helper supports multi-step list definitions. Curatr deliberately
        # imports direct string routes only; executing navigation instructions
        # from an untrusted ZIP would be needlessly powerful.
        movie = movie if isinstance(movie, str) else ""
        show = show if isinstance(show, str) else ""
        episode = episode if isinstance(episode, str) else ""
        if not movie and not show and not episode:
            return None
        expected = "plugin://%s/" % plugin
        for route in (movie, show, episode):
            if route and not route.startswith(expected):
                return None
        try:
            priority = int(data.get("priority") or 0)
        except (TypeError, ValueError):
            priority = 0
        return {
            "id": player_id,
            "name": cls._clean_label(data.get("name") or plugin),
            "plugin": plugin,
            "priority": priority,
            "is_resolvable": str(data.get("is_resolvable", "true")).lower() != "false",
            "play_movie": movie,
            "open_show": show,
            "play_episode": episode,
        }

    def definitions(self):
        players = {}
        for row in BUILTIN_PLAYERS:
            normalised = self._normalise(row, row.get("id") or "")
            if normalised:
                players[normalised["id"]] = normalised
        try:
            names = sorted(os.listdir(self.directory))
        except OSError:
            names = []
        for name in names:
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(self.directory, name)
            try:
                if os.path.getsize(path) > MAX_DEFINITION_BYTES:
                    continue
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                normalised = self._normalise(data, os.path.splitext(name)[0].lower())
                if normalised:
                    players[normalised["id"]] = normalised
            except (OSError, ValueError, TypeError):
                continue
        return sorted(players.values(), key=lambda row: (-row["priority"], row["name"].casefold()))

    @staticmethod
    def installed(player):
        try:
            return bool(xbmc.getCondVisibility("System.HasAddon(%s)" % player["plugin"]))
        except Exception:
            return False

    def available(self, media_type, installed_only=True):
        route = "open_show" if media_type == "show" else "play_movie"
        rows = [row for row in self.definitions() if row.get(route)]
        return [row for row in rows if self.installed(row)] if installed_only else rows

    def selected(self, media_type):
        setting = "show_player_id" if media_type == "show" else "movie_player_id"
        selected = str(self._preferences().get(setting) or "automatic")
        if selected == "information":
            return None
        available = self.available(media_type)
        if selected != "automatic":
            exact = next((row for row in available if row["id"] == selected), None)
            if exact:
                return exact
        return available[0] if available else None

    def preference(self, media_type):
        setting = "show_player_id" if media_type == "show" else "movie_player_id"
        return str(self._preferences().get(setting) or "automatic")

    def choose(self, media_type):
        setting = "show_player_id" if media_type == "show" else "movie_player_id"
        heading = "Choose TV show playback" if media_type == "show" else "Choose movie playback"
        players = self.available(media_type)
        labels = ["Automatic", "Information only"] + [row["name"] for row in players]
        current = str(self._preferences().get(setting) or "automatic")
        values = ["automatic", "information"] + [row["id"] for row in players]
        preselect = values.index(current) if current in values else 0
        choice = xbmcgui.Dialog().select(heading, labels, preselect=preselect)
        if choice < 0:
            return current
        self._set_preference(setting, values[choice])
        self.update_status()
        return values[choice]

    def update_status(self):
        for media_type, setting in (("movie", "movie_player_status"), ("show", "show_player_status")):
            selected = self.preference(media_type)
            player = self.selected(media_type)
            if selected == "information":
                label = "Information only"
            elif selected == "automatic":
                label = "Kodi Library, then %s" % player["name"] if player else "Kodi Library (Automatic)"
            elif player:
                label = player["name"]
            else:
                label = "No compatible installed player"
            self.addon.setSetting(setting, label)

    @staticmethod
    def _image(movie, kind):
        images = movie.get("images") or {}
        value = images.get(kind) if isinstance(images, dict) else ""
        if isinstance(value, dict):
            value = value.get("full") or value.get("medium") or value.get("thumb") or ""
        return str(value or "")

    def build_url(self, player, movie, media_type=None):
        media_type = "show" if (media_type or movie.get("media_type")) == "show" else "movie"
        route = player.get("open_show" if media_type == "show" else "play_movie") or ""
        ids = movie.get("ids") or {}
        identifier_values = {
            "id": str(ids.get("tmdb") or ""), "tmdb": str(ids.get("tmdb") or ""),
            "tmdb_id": str(ids.get("tmdb") or ""), "imdb": str(ids.get("imdb") or ""),
            "trakt": str(ids.get("trakt") or ""),
        }
        identifier_fields = set(re.findall(r"\{(id|tmdb|tmdb_id|imdb|trakt)\}", route))
        if identifier_fields and not any(identifier_values.get(key) for key in identifier_fields):
            return ""
        title = str(movie.get("title") or "")
        values = _Values({
            key: quote_plus(value) for key, value in identifier_values.items()
        })
        escaped_title = quote_plus(title)
        values.update({
            "name": quote_plus(title), "title": quote_plus(title), "title_url": quote_plus(title),
            "title_+": escaped_title, "title_escaped": escaped_title,
            "showname": quote_plus(title), "showname_url": quote_plus(title),
            "showname_+": escaped_title, "showname_escaped": escaped_title,
            "year": quote_plus(str(movie.get("year") or "")),
            "showyear": quote_plus(str(movie.get("year") or "")),
            "plot": quote_plus(str(movie.get("overview") or "")),
            "plot_escaped": quote_plus(str(movie.get("overview") or "")),
            "poster": quote_plus(self._image(movie, "poster")),
            "fanart": quote_plus(self._image(movie, "fanart")),
            "premiered": quote_plus(str(movie.get("released") or "")),
            "thumbnail": quote_plus(self._image(movie, "poster")),
            "now": quote_plus(str(int(time.time()))),
        })
        try:
            url = route.format_map(values)
        except (ValueError, KeyError):
            return ""
        expected = "plugin://%s/" % player["plugin"]
        return url if route and url.startswith(expected) else ""

    def target(self, movie, media_type=None):
        media_type = "show" if (media_type or movie.get("media_type")) == "show" else "movie"
        setting = "%s_player_id" % media_type
        preference = str(self._preferences().get(setting) or "automatic")
        if preference == "information":
            return "", None
        available = self.available(media_type)
        if preference != "automatic":
            available = [row for row in available if row["id"] == preference]
        for player in available:
            url = self.build_url(player, movie, media_type)
            if url:
                return url, player
        return "", None

    def install_from_url(self, url):
        url = str(url or "").strip()
        if urlparse(url).scheme.lower() != "https":
            raise PlayerDefinitionError("The player list address must begin with https://")
        response = None
        try:
            response = requests.get(url, timeout=30, allow_redirects=True, stream=True)
            if response.status_code == 404:
                raise PlayerDefinitionError("No player list was found at that address.")
            response.raise_for_status()
            if urlparse(str(response.url or url)).scheme.lower() != "https":
                raise PlayerDefinitionError("The player list redirected to an unsupported address.")
            chunks = []
            total = 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise PlayerDefinitionError("The downloaded player list is too large.")
                chunks.append(chunk)
        except PlayerDefinitionError:
            raise
        except requests.RequestException as exc:
            raise PlayerDefinitionError("Could not download the player list: %s" % exc)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
        payload = b"".join(chunks)
        if not payload:
            raise PlayerDefinitionError("The downloaded player list is empty.")
        accepted = []
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                candidates = [info for info in archive.infolist() if info.filename.lower().endswith(".json")]
                if len(candidates) > MAX_DEFINITIONS:
                    raise PlayerDefinitionError("The downloaded player list contains too many entries.")
                for info in candidates:
                    base = os.path.basename(info.filename)
                    if not base or info.file_size > MAX_DEFINITION_BYTES:
                        continue
                    raw = archive.read(info)
                    data = json.loads(raw.decode("utf-8-sig"))
                    normalised = self._normalise(data, os.path.splitext(base)[0].lower())
                    if not normalised:
                        continue
                    target = os.path.join(self.directory, "%s.json" % normalised["id"])
                    temp = target + ".tmp"
                    with open(temp, "w", encoding="utf-8") as handle:
                        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
                    os.replace(temp, target)
                    accepted.append(normalised)
        except (zipfile.BadZipFile, UnicodeError, ValueError, OSError) as exc:
            raise PlayerDefinitionError("The downloaded player list could not be read: %s" % exc)
        self.update_status()
        return accepted

    def install_interactive(self):
        saved = str(self.addon.getSetting("community_players_url") or "").strip()
        known_addresses = set(COMMUNITY_FALLBACK_URLS) | OBSOLETE_COMMUNITY_URLS
        default = DEFAULT_COMMUNITY_URL if saved in OBSOLETE_COMMUNITY_URLS or not saved else saved
        if default != saved:
            self.addon.setSetting("community_players_url", default)
        url = xbmcgui.Dialog().input("Player list address", defaultt=default)
        if not url or not url.strip():
            return []
        requested = url.strip()
        attempts = list(COMMUNITY_FALLBACK_URLS) if requested in known_addresses else [requested]
        if requested not in attempts:
            attempts.insert(0, requested)
        rows = None
        error = None
        working_url = requested
        for candidate in attempts:
            try:
                rows = self.install_from_url(candidate)
                working_url = candidate
                break
            except PlayerDefinitionError as exc:
                error = exc
        if rows is None:
            xbmcgui.Dialog().ok("Playback Setup", "Could not update the player list.\n\n%s" % error)
            return []
        self.addon.setSetting("community_players_url", working_url)
        installed = [row for row in rows if self.installed(row)]
        movie_players = [row["name"] for row in installed if row.get("play_movie")]
        show_players = [row["name"] for row in installed if row.get("open_show")]
        detected = "\n\nAvailable for movies: %s" % (", ".join(movie_players) if movie_players else "None")
        detected += "\nAvailable for TV shows: %s" % (", ".join(show_players) if show_players else "None")
        xbmcgui.Dialog().ok(
            "Playback Setup",
            "Updated support for %d video add-on%s.%s\n\nThis finds compatible add-ons already on your device; it does not install new ones."
            % (len(rows), "" if len(rows) == 1 else "s", detected),
        )
        return rows
