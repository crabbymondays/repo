import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import xbmc
import xbmcvfs


class ArtworkCache:
    CACHE_MAX_FILES = 1200
    CACHE_KEEP_FILES = 900
    MAX_IMAGE_BYTES = 12 * 1024 * 1024

    """Small local cache for Trakt artwork used by the plugin/widget interface.

    Trakt requires clients to cache its image CDN responses instead of hotlinking
    them, so ListItems are always pointed at files in the addon profile.
    """

    def __init__(self, addon, workers=6):
        self.addon = addon
        profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
        self.cache_dir = os.path.join(profile, "art")
        if not xbmcvfs.exists(self.cache_dir):
            xbmcvfs.mkdirs(self.cache_dir)
        self.workers = max(1, min(8, int(workers or 1)))
        version = str(addon.getAddonInfo("version") or "").strip()
        self.user_agent = "curatr/%s" % (version or "unknown")

    @staticmethod
    def _normalise_url(url):
        url = str(url or "").strip()
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if not url.startswith("http://") and not url.startswith("https://"):
            return "https://" + url.lstrip("/")
        return url

    @staticmethod
    def _first_image(movie, kind):
        images = movie.get("images") or {} if isinstance(movie, dict) else {}
        if not isinstance(images, dict):
            return ""
        values = images.get(kind)

        def candidates(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                # Handle both {full: url, thumb: url} and {url: ...} image shapes.
                for key in ("full", "medium", "thumb", "url", "image"):
                    if key in value:
                        yield from candidates(value.get(key))
                for key, nested in value.items():
                    if key not in ("full", "medium", "thumb", "url", "image"):
                        yield from candidates(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    yield from candidates(nested)

        for value in candidates(values):
            value = ArtworkCache._normalise_url(value)
            if value:
                return value
        return ""

    def _path_for_url(self, url):
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, digest + ".webp")

    def _download(self, url):
        url = self._normalise_url(url)
        if not url:
            return ""
        target = self._path_for_url(url)
        if xbmcvfs.exists(target):
            return target
        temp = target + ".tmp"
        try:
            response = requests.get(
                url,
                headers={"User-Agent": self.user_agent, "Accept": "image/webp,image/*;q=0.8"},
                timeout=12,
                stream=True,
            )
            response.raise_for_status()
            try:
                declared = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                declared = 0
            if declared and declared > self.MAX_IMAGE_BYTES:
                return ""

            # Stream directly to the profile cache instead of holding the full
            # response in memory. This matters on memory-constrained Kodi devices.
            total = 0
            with open(temp, "wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.MAX_IMAGE_BYTES:
                        raise ValueError("artwork exceeds cache size limit")
                    handle.write(chunk)
            if total <= 0:
                raise ValueError("empty artwork response")
            try:
                os.replace(temp, target)
            except OSError:
                if xbmcvfs.exists(target):
                    os.remove(temp)
                else:
                    os.rename(temp, target)
            return target
        except Exception as exc:
            xbmc.log("curatr artwork cache skipped %s: %s" % (url, exc), xbmc.LOGDEBUG)
            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except OSError:
                pass
            return ""

    def _trim_cache(self):
        """Keep the artwork cache bounded without touching it on every read."""
        try:
            entries = []
            with os.scandir(self.cache_dir) as scan:
                for entry in scan:
                    if not entry.is_file() or entry.name.endswith(".tmp"):
                        continue
                    try:
                        entries.append((entry.stat().st_mtime, entry.path))
                    except OSError:
                        continue
            if len(entries) <= self.CACHE_MAX_FILES:
                return
            entries.sort(reverse=True)
            for _mtime, path in entries[self.CACHE_KEEP_FILES:]:
                try:
                    os.remove(path)
                except OSError:
                    pass
        except OSError:
            pass

    def prefetch_movies(self, movies):
        urls = set()
        for movie in movies or []:
            if not isinstance(movie, dict):
                continue
            for kind in ("poster", "fanart"):
                url = self._first_image(movie, kind)
                if url and not xbmcvfs.exists(self._path_for_url(url)):
                    urls.add(url)
        if not urls:
            return
        with ThreadPoolExecutor(max_workers=min(self.workers, len(urls))) as executor:
            futures = [executor.submit(self._download, url) for url in urls]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass
        self._trim_cache()

    def art_for_movie(self, movie):
        poster_url = self._first_image(movie, "poster")
        fanart_url = self._first_image(movie, "fanart")
        poster = self._download(poster_url) if poster_url else ""
        fanart = self._download(fanart_url) if fanart_url else ""
        art = {}
        if poster:
            art.update({"poster": poster, "thumb": poster, "icon": poster})
        if fanart:
            art.update({"fanart": fanart, "landscape": fanart})
        return art
