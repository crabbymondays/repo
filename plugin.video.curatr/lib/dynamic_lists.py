"""Merge directory snapshots without changing source playback or browsing URLs."""

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from urllib.parse import urlsplit


SORTS = {"lastplayed": "Recently watched", "dateadded": "Recently added", "title": "Title", "source": "Source order"}
CACHE_SECONDS = 60
RETRY_SECONDS = 20
MAX_SOURCES = 16
MAX_SOURCE_ITEMS = 2000
REFRESH_SUMMARY = "Refresh: automatic · Local lists update immediately · Other sources cache for up to 1 minute"
REFRESH_DETAILS = (
    "Refresh is automatic when this list is opened or its widget requests new contents.\n\n"
    "Curatr lists use the latest saved contents immediately. Other add-on paths and linked lists "
    "share a cache for up to one minute; the next request after that checks the source again. "
    "Kodi playback and library changes also invalidate that cache.\n\n"
    "The skin controls when a visible widget requests its contents. Curatr does not poll other "
    "add-ons in the background. If a source is unavailable, its last successful results are retained."
)
FIELDS = ["title", "year", "rating", "playcount", "fanart", "plot", "lastplayed", "season", "episode",
          "showtitle", "thumbnail", "file", "resume", "tvshowid", "watchedepisodes", "art", "uniqueid",
          "dateadded", "customproperties", "runtime", "genre", "premiered", "imdbnumber", "mimetype",
          "studio", "mpaa", "votes", "streamdetails"]


def records(curator):
    return [r for r in curator.state.get("dynamic_lists", []) if isinstance(r, dict) and r.get("id")]


def by_id(curator, list_id):
    return next((r for r in records(curator) if str(r["id"]) == str(list_id)), None)


def valid_source(source):
    if not isinstance(source, dict):
        return False
    kind = source.get("type")
    if kind == "curatr_list":
        return bool(source.get("list_id"))
    if kind == "provider_list":
        return source.get("provider") in ("trakt", "mdblist") and bool(source.get("provider_list_id"))
    if kind != "external_path":
        return False
    path = str(source.get("path") or "")
    try:
        parsed = urlsplit(path)
    except ValueError:
        return False
    return (parsed.scheme == "plugin" and bool(parsed.netloc) and parsed.netloc.lower() != "plugin.video.curatr"
            and len(path) <= 2048 and not any(c in path for c in '\r\n\x00'))


def source_key(source):
    fields = {"curatr_list": ("list_id",), "external_path": ("path",),
              "provider_list": ("provider", "provider_list_id")}.get(source.get("type"), ())
    value = [source.get("type")] + [str(source.get(field) or "") for field in fields]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()


def normalise(record, list_id_map=None):
    if not isinstance(record, dict):
        raise ValueError("Invalid Dynamic List")
    source_rows = record.get("sources", [])
    if not isinstance(source_rows, list):
        source_rows = []
    sources, seen = [], set()
    for row in source_rows:
        if not valid_source(row):
            continue
        row = dict(row)
        if row["type"] == "curatr_list":
            row["list_id"] = (list_id_map or {}).get(str(row["list_id"]), row["list_id"])
        key = source_key(row)
        if key not in seen:
            seen.add(key)
            row["id"] = str(row.get("id") or uuid.uuid4().hex)
            sources.append(row)
    try:
        count = min(1000, max(1, int(record.get("count") or 50)))
    except (TypeError, ValueError):
        count = 50
    return {"id": str(record.get("id") or uuid.uuid4().hex), "name": str(record.get("name") or "Dynamic List"),
            "description": str(record.get("description") or ""), "artwork": record.get("artwork") or {},
            "sources": sources[:MAX_SOURCES], "sort": record.get("sort") if record.get("sort") in SORTS else "lastplayed",
            "descending": record.get("descending", True) is not False,
            "alternate_sources": record.get("alternate_sources") is True, "count": count}


def store(curator, record):
    record = normalise(record)
    rows = records(curator)
    found = any(r["id"] == record["id"] for r in rows)
    curator.state["dynamic_lists"] = [record if r["id"] == record["id"] else r for r in rows] + ([] if found else [record])
    curator.state["dynamic_lists_written"] = True
    curator._save_state()
    return record


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, pending = tempfile.mkstemp(prefix=".pending-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.remove(pending)


def invalidate(profile):
    """Kodi's playback/library notifications invalidate shared directory snapshots."""
    _write(os.path.join(profile, "dynamic_cache", "epoch.json"), {"value": uuid.uuid4().hex})


class SourceCache:
    """One bounded snapshot per source, shared by all lists and plugin invocations."""
    def __init__(self, profile, clock=time.time):
        self.root = os.path.join(profile, "dynamic_cache")
        self.clock = clock

    def get(self, key, fetch):
        path = os.path.join(self.root, key + ".json")
        epoch = _read(os.path.join(self.root, "epoch.json")).get("value", "")
        cached = _read(path)
        def usable(value):
            now = self.clock()
            return (value.get("retry_at", 0) > now or
                    (isinstance(value.get("items"), list) and value.get("epoch", "") == epoch
                     and 0 <= now - value.get("at", 0) < CACHE_SECONDS))
        if usable(cached):
            return cached.get("items", []), bool(cached.get("error"))
        os.makedirs(self.root, exist_ok=True)
        lock = path + ".lock"
        deadline = time.monotonic() + 4
        acquired = False
        try:
            while not acquired:
                try:
                    os.mkdir(lock)
                    acquired = True
                except FileExistsError:
                    try:
                        if self.clock() - os.path.getmtime(lock) > 300:
                            os.rmdir(lock)
                            continue
                    except OSError:
                        pass
                    if time.monotonic() >= deadline:
                        return cached.get("items", []), True
                    time.sleep(0.05)
                    cached = _read(path)
                    if usable(cached):
                        return cached.get("items", []), bool(cached.get("error"))
            # Another invocation may have refreshed it while this caller waited.
            cached = _read(path)
            if usable(cached):
                return cached.get("items", []), bool(cached.get("error"))
            try:
                items = fetch()
                if not isinstance(items, list):
                    raise ValueError("Source did not return a directory")
                value = {"items": items[:MAX_SOURCE_ITEMS], "at": self.clock(), "epoch": epoch}
            except Exception:
                # Keep the last successful response, even if this attempt is offline.
                value = dict(cached, error=True, retry_at=self.clock() + RETRY_SECONDS)
            _write(path, value)
            self._prune(path)
            return value.get("items", []), bool(value.get("error"))
        finally:
            if acquired:
                try:
                    os.rmdir(lock)
                except OSError:
                    pass

    def _prune(self, current):
        try:
            files = [e for e in os.scandir(self.root) if e.name.endswith('.json') and e.name != 'epoch.json']
            if len(files) > 128:
                for entry in sorted(files, key=lambda e: e.stat().st_mtime)[:-96]:
                    if entry.path != current and not os.path.exists(entry.path + '.lock'):
                        os.remove(entry.path)
        except OSError:
            pass


def _date(value):
    if not value:
        return 0
    try:
        if isinstance(value, (float, int)):
            return max(0, float(value))
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0


def _date_field(data, field):
    aliases = (field, "last_watched_at", "last_played") if field == "lastplayed" else (field, "date_added", "added_at")
    values = [data.get(alias) for alias in aliases]
    properties = data.get("customproperties")
    if isinstance(properties, dict):
        props = {str(k).lower(): v for k, v in properties.items()}
        values.extend(props.get(alias) for alias in aliases)
    return max((_date(v) for v in values), default=0)


def media_type(data):
    kind = str(data.get("media_type") or data.get("type") or data.get("mediatype") or "").lower()
    if kind == "show":
        return "tvshow"
    if kind not in ("movie", "tvshow", "episode", "musicvideo"):
        props = data.get("customproperties") or {}
        hint = str(props.get("mediatype") or props.get("dbtype") or "").lower() if isinstance(props, dict) else ""
        if hint in ("movie", "tvshow", "episode", "musicvideo"):
            return hint
        if data.get("filetype") != "directory" and data.get("showtitle") and isinstance(data.get("episode"), int) and data["episode"] >= 0:
            return "episode"
        return "directory" if data.get("filetype") == "directory" else "video"
    return kind


def identity_keys(item):
    data = item["data"]
    kind = media_type(data)
    ids = data.get("ids") or data.get("uniqueid") or {}
    keys = []
    suffix = ()
    if kind == "episode":
        # Some providers give a series ID on episodes. Always include episode coordinates.
        season, episode = data.get("season"), data.get("episode")
        if season is None or episode is None or season == -1 or episode == -1:
            ids = {}
        else:
            suffix = (str(season), str(episode))
    if isinstance(ids, dict):
        ids = dict(ids)
        if not ids.get("imdb") and data.get("imdbnumber") and (kind != "episode" or suffix):
            ids["imdb"] = data["imdbnumber"]
        keys = [(kind, provider, str(ids[provider])) + suffix for provider in ("imdb", "tmdb", "tvdb", "trakt")
                if ids.get(provider) not in (None, "", 0, "0")]
    path = str(data.get("file") or "")
    if path:
        keys.append((kind, "path", path) + suffix)
    return keys


def _alternate_items(items):
    """Take one item per source per turn, retaining each source's own order."""
    queues = defaultdict(deque)
    for item in items:
        queues[item["source_index"]].append(item)
    active = deque(queues.values())
    result = []
    while active:
        queue = active.popleft()
        result.append(queue.popleft())
        if queue:
            active.append(queue)
    return result


def merge_items(groups, sort="lastplayed", descending=True, count=50, alternate_sources=False):
    merged, seen = [], {}
    for source_index, group in enumerate(groups):
        for original in group:
            if not isinstance(original, dict) or not isinstance(original.get("data"), dict):
                continue
            item = dict(original)
            item["source_index"] = source_index
            data = item["data"]
            item["lastplayed"] = _date_field(data, "lastplayed")
            item["dateadded"] = _date_field(data, "dateadded")
            keys = identity_keys(item)
            duplicate = next((seen[k] for k in keys if k in seen), None)
            if duplicate is not None:
                for field in ("lastplayed", "dateadded"):
                    merged[duplicate][field] = max(merged[duplicate][field], item[field])
                for key in keys:
                    seen[key] = duplicate
                continue
            for key in keys:
                seen[key] = len(merged)
            merged.append(item)
    missing = 0
    if sort in ("lastplayed", "dateadded"):
        known = [r for r in merged if r[sort] > 0]
        unknown = [r for r in merged if r[sort] <= 0]
        missing = len(unknown)
        if alternate_sources:
            unknown = _alternate_items(unknown)
        merged = sorted(known, key=lambda r: r[sort], reverse=descending) + unknown
    elif sort == "title":
        merged.sort(key=lambda r: str(r["data"].get("title") or r["data"].get("label") or "").casefold(), reverse=descending)
    elif sort == "source" and alternate_sources:
        merged = _alternate_items(merged)
    return merged[:count], missing


def _native_rows(curator, source):
    response = curator._kodi_json_rpc("Files.GetDirectory", {
        "directory": source["path"], "media": "video", "properties": FIELDS,
        "limits": {"start": 0, "end": MAX_SOURCE_ITEMS}, "sort": {"method": "none"},
    })
    if not isinstance(response, dict) or not isinstance(response.get("files"), list):
        raise ValueError("Source did not return a directory")
    rows = []
    for data in response["files"]:
        if not isinstance(data, dict) or not data.get("file") or data.get("label") == "..":
            continue
        path = str(data["file"])
        if path.startswith(("RunPlugin(", "RunScript(")) or urlsplit(path).netloc.lower() == "plugin.video.curatr":
            continue
        # Pagination controls are navigation, not content. Do not crawl them.
        if re.fullmatch(r"(?:\[.*?\])*\s*(?:next page|next|previous page)(?:\s*[>»]+)?\s*(?:\[.*?\])*", str(data.get("label") or ""), re.I):
            continue
        props = data.get("customproperties") or {}
        playable = next((str(v).lower() for k, v in props.items() if str(k).lower() == "isplayable"), "") if isinstance(props, dict) else ""
        if data.get("filetype") != "directory" and media_type(data) == "video" and playable == "false":
            continue
        rows.append({"kind": "native", "data": data})
    return rows


def load(curator, record):
    record = normalise(record)
    cache = SourceCache(curator.profile_dir)
    groups, warnings = [], []
    for source in record["sources"]:
        label = str(source.get("name") or "Source")
        if source["type"] == "curatr_list":
            local = curator._managed_record_by_id(source["list_id"])
            if not local:
                warnings.append("%s is no longer available." % label)
                continue
            group = [{"kind": "curatr", "data": row} for row in local.get("movies", []) if isinstance(row, dict)]
        else:
            def fetch(source=source):
                if source["type"] == "external_path":
                    return _native_rows(curator, source)
                return [{"kind": "curatr", "data": row} for row in
                        curator._fetch_provider_list_movies(source["provider"], source["provider_list_id"])]
            group, stale = cache.get(source_key(source), fetch)
            if stale:
                warnings.append("%s: using saved items while the source is unavailable or loading." % label)
        groups.append(group)
    items, missing = merge_items(groups, record["sort"], record["descending"], record["count"], record["alternate_sources"])
    if missing:
        order = "alternating between sources" if record["alternate_sources"] else "in source order"
        warnings.append("%d items have no %s date from their source; they follow dated items %s." %
                        (missing, "last-watched" if record["sort"] == "lastplayed" else "added", order))
    return {"items": items, "warnings": warnings}
