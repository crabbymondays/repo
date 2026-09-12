import hashlib
import json
import os
import time
import unicodedata
import uuid
from copy import deepcopy

import xbmc
import xbmcgui
import xbmcvfs

from .ai_factory import create_ai_client
from .art_cache import ArtworkCache
from .artwork_editor import edit_artwork
from .artwork_grid import choose_artwork
from .bundled_art import components as artwork_components
from .catalogue_clients import CatalogueError, MDBListClient, TMDBClient
from .collection_manager import manage_collection
from .folder_contents import manage_folder_contents
from .folder_settings import edit_folder_settings
from .kodi_library import KodiLibraryError, KodiLibraryReader
from .keyword_confirm import confirm_keyword_rules
from .keyword_matcher import PARSER_VERSION, candidate_matches, format_rules, parse_prompt, preferred_genre_ids, score_candidate
from .list_settings import edit_list_settings
from .metadata_cache import MetadataCache
from .menu_art import menu_source
from .menu_background import background_entries, current_choice, import_custom_background
from .list_art import CHOICES as LIST_ART_CHOICES
from .list_art import bundled_source as bundled_list_art_source
from .list_art import label as list_art_label
from .list_art import normalise_state as normalise_list_art
from .list_art import resolved_sources as list_art_sources
from .list_art import summary as list_art_summary
from .list_art import suggest_key as suggested_art_key
from .trakt import (
    TRAKT_CLIENT_ID, TRAKT_CLIENT_SECRET, TRAKT_REDIRECT_URI,
    TraktClient, TraktError,
)
from .trakt_auth import TraktAuthWindow


class Curator:
    AUTO_RETRY_SECONDS = 3600
    DIRECTOR_CACHE_MAX_AGE_SECONDS = 180 * 24 * 3600
    DIRECTOR_CACHE_MAX_ITEMS = 250
    MOVIE_CACHE_MAX_ITEMS = 500
    MOVIE_CACHE_KEEP_ITEMS = 350
    MOVIE_MISS_TTL_SECONDS = 24 * 3600
    KEYWORD_ANALYSIS_MAX_AGE_SECONDS = 90 * 24 * 3600
    KEYWORD_ANALYSIS_MAX_ITEMS = 20
    STATE_LOCK_TIMEOUT_SECONDS = 5.0
    STATE_LOCK_STALE_SECONDS = 30.0

    def __init__(self, addon, update_status=True, init_clients=True):
        self.addon = addon
        self.name = addon.getAddonInfo("name") or "curatr"
        self.profile_dir = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
        if not xbmcvfs.exists(self.profile_dir):
            xbmcvfs.mkdirs(self.profile_dir)
        self.state_path = os.path.join(self.profile_dir, "state.json")
        self.recovery_state_path = os.path.join(self.profile_dir, "state.recovery.json")
        self.state_lock_path = os.path.join(self.profile_dir, ".state-write.lock")
        self._state_baseline = {}
        self._user_collection_baselines = {}
        self._dirty_widget_folder_ids = set()
        self._deleted_widget_folder_ids = set()
        self._had_state_file = any(xbmcvfs.exists(path) for path in (
            self.state_path, self.state_path + ".bak", self.recovery_state_path,
        ))
        self.state = self._load_state()
        self._capture_state_baseline()
        if not self.state.get("install_origin"):
            self.state["install_origin"] = "pre-0.13" if self._had_state_file else (addon.getAddonInfo("version") or "0.13.0")
        self._had_existing_configuration = self._detect_existing_configuration()
        self._migrate_local_list_state()

        self.trakt = None
        self.ai = None
        self.tmdb = None
        self.mdblist = None
        if init_clients:
            addon_version = str(addon.getAddonInfo("version") or "").strip()
            user_agent = "curatr/%s" % (addon_version or "unknown")
            self.trakt = TraktClient(
                TRAKT_CLIENT_ID,
                TRAKT_CLIENT_SECRET,
                self.state,
                redirect_uri=TRAKT_REDIRECT_URI,
                token_callback=self._on_token_update,
                user_agent=user_agent,
            )
            self.ai = create_ai_client(addon, usage_callback=self._on_ai_usage, user_agent=user_agent)
            if self._bool_setting("tmdb_enabled", False):
                self.tmdb = TMDBClient(
                    addon.getSetting("tmdb_api_key"), addon.getSetting("tmdb_region") or "GB",
                    user_agent=user_agent,
                )
            if self._bool_setting("mdblist_enabled", False):
                self.mdblist = MDBListClient(
                    addon.getSetting("mdblist_api_key"), user_agent=user_agent,
                )

        # Script/plugin entry points keep these rows current. The background
        # scheduler can use init_clients=False for its cheap local due check.
        if update_status and init_clients:
            cached_username = str(self.state.get("trakt_username") or "").strip()
            if cached_username and self.state.get("access_token"):
                self._set_trakt_status("Connected as %s" % cached_username)
            elif self._public_username():
                self._set_trakt_status("Public profile: %s (read-only)" % self._public_username())
            else:
                self._set_trakt_status("Not connected: Kodi lists still work")
            self._update_ai_status_rows()
            self._update_mdblist_status()

    # ---------- Localisation / first run ----------

    def _loc(self, string_id, fallback):
        """Return a translated Kodi string, falling back safely to English."""
        try:
            value = str(self.addon.getLocalizedString(int(string_id)) or "").strip()
            if value:
                return value
        except Exception:
            pass
        return str(fallback)

    def _detect_existing_configuration(self):
        """Avoid showing first-run onboarding to upgrades or already configured installs."""
        if self._had_state_file:
            return True
        if str(self.state.get("install_origin") or "") == "pre-0.13":
            return True
        if self.state.get("access_token") or self.state.get("trakt_username"):
            return True
        if self.state.get("ai_lists") or self.state.get("profile"):
            return True
        for setting_id in (
            "openai_api_key",
            "gemini_api_key",
            "anthropic_api_key",
            "openrouter_api_key",
            "compatible_api_key",
            "tmdb_api_key",
            "mdblist_api_key",
            "trakt_public_username",
        ):
            try:
                if str(self.addon.getSetting(setting_id) or "").strip():
                    return True
            except Exception:
                continue
        return False

    def maybe_show_first_run(self):
        """Show one lightweight setup invitation on a genuinely new installation."""
        if self.state.get("onboarding_seen"):
            return False

        # Existing users upgrading from an older curatr build should never be
        # interrupted simply because the onboarding marker did not exist yet.
        if self._had_existing_configuration:
            self.state["onboarding_seen"] = True
            try:
                self._save_state()
            except Exception:
                pass
            return False

        self.state["onboarding_seen"] = True
        try:
            self._save_state()
        except Exception:
            pass

        message = self._loc(32401,
            "Welcome to curatr. You can create lists with Keyword Matching and TMDB, or add an AI service for "
            "more nuanced requests. curatr can use your Kodi Library for preferences, while Trakt remains optional "
            "for additional history and list syncing.\n\nOpen Settings now?")
        try:
            open_now = xbmcgui.Dialog().yesno(
                self._loc(32400, "Welcome to curatr"),
                message,
                nolabel=self._loc(32403, "Later"),
                yeslabel=self._loc(32402, "Open Settings"),
            )
        except TypeError:
            # Compatibility fallback for unusual Kodi Python bindings.
            open_now = xbmcgui.Dialog().yesno(self._loc(32400, "Welcome to curatr"), message)
        if open_now:
            self.open_settings()
        return True

    # ---------- Persistent state ----------

    @staticmethod
    def _read_text(path):
        handle = None
        try:
            handle = xbmcvfs.File(path, "r")
            return handle.read()
        finally:
            if handle:
                handle.close()

    @staticmethod
    def _write_text(path, text):
        handle = None
        try:
            handle = xbmcvfs.File(path, "w")
            written = handle.write(text)
            if written is False:
                raise OSError("Kodi VFS could not write %s" % path)
        finally:
            if handle:
                handle.close()

    def _load_state(self):
        backup_path = self.state_path + ".bak"
        errors = []
        candidates = []
        for path in (self.state_path, backup_path, self.recovery_state_path):
            if not xbmcvfs.exists(path):
                continue
            try:
                raw = self._read_text(path)
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict):
                    raise ValueError("state root is not an object")
                candidates.append((path, data))
            except Exception as exc:
                errors.append("%s: %s" % (os.path.basename(path), exc))
        if candidates:
            live = next((data for path, data in candidates if path == self.state_path), None)
            if live is not None and self._state_content_score(live) > 0:
                return live
            path, data = max(candidates, key=lambda row: self._state_content_score(row[1]))
            if path != self.state_path:
                xbmc.log("curatr restored local lists and folders from its safety copy", xbmc.LOGWARNING)
            return data
        if errors:
            xbmc.log("curatr could not read state: %s" % "; ".join(errors), xbmc.LOGWARNING)
        return {}

    @staticmethod
    def _user_collection_key(name, row):
        if not isinstance(row, dict):
            return ""
        if name == "ai_lists":
            return str(row.get("local_id") or row.get("trakt_id") or "")
        if name == "hidden_movies":
            return str(row.get("marker") or "")
        return str(row.get("id") or "")

    def _capture_state_baseline(self):
        self._state_baseline = deepcopy(self.state)
        self._user_collection_baselines = {
            name: deepcopy(self.state.get(name) if isinstance(self.state.get(name), list) else [])
            for name in ("ai_lists", "widget_folders", "prompt_templates", "hidden_movies")
        }

    @classmethod
    def _state_content_score(cls, state):
        if not isinstance(state, dict):
            return 0
        return sum(
            len(state.get(name) or []) if isinstance(state.get(name), list) else 0
            for name in ("ai_lists", "widget_folders", "prompt_templates", "hidden_movies")
        )

    def _preserve_recovery_snapshot(self, current):
        """Retain recoverable local content even after later backup rotations."""
        if self._state_content_score(current) <= 0:
            return
        collection_names = ("ai_lists", "widget_folders", "prompt_templates", "hidden_movies")
        recovery = {}
        raw_recovery = ""
        if xbmcvfs.exists(self.recovery_state_path):
            try:
                raw_recovery = self._read_text(self.recovery_state_path) or ""
                recovery = json.loads(raw_recovery or "{}")
            except Exception:
                recovery = {}
        if not isinstance(recovery, dict):
            recovery = {}
        recovery = {
            "recovery_format": 1,
            "install_origin": str(current.get("install_origin") or (recovery or {}).get("install_origin") or ""),
            "onboarding_seen": True,
            **{
                name: [
                    deepcopy(row) for row in (recovery or {}).get(name, [])
                    if isinstance(row, dict) and self._user_collection_key(name, row)
                ]
                for name in collection_names
            },
        }
        for name in collection_names:
            archived = recovery[name]
            positions = {
                self._user_collection_key(name, row): index
                for index, row in enumerate(archived)
            }
            for row in current.get(name, []):
                key = self._user_collection_key(name, row)
                if not isinstance(row, dict) or not key:
                    continue
                if key in positions:
                    archived[positions[key]] = deepcopy(row)
                else:
                    positions[key] = len(archived)
                    archived.append(deepcopy(row))
        payload = json.dumps(recovery, ensure_ascii=False, separators=(",", ":"))
        if payload != raw_recovery:
            self._write_text(self.recovery_state_path, payload)

    def _merge_concurrent_state(self):
        """Three-way merge state changed by another curatr process."""
        if not xbmcvfs.exists(self.state_path):
            return
        try:
            current = json.loads(self._read_text(self.state_path) or "{}")
        except Exception:
            return
        if not isinstance(current, dict):
            return

        collection_names = ("ai_lists", "widget_folders", "prompt_templates", "hidden_movies")
        baseline = self._state_baseline if isinstance(self._state_baseline, dict) else {}

        # For ordinary state fields, keep local changes and otherwise adopt the
        # latest value on disk. This prevents a long-running service instance
        # from restoring stale tokens, preferences, caches or activity rows.
        for key in set(baseline) | set(self.state) | set(current):
            if key in collection_names:
                continue
            memory_present = key in self.state
            baseline_present = key in baseline
            disk_present = key in current
            memory_changed = (
                memory_present != baseline_present
                or (memory_present and self.state.get(key) != baseline.get(key))
            )
            if memory_changed:
                continue
            if disk_present:
                self.state[key] = deepcopy(current[key])
            else:
                self.state.pop(key, None)

        # User collections are merged row by row so two processes can safely
        # update different lists, folders, prompts or hidden items at once.
        for name in collection_names:
            disk_rows = current.get(name)
            memory_rows = self.state.get(name)
            baseline_rows = self._user_collection_baselines.get(name, [])
            if not isinstance(disk_rows, list) or not isinstance(memory_rows, list):
                continue
            baseline_by_id = {
                self._user_collection_key(name, row): row for row in baseline_rows
                if self._user_collection_key(name, row)
            }
            memory_by_id = {
                self._user_collection_key(name, row): row for row in memory_rows
                if self._user_collection_key(name, row)
            }
            disk_ids = {
                self._user_collection_key(name, row) for row in disk_rows
                if self._user_collection_key(name, row)
            }
            dirty_ids = {
                key for key, row in memory_by_id.items()
                if key not in baseline_by_id or row != baseline_by_id[key]
            }
            deleted_ids = set(baseline_by_id) - set(memory_by_id)
            if name == "widget_folders":
                dirty_ids.update(self._dirty_widget_folder_ids)
                deleted_ids.update(self._deleted_widget_folder_ids)
            disk_deleted_ids = set(baseline_by_id) - disk_ids

            merged = []
            seen = set()
            for disk_row in disk_rows:
                key = self._user_collection_key(name, disk_row)
                if not key or key in deleted_ids:
                    continue
                merged.append(memory_by_id[key] if key in dirty_ids and key in memory_by_id else disk_row)
                seen.add(key)
            for memory_row in memory_rows:
                key = self._user_collection_key(name, memory_row)
                if not key or key in seen or key in deleted_ids:
                    continue
                if key in disk_deleted_ids and key not in dirty_ids:
                    continue
                merged.append(memory_row)
                seen.add(key)
            self.state[name] = merged

    def _acquire_state_lock(self):
        deadline = time.monotonic() + self.STATE_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                os.mkdir(self.state_lock_path)
                return True
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.state_lock_path)
                    if age >= self.STATE_LOCK_STALE_SECONDS:
                        os.rmdir(self.state_lock_path)
                        continue
                except OSError:
                    pass
            except OSError as exc:
                raise RuntimeError("Could not create curatr's state lock: %s" % exc)
            if time.monotonic() >= deadline:
                raise RuntimeError("Could not safely save curatr data because another update is still in progress.")
            time.sleep(0.05)

    def _release_state_lock(self):
        try:
            os.rmdir(self.state_lock_path)
        except OSError:
            pass

    def _save_state(self, merge_disk_state=True):
        self._acquire_state_lock()
        temp_path = self.state_path + ".tmp"
        backup_path = self.state_path + ".bak"
        moved_existing = False
        try:
            if merge_disk_state:
                self._merge_concurrent_state()
            payload = json.dumps(self.state, ensure_ascii=False, separators=(",", ":"))
            if xbmcvfs.exists(temp_path):
                xbmcvfs.delete(temp_path)
            self._write_text(temp_path, payload)

            # Preserve both live and previous snapshots before rotating the
            # backup. This is especially important when upgrading from 1.0.1,
            # where the backup may be the only copy containing missing lists.
            for snapshot_path in (backup_path, self.state_path):
                if not xbmcvfs.exists(snapshot_path):
                    continue
                try:
                    snapshot = json.loads(self._read_text(snapshot_path) or "{}")
                except Exception:
                    continue
                self._preserve_recovery_snapshot(snapshot)
            if xbmcvfs.exists(self.state_path):
                if xbmcvfs.exists(backup_path):
                    xbmcvfs.delete(backup_path)
                moved_existing = bool(xbmcvfs.rename(self.state_path, backup_path))
                if not moved_existing:
                    # Some VFS implementations cannot rename an open/profile
                    # file. Keep the live file in place and use the established
                    # direct-write fallback rather than deleting it first.
                    self._write_text(self.state_path, payload)
                    xbmcvfs.delete(temp_path)
                    try:
                        self._preserve_recovery_snapshot(self.state)
                    except Exception as exc:
                        xbmc.log("curatr could not update its recovery snapshot: %s" % exc, xbmc.LOGWARNING)
                    self._dirty_widget_folder_ids.clear()
                    self._deleted_widget_folder_ids.clear()
                    self._capture_state_baseline()
                    return
            if not xbmcvfs.rename(temp_path, self.state_path):
                self._write_text(self.state_path, payload)
                if xbmcvfs.exists(temp_path):
                    xbmcvfs.delete(temp_path)
            try:
                self._preserve_recovery_snapshot(self.state)
            except Exception as exc:
                xbmc.log("curatr could not update its recovery snapshot: %s" % exc, xbmc.LOGWARNING)
            self._dirty_widget_folder_ids.clear()
            self._deleted_widget_folder_ids.clear()
            self._capture_state_baseline()
        except Exception:
            if xbmcvfs.exists(temp_path):
                xbmcvfs.delete(temp_path)
            if moved_existing and not xbmcvfs.exists(self.state_path) and xbmcvfs.exists(backup_path):
                xbmcvfs.rename(backup_path, self.state_path)
            raise
        finally:
            self._release_state_lock()

    def _migrate_local_list_state(self):
        """Keep list definitions backward compatible while separating AI regeneration from Trakt refresh.

        Per-list refresh and Trakt sync use independent schedules:
        AI regeneration (re-run the saved prompt) and Trakt list refresh (push the
        current local recommendations to Trakt without calling the AI).
        """
        changed = False
        records = self.state.get("ai_lists") or []
        if not isinstance(records, list):
            records = []
            self.state["ai_lists"] = records
            changed = True

        if not isinstance(self.state.get("prompt_templates"), list):
            self.state["prompt_templates"] = []
            changed = True
        if not isinstance(self.state.get("hidden_movies"), list):
            self.state["hidden_movies"] = []
            changed = True
        if not isinstance(self.state.get("widget_folders"), list):
            self.state["widget_folders"] = []
            changed = True
        if not isinstance(self.state.get("linked_list_cache"), dict):
            self.state["linked_list_cache"] = {}
            changed = True
        if not isinstance(self.state.get("keyword_analysis_cache"), dict):
            self.state["keyword_analysis_cache"] = {}
            changed = True

        folders = []
        for folder in self.state.get("widget_folders", []):
            if not isinstance(folder, dict):
                changed = True
                continue
            normalised = dict(folder)
            normalised["id"] = self._safe_reference_id(normalised.get("id"))
            normalised["name"] = str(normalised.get("name") or "Folder").strip() or "Folder"
            normalised["description"] = str(normalised.get("description") or "").strip()
            normalised["artwork"] = normalise_list_art(normalised.get("artwork"))
            entries = []
            for entry in normalised.get("entries", []):
                if not isinstance(entry, dict):
                    changed = True
                    continue
                item = dict(entry)
                item["id"] = self._safe_reference_id(item.get("id"))
                if item.get("type") == "curatr_list" and item.get("list_id"):
                    item = {"id": item["id"], "type": "curatr_list", "list_id": str(item.get("list_id"))}
                elif item.get("type") == "external_path" and self._valid_external_plugin_path(item.get("path")):
                    item.update({
                        "type": "external_path",
                        "name": str(item.get("name") or "External Shortcut").strip() or "External Shortcut",
                        "description": str(item.get("description") or "").strip(),
                        "path": self._valid_external_plugin_path(item.get("path")),
                        "artwork": normalise_list_art(item.get("artwork")),
                    })
                elif item.get("type") == "provider_list":
                    provider = str(item.get("provider") or "").strip().lower()
                    provider_list_id = str(item.get("provider_list_id") or "").strip()
                    if provider not in ("trakt", "mdblist") or not provider_list_id or len(provider_list_id) > 128:
                        changed = True
                        continue
                    item = {
                        "id": item["id"], "type": "provider_list",
                        "provider": provider, "provider_list_id": provider_list_id,
                        "name": str(item.get("name") or ("Trakt list" if provider == "trakt" else "MDBList list")).strip(),
                        "description": str(item.get("description") or "").strip(),
                        "item_count": max(0, self._safe_int(item.get("item_count"), 0)),
                        "artwork": normalise_list_art(item.get("artwork")),
                    }
                else:
                    changed = True
                    continue
                entries.append(item)
            normalised["entries"] = entries
            if normalised != folder:
                changed = True
            folders.append(normalised)
        self.state["widget_folders"] = folders

        default_regen = self._bool_setting("auto_update", False)
        default_regen_interval = self._setting_int("auto_update_interval_hours", 24, 1, 720)
        default_trakt_refresh = self._bool_setting("trakt_auto_refresh_default", False)
        default_trakt_interval = self._setting_int("trakt_auto_refresh_interval_hours", 24, 1, 720)

        for record in records:
            if not isinstance(record, dict):
                continue
            method = str(record.get("generation_method") or "ai").strip().lower()
            if method not in ("ai", "keyword"):
                method = "ai"
            if record.get("generation_method") != method:
                record["generation_method"] = method
                changed = True
            if not record.get("local_id"):
                record["local_id"] = uuid.uuid4().hex
                changed = True
            if "sync_to_trakt" not in record:
                record["sync_to_trakt"] = bool(record.get("trakt_id"))
                changed = True
            if "movies" not in record:
                record["movies"] = []
                changed = True
            if "description" not in record:
                record["description"] = ""
                changed = True
            content_type = str(record.get("content_type") or "movies").lower()
            if content_type not in ("movies", "shows", "both"):
                content_type = "movies"
            if record.get("content_type") != content_type:
                record["content_type"] = content_type
                changed = True
            normalised_art = normalise_list_art(record.get("artwork"))
            if record.get("artwork") != normalised_art:
                record["artwork"] = normalised_art
                changed = True
            if "local_changed_at" not in record:
                record["local_changed_at"] = self._safe_int(record.get("updated_at"), 0)
                changed = True

            # Preserve refresh behaviour from installations using the combined schedule.
            if "regeneration_enabled" not in record:
                record["regeneration_enabled"] = bool(record.get("auto_refresh_enabled", default_regen))
                changed = True
            if "regeneration_interval_hours" not in record:
                record["regeneration_interval_hours"] = int(
                    record.get("auto_refresh_interval_hours") or default_regen_interval
                )
                changed = True
            if "regeneration_last_attempt_at" not in record:
                record["regeneration_last_attempt_at"] = self._safe_int(record.get("auto_last_attempt_at"), 0)
                changed = True

            # Lists that refreshed and synced together retain both behaviours.
            if "trakt_refresh_enabled" not in record:
                legacy_auto = bool(record.get("auto_refresh_enabled", False))
                inherited = bool(record.get("sync_to_trakt") and legacy_auto)
                record["trakt_refresh_enabled"] = bool(record.get("sync_to_trakt") and (inherited if legacy_auto else default_trakt_refresh))
                changed = True
            if "trakt_refresh_interval_hours" not in record:
                legacy_interval = record.get("auto_refresh_interval_hours") if record.get("sync_to_trakt") else None
                record["trakt_refresh_interval_hours"] = int(legacy_interval or default_trakt_interval)
                changed = True
            if "trakt_refresh_cycle_at" not in record:
                record["trakt_refresh_cycle_at"] = self._safe_int(record.get("trakt_synced_at"), 0)
                changed = True
            if "trakt_last_attempt_at" not in record:
                record["trakt_last_attempt_at"] = 0
                changed = True

        if changed:
            try:
                self._save_state()
            except Exception:
                pass

    @staticmethod
    def _record_key(record):
        if not isinstance(record, dict):
            return ""
        return str(record.get("local_id") or record.get("trakt_id") or "")

    @staticmethod
    def _safe_reference_id(value):
        candidate = str(value or "").strip()
        if candidate and len(candidate) <= 64 and all(ch.isalnum() or ch in ("-", "_") for ch in candidate):
            return candidate
        return uuid.uuid4().hex

    def _public_username(self):
        try:
            return str(self.addon.getSetting("trakt_public_username") or "").strip()
        except Exception:
            return ""

    def _sync_enabled(self):
        """Default Trakt-sync choice for newly created lists."""
        return self._bool_setting("sync_lists_to_trakt", False)

    def _default_regeneration_enabled(self):
        """Default AI-regeneration choice for newly created lists."""
        return self._bool_setting("auto_update", False)

    def _default_regeneration_interval(self):
        return self._setting_int("auto_update_interval_hours", 24, 1, 720)

    def _default_trakt_refresh_enabled(self):
        """Default Trakt-list refresh choice for newly created lists."""
        return self._bool_setting("trakt_auto_refresh_default", False)

    def _default_trakt_refresh_interval(self):
        return self._setting_int("trakt_auto_refresh_interval_hours", 24, 1, 720)


    def _list_storage_label(self, record):
        if not isinstance(record, dict):
            return "Saved in Kodi"
        if record.get("sync_to_trakt"):
            if record.get("trakt_id"):
                return "Kodi + Trakt"
            return "Kodi + Trakt (waiting for first sync)"
        if record.get("trakt_id"):
            return "Kodi only (older Trakt copy kept)"
        return "Kodi only"

    def _has_oauth(self):
        return bool(self.state.get("access_token"))

    def _on_token_update(self, token):
        self.state.update(token)
        self._save_state()

    def _on_ai_usage(self, event):
        if not isinstance(event, dict):
            return
        usage = self.state.setdefault("ai_usage", {})
        usage["requests"] = self._safe_int(usage.get("requests"), 0) + 1
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            usage[key] = self._safe_int(usage.get(key), 0) + self._safe_int(event.get(key), 0)

        kind = str(event.get("kind") or "request")
        kinds = usage.setdefault("by_kind", {})
        bucket = kinds.setdefault(kind, {})
        bucket["requests"] = self._safe_int(bucket.get("requests"), 0) + 1
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
            bucket[key] = self._safe_int(bucket.get(key), 0) + self._safe_int(event.get(key), 0)

        provider = str(event.get("provider") or getattr(self.ai, "provider_id", "ai"))
        providers = usage.setdefault("by_provider", {})
        provider_bucket = providers.setdefault(provider, {})
        provider_bucket["provider_name"] = str(event.get("provider_name") or getattr(self.ai, "provider_name", provider))
        provider_bucket["requests"] = self._safe_int(provider_bucket.get("requests"), 0) + 1
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
            provider_bucket[key] = self._safe_int(provider_bucket.get(key), 0) + self._safe_int(event.get(key), 0)

        usage["last_provider"] = provider
        usage["last_provider_name"] = str(event.get("provider_name") or getattr(self.ai, "provider_name", provider))
        usage["last_model"] = str(event.get("model") or getattr(self.ai, "model", ""))
        usage["last_request_at"] = int(time.time())
        self._save_state()
        self._update_ai_status_rows()

    def _set_ai_setting(self, setting_id, text):
        try:
            self.addon.setSetting(setting_id, text)
        except Exception as exc:
            xbmc.log(
                "curatr could not update %s: %s" % (setting_id, exc),
                xbmc.LOGWARNING,
            )

    def _update_ai_status_rows(self):
        provider_text = "%s: %s" % (
            getattr(self.ai, "provider_name", "AI"),
            getattr(self.ai, "model", "") or "No model selected",
        )
        self._set_ai_setting("ai_provider_status", provider_text)

        fingerprint = self.state.get("taste_fingerprint") or {}
        if fingerprint.get("summary"):
            count = self._safe_int(fingerprint.get("source_rating_count"), 0)
            profile = self.state.get("profile") or {}
            source_names = [
                "Kodi" if value == "kodi" else "Trakt"
                for value in profile.get("sources", []) if value in ("kodi", "trakt")
            ]
            source_suffix = (" from " + " + ".join(source_names)) if source_names else ""
            text = "Ready" + ((": %d ratings%s" % (count, source_suffix)) if count else source_suffix)
            if self._taste_fingerprint_is_stale():
                text += " (refresh due)"
        else:
            text = "Not built yet"
        self._set_ai_setting("taste_fingerprint_status", text)

        usage = self.state.get("ai_usage") or {}
        requests = self._safe_int(usage.get("requests"), 0)
        total = self._safe_int(usage.get("total_tokens"), 0)
        if requests:
            usage_text = "%d request%s: %s tokens" % (
                requests,
                "" if requests == 1 else "s",
                self._format_int(total),
            )
        else:
            usage_text = "No AI API requests recorded yet"
        self._set_ai_setting("ai_usage_status", usage_text)

    @staticmethod
    def _format_int(value):
        try:
            return format(int(value), ",d")
        except (TypeError, ValueError):
            return "0"

    def _set_trakt_status(self, text):
        # This is a disabled/read-only setting in resources/settings.xml.
        # Persisting it lets Kodi display the last verified account even when
        # Settings is opened directly from Kodi's addon information screen.
        try:
            self.addon.setSetting("trakt_status", text)
        except Exception as exc:
            xbmc.log("curatr could not update Trakt status setting: %s" % exc, xbmc.LOGWARNING)

    def _bool_setting(self, setting_id, default=False):
        try:
            raw = str(self.addon.getSetting(setting_id) or "").strip()
        except Exception:
            raw = ""
        if not raw:
            return bool(default)
        try:
            return bool(self.addon.getSettingBool(setting_id))
        except Exception:
            return raw.lower() in ("true", "1", "yes", "on")

    def _notify(self, message, level="info", background=False, force=False):
        if not force and not self._bool_setting("notifications_enabled", True):
            return
        if background and not self._bool_setting("notify_background_updates", True):
            return
        if level == "error" and not self._bool_setting("notify_errors", True):
            return
        icon = xbmcgui.NOTIFICATION_INFO
        if level == "warning":
            icon = xbmcgui.NOTIFICATION_WARNING
        elif level == "error":
            icon = getattr(xbmcgui, "NOTIFICATION_ERROR", xbmcgui.NOTIFICATION_WARNING)
        duration = self._setting_int("notification_duration", 5, 2, 15) * 1000
        xbmcgui.Dialog().notification(self.name, str(message), icon, duration)

    def record_activity(self, message, level="info", detail="", notify=False, background=False):
        event = {
            "timestamp": int(time.time()),
            "message": str(message),
            "level": str(level or "info"),
        }
        if detail:
            event["detail"] = str(detail)
        history = self.state.setdefault("activity", [])
        history.append(event)
        self.state["activity"] = history[-50:]
        self._save_state()
        if notify:
            self._notify(message, level=level, background=background)
        return event

    def report_error(self, message, detail="", background=False):
        return self.record_activity(
            message, level="error", detail=detail, notify=True, background=background
        )

    def show_activity(self):
        history = list(self.state.get("activity") or [])
        if not history:
            xbmcgui.Dialog().textviewer("curatr Activity", "No activity has been recorded yet.")
            return
        rows = []
        for event in reversed(history[-50:]):
            try:
                stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(event.get("timestamp") or 0)))
            except Exception:
                stamp = "Unknown time"
            level = str(event.get("level") or "info").upper()
            text = "[%s] %s: %s" % (level, stamp, event.get("message") or "Activity")
            detail = str(event.get("detail") or "").strip()
            if detail:
                text += "\n" + detail
            rows.append(text)
        xbmcgui.Dialog().textviewer("curatr Activity", "\n\n".join(rows))

    def refresh_trakt_status(self, silent=True):
        if not self._has_oauth():
            public_username = self._public_username()
            if public_username:
                self._set_trakt_status("Public profile: %s (read-only)" % public_username)
                if not silent:
                    self._notify("Using public Trakt profile %s" % public_username)
                return public_username
            self._set_trakt_status("Not connected. Kodi lists still work")
            if not silent:
                self._notify("Trakt is not connected", level="warning")
            return None

        try:
            profile = self.trakt.profile()
            username = ""
            if isinstance(profile, dict):
                username = str(profile.get("username") or "").strip()
            if not username:
                username = "Unknown user"
            self.state["trakt_username"] = username
            self.state["trakt_status_checked_at"] = int(time.time())
            self._save_state()
            self._set_trakt_status("Connected as %s" % username)
            if not silent:
                self._notify("Trakt connected as %s" % username)
            return username
        except TraktError as exc:
            previous = str(self.state.get("trakt_username") or "").strip()
            public_username = self._public_username()
            if exc.status_code in (400, 401):
                if public_username:
                    self._set_trakt_status("Public profile: %s (OAuth unavailable)" % public_username)
                else:
                    self._set_trakt_status("Re-link required" + ((": %s" % previous) if previous else ""))
            elif previous:
                self._set_trakt_status("Connected as %s" % previous)
            else:
                self._set_trakt_status("Status unavailable")
            if not silent:
                self._notify("Could not refresh Trakt status", level="warning")
            return public_username or previous or None

    def open_settings(self):
        # Avoid unnecessary network traffic merely to open Settings.  Display
        # the last verified OAuth user or the configured public-profile mode.
        if self._has_oauth():
            cached = str(self.state.get("trakt_username") or "").strip()
            self._set_trakt_status("Connected as %s" % (cached or "linked account"))
        elif self._public_username():
            self._set_trakt_status("Public profile: %s (read-only)" % self._public_username())
        else:
            self._set_trakt_status("Not connected. Kodi lists still work")
        self._update_ai_status_rows()
        self._update_mdblist_status()
        self.addon.openSettings()

    def _update_mdblist_status(self):
        selected = [row for row in self.state.get("mdblist_selected_lists", []) if isinstance(row, dict)]
        if selected:
            text = "%d account list%s selected" % (len(selected), "" if len(selected) == 1 else "s")
        elif str(self.addon.getSetting("mdblist_list_url") or "").strip():
            text = "Using one public list link"
        else:
            text = "No lists selected"
        self._set_ai_setting("mdblist_selection_status", text)

    def choose_mdblist_lists_interactive(self):
        if not self.mdblist or not self.mdblist.api_key:
            raise RuntimeError("Connect MDBList in Settings to perform this action.")
        lists = self.mdblist.user_lists()
        if not lists:
            raise RuntimeError("No movie lists were found in that MDBList account.")
        existing = {
            str(row.get("id")) for row in self.state.get("mdblist_selected_lists", [])
            if isinstance(row, dict) and row.get("id") not in (None, "")
        }
        preselect = [index for index, row in enumerate(lists) if str(row.get("id")) in existing]
        labels = [
            "%s%s" % (
                row.get("name") or "MDBList list",
                " (%s films)" % row.get("items") if row.get("items") not in (None, "") else "",
            ) for row in lists
        ]
        choices = xbmcgui.Dialog().multiselect("Choose MDBList movie lists", labels, preselect=preselect)
        if choices is None:
            return None
        if len(choices) > 8:
            xbmcgui.Dialog().ok(self.name, "Choose up to 8 MDBList lists. This keeps account requests and AI input efficient.")
            return None
        selected = [{"id": lists[index]["id"], "name": lists[index]["name"]} for index in choices]
        self.state["mdblist_selected_lists"] = selected
        # A changed selection must not reuse stale combined-list cache entries.
        self.state["catalogue_cache"] = {
            key: value for key, value in (self.state.get("catalogue_cache") or {}).items()
            if not str(key).startswith("mdblist-account:")
        }
        self._save_state()
        self._update_mdblist_status()
        xbmcgui.Dialog().ok(self.name, "%d MDBList movie list%s selected." % (len(selected), "" if len(selected) == 1 else "s"))
        return selected

    def test_tmdb_interactive(self):
        if not self.tmdb:
            raise RuntimeError("Turn on TMDB enrichment and enter your API credential first.")
        if self.tmdb.test():
            xbmcgui.Dialog().ok(self.name, "TMDB connected successfully. Future recommendations can use verified catalogue candidates.")
            return True
        raise RuntimeError("TMDB could not be verified.")

    def test_mdblist_interactive(self):
        if not self.mdblist:
            raise RuntimeError("Connect MDBList in Settings to perform this action.")
        selected = [row for row in self.state.get("mdblist_selected_lists", []) if isinstance(row, dict)]
        list_url = str(self.addon.getSetting("mdblist_list_url") or "").strip()
        if selected:
            rows = self.mdblist.fetch_list_id(selected[0].get("id"), limit=1)
            if rows:
                xbmcgui.Dialog().ok(self.name, "MDBList connected successfully and your selected account lists can be read.")
                return True
        elif list_url and self.mdblist.test(list_url):
            xbmcgui.Dialog().ok(self.name, "MDBList connected successfully and the public list can be read.")
            return True
        raise RuntimeError("Choose account lists or enter a public MDBList list link first.")

    def import_api_key_interactive(self, target="ai"):
        """Import one credential from a user-selected local text file."""
        targets = {
            "openai": ("OpenAI", "openai_api_key"),
            "gemini": ("Gemini", "gemini_api_key"),
            "anthropic": ("Claude", "anthropic_api_key"),
            "openrouter": ("OpenRouter", "openrouter_api_key"),
            "compatible": ("Compatible AI service", "compatible_api_key"),
            "tmdb": ("TMDB", "tmdb_api_key"),
            "mdblist": ("MDBList", "mdblist_api_key"),
        }
        selected = str(target or "ai").strip().lower()
        if selected == "ai":
            providers = ["openai", "gemini", "anthropic", "openrouter", "compatible"]
            current = str(self.addon.getSetting("ai_provider") or "openai").strip().lower()
            preselect = providers.index(current) if current in providers else 0
            choice = xbmcgui.Dialog().select(
                "Import API key for",
                [targets[key][0] for key in providers],
                preselect=preselect,
            )
            if choice < 0:
                return False
            selected = providers[choice]
        if selected not in targets:
            raise RuntimeError("That API-key destination is not supported.")

        service_name, setting_id = targets[selected]
        path = xbmcgui.Dialog().browseSingle(
            1,
            "Choose %s key file" % service_name,
            "files",
            ".txt|.key",
        )
        if not path:
            return False

        handle = None
        try:
            handle = xbmcvfs.File(path, "r")
            raw = handle.read(16385)
        except Exception as exc:
            raise RuntimeError("The selected key file could not be read: %s" % exc)
        finally:
            if handle:
                handle.close()
        if len(raw) > 16384:
            raise RuntimeError("That file is too large. Choose a small text file containing only the API key.")

        lines = [line.strip() for line in str(raw or "").lstrip("\ufeff").splitlines() if line.strip()]
        if len(lines) != 1:
            raise RuntimeError("The file must contain only one API key on a single line.")
        key = lines[0].strip().strip('"').strip("'").strip()
        if selected == "tmdb" and key.lower().startswith("bearer "):
            key = key[7:].strip()
        if len(key) < 8 or len(key) > 4096 or any(character.isspace() for character in key):
            raise RuntimeError("The file does not appear to contain a valid API key.")

        existing = str(self.addon.getSetting(setting_id) or "").strip()
        if existing and existing != key and not xbmcgui.Dialog().yesno(
            self.name,
            "%s already has a saved key. Replace it with the key from this file?" % service_name,
        ):
            return False

        self.addon.setSetting(setting_id, key)
        if selected in ("openai", "gemini", "anthropic", "openrouter", "compatible"):
            self.addon.setSetting("ai_provider", selected)
        elif selected == "tmdb":
            self.addon.setSetting("tmdb_enabled", "true")
        elif selected == "mdblist":
            self.addon.setSetting("mdblist_enabled", "true")

        deleted = False
        if xbmcgui.Dialog().yesno(
            self.name,
            "%s key imported and stored locally by Kodi.\n\nDelete the original key file now?" % service_name,
            nolabel="Keep File",
            yeslabel="Delete File",
        ):
            deleted = bool(xbmcvfs.delete(path))
            if not deleted:
                xbmcgui.Dialog().ok(
                    self.name,
                    "The key was imported, but Kodi could not delete the original file. Delete it manually when convenient.",
                )
        if not deleted:
            xbmcgui.Dialog().notification(
                self.name,
                "%s key imported: remember the source file contains your key" % service_name,
                xbmcgui.NOTIFICATION_INFO,
                5000,
            )
        else:
            xbmcgui.Dialog().notification(
                self.name,
                "%s key imported" % service_name,
                xbmcgui.NOTIFICATION_INFO,
                3500,
            )
        return True

    def _catalogue_cache_get(self, key, max_age):
        cache = self.state.get("catalogue_cache") or {}
        row = cache.get(str(key)) if isinstance(cache, dict) else None
        if not isinstance(row, dict):
            return None
        if time.time() - self._safe_int(row.get("cached_at"), 0) > max_age:
            return None
        items = row.get("items")
        return items if isinstance(items, list) else None

    def _catalogue_cache_put(self, key, items):
        cache = self.state.setdefault("catalogue_cache", {})
        cache[str(key)] = {"cached_at": int(time.time()), "items": list(items or [])[:100]}
        if len(cache) > 12:
            ordered = sorted(cache.items(), key=lambda pair: self._safe_int((pair[1] or {}).get("cached_at"), 0), reverse=True)
            self.state["catalogue_cache"] = dict(ordered[:8])
        self._save_state()

    def _grounded_candidate_pool(self, fingerprint):
        """Return compact optional candidates; provider failures never block AI generation."""
        combined = []
        seen = set()

        if self.tmdb and self.tmdb.api_key:
            references = [row for row in (fingerprint or {}).get("representative_likes", []) if isinstance(row, dict)][:2]
            cache_key = "tmdb:" + hashlib.sha256(json.dumps(references, sort_keys=True).encode("utf-8")).hexdigest()[:16]
            rows = self._catalogue_cache_get(cache_key, 24 * 3600)
            if rows is None:
                try:
                    rows = self.tmdb.recommendation_pool(references, limit=40)
                    self._catalogue_cache_put(cache_key, rows)
                except CatalogueError as exc:
                    rows = []
                    xbmc.log("curatr TMDB grounding skipped: %s" % exc, xbmc.LOGWARNING)
            for row in rows:
                marker = (str(row.get("title") or "").casefold(), self._safe_int(row.get("year"), 0))
                if marker[0] and marker not in seen:
                    seen.add(marker); combined.append(row)

        if self.mdblist:
            selected_lists = [
                row for row in self.state.get("mdblist_selected_lists", [])
                if isinstance(row, dict) and str(row.get("id") or "").isdigit()
            ][:8]
            if selected_lists and self.mdblist.api_key:
                selected_ids = sorted(str(row.get("id")) for row in selected_lists)
                signature = json.dumps(selected_ids, separators=(",", ":"))
                cache_key = "mdblist-account:" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
                rows = self._catalogue_cache_get(cache_key, 6 * 3600)
                if rows is None:
                    rows = []
                    account_seen = set()
                    for selected in selected_lists:
                        try:
                            fetched = self.mdblist.fetch_list_id(selected.get("id"), limit=60)
                        except CatalogueError as exc:
                            xbmc.log("curatr MDBList account list skipped: %s" % exc, xbmc.LOGWARNING)
                            continue
                        for row in fetched:
                            marker = (str(row.get("title") or "").casefold(), self._safe_int(row.get("year"), 0))
                            if marker[0] and marker not in account_seen:
                                account_seen.add(marker)
                                rows.append(row)
                                if len(rows) >= 60:
                                    break
                        if len(rows) >= 60:
                            break
                    if rows:
                        self._catalogue_cache_put(cache_key, rows)
                for row in rows or []:
                    marker = (str(row.get("title") or "").casefold(), self._safe_int(row.get("year"), 0))
                    if marker[0] and marker not in seen:
                        seen.add(marker); combined.append(row)
                        if len(combined) >= 60:
                            break
            list_url = str(self.addon.getSetting("mdblist_list_url") or "").strip()
            if list_url and len(combined) < 60:
                cache_key = "mdblist:" + hashlib.sha256(list_url.encode("utf-8")).hexdigest()[:16]
                rows = self._catalogue_cache_get(cache_key, 6 * 3600)
                if rows is None:
                    try:
                        rows = self.mdblist.fetch_list(list_url, limit=60)
                        self._catalogue_cache_put(cache_key, rows)
                    except CatalogueError as exc:
                        rows = []
                        xbmc.log("curatr MDBList grounding skipped: %s" % exc, xbmc.LOGWARNING)
                for row in rows:
                    marker = (str(row.get("title") or "").casefold(), self._safe_int(row.get("year"), 0))
                    if marker[0] and marker not in seen:
                        seen.add(marker); combined.append(row)
                        if len(combined) >= 60:
                            break
        return combined[:60]

    # ---------- Trakt authentication/profile ----------

    def authenticate_trakt(self):
        device = self.trakt.device_code()
        url = device.get("verification_url") or "https://auth.trakt.tv/activate"
        code = device.get("user_code", "")
        device_code = device.get("device_code")
        if not device_code:
            raise RuntimeError("Trakt did not return a device authorization code.")

        expires_in = max(1, int(device.get("expires_in") or 600))
        interval = max(5, int(device.get("interval") or 5))
        deadline = time.time() + expires_in
        monitor = xbmc.Monitor()

        addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        qr_path = os.path.join(addon_path, "resources", "media", "trakt-activate-qr.png")
        backdrop_path = os.path.join(addon_path, "resources", "media", "pixel.png")
        auth_window = None

        try:
            auth_window = TraktAuthWindow(qr_path, backdrop_path, code, url, expires_in)
            auth_window.show()
        except Exception as exc:
            xbmc.log("curatr QR window fallback: %s" % exc, xbmc.LOGWARNING)
            auth_window = None
            xbmcgui.Dialog().ok(
                "Link Trakt",
                "Open this on your phone/computer:\n%s\n\nEnter code:\n%s" % (url, code),
            )

        progress = None
        if auth_window is None:
            progress = xbmcgui.DialogProgress()
            progress.create("Link Trakt", "Waiting for Trakt authorization…")

        try:
            while time.time() < deadline:
                if auth_window is not None and auth_window.cancelled:
                    return
                if progress is not None and progress.iscanceled():
                    return

                seconds_left = max(0, int(deadline - time.time()))
                if auth_window is not None:
                    auth_window.update_waiting(seconds_left)
                elif progress is not None:
                    elapsed = expires_in - seconds_left
                    progress.update(
                        min(99, int((elapsed * 100) / expires_in)),
                        "Visit %s and enter %s" % (url, code),
                    )

                if monitor.waitForAbort(interval):
                    return
                try:
                    token = self.trakt.device_token(device_code)
                    if token.get("access_token"):
                        username = self.refresh_trakt_status(silent=True)
                        if auth_window is not None:
                            auth_window.set_success()
                            monitor.waitForAbort(1)
                        message = "Trakt linked"
                        if username:
                            message = "Trakt linked as %s" % username
                        self.record_activity(message, notify=True)
                        return
                except TraktError as exc:
                    if exc.status_code == 400:
                        continue
                    if exc.status_code == 429:
                        interval += 5
                        continue
                    if exc.status_code == 418:
                        raise RuntimeError("Trakt authorization was denied.")
                    if exc.status_code in (404, 409, 410):
                        raise RuntimeError("Trakt authorization expired or is no longer valid. Start linking again.")
                    raise
        finally:
            if auth_window is not None:
                auth_window.close()
            if progress is not None:
                progress.close()

        xbmcgui.Dialog().ok("Link Trakt", "Authorization timed out. Try again.")

    def _cached_directors(self, trakt_id):
        key = str(trakt_id or "")
        cache = self.state.get("director_cache") or {}
        row = cache.get(key) if isinstance(cache, dict) else None
        if not isinstance(row, dict):
            return None
        cached_at = self._safe_int(row.get("cached_at"), 0)
        if not cached_at or time.time() - cached_at > self.DIRECTOR_CACHE_MAX_AGE_SECONDS:
            return None
        names = row.get("names")
        if not isinstance(names, list):
            return None
        return [str(name).strip() for name in names if str(name).strip()]

    def _cache_directors(self, trakt_id, names):
        key = str(trakt_id or "")
        if not key:
            return
        cache = self.state.setdefault("director_cache", {})
        cleaned = []
        seen = set()
        for name in names or []:
            text = str(name or "").strip()
            marker = text.casefold()
            if text and marker not in seen:
                seen.add(marker)
                cleaned.append(text)
        cache[key] = {"names": cleaned[:20], "cached_at": int(time.time())}
        if len(cache) > self.DIRECTOR_CACHE_MAX_ITEMS:
            ordered = sorted(
                cache.items(),
                key=lambda kv: self._safe_int((kv[1] or {}).get("cached_at"), 0),
                reverse=True,
            )
            self.state["director_cache"] = dict(ordered[: self.DIRECTOR_CACHE_MAX_ITEMS])

    def _cached_actors(self, trakt_id):
        key = str(trakt_id or "")
        cache = self.state.get("actor_cache") or {}
        row = cache.get(key) if isinstance(cache, dict) else None
        if not isinstance(row, dict):
            return None
        cached_at = self._safe_int(row.get("cached_at"), 0)
        if not cached_at or time.time() - cached_at > self.DIRECTOR_CACHE_MAX_AGE_SECONDS:
            return None
        names = row.get("names")
        if not isinstance(names, list):
            return None
        return [str(name).strip() for name in names if str(name).strip()]

    def _cache_actors(self, trakt_id, names):
        key = str(trakt_id or "")
        if not key:
            return
        cache = self.state.setdefault("actor_cache", {})
        cleaned = []
        seen = set()
        for name in names or []:
            text = str(name or "").strip()
            marker = text.casefold()
            if text and marker not in seen:
                seen.add(marker)
                cleaned.append(text)
        cache[key] = {"names": cleaned[:8], "cached_at": int(time.time())}
        if len(cache) > self.DIRECTOR_CACHE_MAX_ITEMS:
            ordered = sorted(
                cache.items(),
                key=lambda kv: self._safe_int((kv[1] or {}).get("cached_at"), 0),
                reverse=True,
            )
            self.state["actor_cache"] = dict(ordered[: self.DIRECTOR_CACHE_MAX_ITEMS])

    def _preference_history_mode(self):
        value = str(self.addon.getSetting("preference_history_source") or "both").strip().lower()
        return value if value in ("both", "kodi", "trakt") else "both"

    def _trakt_preference_available(self):
        return bool(self._has_oauth() or self._public_username())

    def _trakt_preference_movies(self, limit):
        source = "oauth" if self._has_oauth() else "public"
        username = str(self.state.get("trakt_username") or "").strip()
        if source == "oauth":
            self.trakt.ensure_access_token()
            ratings = self.trakt.ratings_movies(limit)
            watched = self.trakt.watched_movies(limit)
            if not username:
                try:
                    username = str((self.trakt.profile() or {}).get("username") or "").strip()
                except Exception:
                    username = ""
        else:
            username = self._public_username()
            ratings = self.trakt.ratings_movies_for_user(username, limit)
            try:
                watched = self.trakt.watched_movies_for_user(username, limit)
            except TraktError as exc:
                watched = []
                xbmc.log("curatr public watched history unavailable: %s" % exc, xbmc.LOGWARNING)

        movies = []
        for row in ratings:
            movie = row.get("movie", {}) if isinstance(row, dict) else {}
            ids = (movie.get("ids") or {}) if isinstance(movie, dict) else {}
            movies.append({
                "title": movie.get("title"), "year": movie.get("year"),
                "rating": row.get("rating") if isinstance(row, dict) else None,
                "playcount": 0, "last_watched_at": "", "ids": dict(ids),
                "directors": [], "genres": [], "source": "trakt",
            })
        for row in watched:
            movie = row.get("movie", {}) if isinstance(row, dict) else {}
            ids = (movie.get("ids") or {}) if isinstance(movie, dict) else {}
            movies.append({
                "title": movie.get("title"), "year": movie.get("year"), "rating": None,
                "playcount": max(1, self._safe_int(row.get("plays"), 1)),
                "last_watched_at": str(row.get("last_watched_at") or ""), "ids": dict(ids),
                "directors": [], "genres": [], "source": "trakt",
            })
        return movies, source, username

    def _trakt_show_history(self, limit, source, username):
        if source == "oauth":
            ratings = self.trakt.ratings_shows(limit)
            watched = self.trakt.watched_shows(limit)
        else:
            ratings = self.trakt.ratings_shows_for_user(username, limit)
            try:
                watched = self.trakt.watched_shows_for_user(username, limit)
            except TraktError:
                watched = []
        rating_rows, watched_rows = [], []
        for row in ratings:
            show = row.get("show", {}) if isinstance(row, dict) else {}
            if not isinstance(show, dict) or not show.get("title"):
                continue
            rating_rows.append({
                "title": show.get("title"), "year": show.get("year"),
                "rating": row.get("rating"), "ids": dict(show.get("ids") or {}),
                "media_type": "show",
            })
        for row in watched:
            show = row.get("show", {}) if isinstance(row, dict) else {}
            if not isinstance(show, dict) or not show.get("title"):
                continue
            watched_rows.append({
                "title": show.get("title"), "year": show.get("year"),
                "playcount": max(1, self._safe_int(row.get("plays"), 1)),
                "last_watched_at": str(row.get("last_watched_at") or ""),
                "ids": dict(show.get("ids") or {}), "media_type": "show",
            })
        return rating_rows, watched_rows

    def _preference_identity_tokens(self, movie):
        tokens = []
        ids = (movie.get("ids") or {}) if isinstance(movie, dict) else {}
        for kind in ("tmdb", "imdb", "trakt"):
            value = ids.get(kind) if isinstance(ids, dict) else None
            if value not in (None, ""):
                tokens.append("%s:%s" % (kind, str(value).strip().casefold()))
        title = self._normalise_title(movie.get("title"))
        year = self._safe_int(movie.get("year"), 0)
        if title:
            tokens.append("title:%s:%s" % (title, year))
        return tokens

    def _merge_preference_movies(self, movies, liked_min):
        merged = []
        token_indexes = {}
        for source_movie in movies:
            if not isinstance(source_movie, dict) or not str(source_movie.get("title") or "").strip():
                continue
            tokens = self._preference_identity_tokens(source_movie)
            index = next((token_indexes[token] for token in tokens if token in token_indexes), None)
            if index is None:
                index = len(merged)
                merged.append({
                    "title": str(source_movie.get("title") or "").strip(),
                    "year": self._safe_int(source_movie.get("year"), 0),
                    "ids": {}, "source_ratings": {}, "sources": [], "playcount": 0,
                    "last_watched_at": "", "directors": [], "genres": [],
                    "kodi_id": self._safe_int(source_movie.get("kodi_id"), 0),
                })
            target = merged[index]
            source = str(source_movie.get("source") or "unknown").strip().lower()
            if source not in target["sources"]:
                target["sources"].append(source)
            if not target.get("kodi_id") and self._safe_int(source_movie.get("kodi_id"), 0):
                target["kodi_id"] = self._safe_int(source_movie.get("kodi_id"), 0)
            rating = source_movie.get("rating")
            if rating is not None:
                rating = max(1, min(10, self._safe_int(rating, 0)))
                if rating:
                    target["source_ratings"][source] = rating
            ids = source_movie.get("ids") or {}
            if isinstance(ids, dict):
                for kind in ("tmdb", "imdb", "trakt"):
                    if ids.get(kind) not in (None, ""):
                        target["ids"][kind] = ids.get(kind)
            target["playcount"] = max(target["playcount"], self._safe_int(source_movie.get("playcount"), 0))
            target["last_watched_at"] = max(
                str(target.get("last_watched_at") or ""), str(source_movie.get("last_watched_at") or "")
            )
            for field in ("directors", "genres"):
                values = list(target[field])
                seen = {str(value).casefold() for value in values}
                for value in source_movie.get(field) or []:
                    text = str(value or "").strip()
                    if text and text.casefold() not in seen:
                        seen.add(text.casefold())
                        values.append(text)
                target[field] = values[:20]
            for token in self._preference_identity_tokens(target):
                token_indexes[token] = index
            for token in tokens:
                token_indexes[token] = index

        ratings = []
        watched = []
        library_items = []
        conflicts = 0
        for movie in merged:
            source_ratings = movie.pop("source_ratings", {})
            values = list(source_ratings.values())
            conflict = len(values) > 1 and max(values) - min(values) >= 4
            if conflict:
                combined_rating = None
                confidence = "conflicting"
                conflicts += 1
            elif values:
                combined_rating = int((sum(values) / float(len(values))) + 0.5)
                confidence = "reduced" if len(values) > 1 and max(values) - min(values) == 3 else "normal"
            else:
                combined_rating = None
                confidence = "none"
            base = {
                "title": movie.get("title"), "year": movie.get("year"),
                "trakt_id": (movie.get("ids") or {}).get("trakt"),
                "tmdb_id": (movie.get("ids") or {}).get("tmdb"),
                "imdb_id": (movie.get("ids") or {}).get("imdb"),
                "kodi_id": movie.get("kodi_id"), "sources": movie.get("sources"),
                "directors": movie.get("directors"), "genres": movie.get("genres"),
            }
            if source_ratings:
                rated = dict(base)
                rated.update({
                    "rating": combined_rating, "source_ratings": source_ratings,
                    "rating_conflict": conflict, "rating_confidence": confidence,
                })
                ratings.append(rated)
            if movie.get("playcount") or movie.get("last_watched_at"):
                seen_row = dict(base)
                seen_row.update({
                    "playcount": max(1, self._safe_int(movie.get("playcount"), 1)),
                    "last_watched_at": movie.get("last_watched_at"),
                })
                watched.append(seen_row)
            if "kodi" in (movie.get("sources") or []):
                library_items.append(dict(base))

        strong_likes = sorted(
            [row for row in ratings if self._safe_int(row.get("rating"), 0) >= liked_min],
            key=lambda row: self._safe_int(row.get("rating"), 0), reverse=True,
        )
        return ratings, watched, library_items[:100], strong_likes, conflicts, len(merged)

    def sync_profile(self, silent=False):
        mode = self._preference_history_mode()
        limit = self._profile_limit()
        liked_min = self._setting_int("liked_rating_min", 8, 6, 10)
        source_movies = []
        sources_used = []
        failures = []
        username = str(self.state.get("trakt_username") or "").strip()
        trakt_source = ""
        kodi_total = 0
        show_ratings = []
        shows_watched = []

        if mode in ("both", "kodi"):
            try:
                library = KodiLibraryReader(limit=limit).movies()
                kodi_movies = library.get("movies") or []
                kodi_total = self._safe_int(library.get("total"), len(kodi_movies))
                source_movies.extend(kodi_movies)
                if kodi_movies:
                    sources_used.append("kodi")
            except KodiLibraryError as exc:
                failures.append("Kodi Library: %s" % exc)

        if mode in ("both", "trakt") and self._trakt_preference_available():
            try:
                trakt_movies, trakt_source, username = self._trakt_preference_movies(limit)
                source_movies.extend(trakt_movies)
                try:
                    show_ratings, shows_watched = self._trakt_show_history(limit, trakt_source, username)
                except Exception as exc:
                    xbmc.log("curatr Trakt TV history skipped: %s" % exc, xbmc.LOGWARNING)
                if trakt_movies:
                    sources_used.append("trakt")
            except Exception as exc:
                failures.append("Trakt: %s" % exc)
        elif mode == "trakt":
            failures.append("Trakt is not connected and no public username is configured")

        if not source_movies and failures:
            raise RuntimeError(failures[0])

        (
            rating_rows, watched_rows, library_rows, strong_likes,
            conflicts, unique_movies,
        ) = self._merge_preference_movies(source_movies, liked_min)
        director_stats = {}
        actor_stats = {}
        for rated in strong_likes[:10]:
            direct_names = [str(value) for value in rated.get("directors") or [] if str(value).strip()]
            trakt_id = rated.get("trakt_id")
            director_names = direct_names or (self._cached_directors(trakt_id) if trakt_id else [])
            actor_names = self._cached_actors(trakt_id) if trakt_id else []
            if trakt_id and (not director_names or actor_names is None):
                try:
                    people = self.trakt.movie_people(trakt_id)
                except TraktError as exc:
                    xbmc.log("curatr director lookup skipped: %s" % exc, xbmc.LOGDEBUG)
                    if exc.status_code == 429:
                        break
                    continue
                except Exception as exc:
                    xbmc.log("curatr director lookup skipped: %s" % exc, xbmc.LOGDEBUG)
                    continue
                directing = (people.get("crew", {}) or {}).get("directing", []) if isinstance(people, dict) else []
                fetched_directors = []
                seen_in_movie = set()
                for credit in directing:
                    person = credit.get("person", {}) if isinstance(credit, dict) else {}
                    name = str(person.get("name") or "").strip()
                    marker = name.casefold()
                    if not name or marker in seen_in_movie:
                        continue
                    seen_in_movie.add(marker)
                    fetched_directors.append(name)
                if fetched_directors:
                    director_names = fetched_directors
                self._cache_directors(trakt_id, director_names)

                actor_names = []
                seen_cast = set()
                cast = people.get("cast", []) if isinstance(people, dict) else []
                for credit in cast[:8] if isinstance(cast, list) else []:
                    person = credit.get("person", {}) if isinstance(credit, dict) else {}
                    name = str(person.get("name") or "").strip()
                    marker = name.casefold()
                    if not name or marker in seen_cast:
                        continue
                    seen_cast.add(marker)
                    actor_names.append(name)
                self._cache_actors(trakt_id, actor_names)

            for name in director_names:
                stat = director_stats.setdefault(name, {"count": 0, "rating_total": 0})
                stat["count"] += 1
                stat["rating_total"] += self._safe_int(rated.get("rating"), liked_min)
            for name in actor_names or []:
                stat = actor_stats.setdefault(name, {"count": 0, "rating_total": 0})
                stat["count"] += 1
                stat["rating_total"] += self._safe_int(rated.get("rating"), liked_min)

        favourite_directors = []
        for name, stat in director_stats.items():
            count = max(1, stat["count"])
            favourite_directors.append({
                "name": name,
                "liked_movies": count,
                "average_rating": round(float(stat["rating_total"]) / count, 2),
            })
        favourite_directors.sort(
            key=lambda item: (item["liked_movies"], item["average_rating"], item["name"].casefold()),
            reverse=True,
        )

        # Cast is much larger than directing crew, so require repeated evidence
        # before describing an actor as a preference.
        favourite_actors = []
        for name, stat in actor_stats.items():
            count = max(1, stat["count"])
            if count < 2:
                continue
            favourite_actors.append({
                "name": name,
                "liked_movies": count,
                "average_rating": round(float(stat["rating_total"]) / count, 2),
            })
        favourite_actors.sort(
            key=lambda item: (item["liked_movies"], item["average_rating"], item["name"].casefold()),
            reverse=True,
        )

        self.state["profile"] = {
            "ratings": rating_rows,
            "watched": watched_rows,
            "library": library_rows,
            "strong_likes": strong_likes[:100],
            "show_ratings": show_ratings,
            "shows_watched": shows_watched,
            "favourite_directors": favourite_directors[:20],
            "favourite_actors": favourite_actors[:12],
            "liked_rating_threshold": liked_min,
            "source": "+".join(sources_used) or "prompt_only",
            "sources": sources_used,
            "preference_history_mode": mode,
            "username": username,
            "trakt_source": trakt_source,
            "kodi_library_total": kodi_total,
            "unique_movies": unique_movies,
            "conflicting_ratings": conflicts,
            "source_failures": failures[:3],
            "synced_at": int(time.time()),
        }
        self._save_state()

        fingerprint_updated = False
        if not silent and self.ai.api_key:
            try:
                self._ensure_taste_fingerprint(self.state["profile"], force=True)
                fingerprint_updated = True
            except Exception as exc:
                xbmc.log("curatr taste fingerprint refresh failed: %s" % exc, xbmc.LOGWARNING)
                xbmcgui.Dialog().ok(
                    self.name,
                    "Your preferences were refreshed, but curatr could not rebuild the AI summary.\n\n%s"
                    % exc,
                )

        if not silent:
            labels = ["Kodi Library" if value == "kodi" else "Trakt" for value in sources_used]
            message = "%s preferences refreshed" % (" + ".join(labels) if labels else "Prompt-only")
            if conflicts:
                message += ": %d conflicting rating%s ignored" % (conflicts, "" if conflicts == 1 else "s")
            if fingerprint_updated:
                message += " + AI preference summary rebuilt"
            self.record_activity(
                message, level="warning" if failures else "info",
                detail="\n".join(failures), notify=True,
            )
        return self.state["profile"]

    # ---------- List creation/update ----------

    def _format_list_draft_field(self, field, draft):
        values = {
            "name": "Name  •  %s" % (draft.get("name") or "Not set"),
            "description": "Description  •  %s" % (self._shorten_text(draft.get("description"), 56) or "None"),
            "prompt": "Request  •  %s" % (self._shorten_text(draft.get("prompt"), 64) or "Not set"),
            "generation_method": "Creation Method  •  %s" % ("Keyword Matching" if draft.get("generation_method") == "keyword" else "AI"),
            "content_type": "Items  •  %s" % {"movies": "Movies", "shows": "TV Shows", "both": "Movies & TV Shows"}.get(draft.get("content_type"), "Movies"),
            "count": "Number of Items  •  %d" % self._safe_int(draft.get("count"), 20),
            "regeneration_enabled": "Auto Refresh  •  %s" % ("On" if draft.get("regeneration_enabled") else "Off"),
            "regeneration_interval_hours": "Refresh Interval  •  %s" % self._format_interval(draft.get("regeneration_interval_hours") or 24),
            "sync_to_trakt": "Sync to Trakt  •  %s" % ("On" if draft.get("sync_to_trakt") else "Off"),
            "trakt_refresh_schedule": "Auto Sync  •  %s" % (
                self._format_interval(draft.get("trakt_refresh_interval_hours") or 24)
                if draft.get("sync_to_trakt") and draft.get("trakt_refresh_enabled") else "Off"
            ),
        }
        if field == "artwork":
            icon, fanart, _style = list_art_summary({"name": draft.get("name"), "prompt": draft.get("prompt"), "artwork": draft.get("artwork")})
            return "Artwork  •  %s / %s" % (icon, fanart)
        return values.get(field, field.replace("_", " ").title())

    def _edit_list_draft_field(self, field, draft, existing=False):
        if field == "name":
            value = xbmcgui.Dialog().input("List name", defaultt=str(draft.get("name") or ""))
            if value and value.strip():
                draft["name"] = value.strip()
        elif field == "description":
            draft["description"] = str(xbmcgui.Dialog().input("List description (optional)", defaultt=str(draft.get("description") or "")) or "").strip()
        elif field == "artwork":
            draft["artwork"] = self._edit_compact_artwork(
                "List Artwork", draft.get("artwork"), preview_record=draft,
            )
        elif field == "prompt":
            if draft.get("generation_method") == "keyword":
                return self._edit_keyword_list_draft(
                    draft, confirm_label="Save" if existing else "Use Filters",
                ) or draft
            value = xbmcgui.Dialog().input("What are you in the mood for?", defaultt=str(draft.get("prompt") or ""))
            if value and value.strip():
                draft["prompt"] = value.strip()
        elif field == "generation_method":
            selected = xbmcgui.Dialog().select("Creation method", ["AI: best for nuanced requests", "Keyword Matching: no AI request"], preselect=1 if draft.get("generation_method") == "keyword" else 0)
            if selected >= 0:
                draft["generation_method"] = "keyword" if selected == 1 else "ai"
        elif field == "content_type":
            choices = ("movies", "shows", "both")
            selected = xbmcgui.Dialog().select("Items", ["Movies only", "TV Shows only", "Movies & TV Shows"], preselect=choices.index(draft.get("content_type") or "movies"))
            if selected >= 0:
                draft["content_type"] = choices[selected]
        elif field == "count":
            value = xbmcgui.Dialog().numeric(0, "Number of items (5-50)", defaultt=str(draft.get("count") or 20))
            if value:
                try:
                    draft["count"] = max(5, min(50, int(value)))
                except (TypeError, ValueError):
                    xbmcgui.Dialog().ok(self.name, "Enter a number between 5 and 50.")
        elif field == "regeneration_enabled":
            selected = xbmcgui.Dialog().select("Auto Refresh", ["Off", "On"], preselect=1 if draft.get("regeneration_enabled") else 0)
            if selected >= 0:
                draft["regeneration_enabled"] = selected == 1
        elif field == "regeneration_interval_hours":
            hours = self._choose_interval_hours("Refresh Interval", self._safe_int(draft.get("regeneration_interval_hours"), 24))
            if hours is not None:
                draft["regeneration_interval_hours"] = hours
        elif field == "sync_to_trakt":
            selected = xbmcgui.Dialog().select("Sync to Trakt", ["Off", "On"], preselect=1 if draft.get("sync_to_trakt") else 0)
            if selected >= 0:
                draft["sync_to_trakt"] = selected == 1
                if not draft["sync_to_trakt"]:
                    draft["trakt_refresh_enabled"] = False
        elif field == "trakt_refresh_schedule":
            if not draft.get("sync_to_trakt"):
                xbmcgui.Dialog().ok(self.name, "Turn on Sync to Trakt before setting an Auto Sync schedule.")
                return draft
            hours = self._choose_schedule_hours(
                "Auto Sync", bool(draft.get("trakt_refresh_enabled")),
                self._safe_int(draft.get("trakt_refresh_interval_hours"), 24),
            )
            if hours is not None:
                draft["trakt_refresh_enabled"] = bool(hours)
                if hours:
                    draft["trakt_refresh_interval_hours"] = hours
        return draft

    @staticmethod
    def _draft_keyword_rules(draft):
        prompt = str(draft.get("prompt") or "").strip()
        rules = draft.get("keyword_rules")
        if (isinstance(rules, dict)
                and draft.get("_keyword_prompt", prompt) == prompt
                and rules.get("version") == PARSER_VERSION):
            return deepcopy(rules)
        return parse_prompt(prompt)

    def _edit_keyword_list_draft(self, draft, confirm_label="Use Filters", start_editing=True):
        """Edit a private copy; Back/Cancel must not change saved or draft rules."""
        updated = dict(draft)
        prompt = str(draft.get("prompt") or "").strip()
        rules = self._draft_keyword_rules(draft)
        addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        while True:
            decision = confirm_keyword_rules(
                addon_path, prompt, rules, edit_existing=True,
                confirm_label=confirm_label, start_editing=start_editing,
            )
            if decision == "edit":
                value = xbmcgui.Dialog().input("Edit Request", defaultt=prompt)
                if value and value.strip() and value.strip() != prompt:
                    prompt = value.strip()
                    rules = parse_prompt(prompt)
                continue
            if decision != "create":
                return None
            if not rules.get("confidence"):
                xbmcgui.Dialog().ok(self.name, "Add at least one Keyword Matching filter.")
                continue
            if updated.get("content_type") == "shows" and (rules.get("people") or rules.get("reference_movies") or rules.get("collection_query")):
                xbmcgui.Dialog().ok(self.name, "Keyword Matching cannot use named people, collections or references for TV Shows only. Use TV filters or choose AI.")
                continue
            prompt = prompt or format_rules(rules).split("\n", 1)[0]
            updated.update({"prompt": prompt, "keyword_rules": rules,
                            "_keyword_prompt": prompt, "_keyword_confirmed": True})
            return updated

    def _prepare_keyword_list_draft(self, draft, confirm_label):
        prompt = str(draft.get("prompt") or "").strip()
        rules = self._draft_keyword_rules(draft)
        confirmed = (draft.get("_keyword_confirmed")
                     and draft.get("_keyword_prompt") == prompt
                     and rules.get("confidence"))
        unsupported = draft.get("content_type") == "shows" and (
            rules.get("people") or rules.get("reference_movies") or rules.get("collection_query")
        )
        if confirmed and not unsupported:
            return dict(draft, keyword_rules=rules)
        return self._edit_keyword_list_draft(draft, confirm_label, start_editing=False)

    def _simple_list_draft(self, draft):
        prompt = xbmcgui.Dialog().input("What are you in the mood for?", defaultt=str(draft.get("prompt") or ""))
        if not prompt or not prompt.strip():
            return None
        draft["prompt"] = prompt.strip()
        method = xbmcgui.Dialog().select(
            "How should curatr build this list?",
            ["Create with AI: best for nuanced requests", "Create with Keyword Matching: no AI request"],
            preselect=1 if draft.get("generation_method") == "keyword" else 0,
        )
        if method < 0:
            return None
        draft["generation_method"] = "keyword" if method == 1 else "ai"
        content = xbmcgui.Dialog().select(
            "What should this list contain?", ["Movies only", "TV Shows only", "Movies & TV Shows"],
            preselect=("movies", "shows", "both").index(draft.get("content_type") or "movies"),
        )
        if content < 0:
            return None
        draft["content_type"] = ("movies", "shows", "both")[content]
        name = xbmcgui.Dialog().input("Name this list", defaultt=str(draft.get("name") or "My Picks"))
        if not name or not name.strip():
            return None
        draft["name"] = name.strip()
        draft["description"] = str(xbmcgui.Dialog().input("List description (optional)", defaultt=str(draft.get("description") or "")) or "").strip()
        count = xbmcgui.Dialog().numeric(0, "How many items? (5-50)", defaultt=str(draft.get("count") or 20))
        if not count:
            return None
        try:
            draft["count"] = max(5, min(50, int(count)))
        except (TypeError, ValueError):
            xbmcgui.Dialog().ok(self.name, "Enter a number between 5 and 50.")
            return None
        return draft

    def create_list_interactive(self, preset_prompt=None, preset_name=None, preset_count=None, initial=None):
        """Collect list choices in a tabbed window, then create or preview it."""
        draft = dict(initial or {})
        draft.setdefault("name", preset_name or "My Picks")
        draft.setdefault("description", "")
        draft.setdefault("prompt", preset_prompt or "")
        draft.setdefault("generation_method", "ai")
        draft.setdefault("content_type", "movies")
        draft.setdefault("count", max(5, min(50, self._safe_int(preset_count, 20))))
        draft.setdefault("regeneration_enabled", self._default_regeneration_enabled())
        draft.setdefault("regeneration_interval_hours", self._default_regeneration_interval())
        draft.setdefault("sync_to_trakt", bool(self._sync_enabled() and self._has_oauth()))
        draft.setdefault("trakt_refresh_enabled", bool(draft["sync_to_trakt"] and self._default_trakt_refresh_enabled()))
        draft.setdefault("trakt_refresh_interval_hours", self._default_trakt_refresh_interval())
        draft.setdefault("artwork", normalise_list_art({}))
        draft.setdefault("movies", [])
        draft["generation_method"] = "keyword" if draft.get("generation_method") == "keyword" else "ai"
        if draft.get("content_type") not in ("movies", "shows", "both"):
            draft["content_type"] = "movies"
        draft["count"] = max(5, min(50, self._safe_int(draft.get("count"), 20)))
        draft["regeneration_interval_hours"] = max(1, self._safe_int(draft.get("regeneration_interval_hours"), 24))

        while True:
            if self._bool_setting("advanced_list_creation", True):
                action, draft = edit_list_settings(
                    xbmcvfs.translatePath(self.addon.getAddonInfo("path")), draft,
                    self._edit_list_draft_field, self._format_list_draft_field,
                )
            else:
                draft = self._simple_list_draft(draft)
                action = "create" if draft else "cancel"
            if action == "cancel":
                return None
            if action in ("preview", "create"):
                if not draft["name"] or (draft["generation_method"] != "keyword" and not draft["prompt"]):
                    xbmcgui.Dialog().ok(self.name, "Enter a list name and request first.")
                    continue
                if self._managed_record_by_name(draft["name"]):
                    xbmcgui.Dialog().ok(self.name, "A curatr list already uses that name. Choose a different name so the original is not replaced.")
                    continue
                rules = None
                if draft["generation_method"] == "keyword":
                    self._require_keyword_catalogue()
                    edited = self._prepare_keyword_list_draft(
                        draft, "Preview List" if action == "preview" else "Create List",
                    )
                    if edited is None:
                        continue
                    draft = edited
                    rules = draft["keyword_rules"]
                else:
                    self._require_ai()
                preview = action == "preview"
                self._notify("Finding %d items for %s…" % (draft["count"], draft["name"]))
                if draft["generation_method"] == "keyword":
                    record = self._generate_keyword_and_write(
                        draft["name"], draft["prompt"], draft["count"], rules,
                        description=draft["description"], content_type=draft["content_type"],
                        sync_to_trakt=draft["sync_to_trakt"], persist=not preview,
                    )
                else:
                    record = self._generate_and_write(
                        draft["name"], draft["prompt"], draft["count"],
                        description=draft["description"], content_type=draft["content_type"],
                        sync_to_trakt=draft["sync_to_trakt"], persist=not preview,
                    )
                record["regeneration_enabled"] = bool(draft["regeneration_enabled"])
                record["regeneration_interval_hours"] = draft["regeneration_interval_hours"]
                record["artwork"] = normalise_list_art(draft.get("artwork"))
                record["trakt_refresh_enabled"] = bool(draft.get("sync_to_trakt") and draft.get("trakt_refresh_enabled"))
                record["trakt_refresh_interval_hours"] = self._safe_int(draft.get("trakt_refresh_interval_hours"), 24)
                if not preview:
                    self._store_managed_record(record)
                    self._save_state()
                    return record
                draft["movies"] = deepcopy(record.get("movies") or [])
                return {"kind": "list_preview", "created_at": int(time.time()), "draft": draft, "record": record}

    def create_related_list_interactive(self, list_id="", folder_id="", entry_id=""):
        """Create a separate AI list using a compact snapshot of another list as evidence."""
        self._require_ai()
        if list_id:
            source = self._managed_record_by_id(list_id)
            if not source:
                raise RuntimeError("That curatr list no longer exists.")
            source_name = str(source.get("name") or "curatr list")
            movies = [row for row in source.get("movies", []) if isinstance(row, dict)]
        else:
            entry, movies = self.linked_provider_list_movies(folder_id, entry_id, force=False)
            source_name = str(entry.get("name") or "linked list")
        references = []
        seen = set()
        for movie in movies:
            title = str(movie.get("title") or "").strip()
            year = self._safe_int(movie.get("year"), 0)
            marker = (title.casefold(), year)
            if not title or marker in seen:
                continue
            seen.add(marker)
            references.append({"title": title, "year": year, "media_type": str(movie.get("media_type") or "movie")})
            if len(references) >= 30:
                break
        if len(references) < 2:
            raise RuntimeError("That list does not contain enough identifiable items to use as an AI reference.")

        instruction = xbmcgui.Dialog().input(
            "What should curatr find?",
            defaultt="Find more titles like these. Keep the strongest shared qualities without repeating the references.",
        )
        if not instruction or not instruction.strip():
            return None
        instruction = instruction.strip()
        name = xbmcgui.Dialog().input("Name the new list", defaultt="More like %s" % source_name)
        if not name or not name.strip():
            return None
        name = name.strip()
        if self._managed_record_by_name(name):
            xbmcgui.Dialog().ok(self.name, "A curatr list already uses that name. Choose a different name so the original is not replaced.")
            return None
        description = xbmcgui.Dialog().input(
            "List description (optional)", defaultt="More recommendations inspired by %s." % source_name,
        )
        type_choice = xbmcgui.Dialog().select("What should the new list contain?", ["Movies only", "TV Shows only", "Movies & TV Shows"])
        if type_choice < 0:
            return None
        content_type = ("movies", "shows", "both")[type_choice]
        count_text = xbmcgui.Dialog().numeric(0, "How many items? (5-50)", defaultt="20")
        if not count_text or not str(count_text).strip():
            return None
        try:
            count = max(5, min(50, int(count_text)))
        except (TypeError, ValueError):
            xbmcgui.Dialog().ok(self.name, "Enter a number between 5 and 50.")
            return None
        confirmation = (
            "Create '%s' with %d new items using up to %d titles from '%s' as a reference?"
            "\n\nThis makes one AI recommendation request and does not change the original list."
            % (name, count, len(references), source_name)
        )
        try:
            confirmed = xbmcgui.Dialog().yesno(
                self.name, confirmation, nolabel="Cancel", yeslabel="Create List",
            )
        except TypeError:
            # Retain compatibility with Kodi Python bindings/skins that expose
            # the older positional-only yes/no signature.
            confirmed = xbmcgui.Dialog().yesno(self.name, confirmation)
        if not confirmed:
            return None
        self._notify("Finding %d related items…" % count)
        return self._generate_and_write(
            name, instruction, count, description=str(description or "").strip(),
            reference_movies=references, content_type=content_type,
        )

    @staticmethod
    def quick_pick_presets():
        return [
            ("Surprise Me", "Surprise me with excellent films that fit my overall taste. Favour strong personal matches and a varied mix rather than obvious defaults."),
            ("Easy Watch Tonight", "Give me engaging, accessible films for an easy evening. Keep them satisfying and well paced, but still tailored to my taste."),
            ("Dark & Tense", "Give me dark, tense, atmospheric films with strong direction and escalating pressure. Use my taste to avoid generic choices."),
            ("Hidden Gems", "Find excellent less-obvious films I am unlikely to have seen. Prioritise quality and fit with my taste over popularity."),
            ("Comfort Watch", "Pick warm, enjoyable or reassuring films that suit my taste and work as a comfort watch without feeling bland or generic."),
            ("Something Different", "Push slightly outside my usual comfort zone while keeping a clear connection to qualities I consistently like. Surprise me intelligently."),
        ]

    def quick_pick_interactive(self):
        self._require_ai()
        presets = self.quick_pick_presets()
        choice = xbmcgui.Dialog().select("Quick Pick", [row[0] for row in presets])
        if choice < 0:
            return None
        label, prompt = presets[choice]
        count = self._setting_int("quick_pick_count", 15, 5, 50)
        name = "Quick Pick: %s" % label
        self._notify("Finding fresh picks for %s…" % label)
        record = self._generate_and_write(name, prompt, count, persist=False)
        return {
            "kind": "list_preview", "created_at": int(time.time()),
            "draft": {
                "name": name, "description": "", "prompt": prompt,
                "generation_method": "ai", "content_type": "movies", "count": count,
                "regeneration_enabled": self._default_regeneration_enabled(),
                "regeneration_interval_hours": self._default_regeneration_interval(),
            },
            "record": record,
        }

    def save_list_preview(self, preview):
        """Persist the exact items shown by a temporary list preview."""
        record = dict((preview or {}).get("record") or {})
        name = str(record.get("name") or "").strip()
        movies = [row for row in record.get("movies", []) if isinstance(row, dict)]
        if not name or not movies:
            raise RuntimeError("That preview is no longer available. Generate it again first.")
        if self._managed_record_by_name(name):
            replacement = xbmcgui.Dialog().input("List name", defaultt=name)
            if not replacement or not replacement.strip():
                return None
            name = replacement.strip()
            if self._managed_record_by_name(name):
                xbmcgui.Dialog().ok(self.name, "A curatr list already uses that name. Choose a different name.")
                return None
            record["name"] = name
        record["local_id"] = uuid.uuid4().hex
        record["local_changed_at"] = int(time.time())
        self._store_managed_record(record)
        self._save_state()
        if record.get("sync_to_trakt") and self._has_oauth():
            try:
                record = self.sync_list_to_trakt(record["local_id"], silent=True)
            except Exception as exc:
                self.record_activity("%s was saved locally; initial Trakt sync was skipped" % name, level="warning", detail=str(exc), notify=False)
        self.record_activity("Created %s from preview" % name, notify=True)
        return record

    def _managed_record_by_id(self, list_id):
        wanted = str(list_id)
        for item in self.state.get("ai_lists", []):
            if not isinstance(item, dict):
                continue
            if self._record_key(item) == wanted or str(item.get("trakt_id") or "") == wanted:
                return item
        return None

    def refresh_list(self, list_id, silent=False):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")

        name = record.get("name") or "My Picks"
        method = str(record.get("generation_method") or "ai").lower()
        content_type = str(record.get("content_type") or "movies").lower()
        if not silent:
            self._notify("Refreshing %s%s…" % (name, " with Keyword Matching" if method == "keyword" else ""))
        if method == "keyword":
            rules = record.get("keyword_rules")
            if not isinstance(rules, dict):
                rules = parse_prompt(record.get("prompt") or "")
            result = self._generate_keyword_and_write(
                name, record.get("prompt") or "", self._safe_int(record.get("count"), 20),
                rules, silent=True, managed_record=record, content_type=content_type,
            )
        else:
            result = self._generate_and_write(
                name,
                record.get("prompt") or "Recommend something for me.",
                self._safe_int(record.get("count"), 20),
                silent=True,
                managed_record=record, content_type=content_type,
            )
        if not silent:
            self.record_activity(
                "%s refreshed in Kodi with %d items"
                % (result.get("name") or name, self._safe_int(result.get("last_result_count"), 0)),
                notify=True,
            )
        return result

    def edit_list_interactive(self, list_id):
        """Retain the edit entry point used by existing plugin routes."""
        return self.list_settings_interactive(list_id)

    def _edit_list_name(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        current_name = str(record.get("name") or "My Picks")
        new_name = xbmcgui.Dialog().input("List name", defaultt=current_name)
        if not new_name or not new_name.strip():
            return record
        new_name = new_name.strip()
        if new_name == current_name:
            return record
        duplicate = self._managed_record_by_name(new_name)
        if duplicate and self._record_key(duplicate) != self._record_key(record):
            xbmcgui.Dialog().ok(self.name, "A different list already uses the name '%s'." % new_name)
            return record

        updated = dict(record)
        updated["name"] = new_name
        updated["edited_at"] = int(time.time())
        self._store_managed_record(updated, record)
        self._save_state()

        if updated.get("trakt_id") and updated.get("sync_to_trakt") and self._has_oauth():
            try:
                self._require_trakt_write()
                self.trakt.update_list(updated.get("trakt_id"), name=new_name)
            except Exception as exc:
                self.record_activity(
                    "Saved local rename; Trakt rename was skipped",
                    level="warning", detail=str(exc), notify=True,
                )
        self.record_activity("Renamed list to %s" % new_name, notify=True)
        return updated


    def _edit_list_description(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        current = str(record.get("description") or "")
        value = xbmcgui.Dialog().input("List description (optional)", defaultt=current)
        value = str(value or "").strip()
        if value == current:
            return record
        updated = dict(record)
        updated["description"] = value
        updated["edited_at"] = int(time.time())
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity(
            "Updated the description for %s" % (updated.get("name") or "curatr list"),
            notify=True,
        )
        return updated

    def _edit_list_count(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        current_count = max(5, min(50, self._safe_int(record.get("count"), 20)))
        count_text = xbmcgui.Dialog().numeric(0, "Number of items (5-50)", defaultt=str(current_count))
        if not count_text:
            return record
        try:
            new_count = max(5, min(50, int(count_text)))
        except (TypeError, ValueError):
            new_count = current_count
        updated = dict(record)
        updated["count"] = new_count
        updated["edited_at"] = int(time.time())
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity("Item count for %s set to %d" % (updated.get("name") or "curatr list", new_count), notify=True)
        return updated

    def _toggle_list_trakt_sync(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        key = self._record_key(record)
        if record.get("sync_to_trakt"):
            if not xbmcgui.Dialog().yesno(
                self.name,
                "Stop updating the Trakt copy of '%s'?\n\nThe list in Kodi will stay as it is. Any existing Trakt copy will not be deleted."
                % (record.get("name") or "curatr list"),
            ):
                return record
            updated = self.set_list_trakt_sync(key, False)
            self.record_activity("%s is now local only" % (updated.get("name") or "curatr list"), notify=True)
            return updated

        if not self._has_oauth():
            xbmcgui.Dialog().ok(
                self.name,
                "This list is saved in Kodi only.\n\nTo save a copy to Trakt, curatr itself needs a valid Trakt connection. "
                "You can leave it in Kodi and still use it as a widget or play it through Redlight.",
            )
            return record

        updated = self.set_list_trakt_sync(key, True)
        sync_now = xbmcgui.Dialog().yesno(
            self.name,
            "A Trakt copy is now enabled for '%s'.\n\nCopy the current picks to Trakt now?"
            % (updated.get("name") or "curatr list"),
        )
        if sync_now:
            return self.sync_list_to_trakt(key, silent=False)
        self.record_activity("Trakt copy enabled for %s" % (updated.get("name") or "curatr list"), notify=True)
        return updated

    def _toggle_list_regeneration(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        updated = dict(record)
        enabled = not bool(record.get("regeneration_enabled"))
        updated["regeneration_enabled"] = enabled
        updated["regeneration_last_attempt_at"] = 0
        if not updated.get("regeneration_interval_hours"):
            updated["regeneration_interval_hours"] = self._default_regeneration_interval()
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity(
            "Auto Refresh %s for %s" % ("enabled" if enabled else "disabled", updated.get("name") or "curatr list"),
            notify=True,
        )
        return updated

    def _edit_list_regeneration_interval(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        current = self._safe_int(record.get("regeneration_interval_hours"), self._default_regeneration_interval())
        current = max(1, min(720, current))
        hours = self._choose_interval_hours("Refresh Every", current)
        if hours is None:
            return record
        updated = dict(record)
        updated["regeneration_interval_hours"] = hours
        updated["regeneration_last_attempt_at"] = 0
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity(
            "Refresh interval for %s set to %d hour(s)" % (updated.get("name") or "curatr list", hours),
            notify=True,
        )
        return updated

    def _toggle_list_trakt_refresh(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        if not record.get("sync_to_trakt"):
            xbmcgui.Dialog().ok(
                self.name,
                "Turn on 'Save a copy to Trakt' for this list first. Automatic Trakt updates only copy your current Kodi list to Trakt.",
            )
            return record
        if not self._has_oauth():
            xbmcgui.Dialog().ok(
                self.name,
                "Automatic Trakt updates need curatr to be connected to Trakt. List refreshes work independently for Kodi-only lists.",
            )
            return record

        updated = dict(record)
        enabled = not bool(record.get("trakt_refresh_enabled"))
        updated["trakt_refresh_enabled"] = enabled
        updated["trakt_last_attempt_at"] = 0
        if not updated.get("trakt_refresh_interval_hours"):
            updated["trakt_refresh_interval_hours"] = self._default_trakt_refresh_interval()
        # Start a newly enabled schedule from now. Manual "Sync now" is available
        # when the user wants the Trakt copy updated immediately.
        if enabled:
            updated["trakt_refresh_cycle_at"] = int(time.time())
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity(
            "Automatic Trakt update %s for %s" % ("enabled" if enabled else "disabled", updated.get("name") or "curatr list"),
            notify=True,
        )
        return updated

    def _edit_list_trakt_refresh_interval(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        current = self._safe_int(record.get("trakt_refresh_interval_hours"), self._default_trakt_refresh_interval())
        current = max(1, min(720, current))
        hours = self._choose_interval_hours("Sync Every", current)
        if hours is None:
            return record
        updated = dict(record)
        updated["trakt_refresh_interval_hours"] = hours
        updated["trakt_last_attempt_at"] = 0
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity(
            "Trakt update interval for %s set to %d hour(s)" % (updated.get("name") or "curatr list", hours),
            notify=True,
        )
        return updated


    def _store_list_artwork(self, record, artwork):
        updated = dict(record)
        updated["artwork"] = normalise_list_art(artwork)
        updated["edited_at"] = int(time.time())
        self._store_managed_record(updated, record)
        self._save_state()
        return updated

    def _bundled_art_source(self, key, kind, style, colour="default"):
        return bundled_list_art_source(self.addon, key, kind, style, colour)

    def _bundled_art_entries(self, kind, style, colour="default", layered=False):
        root = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        entries = []
        for key, label in LIST_ART_CHOICES:
            if key == "blank" and kind == "icon" and style == "white":
                continue
            symbol, background, _palette = artwork_components(root, key, kind, style, colour)
            entries.append({
                "key": key, "label": label, "colour": colour,
                "source": (symbol or background) if layered else self._bundled_art_source(key, kind, style, colour),
                "background": background if layered and symbol else "", "layered": bool(layered and symbol),
            })
        return entries

    def _person_artwork_entries(self):
        tmdb = getattr(self, "tmdb", None)
        if not tmdb or not tmdb.api_key:
            xbmcgui.Dialog().ok(self.name, "Person artwork needs TMDB to be enabled with an API key under Metadata.")
            return []
        query = xbmcgui.Dialog().input("Search for a director or actor")
        if not query or not query.strip():
            return []
        people = tmdb.search_people(query.strip(), limit=20)
        people = [row for row in people if isinstance(row, dict) and row.get("profile_path")]
        if not people:
            xbmcgui.Dialog().ok(self.name, "No suitable person artwork was found on TMDB.")
            return []
        entries = []
        for person in people:
            source = tmdb.image_url(person.get("profile_path"), "h632")
            if not source:
                continue
            entries.append({
                "label": str(person.get("name") or "Person artwork"),
                "source": source,
                "mode": "person",
            })
        preview_paths = ArtworkCache(self.addon, workers=6).cache_urls(
            [entry.get("source") for entry in entries], limit=20,
        )
        for entry in entries:
            entry["preview_source"] = preview_paths.get(entry.get("source")) or entry.get("source")
        return entries

    def _custom_artwork_choice(self, kind):
        title = "Choose a square icon" if kind == "icon" else "Choose landscape fanart"
        source = xbmcgui.Dialog().browseSingle(2, title, "files", ".png|.jpg|.jpeg|.webp")
        if not source:
            return None
        return {"source": str(source), "label": "Custom"}

    def _edit_artwork_window(self, heading, artwork, preview_record=None, content_entries=None):
        initial = normalise_list_art(artwork)
        record = dict(preview_record or {})
        if content_entries is None:
            content_entries = self._artwork_content_provider(record)
        loaded_contents = []

        def preview_provider(draft):
            preview_record_value = dict(record)
            preview_record_value["artwork"] = normalise_list_art(draft)
            sources = list_art_sources(self.addon, preview_record_value)
            icon, fanart, _style = list_art_summary(preview_record_value)
            sources.update({"icon_label": icon, "fanart_label": fanart,
                            "automatic_key": suggested_art_key(record.get("name"), record.get("prompt"))})
            return sources

        def choice_provider(kind, source, style, colour="default"):
            if source == "curatr":
                return self._bundled_art_entries(kind, style, colour, layered=True)
            if source == "contents" and content_entries:
                if not loaded_contents:
                    loaded_contents.extend(content_entries() or [])
                return loaded_contents
            if source == "person":
                return self._person_artwork_entries()
            return []

        return edit_artwork(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")),
            heading,
            initial,
            normalise_list_art({}),
            preview_provider,
            choice_provider,
            self._custom_artwork_choice,
            has_contents=bool(content_entries),
            person_available=bool(
                getattr(self, "tmdb", None) and getattr(self.tmdb, "api_key", "")
            ),
        )

    def _choose_bundled_art(self, heading, kind):
        if kind == "icon":
            style_choice = xbmcgui.Dialog().select("Icon style", ["White", "Colours"])
            if style_choice < 0:
                return "", ""
            style = "genre_colours" if style_choice == 1 else "white"
            layout = "icon"
        else:
            style = "colour"
            layout = "fanart"
        entries = self._bundled_art_entries(kind, style)
        selected = choose_artwork(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")), heading, entries, layout
        )
        if not selected:
            return "", ""
        return selected["key"], style

    def _change_list_icon(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        art = normalise_list_art(record.get("artwork"))
        choice = xbmcgui.Dialog().select("Change list icon", [
            "Automatic: match the list name and prompt",
            "Choose a curatr icon",
            "Match current fanart",
            "Search for a director or actor",
            "Choose a custom image",
        ])
        if choice < 0:
            return record
        if choice == 0:
            art.update({"icon_mode": "auto", "icon_key": "", "icon_source": "", "icon_label": "", "icon_style": "white"})
        elif choice == 1:
            key, icon_style = self._choose_bundled_art("Choose list icon", "icon")
            if not key:
                return record
            art.update({"icon_mode": "bundled", "icon_key": key, "icon_source": "", "icon_label": "", "icon_style": icon_style})
        elif choice == 2:
            fanart_mode = art.get("fanart_mode")
            if fanart_mode == "auto":
                art.update({"icon_mode": "auto", "icon_key": "", "icon_source": "", "icon_label": "", "icon_style": "white"})
            elif fanart_mode == "bundled":
                icon_style = "genre_colours" if art.get("fanart_style") == "colour" else "white"
                art.update({"icon_mode": "bundled", "icon_key": art.get("fanart_key") or "", "icon_source": "", "icon_label": "", "icon_style": icon_style})
            elif fanart_mode in ("item", "person", "custom") and art.get("fanart_source"):
                art.update({
                    "icon_mode": "person" if fanart_mode == "person" else "custom", "icon_key": "",
                    "icon_source": art.get("fanart_source") or "",
                    "icon_label": art.get("fanart_label") or "Custom",
                })
            elif fanart_mode == "default":
                art.update({"icon_mode": "default", "icon_key": "", "icon_source": "", "icon_label": ""})
            else:
                xbmcgui.Dialog().ok(self.name, "The current fanart cannot be used as an icon.")
                return record
        elif choice == 3:
            source, label = self._fanart_from_person()
            if not source:
                return record
            art.update({
                "icon_mode": "person", "icon_source": source,
                "icon_key": "", "icon_label": label,
            })
        elif choice == 4:
            path = xbmcgui.Dialog().browseSingle(2, "Choose a square icon", "files", ".png|.jpg|.jpeg|.webp")
            if not path:
                return record
            art.update({"icon_mode": "custom", "icon_source": str(path), "icon_key": "", "icon_label": "Custom"})
        updated = self._store_list_artwork(record, art)
        self.record_activity("Updated the icon for %s" % (updated.get("name") or "curatr list"), notify=True)
        return updated

    def _fanart_from_list_item(self, record):
        return self._fanart_from_movies(record.get("movies"), "Choose fanart from this list")

    def _fanart_entries_from_movies(self, movies):
        # Contents is loaded on demand. Enrich a bounded preview copy, never the
        # saved list, and reuse the normal metadata cache for missing backdrops.
        movies = deepcopy([row for row in (movies or []) if isinstance(row, dict)][:250])
        available = {ArtworkCache._first_image(row, "fanart") for row in movies}
        available.discard("")
        missing = [row for row in movies if not ArtworkCache._first_image(row, "fanart")][:max(0, 24 - len(available))]
        if missing:
            MetadataCache(self.addon).enrich(missing, getattr(self, "tmdb", None), include_artwork=True)
        choices = []
        seen = set()
        for movie in movies:
            url = ArtworkCache._first_image(movie, "fanart")
            if url and url not in seen:
                seen.add(url)
                choices.append((movie, url))
                if len(choices) >= 24:
                    break
        if not choices:
            xbmcgui.Dialog().ok(self.name, "No landscape artwork is available from these contents.")
            return []
        preview_paths = ArtworkCache(self.addon, workers=6).cache_urls([source for _movie, source in choices], limit=24)
        entries = []
        for movie, source in choices:
            label = "%s%s" % (
                movie.get("title") or "Item artwork",
                " (%s)" % movie.get("year") if movie.get("year") else "",
            )
            entries.append({
                "label": label,
                "source": source,
                "preview_source": preview_paths.get(source) or source,
                "mode": "item",
            })
        return entries

    def _fanart_from_movies(self, movies, heading="Choose fanart from contents"):
        entries = self._fanart_entries_from_movies(movies)
        if not entries:
            return "", ""
        selected = choose_artwork(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")),
            heading, entries, "fanart",
        )
        return (selected["source"], selected["label"]) if selected else ("", "")

    def _fanart_entries_from_external_path(self, path):
        path = self._valid_external_plugin_path(path)
        if not path:
            return []
        try:
            directory = self._kodi_json_rpc("Files.GetDirectory", {
                "directory": path, "media": "video",
                "properties": ["title", "year", "thumbnail", "fanart"],
                "limits": {"start": 0, "end": 60},
            })
        except Exception as exc:
            xbmcgui.Dialog().ok(self.name, "This add-on did not make its artwork available to Kodi.\n\n%s" % exc)
            return []
        entries = []
        for row in directory.get("files", []) if isinstance(directory, dict) else []:
            if not isinstance(row, dict):
                continue
            source = str(row.get("fanart") or row.get("thumbnail") or "").strip()
            if not source:
                continue
            title = str(row.get("label") or row.get("title") or "Item artwork").strip()
            year = self._safe_int(row.get("year"), 0)
            label = "%s%s" % (title, " (%d)" % year if year else "")
            entries.append({"label": label, "source": source, "mode": "item"})
            if len(entries) >= 30:
                break
        if not entries:
            xbmcgui.Dialog().ok(self.name, "This add-on did not provide any landscape artwork for this path.")
            return []
        previews = ArtworkCache(self.addon, workers=6).cache_urls(
            [entry["source"] for entry in entries if entry["source"].startswith(("http://", "https://", "//"))],
            limit=30,
        )
        for entry in entries:
            entry["preview_source"] = previews.get(entry["source"]) or entry["source"]
        return entries

    def _fanart_from_external_path(self, path):
        entries = self._fanart_entries_from_external_path(path)
        if not entries:
            return "", ""
        selected = choose_artwork(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")),
            "Choose fanart from contents", entries, "fanart",
        )
        return (selected["source"], selected["label"]) if selected else ("", "")

    def _fanart_from_person(self):
        entries = self._person_artwork_entries()
        if not entries:
            return "", ""
        selected = choose_artwork(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")),
            "Choose a director or actor", entries, "icon",
        )
        return (selected["source"], selected["label"]) if selected else ("", "")

    def _change_list_fanart(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        art = normalise_list_art(record.get("artwork"))
        choice = xbmcgui.Dialog().select("Change list fanart", [
            "Automatic: match the list name and prompt",
            "Choose curatr genre fanart",
            "Match current icon",
            "Choose fanart from this list",
            "Search for a director or actor",
            "Choose a custom image",
            "Use the default curatr background",
        ])
        if choice < 0:
            return record
        if choice == 0:
            art.update({"fanart_mode": "auto", "fanart_key": "", "fanart_source": "", "fanart_label": ""})
        elif choice == 1:
            key, fanart_style = self._choose_bundled_art("Choose list fanart", "fanart")
            if not key:
                return record
            art.update({"fanart_mode": "bundled", "fanart_key": key, "fanart_source": "", "fanart_label": "", "fanart_style": fanart_style})
        elif choice == 2:
            icon_mode = art.get("icon_mode")
            if icon_mode == "auto":
                art.update({"fanart_mode": "auto", "fanart_key": "", "fanart_source": "", "fanart_label": ""})
            elif icon_mode == "bundled":
                fanart_style = "colour" if art.get("icon_style") == "genre_colours" else "monochrome"
                art.update({"fanart_mode": "bundled", "fanart_key": art.get("icon_key") or "", "fanart_source": "", "fanart_label": "", "fanart_style": fanart_style})
            elif icon_mode in ("person", "custom") and art.get("icon_source"):
                art.update({
                    "fanart_mode": "person" if icon_mode == "person" else "custom", "fanart_key": "",
                    "fanart_source": art.get("icon_source") or "",
                    "fanart_label": art.get("icon_label") or "Custom",
                })
            elif icon_mode == "default":
                art.update({"fanart_mode": "default", "fanart_key": "", "fanart_source": "", "fanart_label": ""})
            else:
                xbmcgui.Dialog().ok(self.name, "The current icon cannot be used as fanart.")
                return record
        elif choice == 3:
            source, label = self._fanart_from_list_item(record)
            if not source:
                return record
            art.update({"fanart_mode": "item", "fanart_source": source, "fanart_key": "", "fanart_label": label})
        elif choice == 4:
            source, label = self._fanart_from_person()
            if not source:
                return record
            art.update({"fanart_mode": "person", "fanart_source": source, "fanart_key": "", "fanart_label": label})
        elif choice == 5:
            source = xbmcgui.Dialog().browseSingle(2, "Choose landscape fanart", "files", ".png|.jpg|.jpeg|.webp")
            if not source:
                return record
            art.update({"fanart_mode": "custom", "fanart_source": str(source), "fanart_key": "", "fanart_label": ""})
        else:
            art.update({"fanart_mode": "default", "fanart_source": "", "fanart_key": "", "fanart_label": ""})
        updated = self._store_list_artwork(record, art)
        self.record_activity("Updated the fanart for %s" % (updated.get("name") or "curatr list"), notify=True)
        return updated

    def _suggest_list_artwork(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        suggestions = []
        if self.tmdb and self.tmdb.api_key:
            try:
                for person in self.tmdb.search_people(record.get("name") or "", limit=5):
                    profile = self.tmdb.image_url(person.get("profile_path"), "h632")
                    if not profile:
                        continue
                    department = str(person.get("known_for_department") or "").casefold()
                    person_key = "director" if department in ("directing", "writing", "production") else "actor"
                    suggestions.append({
                        "label": "%s: %s" % (person.get("name"), "Director / creator" if person_key == "director" else "Actor"),
                        "source": profile, "mode": "person", "icon_key": person_key,
                        "art_label": str(person.get("name") or "Person artwork"),
                    })
            except CatalogueError as exc:
                xbmc.log("curatr person artwork suggestion skipped: %s" % exc, xbmc.LOGWARNING)
        for movie in [row for row in record.get("movies", []) if isinstance(row, dict)]:
            source = ArtworkCache._first_image(movie, "fanart")
            if source:
                suggestions.append({
                    "label": "%s%s: list-item fanart" % (movie.get("title") or "Untitled", " (%s)" % movie.get("year") if movie.get("year") else ""),
                    "source": source, "mode": "item", "icon_key": "",
                    "art_label": "%s%s" % (
                        movie.get("title") or "Item artwork",
                        " (%s)" % movie.get("year") if movie.get("year") else "",
                    ),
                })
            if len(suggestions) >= 15:
                break
        if not suggestions:
            xbmcgui.Dialog().ok(self.name, "No person or list-item artwork suggestions are available yet. Enable TMDB or refresh this list first.")
            return record
        grid_entries = [
            {
                "label": row.get("art_label") or row["label"],
                "source": row["source"],
                "suggestion": row,
            }
            for row in suggestions
        ]
        preview_paths = ArtworkCache(self.addon, workers=6).cache_urls(
            [entry.get("source") for entry in grid_entries], limit=20,
        )
        for entry in grid_entries:
            entry["preview_source"] = preview_paths.get(entry.get("source")) or entry.get("source")
        grid_choice = choose_artwork(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")),
            "Suggested artwork", grid_entries, "fanart",
        )
        if not grid_choice:
            return record
        selected = grid_choice["suggestion"]
        art = normalise_list_art(record.get("artwork"))
        art.update({
            "fanart_mode": selected["mode"],
            "fanart_source": selected["source"],
            "fanart_key": "",
            "fanart_label": selected.get("art_label") or "",
        })
        if selected.get("icon_key") and xbmcgui.Dialog().yesno(self.name, "Use the matching %s icon too?" % list_art_label(selected["icon_key"]).lower()):
            icon_style = "genre_colours" if art.get("fanart_style") == "colour" else "white"
            art.update({"icon_mode": "bundled", "icon_key": selected["icon_key"], "icon_source": "", "icon_label": "", "icon_style": icon_style})
        updated = self._store_list_artwork(record, art)
        self.record_activity("Applied suggested artwork to %s" % (updated.get("name") or "curatr list"), notify=True)
        return updated

    def _list_artwork_legacy(self, list_id):
        while True:
            record = self._managed_record_by_id(list_id)
            if not record:
                raise RuntimeError("That list has already been removed.")
            icon, fanart, _style = list_art_summary(record)
            actions = [
                ("icon", "Icon: %s" % icon),
                ("fanart", "Fanart: %s" % fanart),
            ]
            actions.extend([
                ("suggest", "Find a person, movie or show"),
                ("reset", "Reset icon and fanart to Automatic"),
            ])
            choice = xbmcgui.Dialog().select(
                "Artwork: %s" % (record.get("name") or "curatr list"),
                [label for _action, label in actions],
            )
            if choice < 0:
                return record
            key = self._record_key(record)
            action = actions[choice][0]
            if action == "icon":
                self._change_list_icon(key)
            elif action == "fanart":
                self._change_list_fanart(key)
            elif action == "suggest":
                self._suggest_list_artwork(key)
            else:
                self._store_list_artwork(record, {})
                self.record_activity("Reset artwork for %s to Automatic" % (record.get("name") or "curatr list"), notify=True)

    def list_artwork_interactive(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        original = normalise_list_art(record.get("artwork"))
        result, artwork = self._edit_artwork_window(
            "Artwork: %s" % (record.get("name") or "curatr list"),
            original,
            preview_record=record,
            content_entries=lambda: self._fanart_entries_from_movies(record.get("movies")),
        )
        if result == "fallback":
            return self._list_artwork_legacy(list_id)
        artwork = normalise_list_art(artwork)
        if result != "save" or artwork == original:
            return record
        updated = self._store_list_artwork(record, artwork)
        self.record_activity(
            "Updated artwork for %s" % (updated.get("name") or "curatr list"),
            notify=True,
        )
        return updated


    def _view_list_settings(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")

        def when(value):
            stamp = self._safe_int(value, 0)
            if not stamp:
                return "Never"
            try:
                return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))
            except Exception:
                return str(stamp)

        refresh_schedule = "Manual only"
        if record.get("regeneration_enabled"):
            refresh_schedule = "Every %d hour(s)" % self._safe_int(record.get("regeneration_interval_hours"), 24)
        method_label = "Keyword Matching" if str(record.get("generation_method") or "ai").lower() == "keyword" else "AI"
        trakt_update = "Off"
        if record.get("sync_to_trakt"):
            trakt_update = "Manual only"
            if record.get("trakt_refresh_enabled"):
                trakt_update = "Every %d hour(s)" % self._safe_int(record.get("trakt_refresh_interval_hours"), 24)

        icon_art, fanart_art, fanart_style = list_art_summary(record)
        request_label = "Request" if str(record.get("generation_method") or "ai").lower() == "keyword" else "Prompt"
        content_label = {"movies": "Movies only", "shows": "TV Shows only", "both": "Movies & TV Shows"}.get(
            str(record.get("content_type") or "movies"), "Movies only",
        )
        text = (
            "List: %s\n\nDescription:\n%s\n\nItems requested: %d\nContent: %s\nSaved: %s\n\nIcon: %s\nFanart: %s\nFanart style: %s\n\n"
            "Creation method: %s\nGrounded candidates considered: %d\n\nAuto Refresh: %s\nLast refresh: %s\n\n"
            "Trakt Sync: %s\nLast sync: %s\n\n%s:\n%s"
            % (
                record.get("name") or "curatr list",
                record.get("description") or "Not set",
                self._safe_int(record.get("count"), 20),
                content_label,
                self._list_storage_label(record),
                icon_art,
                fanart_art,
                fanart_style,
                method_label,
                self._safe_int(record.get("grounded_candidate_count"), 0),
                refresh_schedule,
                when(record.get("updated_at")),
                trakt_update,
                when(record.get("trakt_synced_at")),
                request_label,
                record.get("prompt") or "",
            )
        )
        xbmcgui.Dialog().textviewer("List details", text)
        return record

    def save_list_prompt_as_template(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        return self._save_prompt_template(
            record.get("name") or "Saved Prompt",
            record.get("prompt") or "",
            self._safe_int(record.get("count"), 20),
        )

    def delete_list_interactive(self, list_id):
        """Delete a managed curatr list locally, with an optional Trakt deletion."""
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")

        key = self._record_key(record)
        name = str(record.get("name") or "curatr list")
        trakt_id = record.get("trakt_id")
        delete_remote = False

        if trakt_id:
            options = [
                "Delete from curatr only",
                "Delete from curatr and Trakt",
                "Cancel",
            ]
            choice = xbmcgui.Dialog().select("Delete list: %s" % name, options)
            if choice < 0 or choice == 2:
                return False
            delete_remote = choice == 1

            if delete_remote and not self._has_oauth():
                xbmcgui.Dialog().ok(
                    self.name,
                    "The Trakt copy cannot be deleted because curatr is not currently connected to Trakt. "
                    "Reconnect Trakt first, or choose 'Delete from curatr only'.",
                )
                return False

            warning = (
                "Permanently delete '%s' from curatr and Trakt?\n\nThis cannot be undone." % name
                if delete_remote else
                "Delete '%s' from curatr?\n\nIts existing Trakt copy will be left untouched." % name
            )
        else:
            warning = "Permanently delete '%s' from curatr?\n\nThis cannot be undone." % name

        if not xbmcgui.Dialog().yesno(self.name, warning):
            return False

        # Delete the remote copy first. If Trakt refuses the deletion, keep the
        # local record intact so the user can retry without losing the link.
        if delete_remote:
            self._require_trakt_write()
            self.trakt.delete_list(trakt_id)

        kept = []
        for item in self.state.get("ai_lists", []):
            if not isinstance(item, dict) or self._record_key(item) != key:
                kept.append(item)
        self.state["ai_lists"] = kept
        self._save_state()
        self.record_activity(
            "Deleted %s%s" % (name, " from curatr and Trakt" if delete_remote else " from curatr"),
            notify=True,
        )
        return True

    def list_settings_interactive(self, list_id):
        """Edit an existing list using the same tabbed form used for creation."""
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        original = dict(record)
        draft = {
            "name": str(record.get("name") or "curatr list"),
            "movies": deepcopy(record.get("movies") or []),
            "description": str(record.get("description") or ""),
            "prompt": str(record.get("prompt") or ""),
            "keyword_rules": deepcopy(record.get("keyword_rules")),
            "_keyword_prompt": str(record.get("prompt") or ""),
            "_keyword_confirmed": isinstance(record.get("keyword_rules"), dict),
            "generation_method": "keyword" if str(record.get("generation_method") or "ai") == "keyword" else "ai",
            "content_type": record.get("content_type") if record.get("content_type") in ("movies", "shows", "both") else "movies",
            "count": max(5, min(50, self._safe_int(record.get("count"), 20))),
            "artwork": normalise_list_art(record.get("artwork")),
            "regeneration_enabled": bool(record.get("regeneration_enabled")),
            "regeneration_interval_hours": self._safe_int(record.get("regeneration_interval_hours"), self._default_regeneration_interval()),
            "sync_to_trakt": bool(record.get("sync_to_trakt")),
            "trakt_refresh_enabled": bool(record.get("trakt_refresh_enabled")),
            "trakt_refresh_interval_hours": self._safe_int(record.get("trakt_refresh_interval_hours"), self._default_trakt_refresh_interval()),
        }
        action, draft = edit_list_settings(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")), draft,
            lambda field, values: self._edit_list_draft_field(field, values, existing=True),
            self._format_list_draft_field, existing=True,
        )
        if action != "save":
            return record
        name = str(draft.get("name") or "").strip()
        prompt = str(draft.get("prompt") or "").strip()
        if not name or not prompt:
            xbmcgui.Dialog().ok(self.name, "A list name and request are required.")
            return record
        duplicate = self._managed_record_by_name(name)
        if duplicate and self._record_key(duplicate) != self._record_key(record):
            xbmcgui.Dialog().ok(self.name, "A curatr list already uses that name.")
            return record
        method = draft.get("generation_method")
        rules = None
        if method == "keyword":
            edited = self._prepare_keyword_list_draft(draft, "Save")
            if edited is None:
                return record
            draft = edited
            prompt = draft["prompt"]
            rules = draft["keyword_rules"]
        updated = dict(record)
        updated.update({
            "name": name,
            "description": str(draft.get("description") or "").strip(),
            "prompt": prompt,
            "generation_method": method,
            "content_type": draft.get("content_type"),
            "count": max(5, min(50, self._safe_int(draft.get("count"), 20))),
            "artwork": normalise_list_art(draft.get("artwork")),
            "regeneration_enabled": bool(draft.get("regeneration_enabled")),
            "regeneration_interval_hours": self._safe_int(draft.get("regeneration_interval_hours"), 24),
            "sync_to_trakt": bool(draft.get("sync_to_trakt")),
            "trakt_refresh_enabled": bool(draft.get("sync_to_trakt") and draft.get("trakt_refresh_enabled")),
            "trakt_refresh_interval_hours": self._safe_int(draft.get("trakt_refresh_interval_hours"), 24),
            "edited_at": int(time.time()),
            "local_changed_at": int(time.time()),
        })
        if rules is not None:
            updated["keyword_rules"] = rules
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity("Updated list settings: %s" % name, notify=True)

        changed_results = any(original.get(field) != updated.get(field) for field in (
            "prompt", "generation_method", "content_type", "count", "keyword_rules",
        ))
        if changed_results:
            message = "List settings saved. Would you like to refresh this list now?"
            if method == "ai":
                message += "\n\nRefreshing will make one AI recommendation request."
            try:
                refresh_now = xbmcgui.Dialog().yesno(self.name, message, nolabel="Not Now", yeslabel="Refresh Now")
            except TypeError:
                refresh_now = xbmcgui.Dialog().yesno(self.name, message)
            if refresh_now:
                updated = self.refresh_list(self._record_key(updated), silent=False)
        if updated.get("sync_to_trakt") and not original.get("sync_to_trakt"):
            if self._has_oauth():
                updated = self.sync_list_to_trakt(self._record_key(updated), silent=True)
            else:
                xbmcgui.Dialog().ok(self.name, "Sync to Trakt is enabled for this list. Connect Trakt in Settings before its first sync.")
        return updated

    def _edit_list_content_type(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        values = ("movies", "shows", "both")
        current = str(record.get("content_type") or "movies")
        choice = xbmcgui.Dialog().select(
            "List content", ["Movies only", "TV Shows only", "Movies & TV Shows"],
            preselect=values.index(current) if current in values else 0,
        )
        if choice < 0 or values[choice] == current:
            return record
        if values[choice] == "shows" and str(record.get("generation_method") or "ai") == "keyword":
            rules = record.get("keyword_rules") or parse_prompt(record.get("prompt") or "")
            if rules.get("people") or rules.get("reference_movies") or rules.get("collection_query"):
                xbmcgui.Dialog().ok(
                    self.name,
                    "This Keyword Matching request uses named people, a collection or reference films. "
                    "Change the creation method to AI before making it TV Shows only.",
                )
                return record
        updated = dict(record)
        updated["content_type"] = values[choice]
        updated["edited_at"] = int(time.time())
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity("Updated content type for %s" % (updated.get("name") or "curatr list"), notify=True)
        content_label = {
            "movies": "Movies only",
            "shows": "TV Shows only",
            "both": "Movies & TV Shows",
        }[updated["content_type"]]
        message = (
            "Content changed to %s.\n\nWould you like to refresh this list now?"
            % content_label
        )
        if str(updated.get("generation_method") or "ai").lower() == "ai":
            message += "\n\nRefreshing will make one AI recommendation request."
        try:
            refresh_now = xbmcgui.Dialog().yesno(
                self.name, message, nolabel="Not Now", yeslabel="Refresh Now",
            )
        except TypeError:
            refresh_now = xbmcgui.Dialog().yesno(self.name, message)
        if refresh_now:
            return self.refresh_list(self._record_key(updated), silent=False)
        return updated

    def _edit_list_generation_method(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        current = str(record.get("generation_method") or "ai").lower()
        choice = xbmcgui.Dialog().select(
            "Creation method",
            ["AI: best for nuanced requests", "Keyword Matching: no AI request"],
            preselect=1 if current == "keyword" else 0,
        )
        if choice < 0:
            return record
        method = "keyword" if choice == 1 else "ai"
        if method == current:
            return record
        updated = dict(record)
        if method == "ai":
            self._require_ai()
            updated["generation_method"] = "ai"
        else:
            self._require_keyword_catalogue()
            rules = parse_prompt(updated.get("prompt") or "")
            if not rules.get("confidence"):
                xbmcgui.Dialog().ok(
                    self.name,
                    "The saved request does not contain a clear Keyword Matching filter. Edit the request first, "
                    "using details such as genre, decade, runtime, rating, actor or director.",
                )
                return record
            if not xbmcgui.Dialog().yesno(
                self.name,
                "Use Keyword Matching for future refreshes?\n\n%s\n\nThe current items will stay unchanged until the next refresh."
                % format_rules(rules),
            ):
                return record
            updated["generation_method"] = "keyword"
            updated["keyword_rules"] = rules
        updated["edited_at"] = int(time.time())
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity(
            "%s now uses %s" % (updated.get("name") or "curatr list", "Keyword Matching" if method == "keyword" else "AI"),
            notify=True,
        )
        return updated

    @staticmethod
    def _shorten_text(value, limit=70):
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 1)].rstrip() + "…"

    @staticmethod
    def _format_interval(hours):
        hours = max(1, int(hours or 1))
        labels = {
            6: "Every 6 hours", 12: "Every 12 hours", 24: "Every day",
            72: "Every 3 days", 168: "Every week", 336: "Every 2 weeks",
            720: "Every month",
        }
        return labels.get(hours, "Every %d hours" % hours)

    def _choose_interval_hours(self, heading, current):
        values = [6, 12, 24, 72, 168]
        labels = [self._format_interval(value) for value in values] + ["Custom"]
        preselect = values.index(current) if current in values else len(labels) - 1
        choice = xbmcgui.Dialog().select(heading, labels, preselect=preselect)
        if choice < 0:
            return None
        if choice < len(values):
            return values[choice]
        value = xbmcgui.Dialog().numeric(0, "%s (hours, 1-720)" % heading, defaultt=str(current))
        if not value:
            return None
        try:
            return max(1, min(720, int(value)))
        except (TypeError, ValueError):
            return current

    def _choose_schedule_hours(self, heading, enabled, current):
        values = [0, 24, 72, 168, 336, 720]
        labels = ["Never", "Every day", "Every 3 days", "Every week", "Every 2 weeks", "Every month"]
        selected = current if enabled else 0
        try:
            preselect = values.index(selected)
        except ValueError:
            preselect = 1 if enabled else 0
        choice = xbmcgui.Dialog().select(heading, labels, preselect=preselect)
        return None if choice < 0 else values[choice]

    def _edit_list_refresh_schedule(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        hours = self._choose_schedule_hours(
            "Auto Refresh", bool(record.get("regeneration_enabled")),
            self._safe_int(record.get("regeneration_interval_hours"), 24),
        )
        if hours is None:
            return record
        updated = dict(record)
        updated["regeneration_enabled"] = bool(hours)
        if hours:
            updated["regeneration_interval_hours"] = hours
        updated["regeneration_last_attempt_at"] = 0
        self._store_managed_record(updated, record)
        self._save_state()
        return updated

    def _edit_list_sync_schedule(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        hours = self._choose_schedule_hours(
            "Auto Sync", bool(record.get("trakt_refresh_enabled")),
            self._safe_int(record.get("trakt_refresh_interval_hours"), 24),
        )
        if hours is None:
            return record
        if hours and not record.get("sync_to_trakt"):
            xbmcgui.Dialog().ok(self.name, "Sync this list to Trakt once before turning on Auto Sync.")
            return record
        if hours and not self._has_oauth():
            xbmcgui.Dialog().ok(self.name, "Auto Sync needs curatr to be connected to Trakt.")
            return record
        updated = dict(record)
        updated["trakt_refresh_enabled"] = bool(hours)
        if hours:
            updated["trakt_refresh_interval_hours"] = hours
            updated["trakt_refresh_cycle_at"] = int(time.time())
        updated["trakt_last_attempt_at"] = 0
        self._store_managed_record(updated, record)
        self._save_state()
        return updated

    # ---------- Saved prompts / hide list / backup ----------

    def _save_prompt_template(self, name, prompt, count=20):
        prompt = str(prompt or "").strip()
        if not prompt:
            return None
        name = str(name or "Saved Prompt").strip() or "Saved Prompt"
        templates = [row for row in self.state.get("prompt_templates", []) if isinstance(row, dict)]
        existing = next((row for row in templates if str(row.get("name") or "").casefold() == name.casefold()), None)
        if existing:
            updated = dict(existing)
            updated.update({"name": name, "prompt": prompt, "count": max(5, min(50, self._safe_int(count, 20)))})
            templates = [updated if row.get("id") == existing.get("id") else row for row in templates]
        else:
            templates.append({"id": uuid.uuid4().hex, "name": name, "prompt": prompt, "count": max(5, min(50, self._safe_int(count, 20)))})
        self.state["prompt_templates"] = templates
        self._save_state()
        self.record_activity("Saved prompt template: %s" % name, notify=True)
        return templates[-1] if not existing else updated

    def create_prompt_template_interactive(self):
        name = xbmcgui.Dialog().input("Template name")
        if not name or not name.strip():
            return None
        prompt = xbmcgui.Dialog().input("Saved prompt")
        if not prompt or not prompt.strip():
            return None
        count_text = xbmcgui.Dialog().numeric(0, "Default number of items (5-50)", defaultt="20")
        count = max(5, min(50, self._safe_int(count_text, 20)))
        return self._save_prompt_template(name.strip(), prompt.strip(), count)

    def use_prompt_template(self, template_id):
        template = next((row for row in self.state.get("prompt_templates", []) if isinstance(row, dict) and str(row.get("id")) == str(template_id)), None)
        if not template:
            raise RuntimeError("That saved prompt no longer exists.")
        return self.create_list_interactive(
            preset_prompt=template.get("prompt"),
            preset_name=template.get("name") or "Saved Prompt",
            preset_count=self._safe_int(template.get("count"), 20),
        )

    def prompt_templates_interactive(self):
        while True:
            templates = [row for row in self.state.get("prompt_templates", []) if isinstance(row, dict)]
            choices = ["Create a new saved prompt"] + ["%s: %d items" % (row.get("name") or "Saved Prompt", self._safe_int(row.get("count"), 20)) for row in templates]
            choice = xbmcgui.Dialog().select("Saved Prompts", choices)
            if choice < 0:
                return None
            if choice == 0:
                self.create_prompt_template_interactive()
                continue
            template = templates[choice - 1]
            action = xbmcgui.Dialog().select(template.get("name") or "Saved Prompt", [
                "Create a list from this prompt",
                "Edit template name",
                "Edit prompt",
                "Edit default item count",
                "Delete saved prompt",
            ])
            if action < 0:
                continue
            tid = template.get("id")
            if action == 0:
                return self.use_prompt_template(tid)
            updated = dict(template)
            if action == 1:
                value = xbmcgui.Dialog().input("Template name", defaultt=str(template.get("name") or ""))
                if value and value.strip(): updated["name"] = value.strip()
            elif action == 2:
                value = xbmcgui.Dialog().input("Saved prompt", defaultt=str(template.get("prompt") or ""))
                if value and value.strip(): updated["prompt"] = value.strip()
            elif action == 3:
                value = xbmcgui.Dialog().numeric(0, "Default number of items (5-50)", defaultt=str(self._safe_int(template.get("count"), 20)))
                if value: updated["count"] = max(5, min(50, self._safe_int(value, 20)))
            elif action == 4:
                if xbmcgui.Dialog().yesno(self.name, "Delete the saved prompt '%s'?" % (template.get("name") or "Saved Prompt")):
                    self.state["prompt_templates"] = [row for row in templates if str(row.get("id")) != str(tid)]
                    self._save_state()
                continue
            self.state["prompt_templates"] = [updated if str(row.get("id")) == str(tid) else row for row in templates]
            self._save_state()

    @staticmethod
    def _movie_marker(movie):
        if not isinstance(movie, dict):
            return ""
        ids = movie.get("ids") or {}
        trakt_id = ids.get("trakt") if isinstance(ids, dict) else None
        prefix = "show:" if str(movie.get("media_type") or "movie") == "show" else ""
        if trakt_id not in (None, ""):
            return "%strakt:%s" % (prefix, trakt_id)
        title = str(movie.get("title") or "").strip().casefold()
        year = Curator._safe_int(movie.get("year"), 0)
        return "%stitle:%s:%s" % (prefix, title, year) if title else ""

    def is_movie_hidden(self, movie):
        marker = self._movie_marker(movie)
        return bool(marker and any(str(row.get("marker") or "") == marker for row in self.state.get("hidden_movies", []) if isinstance(row, dict)))

    def hide_movie(self, trakt_id="", title="", year=0, confirm=True, media_type="movie"):
        media_type = "show" if media_type == "show" else "movie"
        movie = {"title": title, "year": self._safe_int(year, 0), "ids": {"trakt": trakt_id}, "media_type": media_type}
        marker = self._movie_marker(movie)
        if not marker:
            return False
        if confirm and not xbmcgui.Dialog().yesno(self.name, "Hide '%s'?\n\nIt will be removed from current curatr lists and excluded from future recommendations." % (title or "this item")):
            return False
        hidden = [row for row in self.state.get("hidden_movies", []) if isinstance(row, dict)]
        if not any(row.get("marker") == marker for row in hidden):
            hidden.append({"marker": marker, "trakt_id": trakt_id, "title": title, "year": self._safe_int(year, 0), "media_type": media_type, "hidden_at": int(time.time())})
        self.state["hidden_movies"] = hidden[-500:]
        for record in self.state.get("ai_lists", []):
            if not isinstance(record, dict):
                continue
            movies = [m for m in record.get("movies", []) if not (isinstance(m, dict) and self._movie_marker(m) == marker)]
            if len(movies) != len(record.get("movies", [])):
                record["movies"] = movies
                record["last_result_count"] = len(movies)
                record["local_changed_at"] = int(time.time())
            recs = []
            for rec in record.get("recommendations", []):
                if not isinstance(rec, dict):
                    continue
                if str(rec.get("media_type") or "movie") == media_type and str(rec.get("title") or "").strip().casefold() == str(title or "").strip().casefold() and self._safe_int(rec.get("year"), 0) == self._safe_int(year, 0):
                    continue
                recs.append(rec)
            record["recommendations"] = recs
        self._save_state()
        self.record_activity("Hidden from future recommendations: %s" % (title or marker), notify=True)
        return True

    def manage_hidden_interactive(self):
        while True:
            hidden = [row for row in self.state.get("hidden_movies", []) if isinstance(row, dict)]
            if not hidden:
                xbmcgui.Dialog().ok("Hidden", "You haven't hidden any movies or TV shows yet.\n\nUse the Hide action from an item's context menu.")
                return None
            labels = ["%s%s%s" % (row.get("title") or "Unknown item", " (%s)" % row.get("year") if row.get("year") else "", " • TV Show" if row.get("media_type") == "show" else "") for row in hidden]
            labels.append("Restore all hidden items")
            choice = xbmcgui.Dialog().select("Hidden", labels)
            if choice < 0:
                return None
            if choice == len(hidden):
                if xbmcgui.Dialog().yesno(self.name, "Allow all hidden items to be recommended again?"):
                    self.state["hidden_movies"] = []
                    self._save_state()
                continue
            row = hidden[choice]
            if xbmcgui.Dialog().yesno(self.name, "Allow '%s' to be recommended again?" % (row.get("title") or "this item")):
                self.state["hidden_movies"] = [item for item in hidden if item.get("marker") != row.get("marker")]
                self._save_state()

    # ---------- Lightweight widget folders ----------

    def widget_folders(self):
        return [row for row in self.state.get("widget_folders", []) if isinstance(row, dict)]

    def widget_folder_by_id(self, folder_id):
        wanted = str(folder_id or "")
        return next((row for row in self.widget_folders() if str(row.get("id") or "") == wanted), None)

    def recover_widget_folder(self, folder_id):
        """Restore a missing widget folder from the last valid state snapshot when possible."""
        wanted = str(folder_id or "")
        if not wanted or self.widget_folder_by_id(wanted):
            return self.widget_folder_by_id(wanted)
        recovered = None
        for snapshot in self._recovery_snapshots():
            recovered = next((
                dict(row) for row in (snapshot.get("widget_folders") or [])
                if isinstance(row, dict) and str(row.get("id") or "") == wanted
            ), None)
            if recovered:
                break
        if not recovered:
            return None
        self.state["widget_folders"] = self.widget_folders() + [recovered]
        self._dirty_widget_folder_ids.add(wanted)
        self._save_state()
        xbmc.log("curatr restored a missing widget folder from its safety backup", xbmc.LOGWARNING)
        return recovered

    def _recovery_snapshots(self):
        snapshots = []
        seen = set()
        for path in (self.recovery_state_path, self.state_path + ".bak"):
            if not xbmcvfs.exists(path):
                continue
            try:
                snapshot = json.loads(self._read_text(path) or "{}")
                marker = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except Exception:
                continue
            if not isinstance(snapshot, dict) or self._state_content_score(snapshot) <= 0 or marker in seen:
                continue
            seen.add(marker)
            snapshots.append(snapshot)
        snapshots.sort(key=self._state_content_score, reverse=True)
        return snapshots

    def recover_previous_state_interactive(self):
        """Merge missing local lists, folders, prompts and hidden items from a safety snapshot."""
        snapshots = self._recovery_snapshots()
        if not snapshots:
            xbmcgui.Dialog().ok(
                "Recover Previous State",
                "No usable local safety snapshot was found. Lists previously synced to Trakt may still be available there.",
            )
            return False
        additions = {name: [] for name in (
            "ai_lists", "widget_folders", "prompt_templates", "hidden_movies",
        )}
        for name in ("ai_lists", "widget_folders", "prompt_templates", "hidden_movies"):
            current = [dict(row) for row in self.state.get(name, []) if isinstance(row, dict)]
            current_ids = {self._user_collection_key(name, row) for row in current}
            for snapshot in snapshots:
                for row in snapshot.get(name, []):
                    key = self._user_collection_key(name, row)
                    if not isinstance(row, dict) or not key or key in current_ids:
                        continue
                    additions[name].append(dict(row))
                    current_ids.add(key)
        total = sum(len(rows) for rows in additions.values())
        if not total:
            xbmcgui.Dialog().ok("Recover Previous State", "The safety snapshot contains no missing local content.")
            return False
        message = (
            "Restore missing local content from curatr's safety snapshot?\n\n"
            "Lists: %d\nFolders: %d\nSaved prompts: %d\nHidden items: %d\n\n"
            "Existing content will be kept."
        ) % (
            len(additions["ai_lists"]), len(additions["widget_folders"]),
            len(additions["prompt_templates"]), len(additions["hidden_movies"]),
        )
        if not xbmcgui.Dialog().yesno(self.name, message):
            return False
        for name, missing in additions.items():
            if missing:
                self.state[name] = [
                    dict(row) for row in self.state.get(name, []) if isinstance(row, dict)
                ] + missing
        self._dirty_widget_folder_ids.update(
            str(row.get("id") or "") for row in additions["widget_folders"] if row.get("id")
        )
        self._save_state()
        self.record_activity(
            "Recovered %d local item%s from a safety snapshot" % (total, "" if total == 1 else "s"),
            notify=True,
        )
        return True

    def _store_widget_folder(self, updated, previous=None):
        folders = self.widget_folders()
        wanted = str((previous or updated).get("id") or "")
        if not wanted:
            updated = dict(updated)
            updated["id"] = uuid.uuid4().hex
            folders.append(updated)
        else:
            replaced = False
            stored = []
            for row in folders:
                if str(row.get("id") or "") == wanted:
                    stored.append(updated)
                    replaced = True
                else:
                    stored.append(row)
            if not replaced:
                stored.append(updated)
            folders = stored
        self.state["widget_folders"] = folders
        folder_id = str(updated.get("id") or "")
        if folder_id:
            self._dirty_widget_folder_ids.add(folder_id)
        self._save_state()
        return updated

    def _visible_folder_entries(self, entries):
        return [row for row in entries if isinstance(row, dict)
                and str(row.get("id") or "")
                and (row.get("type") != "curatr_list"
                     or self._managed_record_by_id(row.get("list_id")))]

    def _reorder_folder_entries(self, entries, entry_id, operation):
        """Move among visible items while preserving any unresolved stored links."""
        entries = [dict(row) for row in entries if isinstance(row, dict)]
        visible = self._visible_folder_entries(entries)
        index = next((i for i, row in enumerate(visible)
                      if str(row.get("id") or "") == str(entry_id or "")), -1)
        if index < 0:
            return entries
        target = {"move_up": max(0, index - 1), "move_down": min(len(visible) - 1, index + 1),
                  "move_front": 0, "move_back": len(visible) - 1}.get(operation, index)
        item = visible.pop(index)
        visible.insert(target, item)
        ids = {str(row["id"]) for row in visible}
        ordered = iter(visible)
        return [next(ordered) if str(row.get("id") or "") in ids else row for row in entries]

    def _folder_entry_label(self, entry):
        if not isinstance(entry, dict):
            return "Unknown item"
        if entry.get("type") == "curatr_list":
            record = self._managed_record_by_id(entry.get("list_id"))
            return str((record or {}).get("name") or "")
        if entry.get("type") == "provider_list":
            return str(entry.get("name") or ("Trakt list" if entry.get("provider") == "trakt" else "MDBList list"))
        return str(entry.get("name") or "External Shortcut")

    def _folder_content_rows(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        return self._draft_folder_content_rows(folder) if folder else []

    @staticmethod
    def _folder_content_actions(row):
        index = max(0, int(row.get("index") or 0))
        total = max(1, int(row.get("total") or 1))
        first = index == 0
        last = index >= total - 1
        actions = [
            {"key": "move_up", "label": "Move Up", "detail": "Already first" if first else "Move one place earlier", "enabled": not first},
            {"key": "move_down", "label": "Move Down", "detail": "Already last" if last else "Move one place later", "enabled": not last},
            {"key": "move_front", "label": "Move to Front", "detail": "Already at the front" if first else "Make this the first item", "enabled": not first},
            {"key": "move_back", "label": "Move to Back", "detail": "Already at the back" if last else "Make this the last item", "enabled": not last},
        ]
        kind = str(row.get("kind") or "")
        settings_label = {
            "curatr_list": "List Settings",
            "provider_list": "Linked List Settings",
            "external_path": "Shortcut Settings",
        }.get(kind, "Item Settings")
        actions.append({"key": "settings", "label": settings_label, "detail": "Change this item's details"})
        if kind == "provider_list":
            actions.append({"key": "refresh", "label": "Refresh Linked Items", "detail": "Reload this provider list"})
        actions.extend([
            {"key": "artwork", "label": "Artwork", "detail": "Change its icon or fanart"},
            {"key": "remove", "label": "Remove from Folder", "detail": "Keep the source but remove this shortcut"},
        ])
        return actions

    def _move_widget_folder_entry(self, folder_id, entry_id, operation):
        folder, _entry = self._widget_folder_entry(folder_id, entry_id)
        original = folder.get("entries", [])
        entries = self._reorder_folder_entries(original, entry_id, operation)
        if entries == original:
            return folder
        updated = dict(folder)
        updated["entries"] = entries
        updated["updated_at"] = int(time.time())
        return self._store_widget_folder(updated, folder)

    def _add_widget_folder_content_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        choices = [
            ("curatr", "Add a curatr list"),
            ("path", "Add a path"),
            ("trakt", "Add from Trakt"),
            ("mdblist", "Add from MDBList"),
            ("favourite", "Import a Kodi Favourite"),
        ]
        selected = xbmcgui.Dialog().select(
            "Add to %s" % (folder.get("name") or "Folder"),
            [label for _key, label in choices],
        )
        if selected < 0:
            return folder
        action = choices[selected][0]
        if action == "curatr":
            return self.add_list_to_widget_folder_interactive(folder_id=folder_id) or folder
        if action == "path":
            return self.add_external_path_interactive(folder_id) or folder
        if action in ("trakt", "mdblist"):
            return self.add_provider_list_to_widget_folder_interactive(folder_id, action) or folder
        return self.import_kodi_favourite_interactive(folder_id) or folder

    def _folder_content_action(self, folder_id, entry_id, action):
        if action in ("move_up", "move_down", "move_front", "move_back"):
            return self._move_widget_folder_entry(folder_id, entry_id, action)
        folder, entry = self._widget_folder_entry(folder_id, entry_id)
        if action == "settings":
            if entry.get("type") == "curatr_list":
                return self.list_settings_interactive(entry.get("list_id"))
            return self.edit_widget_folder_entry_interactive(folder_id, entry_id, details_only=True)
        if action == "refresh" and entry.get("type") == "provider_list":
            cache_key = self._provider_cache_key(entry.get("provider"), entry.get("provider_list_id"))
            cache = dict(self.state.get("linked_list_cache") or {})
            cache.pop(cache_key, None)
            self.state["linked_list_cache"] = cache
            self._save_state()
            self.linked_provider_list_movies(folder_id, entry_id, force=True)
            return self.widget_folder_by_id(folder_id) or folder
        if action == "artwork":
            return self.edit_widget_folder_entry_artwork_interactive(folder_id, entry_id)
        if action == "remove":
            return self.remove_widget_folder_entry_interactive(folder_id, entry_id)
        return folder

    def _artwork_content_provider(self, record):
        record = record if isinstance(record, dict) else {}
        if isinstance(record.get("movies"), list):
            return lambda: self._fanart_entries_from_movies(record["movies"])
        if record.get("type") == "provider_list":
            return lambda: self._provider_artwork_entries(record)
        return None

    def _edit_compact_artwork(self, heading, value, content_entries=None, preview_record=None):
        original = normalise_list_art(value)
        if content_entries is None:
            content_entries = self._artwork_content_provider(preview_record)
        result, artwork = self._edit_artwork_window(
            heading,
            original,
            preview_record=preview_record,
            content_entries=content_entries,
        )
        if result == "fallback":
            content_fanart = None
            if content_entries:
                def content_fanart():
                    entries = content_entries() or []
                    selected = choose_artwork(
                        xbmcvfs.translatePath(self.addon.getAddonInfo("path")),
                        "Choose fanart from contents", entries, "fanart",
                    ) if entries else None
                    return (selected["source"], selected["label"]) if selected else ("", "")
            return self._edit_compact_artwork_legacy(
                heading, original, content_fanart=content_fanart,
            )
        return normalise_list_art(artwork) if result == "save" else original

    def _edit_compact_artwork_legacy(self, heading, value, content_fanart=None):
        art = normalise_list_art(value)
        while True:
            icon, fanart, _style = list_art_summary({"artwork": art})
            actions = [("icon", "Icon: %s" % icon), ("fanart", "Fanart: %s" % fanart)]
            actions.extend([("reset", "Reset to Automatic"), ("done", "Done")])
            choice = xbmcgui.Dialog().select(heading, [label for _key, label in actions])
            if choice < 0 or actions[choice][0] == "done":
                return art
            action = actions[choice][0]
            if action == "reset":
                art = normalise_list_art({})
                continue
            if action == "icon":
                selected = xbmcgui.Dialog().select("Change icon", [
                    "Automatic", "Choose a curatr icon", "Match current fanart",
                    "Choose a custom image",
                ])
                if selected == 0:
                    art.update({"icon_mode": "auto", "icon_key": "", "icon_source": "", "icon_label": "", "icon_style": "white"})
                elif selected == 1:
                    key, icon_style = self._choose_bundled_art("Choose icon", "icon")
                    if key:
                        art.update({"icon_mode": "bundled", "icon_key": key, "icon_source": "", "icon_label": "", "icon_style": icon_style})
                elif selected == 2:
                    mode = art.get("fanart_mode")
                    if mode == "auto":
                        art.update({"icon_mode": "auto", "icon_key": "", "icon_source": "", "icon_label": ""})
                    elif mode == "bundled":
                        icon_style = "genre_colours" if art.get("fanart_style") == "colour" else "white"
                        art.update({"icon_mode": "bundled", "icon_key": art.get("fanart_key") or "", "icon_source": "", "icon_label": "", "icon_style": icon_style})
                    elif mode in ("item", "person", "custom") and art.get("fanart_source"):
                        art.update({"icon_mode": "person" if mode == "person" else "custom", "icon_key": "", "icon_source": art.get("fanart_source"), "icon_label": art.get("fanart_label") or "Custom"})
                    elif mode == "default":
                        art.update({"icon_mode": "default", "icon_key": "", "icon_source": "", "icon_label": ""})
                elif selected == 3:
                    source = xbmcgui.Dialog().browseSingle(2, "Choose a square icon", "files", ".png|.jpg|.jpeg|.webp")
                    if source:
                        art.update({"icon_mode": "custom", "icon_key": "", "icon_source": str(source), "icon_label": "Custom"})
                continue
            fanart_actions = [
                ("auto", "Automatic"),
                ("bundled", "Choose curatr genre fanart"),
                ("match", "Match current icon"),
            ]
            if content_fanart:
                fanart_actions.append(("contents", "Choose fanart from contents"))
            fanart_actions.extend([
                ("custom", "Choose a custom image"),
                ("default", "Use the default curatr background"),
            ])
            selected = xbmcgui.Dialog().select("Change fanart", [label for _key, label in fanart_actions])
            if selected < 0:
                continue
            fanart_action = fanart_actions[selected][0]
            if fanart_action == "auto":
                art.update({"fanart_mode": "auto", "fanart_key": "", "fanart_source": "", "fanart_label": ""})
            elif fanart_action == "bundled":
                key, fanart_style = self._choose_bundled_art("Choose fanart", "fanart")
                if key:
                    art.update({"fanart_mode": "bundled", "fanart_key": key, "fanart_source": "", "fanart_label": "", "fanart_style": fanart_style})
            elif fanart_action == "match":
                mode = art.get("icon_mode")
                if mode == "auto":
                    art.update({"fanart_mode": "auto", "fanart_key": "", "fanart_source": "", "fanart_label": ""})
                elif mode == "bundled":
                    fanart_style = "colour" if art.get("icon_style") == "genre_colours" else "monochrome"
                    art.update({"fanart_mode": "bundled", "fanart_key": art.get("icon_key") or "", "fanart_source": "", "fanart_label": "", "fanart_style": fanart_style})
                elif mode in ("person", "custom") and art.get("icon_source"):
                    art.update({"fanart_mode": "person" if mode == "person" else "custom", "fanart_key": "", "fanart_source": art.get("icon_source"), "fanart_label": art.get("icon_label") or "Custom"})
                elif mode == "default":
                    art.update({"fanart_mode": "default", "fanart_key": "", "fanart_source": "", "fanart_label": ""})
            elif fanart_action == "contents":
                source, label = content_fanart()
                if source:
                    art.update({"fanart_mode": "item", "fanart_key": "", "fanart_source": source, "fanart_label": label})
            elif fanart_action == "custom":
                source = xbmcgui.Dialog().browseSingle(2, "Choose landscape fanart", "files", ".png|.jpg|.jpeg|.webp")
                if source:
                    art.update({"fanart_mode": "custom", "fanart_key": "", "fanart_source": str(source), "fanart_label": "Custom"})
            elif fanart_action == "default":
                art.update({"fanart_mode": "default", "fanart_key": "", "fanart_source": "", "fanart_label": ""})

    def _draft_folder_content_rows(self, draft):
        entries = self._visible_folder_entries(draft.get("entries", []))
        rows = []
        for index, entry in enumerate(entries):
            kind = str(entry.get("type") or "")
            label = self._folder_entry_label(entry)
            art_record = entry
            if kind == "curatr_list":
                record = self._managed_record_by_id(entry.get("list_id"))
                if record:
                    art_record = record
                    count = len([
                        row for row in record.get("movies", [])
                        if isinstance(row, dict)
                    ])
                    detail = "curatr list  •  %d item%s" % (
                        count, "" if count == 1 else "s",
                    )
            elif kind == "provider_list":
                provider = "Trakt" if entry.get("provider") == "trakt" else "MDBList"
                count = max(0, self._safe_int(entry.get("item_count"), 0))
                detail = "%s list  •  %d item%s" % (
                    provider, count, "" if count == 1 else "s",
                )
            else:
                detail = "Add-on path"
            rows.append({
                "key": str(entry.get("id") or ""),
                "label": label,
                "detail": detail,
                "kind": kind,
                "index": index,
                "total": len(entries),
                "art": list_art_sources(self.addon, art_record),
            })
        return rows

    @staticmethod
    def _draft_folder_content_actions(row):
        index = max(0, int(row.get("index") or 0))
        total = max(1, int(row.get("total") or 1))
        first = index == 0
        last = index >= total - 1
        actions = [
            {"key": "move_up", "label": "Move Up", "enabled": not first},
            {"key": "move_down", "label": "Move Down", "enabled": not last},
            {"key": "move_front", "label": "Move to Front", "enabled": not first},
            {"key": "move_back", "label": "Move to Back", "enabled": not last},
        ]
        if row.get("kind") in ("provider_list", "external_path"):
            actions.extend([
                {"key": "settings", "label": "Item Settings"},
                {"key": "artwork", "label": "Artwork"},
            ])
        actions.append({"key": "remove", "label": "Remove from Folder"})
        return actions

    def _edit_draft_folder_content(self, draft, entry_id, action):
        entries = [
            dict(row) for row in draft.get("entries", []) if isinstance(row, dict)
        ]
        index = next((
            position for position, row in enumerate(entries)
            if str(row.get("id") or "") == str(entry_id or "")
        ), -1)
        if index < 0:
            return draft
        entry = entries[index]
        if action in ("move_up", "move_down", "move_front", "move_back"):
            entries = self._reorder_folder_entries(entries, entry_id, action)
        elif action == "settings" and entry.get("type") in ("provider_list", "external_path"):
            name = xbmcgui.Dialog().input(
                "Item name", defaultt=str(entry.get("name") or "")
            )
            if name and str(name).strip():
                entry["name"] = str(name).strip()
            description = xbmcgui.Dialog().input(
                "Item description (optional)",
                defaultt=str(entry.get("description") or ""),
            )
            entry["description"] = str(description or "").strip()
            entries[index] = entry
        elif action == "artwork" and entry.get("type") in ("provider_list", "external_path"):
            content_entries = None
            if entry.get("type") == "external_path":
                content_entries = lambda: self._fanart_entries_from_external_path(
                    entry.get("path")
                )
            entry["artwork"] = self._edit_compact_artwork(
                "%s artwork" % self._folder_entry_label(entry),
                entry.get("artwork"), content_entries=content_entries,
                preview_record=entry,
            )
            entries[index] = entry
        elif action == "remove":
            if not xbmcgui.Dialog().yesno(
                self.name,
                "Remove %s from this folder?" % self._folder_entry_label(entry),
            ):
                return draft
            entries.pop(index)
        draft["entries"] = entries
        return draft

    def _add_draft_folder_content(self, draft):
        entries = [
            dict(row) for row in draft.get("entries", []) if isinstance(row, dict)
        ]
        folder = {"name": draft.get("name") or "New Folder", "entries": entries}
        choices = [
            ("curatr", "Add a curatr list"),
            ("path", "Add a path"),
            ("trakt", "Add from Trakt"),
            ("mdblist", "Add from MDBList"),
            ("favourite", "Import a Kodi Favourite"),
        ]
        selected = xbmcgui.Dialog().select(
            "Add Item", [label for _key, label in choices]
        )
        if selected < 0:
            return draft
        kind = choices[selected][0]
        entry = None
        if kind == "curatr":
            existing = {
                str(row.get("list_id") or "") for row in entries
                if row.get("type") == "curatr_list"
            }
            available = [
                row for row in self.state.get("ai_lists", [])
                if isinstance(row, dict) and self._record_key(row) not in existing
            ]
            if not available:
                xbmcgui.Dialog().ok(
                    self.name, "Every curatr list is already in this folder."
                )
                return draft
            choice = xbmcgui.Dialog().select(
                "Add a curatr list",
                [row.get("name") or "curatr list" for row in available],
            )
            if choice >= 0:
                entry = {
                    "id": uuid.uuid4().hex,
                    "type": "curatr_list",
                    "list_id": self._record_key(available[choice]),
                }
        elif kind == "path":
            entry = self._choose_external_folder_entry(folder)
        elif kind in ("trakt", "mdblist"):
            entry = self._choose_provider_folder_entry(folder, kind)
        elif kind == "favourite":
            entry = self._choose_favourite_folder_entry(folder)
        if entry:
            entries.append(entry)
            draft["entries"] = entries
        return draft

    def _format_folder_draft_field(self, field, draft):
        if field == "name":
            return "Name  •  %s" % (draft.get("name") or "Not set")
        if field == "description":
            return "Description  •  %s" % (self._shorten_text(draft.get("description"), 56) or "None")
        if field == "manage_contents":
            folder = self.widget_folder_by_id(draft.get("id")) or {}
            count = len(self._visible_folder_entries(folder.get("entries", [])))
            return "Manage Contents  •  %d item%s" % (count, "" if count == 1 else "s")
        icon, fanart, _style = list_art_summary({"name": draft.get("name"), "artwork": draft.get("artwork")})
        return "Artwork  •  %s / %s" % (icon, fanart)

    def _edit_folder_draft_field(self, field, draft):
        if field == "name":
            value = xbmcgui.Dialog().input("Folder name", defaultt=str(draft.get("name") or ""))
            if value and value.strip():
                draft["name"] = value.strip()
        elif field == "description":
            draft["description"] = str(xbmcgui.Dialog().input("Folder description (optional)", defaultt=str(draft.get("description") or "")) or "").strip()
        elif field == "artwork":
            draft["artwork"] = self._edit_compact_artwork(
                "Folder Artwork", draft.get("artwork"), preview_record=draft,
            )
        elif field == "manage_contents":
            self._manage_widget_folder_contents_interactive(draft.get("id"))
        return draft

    def _fallback_draft_folder_contents(self, draft):
        """Keep folder creation usable when a platform cannot load the custom window."""
        while True:
            rows = self._draft_folder_content_rows(draft)
            labels = [str(row.get("label") or "Item") for row in rows]
            choice = xbmcgui.Dialog().select(
                "Folder Contents", labels + ["Add Item", "Done"]
            )
            if choice < 0 or choice == len(rows) + 1:
                return draft
            if choice == len(rows):
                draft = self._add_draft_folder_content(draft)
                continue
            entry = rows[choice]
            actions = self._draft_folder_content_actions(entry)
            selected = xbmcgui.Dialog().select(
                str(entry.get("label") or "Folder Item"),
                [str(row.get("label") or "Action") for row in actions],
            )
            if selected >= 0:
                draft = self._edit_draft_folder_content(
                    draft, entry.get("key"), actions[selected].get("key")
                )

    def _fallback_folder_settings(self, draft, existing=False):
        while True:
            fields = ("name", "description", "artwork")
            content_count = len([
                row for row in draft.get("entries", []) if isinstance(row, dict)
            ])
            content_label = (
                self._format_folder_draft_field("manage_contents", draft)
                if existing else "Contents  •  %d item%s" % (
                    content_count, "" if content_count == 1 else "s",
                )
            )
            save_label = "Save Changes" if existing else "Create Folder"
            labels = [self._format_folder_draft_field(field, draft) for field in fields]
            choice = xbmcgui.Dialog().select(
                "Folder Settings", labels + [content_label, save_label, "Cancel"]
            )
            if choice < 0 or choice == 5:
                return "cancel", draft
            if choice < len(fields):
                draft = self._edit_folder_draft_field(fields[choice], draft)
            elif choice == 3:
                if existing:
                    self._manage_widget_folder_contents_interactive(draft.get("id"))
                else:
                    draft = self._fallback_draft_folder_contents(draft)
            elif choice == 4:
                return "create", draft

    def create_widget_folder_interactive(self, manage_after=True):
        draft = {
            "name": "New Folder", "description": "", "artwork": normalise_list_art({}),
            "entries": [],
        }
        addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        action, draft = edit_folder_settings(
            addon_path, draft,
            self._edit_folder_draft_field, self._format_folder_draft_field,
            content_rows=self._draft_folder_content_rows,
            content_actions=self._draft_folder_content_actions,
            content_handler=self._edit_draft_folder_content,
            content_add_handler=self._add_draft_folder_content,
            add_art=menu_source(addon_path, "menu_add_folder.png"),
        )
        if action == "failed":
            action, draft = self._fallback_folder_settings(draft)
        if action != "create":
            return None
        name = str(draft.get("name") or "").strip()
        if not name:
            xbmcgui.Dialog().ok(self.name, "Enter a folder name first.")
            return None
        if any(self._normalised_restore_name(row.get("name")) == self._normalised_restore_name(name) for row in self.widget_folders()):
            xbmcgui.Dialog().ok(self.name, "A folder already uses that name.")
            return None
        now = int(time.time())
        folder = {
            "id": uuid.uuid4().hex,
            "name": name,
            "description": str(draft.get("description") or "").strip(),
            "artwork": normalise_list_art(draft.get("artwork")),
            "entries": [
                dict(row) for row in draft.get("entries", []) if isinstance(row, dict)
            ],
            "created_at": now,
            "updated_at": now,
        }
        self.state["widget_folders"] = self.widget_folders() + [folder]
        self._dirty_widget_folder_ids.add(folder["id"])
        self._save_state()
        self.record_activity("Created folder: %s" % folder["name"], notify=True)
        return folder

    def add_list_to_widget_folder_interactive(self, list_id="", folder_id=""):
        record = self._managed_record_by_id(list_id) if list_id else None
        folders = self.widget_folders()
        if not folders:
            if not xbmcgui.Dialog().yesno(self.name, "Create a folder first?"):
                return None
            created = self.create_widget_folder_interactive(manage_after=False)
            folders = self.widget_folders()
            if not created:
                return None
        folder = self.widget_folder_by_id(folder_id) if folder_id else None
        if not folder:
            choice = xbmcgui.Dialog().select("Choose Folder", [row.get("name") or "Folder" for row in folders])
            if choice < 0:
                return None
            folder = folders[choice]
        if not record:
            existing = {str(row.get("list_id") or "") for row in folder.get("entries", []) if isinstance(row, dict) and row.get("type") == "curatr_list"}
            lists = [row for row in self.state.get("ai_lists", []) if isinstance(row, dict) and self._record_key(row) not in existing]
            if not lists:
                xbmcgui.Dialog().ok(self.name, "Every curatr list is already in this folder.")
                return folder
            choice = xbmcgui.Dialog().select("Add a curatr list", [row.get("name") or "curatr list" for row in lists])
            if choice < 0:
                return None
            record = lists[choice]
        key = self._record_key(record)
        entries = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)]
        if any(row.get("type") == "curatr_list" and str(row.get("list_id")) == key for row in entries):
            xbmcgui.Dialog().ok(self.name, "That list is already in this folder.")
            return folder
        entries.append({"id": uuid.uuid4().hex, "type": "curatr_list", "list_id": key})
        updated = dict(folder)
        updated["entries"] = entries
        updated["updated_at"] = int(time.time())
        self._store_widget_folder(updated, folder)
        self.record_activity("Added %s to %s" % (record.get("name") or "curatr list", updated.get("name")), notify=True)
        return updated

    def add_media_to_list_interactive(self, media):
        """Add a selected Kodi list item to an existing compatible curatr list."""
        media = dict(media or {})
        media_type = "show" if str(media.get("media_type") or "movie") == "show" else "movie"
        title = str(media.get("title") or "").strip()
        if not title:
            raise RuntimeError("curatr could not read the selected title.")
        compatible = []
        for row in self.state.get("ai_lists", []):
            if not isinstance(row, dict):
                continue
            content_type = str(row.get("content_type") or "movies")
            if content_type == "both" or (media_type == "movie" and content_type == "movies") or (media_type == "show" and content_type == "shows"):
                compatible.append(row)
        if not compatible:
            raise RuntimeError("Create a compatible curatr list first.")
        choice = xbmcgui.Dialog().select("Add to curatr list", [str(row.get("name") or "curatr list") for row in compatible])
        if choice < 0:
            return None
        record = compatible[choice]
        ids = dict(media.get("ids") or {})
        marker = (media_type, self._normalise_title(title), self._safe_int(media.get("year"), 0))
        for existing in record.get("movies", []):
            if not isinstance(existing, dict):
                continue
            existing_ids = existing.get("ids") or {}
            same_id = any(ids.get(key) and str(ids.get(key)) == str(existing_ids.get(key)) for key in ("tmdb", "imdb", "tvdb"))
            existing_marker = (
                str(existing.get("media_type") or "movie"), self._normalise_title(existing.get("title")),
                self._safe_int(existing.get("year"), 0),
            )
            if same_id or existing_marker == marker:
                xbmcgui.Dialog().ok(self.name, "%s is already in %s." % (title, record.get("name") or "that list"))
                return record
        compact = self._compact_movie(media)
        compact["media_type"] = media_type
        updated = dict(record)
        updated["movies"] = [dict(row) for row in record.get("movies", []) if isinstance(row, dict)] + [compact]
        updated["count"] = max(self._safe_int(record.get("count"), 0), len(updated["movies"]))
        updated["last_result_count"] = len(updated["movies"])
        updated["local_changed_at"] = updated["updated_at"] = int(time.time())
        self._store_managed_record(updated, record)
        self._save_state()
        if updated.get("sync_to_trakt") and self._has_oauth():
            try:
                updated = self.sync_list_to_trakt(updated.get("local_id"), silent=True)
            except Exception as exc:
                self.record_activity("Added %s locally; Trakt sync was skipped" % title, level="warning", detail=str(exc), notify=False)
        self.record_activity("Added %s to %s" % (title, updated.get("name") or "curatr list"), notify=True)
        return updated

    def _choose_provider_folder_entry(self, folder, provider):
        provider = str(provider or "").strip().lower()
        if provider == "trakt":
            if not self._has_oauth():
                raise RuntimeError("Connect Trakt in Settings to perform this action.")
            rows = self.trakt.lists()
            service_name = "Trakt"
            choices = []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                ids = row.get("ids") or {}
                list_id = ids.get("trakt") if isinstance(ids, dict) else None
                list_id = list_id or row.get("id") or (ids.get("slug") if isinstance(ids, dict) else None)
                name = str(row.get("name") or "").strip()
                if list_id in (None, "") or not name:
                    continue
                choices.append({
                    "id": str(list_id), "name": name,
                    "description": str(row.get("description") or "").strip(),
                    "items": row.get("item_count"),
                })
        elif provider == "mdblist":
            if not self.mdblist or not self.mdblist.api_key:
                raise RuntimeError("Connect MDBList in Settings to perform this action.")
            choices = self.mdblist.user_lists()
            service_name = "MDBList"
        else:
            raise RuntimeError("That linked-list provider is not supported.")

        existing = {
            str(row.get("provider_list_id") or "") for row in folder.get("entries", [])
            if isinstance(row, dict) and row.get("type") == "provider_list" and row.get("provider") == provider
        }
        choices = [row for row in choices if isinstance(row, dict) and str(row.get("id") or "") not in existing]
        if not choices:
            xbmcgui.Dialog().ok(self.name, "No unused %s movie lists were found for this folder." % service_name)
            return None
        labels = [
            "%s%s" % (
                row.get("name") or (service_name + " list"),
                " (%s items)" % row.get("items") if row.get("items") not in (None, "") else "",
            ) for row in choices
        ]
        selected = xbmcgui.Dialog().select("Add from %s" % service_name, labels)
        if selected < 0:
            return None
        chosen = choices[selected]
        name = str(chosen.get("name") or (service_name + " list")).strip()
        description = str(chosen.get("description") or "Linked directly to your %s account." % service_name).strip()
        artwork = normalise_list_art({"icon_mode": "default", "fanart_mode": "default"})
        if xbmcgui.Dialog().yesno(self.name, "%s added. Customise its name, description or artwork now?" % name):
            custom_name = xbmcgui.Dialog().input("List name", defaultt=name)
            if custom_name and custom_name.strip():
                name = custom_name.strip()
            custom_description = xbmcgui.Dialog().input("List description", defaultt=description)
            description = str(custom_description or "").strip()
            artwork = self._edit_compact_artwork(
                "%s artwork" % name,
                artwork,
                preview_record={
                    "name": name, "description": description,
                    "type": "provider_list", "provider": provider,
                    "provider_list_id": str(chosen.get("id")),
                },
            )
        return {
            "id": uuid.uuid4().hex, "type": "provider_list",
            "provider": provider, "provider_list_id": str(chosen.get("id")),
            "name": name, "description": description, "artwork": artwork,
            "item_count": self._safe_int(chosen.get("items"), 0),
        }

    def add_provider_list_to_widget_folder_interactive(self, folder_id, provider):
        """Add a lightweight account-list reference without copying its contents."""
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        entry = self._choose_provider_folder_entry(folder, provider)
        if not entry:
            return folder
        updated = dict(folder)
        updated["entries"] = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)] + [entry]
        updated["updated_at"] = int(time.time())
        updated = self._store_widget_folder(updated, folder)
        try:
            self.linked_provider_list_movies(updated.get("id"), entry.get("id"), force=True)
        except Exception as exc:
            self.record_activity(
                "%s was added, but its contents could not be loaded yet" % entry.get("name"),
                level="warning", detail=str(exc), notify=True,
            )
        service_name = "Trakt" if entry.get("provider") == "trakt" else "MDBList"
        self.record_activity(
            "Added %s %s to %s" % (service_name, entry.get("name"), updated.get("name")),
            notify=True,
        )
        return updated

    @staticmethod
    def _provider_cache_key(provider, provider_list_id):
        return "%s:%s" % (str(provider or "").strip().lower(), str(provider_list_id or "").strip())

    def _fetch_provider_list_movies(self, provider, provider_list_id):
        if provider == "trakt":
            if not self._has_oauth():
                raise RuntimeError("Reconnect Trakt to open this linked list.")
            response = self.trakt.list_items(provider_list_id, limit=250, extended=True)
            movies = []
            for row in response if isinstance(response, list) else []:
                if not isinstance(row, dict):
                    continue
                item = row.get("show") if isinstance(row.get("show"), dict) else row.get("movie")
                if isinstance(item, dict):
                    item = dict(item)
                    item["media_type"] = "show" if isinstance(row.get("show"), dict) else "movie"
                    movies.append(item)
        elif provider == "mdblist":
            if not self.mdblist or not self.mdblist.api_key:
                raise RuntimeError("Connect MDBList in Settings to perform this action.")
            movies = self.mdblist.fetch_list_id(provider_list_id, limit=250)
        else:
            raise RuntimeError("That linked-list provider is not supported.")
        return [row for row in movies if isinstance(row, dict) and row.get("title")][:250]

    def _provider_artwork_entries(self, entry):
        """Preview linked contents, including links in an unsaved folder draft."""
        provider = str(entry.get("provider") or "").strip().lower()
        list_id = str(entry.get("provider_list_id") or "").strip()
        cache = self.state.get("linked_list_cache") or {}
        cached = cache.get(self._provider_cache_key(provider, list_id)) if isinstance(cache, dict) else None
        if isinstance(cached, dict) and isinstance(cached.get("movies"), list):
            movies = cached["movies"]
        else:
            movies = self._fetch_provider_list_movies(provider, list_id)
        return self._fanart_entries_from_movies(movies)

    def linked_provider_list_movies(self, folder_id, entry_id, force=False):
        """Load a linked list lazily, with a bounded cache and stale fallback."""
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        entry = next((
            row for row in folder.get("entries", [])
            if isinstance(row, dict) and str(row.get("id") or "") == str(entry_id or "")
        ), None)
        if not entry or entry.get("type") != "provider_list":
            raise RuntimeError("That linked list no longer exists in this folder.")
        provider = str(entry.get("provider") or "").strip().lower()
        provider_list_id = str(entry.get("provider_list_id") or "").strip()
        cache_key = self._provider_cache_key(provider, provider_list_id)
        cache = self.state.get("linked_list_cache") or {}
        cached = cache.get(cache_key) if isinstance(cache, dict) else None
        cache_seconds = 30 * 60
        if not force and isinstance(cached, dict):
            cached_at = self._safe_int(cached.get("cached_at"), 0)
            movies = cached.get("movies")
            if isinstance(movies, list) and time.time() - cached_at < cache_seconds:
                return entry, movies

        try:
            movies = self._fetch_provider_list_movies(provider, provider_list_id)
            cache = dict(cache) if isinstance(cache, dict) else {}
            cache[cache_key] = {"cached_at": int(time.time()), "movies": movies}
            if len(cache) > 20:
                ordered = sorted(
                    cache.items(), key=lambda pair: self._safe_int((pair[1] or {}).get("cached_at"), 0), reverse=True
                )
                cache = dict(ordered[:15])
            self.state["linked_list_cache"] = cache
            entry["item_count"] = len(movies)
            folder["updated_at"] = int(time.time())
            self._dirty_widget_folder_ids.add(str(folder.get("id") or ""))
            self._save_state()
            return entry, movies
        except Exception as exc:
            if isinstance(cached, dict) and isinstance(cached.get("movies"), list):
                self.record_activity(
                    "Using cached %s list: %s" % (provider.title(), entry.get("name") or "Linked list"),
                    level="warning", detail=str(exc), notify=False,
                )
                return entry, cached.get("movies")
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(str(exc))

    @staticmethod
    def _valid_external_plugin_path(value):
        path = str(value or "").strip()
        return path if path.startswith("plugin://") and "\n" not in path and "\r" not in path and len(path) <= 2048 else ""

    @staticmethod
    def _kodi_json_rpc(method, params=None):
        request = {"jsonrpc": "2.0", "id": 1, "method": str(method)}
        if isinstance(params, dict):
            request["params"] = params
        try:
            response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
        except Exception as exc:
            raise RuntimeError("Kodi could not open the add-on browser: %s" % exc)
        if not isinstance(response, dict) or response.get("error"):
            message = ((response.get("error") or {}).get("message") if isinstance(response, dict) else "") or "Kodi returned an error."
            raise RuntimeError(str(message))
        return response.get("result") or {}

    def _browse_external_plugin_path(self):
        """Browse installed video plug-ins using Kodi's own directory API."""
        result = self._kodi_json_rpc("Addons.GetAddons", {
            "type": "xbmc.addon.video",
            "enabled": True,
            "properties": ["name", "thumbnail", "enabled"],
        })
        addons = []
        for row in result.get("addons", []) if isinstance(result, dict) else []:
            if not isinstance(row, dict) or row.get("enabled") is False:
                continue
            addon_id = str(row.get("addonid") or "").strip()
            if not addon_id or addon_id == "plugin.video.curatr":
                continue
            addons.append({
                "id": addon_id,
                "name": str(row.get("name") or addon_id).strip() or addon_id,
                "thumbnail": str(row.get("thumbnail") or ""),
            })
        addons.sort(key=lambda row: row["name"].casefold())
        if not addons:
            raise RuntimeError("Kodi did not return any installed video add-ons.")
        selected = xbmcgui.Dialog().select("Choose a video add-on", [row["name"] for row in addons])
        if selected < 0:
            return None
        chosen = addons[selected]
        current_path = "plugin://%s/" % chosen["id"]
        current_name = chosen["name"]
        current_thumbnail = chosen["thumbnail"]
        trail = []
        while True:
            try:
                directory = self._kodi_json_rpc("Files.GetDirectory", {
                    "directory": current_path,
                    "media": "video",
                    "properties": ["title", "thumbnail", "fanart"],
                })
                children = []
                for row in directory.get("files", []) if isinstance(directory, dict) else []:
                    if not isinstance(row, dict) or str(row.get("filetype") or "") != "directory":
                        continue
                    path = self._valid_external_plugin_path(row.get("file"))
                    if not path:
                        continue
                    children.append({
                        "path": path,
                        "name": str(row.get("label") or row.get("title") or "Folder").strip() or "Folder",
                        "thumbnail": str(row.get("thumbnail") or row.get("fanart") or ""),
                    })
            except Exception as exc:
                xbmcgui.Dialog().ok(self.name, "This add-on did not make that page available to Kodi's browser.\n\n%s" % exc)
                if trail:
                    current_path, current_name, current_thumbnail = trail.pop()
                    continue
                return None
            choices = ["[B]Choose This Path[/B]"] + [row["name"] for row in children]
            selected = xbmcgui.Dialog().select(current_name, choices)
            if selected == 0:
                return current_path, current_name, current_thumbnail
            if selected < 0:
                if not trail:
                    return None
                current_path, current_name, current_thumbnail = trail.pop()
                continue
            child = children[selected - 1]
            trail.append((current_path, current_name, current_thumbnail))
            current_path = child["path"]
            current_name = child["name"]
            current_thumbnail = child["thumbnail"] or current_thumbnail

    def _choose_external_folder_entry(self, folder):
        method = xbmcgui.Dialog().select("Add an external shortcut", [
            "Browse installed video add-ons", "Enter a plugin path manually",
        ])
        if method < 0:
            return None
        suggested_name = ""
        suggested_thumbnail = ""
        if method == 0:
            selected = self._browse_external_plugin_path()
            if not selected:
                return None
            path, suggested_name, suggested_thumbnail = selected
        else:
            path = self._valid_external_plugin_path(xbmcgui.Dialog().input("External plugin path"))
        if not path:
            xbmcgui.Dialog().ok(self.name, "Enter a complete path beginning with plugin://")
            return None
        existing_paths = {
            str(row.get("path") or "") for row in folder.get("entries", [])
            if isinstance(row, dict) and row.get("type") == "external_path"
        }
        if path in existing_paths:
            xbmcgui.Dialog().ok(self.name, "That page is already in this folder.")
            return None
        name = xbmcgui.Dialog().input("Shortcut name", defaultt=suggested_name)
        if not name or not str(name).strip():
            return None
        description = xbmcgui.Dialog().input("Shortcut description (optional)")
        artwork = normalise_list_art({"icon_mode": "default", "fanart_mode": "default"})
        if suggested_thumbnail.startswith(("special://", "/", "image://", "http://", "https://")):
            artwork.update({"icon_mode": "custom", "icon_source": suggested_thumbnail, "icon_label": str(name).strip()})
        if xbmcgui.Dialog().yesno(self.name, "Customise this shortcut's artwork now?"):
            artwork = self._edit_compact_artwork(
                "Shortcut artwork",
                artwork,
                preview_record={"name": name, "description": description},
                content_entries=lambda: self._fanart_entries_from_external_path(path),
            )
        return {
            "id": uuid.uuid4().hex, "type": "external_path",
            "name": str(name).strip(), "description": str(description or "").strip(),
            "path": path, "artwork": artwork,
        }

    def add_external_path_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        entry = self._choose_external_folder_entry(folder)
        if not entry:
            return folder
        updated = dict(folder)
        updated["entries"] = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)] + [entry]
        updated["updated_at"] = int(time.time())
        self._store_widget_folder(updated, folder)
        self.record_activity("Added external shortcut to %s" % updated.get("name"), notify=True)
        return updated

    def add_external_shortcut_interactive(self, path, suggested_name="", suggested_thumbnail=""):
        """Store a selected Kodi add-on folder without reopening curatr's path browser."""
        path = self._valid_external_plugin_path(path)
        if not path:
            raise RuntimeError("Only complete plugin:// folder paths can be added to curatr folders.")
        folders = self.widget_folders()
        if not folders:
            if not xbmcgui.Dialog().yesno(self.name, "Create a curatr folder first?"):
                return None
            if not self.create_widget_folder_interactive():
                return None
            folders = self.widget_folders()
        choice = xbmcgui.Dialog().select("Add to curatr Folder", [str(row.get("name") or "Folder") for row in folders])
        if choice < 0:
            return None
        folder = folders[choice]
        if any(
            isinstance(row, dict) and row.get("type") == "external_path" and str(row.get("path") or "") == path
            for row in folder.get("entries", [])
        ):
            xbmcgui.Dialog().ok(self.name, "That page is already in this folder.")
            return folder
        name = xbmcgui.Dialog().input("Shortcut name", defaultt=str(suggested_name or "").strip())
        if not name or not str(name).strip():
            return None
        artwork = normalise_list_art({"icon_mode": "default", "fanart_mode": "default"})
        thumbnail = str(suggested_thumbnail or "").strip()
        if thumbnail.startswith(("special://", "/", "image://", "http://", "https://")):
            artwork.update({"icon_mode": "custom", "icon_source": thumbnail, "icon_label": str(name).strip()})
        entry = {
            "id": uuid.uuid4().hex, "type": "external_path", "name": str(name).strip(),
            "description": "", "path": path, "artwork": artwork,
        }
        updated = dict(folder)
        updated["entries"] = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)] + [entry]
        updated["updated_at"] = int(time.time())
        self._store_widget_folder(updated, folder)
        self.record_activity("Added external shortcut to %s" % updated.get("name"), notify=True)
        return updated

    def _choose_favourite_folder_entry(self, folder):
        request = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Favourites.GetFavourites",
            "params": {"properties": ["path", "thumbnail", "window", "windowparameter"]},
        })
        try:
            response = json.loads(xbmc.executeJSONRPC(request))
        except Exception as exc:
            raise RuntimeError("Kodi Favourites could not be read: %s" % exc)
        if not isinstance(response, dict) or response.get("error"):
            raise RuntimeError("Kodi did not allow curatr to read Favourites on this installation.")
        favourites = []
        rows = ((response.get("result") or {}).get("favourites") or []) if isinstance(response, dict) else []
        existing_paths = {str(row.get("path") or "") for row in folder.get("entries", []) if isinstance(row, dict) and row.get("type") == "external_path"}
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = self._valid_external_plugin_path(row.get("path") or row.get("windowparameter"))
            if not path or path in existing_paths:
                continue
            favourites.append((str(row.get("title") or "Kodi Favourite"), path, str(row.get("thumbnail") or "")))
        if not favourites:
            xbmcgui.Dialog().ok(self.name, "No unused plugin paths were found in Kodi Favourites. Add the page to Kodi Favourites first, then try again.")
            return None
        choice = xbmcgui.Dialog().select("Import from Kodi Favourites", [row[0] for row in favourites])
        if choice < 0:
            return None
        name, path, thumbnail = favourites[choice]
        artwork = normalise_list_art({"icon_mode": "default", "fanart_mode": "default"})
        if thumbnail.startswith(("special://", "/")) or thumbnail.startswith("image://"):
            artwork.update({"icon_mode": "custom", "icon_source": thumbnail, "icon_label": "Custom"})
        return {
            "id": uuid.uuid4().hex, "type": "external_path", "name": name,
            "description": "Imported from Kodi Favourites.", "path": path, "artwork": artwork,
        }

    def import_kodi_favourite_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        entry = self._choose_favourite_folder_entry(folder)
        if not entry:
            return folder
        updated = dict(folder)
        updated["entries"] = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)] + [entry]
        updated["updated_at"] = int(time.time())
        self._store_widget_folder(updated, folder)
        if xbmcgui.Dialog().yesno(self.name, "Favourite imported. Customise its artwork now?"):
            return self.edit_widget_folder_entry_artwork_interactive(folder_id, entry["id"])
        return updated

    def _widget_folder_entry(self, folder_id, entry_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        entry = next((
            row for row in folder.get("entries", [])
            if isinstance(row, dict) and str(row.get("id")) == str(entry_id)
        ), None)
        if not entry:
            raise RuntimeError("That folder item no longer exists.")
        return folder, entry

    def _entry_content_artwork(self, folder, entry):
        entry_type = str(entry.get("type") or "")
        if entry_type == "provider_list":
            return lambda: self._fanart_entries_from_movies(
                self.linked_provider_list_movies(folder.get("id"), entry.get("id"))[1]
            )
        if entry_type == "external_path":
            return lambda: self._fanart_entries_from_external_path(entry.get("path"))
        return None

    def edit_widget_folder_entry_artwork_interactive(self, folder_id, entry_id):
        folder, entry = self._widget_folder_entry(folder_id, entry_id)
        if entry.get("type") == "curatr_list":
            return self.list_artwork_interactive(entry.get("list_id"))
        updated_entry = dict(entry)
        original = normalise_list_art(entry.get("artwork"))
        artwork = self._edit_compact_artwork(
            "%s artwork" % self._folder_entry_label(entry), entry.get("artwork"),
            content_entries=self._entry_content_artwork(folder, entry),
            preview_record=entry,
        )
        if artwork == original:
            return folder
        updated_entry["artwork"] = artwork
        entries = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)]
        index = next(i for i, row in enumerate(entries) if str(row.get("id")) == str(entry_id))
        entries[index] = updated_entry
        updated = dict(folder); updated["entries"] = entries; updated["updated_at"] = int(time.time())
        return self._store_widget_folder(updated, folder)

    def remove_widget_folder_entry_interactive(self, folder_id, entry_id):
        folder, entry = self._widget_folder_entry(folder_id, entry_id)
        if not xbmcgui.Dialog().yesno(self.name, "Remove '%s' from this folder?" % self._folder_entry_label(entry)):
            return folder
        updated = dict(folder)
        updated["entries"] = [
            dict(row) for row in folder.get("entries", [])
            if isinstance(row, dict) and str(row.get("id")) != str(entry_id)
        ]
        updated["updated_at"] = int(time.time())
        return self._store_widget_folder(updated, folder)

    def edit_widget_folder_artwork_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        original = normalise_list_art(folder.get("artwork"))
        artwork = self._edit_compact_artwork(
            "Folder artwork", original, preview_record=folder,
        )
        if artwork == original:
            return folder
        updated = dict(folder)
        updated["artwork"] = artwork
        updated["updated_at"] = int(time.time())
        return self._store_widget_folder(updated, folder)

    def delete_widget_folder_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        name = str(folder.get("name") or "Folder")
        if not xbmcgui.Dialog().yesno(
            self.name, "Delete the folder '%s'?\n\nIts lists and linked sources will not be deleted." % name,
        ):
            return False
        self.state["widget_folders"] = [
            row for row in self.widget_folders() if str(row.get("id")) != str(folder_id)
        ]
        self._deleted_widget_folder_ids.add(str(folder_id))
        self._save_state()
        self.record_activity("Deleted widget folder: %s" % name, notify=True)
        return True

    def edit_widget_folder_entry_interactive(self, folder_id, entry_id, details_only=False):
        folder, entry = self._widget_folder_entry(folder_id, entry_id)
        while True:
            actions = []
            if entry.get("type") == "external_path":
                actions.extend([
                    ("name", "Name"), ("description", "Description"), ("path", "Plugin path"),
                ])
            elif entry.get("type") == "provider_list":
                actions.extend([
                    ("name", "Name"), ("description", "Description"),
                    ("refresh", "Refresh cached items"),
                ])
            if not details_only:
                actions.extend([
                    ("move_up", "Move up"), ("move_down", "Move down"),
                    ("move_front", "Move to front"), ("move_back", "Move to back"),
                ])
            if not actions:
                return folder
            choice = xbmcgui.Dialog().select(
                self._folder_entry_label(entry), [label for _key, label in actions],
            )
            if choice < 0:
                return folder
            action = actions[choice][0]
            entries = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)]
            index = next((i for i, row in enumerate(entries) if str(row.get("id")) == str(entry_id)), -1)
            if index < 0:
                return folder
            if action in ("move_up", "move_down", "move_front", "move_back"):
                folder = self._move_widget_folder_entry(folder_id, entry_id, action)
                continue
            if entry.get("type") == "external_path":
                updated_entry = dict(entry)
                if action == "name":
                    value = xbmcgui.Dialog().input("Shortcut name", defaultt=str(entry.get("name") or ""))
                    if value and value.strip():
                        updated_entry["name"] = value.strip()
                elif action == "description":
                    value = xbmcgui.Dialog().input("Shortcut description", defaultt=str(entry.get("description") or ""))
                    updated_entry["description"] = str(value or "").strip()
                elif action == "path":
                    value = self._valid_external_plugin_path(xbmcgui.Dialog().input("Plugin path", defaultt=str(entry.get("path") or "")))
                    if value:
                        updated_entry["path"] = value
                    else:
                        xbmcgui.Dialog().ok(self.name, "Enter a complete path beginning with plugin://")
                entries[index] = updated_entry
                entry = updated_entry
            elif entry.get("type") == "provider_list":
                updated_entry = dict(entry)
                if action == "name":
                    value = xbmcgui.Dialog().input("List name", defaultt=str(entry.get("name") or ""))
                    if value and value.strip():
                        updated_entry["name"] = value.strip()
                elif action == "description":
                    value = xbmcgui.Dialog().input("List description", defaultt=str(entry.get("description") or ""))
                    updated_entry["description"] = str(value or "").strip()
                elif action == "refresh":
                    cache_key = self._provider_cache_key(entry.get("provider"), entry.get("provider_list_id"))
                    cache = dict(self.state.get("linked_list_cache") or {})
                    cache.pop(cache_key, None)
                    self.state["linked_list_cache"] = cache
                    self._save_state()
                    xbmcgui.Dialog().notification(self.name, "Linked list will refresh when next opened", xbmcgui.NOTIFICATION_INFO, 3000)
                entries[index] = updated_entry
                entry = updated_entry
            updated = dict(folder); updated["entries"] = entries; updated["updated_at"] = int(time.time())
            folder = self._store_widget_folder(updated, folder)

    def _manage_widget_folder_contents_legacy(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        while True:
            choices = [
                "Add a curatr list", "Add from Trakt", "Add from MDBList", "Add an external shortcut",
                "Import from Kodi Favourites", "Manage folder items",
            ]
            choice = xbmcgui.Dialog().select(folder.get("name") or "Folder", choices)
            if choice < 0:
                return folder
            if choice == 0:
                folder = self.add_list_to_widget_folder_interactive(folder_id=folder_id) or folder
            elif choice == 1:
                folder = self.add_provider_list_to_widget_folder_interactive(folder_id, "trakt") or folder
            elif choice == 2:
                folder = self.add_provider_list_to_widget_folder_interactive(folder_id, "mdblist") or folder
            elif choice == 3:
                folder = self.add_external_path_interactive(folder_id) or folder
            elif choice == 4:
                folder = self.import_kodi_favourite_interactive(folder_id) or folder
            elif choice == 5:
                entries = self._visible_folder_entries(folder.get("entries", []))
                if not entries:
                    xbmcgui.Dialog().ok(self.name, "This folder is empty.")
                    continue
                selected = xbmcgui.Dialog().select("Manage folder items", [self._folder_entry_label(row) for row in entries])
                if selected >= 0:
                    folder = self.edit_widget_folder_entry_interactive(folder_id, entries[selected].get("id")) or folder

    def _manage_widget_folder_contents_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        result = manage_folder_contents(
            addon_path,
            "%s Contents" % (folder.get("name") or "Folder"),
            "Choose an item to reorder or edit, or use Add Item for a new source.",
            lambda: self._folder_content_rows(folder_id),
            self._folder_content_actions,
            lambda entry_id, action: self._folder_content_action(folder_id, entry_id, action),
            lambda: self._add_widget_folder_content_interactive(folder_id),
            menu_source(addon_path, "menu_add_folder.png"),
        )
        if result == "fallback":
            return self._manage_widget_folder_contents_legacy(folder_id)
        return self.widget_folder_by_id(folder_id) or folder

    def manage_widget_folder_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        draft = {
            "id": str(folder.get("id") or ""),
            "name": str(folder.get("name") or "Folder"),
            "description": str(folder.get("description") or ""),
            "artwork": normalise_list_art(folder.get("artwork")),
        }
        action, draft = edit_folder_settings(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")), draft,
            self._edit_folder_draft_field, self._format_folder_draft_field, existing=True,
        )
        if action == "failed":
            action, draft = self._fallback_folder_settings(draft, existing=True)
        if action != "create":
            return self.widget_folder_by_id(folder_id) or folder
        name = str(draft.get("name") or "").strip()
        if not name:
            xbmcgui.Dialog().ok(self.name, "Enter a folder name first.")
            return folder
        duplicate = any(
            str(row.get("id") or "") != str(folder_id)
            and self._normalised_restore_name(row.get("name")) == self._normalised_restore_name(name)
            for row in self.widget_folders()
        )
        if duplicate:
            xbmcgui.Dialog().ok(self.name, "A folder already uses that name.")
            return folder
        current = self.widget_folder_by_id(folder_id) or folder
        updated = dict(current)
        updated.update({
            "name": name,
            "description": str(draft.get("description") or "").strip(),
            "artwork": normalise_list_art(draft.get("artwork")),
            "updated_at": int(time.time()),
        })
        saved = self._store_widget_folder(updated, current)
        self.record_activity("Updated folder settings: %s" % name, notify=True)
        return saved

    def _folder_manager_rows(self):
        rows = []
        for folder in self.widget_folders():
            folder_id = str(folder.get("id") or "")
            if not folder_id:
                continue
            entries = self._visible_folder_entries(folder.get("entries", []))
            curatr_count = sum(row.get("type") == "curatr_list" for row in entries)
            linked_count = sum(row.get("type") == "provider_list" for row in entries)
            path_count = sum(row.get("type") == "external_path" for row in entries)
            parts = []
            if curatr_count:
                parts.append("%d local" % curatr_count)
            if linked_count:
                parts.append("%d linked" % linked_count)
            if path_count:
                parts.append("%d path%s" % (path_count, "" if path_count == 1 else "s"))
            rows.append({
                "key": folder_id,
                "label": str(folder.get("name") or "Folder"),
                "detail": "%d item%s  •  Custom folder" % (
                    len(entries), "" if len(entries) == 1 else "s",
                ),
                "status": "  •  ".join(parts) if parts else "Empty  •  Ready for lists or paths",
                "summary": self._shorten_text(folder.get("description") or "No description", 96),
                "art": list_art_sources(self.addon, folder),
            })
        return rows

    @staticmethod
    def _folder_manager_actions(_row):
        return [
            {"key": "contents", "label": "Manage Contents", "detail": "Add, reorder or edit folder items"},
            {"key": "settings", "label": "Folder Settings", "detail": "Change its name and description"},
            {"key": "artwork", "label": "Artwork", "detail": "Change its icon or fanart"},
            {"key": "delete", "label": "Delete Folder", "detail": "Lists and linked sources are kept"},
        ]

    def _folder_manager_action(self, folder_id, action):
        if action == "contents":
            return self._manage_widget_folder_contents_interactive(folder_id)
        if action == "settings":
            return self.manage_widget_folder_interactive(folder_id)
        if action == "artwork":
            return self.edit_widget_folder_artwork_interactive(folder_id)
        if action == "delete":
            return self.delete_widget_folder_interactive(folder_id)
        return self.widget_folder_by_id(folder_id)

    def _manage_widget_folders_legacy(self):
        while True:
            folders = self.widget_folders()
            choices = ["Create a Folder"] + [row.get("name") or "Folder" for row in folders]
            choice = xbmcgui.Dialog().select("Folders", choices)
            if choice < 0:
                return None
            if choice == 0:
                self.create_widget_folder_interactive()
            else:
                self.manage_widget_folder_interactive(folders[choice - 1].get("id"))

    def manage_widget_folders_interactive(self):
        addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        result = manage_collection(
            addon_path,
            "Manage Folders",
            "Select a folder to change its settings or organise its contents.",
            "Create Folder",
            self._folder_manager_rows,
            self._folder_manager_actions,
            self._folder_manager_action,
            self.create_widget_folder_interactive,
        )
        if result == "fallback":
            return self._manage_widget_folders_legacy()
        return result

    def why_recommended(self, list_id, trakt_id="", title="", year=0, media_type="movie"):
        record = self._managed_record_by_id(list_id) if list_id else None
        if not record:
            # Find the first list containing the movie.
            for row in self.state.get("ai_lists", []):
                if not isinstance(row, dict): continue
                for movie in row.get("movies", []):
                    ids = movie.get("ids") or {} if isinstance(movie, dict) else {}
                    if str((movie or {}).get("media_type") or "movie") == media_type and str(ids.get("trakt") or "") == str(trakt_id):
                        record = row; break
                if record: break
        movie = None
        if record:
            for item in record.get("movies", []):
                if not isinstance(item, dict): continue
                ids = item.get("ids") or {}
                if str(item.get("media_type") or "movie") != media_type: continue
                if trakt_id and str(ids.get("trakt") or "") == str(trakt_id): movie = item; break
                if not trakt_id and str(item.get("title") or "").casefold() == str(title or "").casefold() and self._safe_int(item.get("year"), 0) == self._safe_int(year, 0): movie = item; break
        reason = str((movie or {}).get("ai_reason") or (movie or {}).get("match_reason") or "").strip()
        fingerprint = self.state.get("taste_fingerprint") or {}
        pieces = []
        if reason:
            pieces.append("WHY IT FITS\n%s" % reason)
        if record:
            pieces.append("LIST\n%s\n\nPROMPT\n%s" % (record.get("name") or "curatr list", record.get("prompt") or ""))
        summary = str(fingerprint.get("summary") or "").strip()
        if summary:
            pieces.append("YOUR PREFERENCES\n%s" % summary)
        if not pieces:
            pieces.append("No saved explanation is available for this recommendation yet. It can be regenerated to create one.")
        heading = "%s%s" % (str((movie or {}).get("title") or title or "Why this pick?"), " (%s)" % ((movie or {}).get("year") or year) if ((movie or {}).get("year") or year) else "")
        xbmcgui.Dialog().textviewer(heading, "\n\n".join(pieces))

    def export_backup(self):
        payload_lists = []
        for row in self.state.get("ai_lists", []):
            if not isinstance(row, dict):
                continue
            clean = dict(row)
            clean.pop("trakt_id", None)
            clean.pop("trakt_synced_at", None)
            clean["sync_to_trakt"] = False
            clean["trakt_refresh_enabled"] = False
            payload_lists.append(clean)
        payload = {
            "format": "curatr-backup",
            "version": 3,
            "exported_at": int(time.time()),
            "lists": payload_lists,
            "prompt_templates": [row for row in self.state.get("prompt_templates", []) if isinstance(row, dict)],
            "hidden_movies": [row for row in self.state.get("hidden_movies", []) if isinstance(row, dict)],
            "widget_folders": [row for row in self.widget_folders()],
        }
        filename = "curatr-backup-%s.json" % time.strftime("%Y%m%d-%H%M%S", time.localtime())
        folder = self.profile_dir
        try:
            selected = xbmcgui.Dialog().browseSingle(3, "Choose backup folder", "files", defaultt=self.profile_dir)
            if selected:
                folder = selected
        except Exception:
            pass
        path = os.path.join(folder, filename)
        self._write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
        self.record_activity("Backup created: %s" % filename, notify=True)
        xbmcgui.Dialog().ok("Backup created", "Saved to:\n%s\n\nAPI keys and Trakt login tokens are never included. Direct external plugin paths are included, so review the file before sharing it." % path)
        return path

    @staticmethod
    def _normalised_restore_name(value):
        return " ".join(str(value or "").split()).casefold()

    def _prepare_restored_list(self, row, local_id=None):
        restored = dict(row)
        restored["local_id"] = local_id or restored.get("local_id") or uuid.uuid4().hex
        restored["sync_to_trakt"] = False
        restored["trakt_refresh_enabled"] = False
        restored.pop("trakt_id", None)
        restored.pop("trakt_synced_at", None)
        artwork = normalise_list_art(restored.get("artwork"))
        # Device-local custom paths cannot be expected to exist on the Kodi box
        # receiving the backup. Bundled choices and cached remote URLs remain portable.
        if artwork.get("icon_mode") == "custom":
            artwork.update({"icon_mode": "auto", "icon_source": ""})
        if artwork.get("fanart_mode") == "custom":
            artwork.update({"fanart_mode": "auto", "fanart_source": ""})
        restored["artwork"] = artwork
        return restored

    def _merge_prompt_templates_from_backup(self, incoming):
        current = [dict(row) for row in self.state.get("prompt_templates", []) if isinstance(row, dict)]
        by_id = {str(row.get("id") or ""): idx for idx, row in enumerate(current) if row.get("id")}
        by_name = {
            self._normalised_restore_name(row.get("name")): idx
            for idx, row in enumerate(current)
            if self._normalised_restore_name(row.get("name"))
        }
        added = 0
        updated = 0
        for row in incoming if isinstance(incoming, list) else []:
            if not isinstance(row, dict):
                continue
            restored = dict(row)
            rid = str(restored.get("id") or "")
            name_key = self._normalised_restore_name(restored.get("name"))
            idx = by_id.get(rid) if rid else None
            if idx is None and name_key:
                idx = by_name.get(name_key)
            if idx is not None:
                existing_id = current[idx].get("id")
                if not restored.get("id"):
                    restored["id"] = existing_id or uuid.uuid4().hex
                elif name_key and str(restored.get("id")) != str(existing_id or ""):
                    # A same-name template on another device is the same logical prompt.
                    restored["id"] = existing_id or restored.get("id")
                current[idx] = restored
                updated += 1
            else:
                restored["id"] = restored.get("id") or uuid.uuid4().hex
                current.append(restored)
                idx = len(current) - 1
                added += 1
            rid = str(current[idx].get("id") or "")
            name_key = self._normalised_restore_name(current[idx].get("name"))
            if rid:
                by_id[rid] = idx
            if name_key:
                by_name[name_key] = idx
        self.state["prompt_templates"] = current
        return added, updated

    def _merge_hidden_movies_from_backup(self, incoming):
        merged = []
        positions = {}
        for row in self.state.get("hidden_movies", []):
            if not isinstance(row, dict):
                continue
            marker = str(row.get("marker") or "")
            if not marker:
                continue
            positions[marker] = len(merged)
            merged.append(dict(row))
        added = 0
        updated = 0
        for row in incoming if isinstance(incoming, list) else []:
            if not isinstance(row, dict):
                continue
            marker = str(row.get("marker") or "")
            if not marker:
                continue
            if marker in positions:
                idx = positions[marker]
                combined = dict(merged[idx])
                combined.update(row)
                merged[idx] = combined
                updated += 1
            else:
                positions[marker] = len(merged)
                merged.append(dict(row))
                added += 1
        self.state["hidden_movies"] = merged[-500:]
        return added, updated

    def _prepare_restored_widget_folder(self, row, list_id_map=None):
        restored = dict(row)
        restored["id"] = self._safe_reference_id(restored.get("id"))
        restored["name"] = str(restored.get("name") or "Folder").strip() or "Folder"
        restored["description"] = str(restored.get("description") or "").strip()
        artwork = normalise_list_art(restored.get("artwork"))
        if artwork.get("icon_mode") == "custom":
            artwork.update({"icon_mode": "auto", "icon_source": "", "icon_label": ""})
        if artwork.get("fanart_mode") == "custom":
            artwork.update({"fanart_mode": "auto", "fanart_source": "", "fanart_label": ""})
        restored["artwork"] = artwork
        entries = []
        mapping = list_id_map or {}
        for row_entry in restored.get("entries", []):
            if not isinstance(row_entry, dict):
                continue
            entry = dict(row_entry)
            entry["id"] = self._safe_reference_id(entry.get("id"))
            if entry.get("type") == "curatr_list" and entry.get("list_id"):
                original = str(entry.get("list_id"))
                entry = {"id": entry["id"], "type": "curatr_list", "list_id": str(mapping.get(original, original))}
            elif entry.get("type") == "external_path" and self._valid_external_plugin_path(entry.get("path")):
                entry["path"] = self._valid_external_plugin_path(entry.get("path"))
                entry["name"] = str(entry.get("name") or "External Shortcut").strip() or "External Shortcut"
                entry["description"] = str(entry.get("description") or "").strip()
                entry_art = normalise_list_art(entry.get("artwork"))
                if entry_art.get("icon_mode") == "custom":
                    entry_art.update({"icon_mode": "default", "icon_source": "", "icon_label": ""})
                if entry_art.get("fanart_mode") == "custom":
                    entry_art.update({"fanart_mode": "default", "fanart_source": "", "fanart_label": ""})
                entry["artwork"] = entry_art
            elif entry.get("type") == "provider_list":
                provider = str(entry.get("provider") or "").strip().lower()
                provider_list_id = str(entry.get("provider_list_id") or "").strip()
                if provider not in ("trakt", "mdblist") or not provider_list_id or len(provider_list_id) > 128:
                    continue
                entry["provider"] = provider
                entry["provider_list_id"] = provider_list_id
                entry["name"] = str(entry.get("name") or ("Trakt list" if provider == "trakt" else "MDBList list")).strip()
                entry["description"] = str(entry.get("description") or "").strip()
                entry_art = normalise_list_art(entry.get("artwork"))
                if entry_art.get("icon_mode") == "custom":
                    entry_art.update({"icon_mode": "default", "icon_source": "", "icon_label": ""})
                if entry_art.get("fanart_mode") == "custom":
                    entry_art.update({"fanart_mode": "default", "fanart_source": "", "fanart_label": ""})
                entry["artwork"] = entry_art
            else:
                continue
            entries.append(entry)
        restored["entries"] = entries
        return restored

    def _merge_widget_folders_from_backup(self, incoming, list_id_map=None):
        current = [dict(row) for row in self.widget_folders()]
        by_id = {str(row.get("id")): idx for idx, row in enumerate(current) if row.get("id")}
        by_name = {self._normalised_restore_name(row.get("name")): idx for idx, row in enumerate(current) if self._normalised_restore_name(row.get("name"))}
        added = 0
        updated = 0
        for row in incoming if isinstance(incoming, list) else []:
            if not isinstance(row, dict):
                continue
            restored = self._prepare_restored_widget_folder(row, list_id_map)
            idx = by_id.get(str(restored.get("id")))
            if idx is None:
                idx = by_name.get(self._normalised_restore_name(restored.get("name")))
            if idx is not None:
                restored["id"] = current[idx].get("id") or restored.get("id")
                current[idx] = restored
                updated += 1
            else:
                current.append(restored)
                idx = len(current) - 1
                added += 1
            by_id[str(current[idx].get("id"))] = idx
            by_name[self._normalised_restore_name(current[idx].get("name"))] = idx
        self.state["widget_folders"] = current
        self._dirty_widget_folder_ids.update(
            str(row.get("id") or "") for row in current if isinstance(row, dict) and row.get("id")
        )
        return added, updated

    def import_backup(self):
        path = ""
        try:
            path = xbmcgui.Dialog().browseSingle(1, "Choose curatr backup", "files", mask=".json", defaultt=self.profile_dir)
        except Exception:
            pass
        if not path or not str(path).lower().endswith(".json"):
            return None
        try:
            payload = json.loads(self._read_text(path))
        except Exception as exc:
            raise RuntimeError("That backup could not be read: %s" % exc)
        if not isinstance(payload, dict) or payload.get("format") not in ("curatr-backup", "ai-trakt-curator-backup"):
            raise RuntimeError("That file is not a curatr backup.")
        if not xbmcgui.Dialog().yesno(
            self.name,
            "Restore this curatr backup?\n\n"
            "Lists with the same local ID are updated automatically. If a different list has the same name, curatr will ask whether to replace it, keep both, or skip it. Saved prompts, hidden items and folders are merged and de-duplicated.\n\n"
            "Trakt syncing stays off for restored lists until you enable it again.",
        ):
            return None

        current_lists = [dict(row) for row in self.state.get("ai_lists", []) if isinstance(row, dict)]
        id_to_index = {
            str(row.get("local_id") or ""): idx
            for idx, row in enumerate(current_lists)
            if row.get("local_id")
        }
        name_to_indexes = {}
        for idx, row in enumerate(current_lists):
            key = self._normalised_restore_name(row.get("name"))
            if key:
                name_to_indexes.setdefault(key, []).append(idx)

        list_added = 0
        list_updated = 0
        list_kept_both = 0
        list_skipped = 0
        list_id_map = {}

        for row in payload.get("lists", []):
            if not isinstance(row, dict):
                continue
            incoming_id = str(row.get("local_id") or "")
            if incoming_id and incoming_id in id_to_index:
                idx = id_to_index[incoming_id]
                current_lists[idx] = self._prepare_restored_list(row, local_id=current_lists[idx].get("local_id"))
                list_id_map[incoming_id] = str(current_lists[idx].get("local_id"))
                list_updated += 1
                continue

            name_key = self._normalised_restore_name(row.get("name"))
            name_matches = name_to_indexes.get(name_key, []) if name_key else []
            if name_matches:
                idx = name_matches[0]
                existing_name = current_lists[idx].get("name") or "Untitled list"
                incoming_name = row.get("name") or "Untitled list"
                choice = xbmcgui.Dialog().select(
                    "Duplicate list found",
                    [
                        "Replace existing '%s' with backup version" % existing_name,
                        "Keep both copies",
                        "Skip backup list '%s'" % incoming_name,
                    ],
                )
                if choice < 0 or choice == 2:
                    list_skipped += 1
                    continue
                if choice == 0:
                    existing_id = current_lists[idx].get("local_id") or uuid.uuid4().hex
                    current_lists[idx] = self._prepare_restored_list(row, local_id=existing_id)
                    if incoming_id:
                        list_id_map[incoming_id] = str(existing_id)
                    id_to_index[str(existing_id)] = idx
                    list_updated += 1
                    continue
                # Keep both: ensure the imported list has a unique local ID.
                candidate_id = incoming_id or uuid.uuid4().hex
                if candidate_id in id_to_index:
                    candidate_id = uuid.uuid4().hex
                restored = self._prepare_restored_list(row, local_id=candidate_id)
                current_lists.append(restored)
                new_idx = len(current_lists) - 1
                id_to_index[str(candidate_id)] = new_idx
                if incoming_id:
                    list_id_map[incoming_id] = str(candidate_id)
                name_to_indexes.setdefault(name_key, []).append(new_idx)
                list_added += 1
                list_kept_both += 1
                continue

            candidate_id = incoming_id or uuid.uuid4().hex
            if candidate_id in id_to_index:
                candidate_id = uuid.uuid4().hex
            restored = self._prepare_restored_list(row, local_id=candidate_id)
            current_lists.append(restored)
            new_idx = len(current_lists) - 1
            id_to_index[str(candidate_id)] = new_idx
            if incoming_id:
                list_id_map[incoming_id] = str(candidate_id)
            if name_key:
                name_to_indexes.setdefault(name_key, []).append(new_idx)
            list_added += 1

        self.state["ai_lists"] = current_lists
        prompts_added, prompts_updated = self._merge_prompt_templates_from_backup(payload.get("prompt_templates"))
        hidden_added, hidden_updated = self._merge_hidden_movies_from_backup(payload.get("hidden_movies"))
        folders_added, folders_updated = self._merge_widget_folders_from_backup(payload.get("widget_folders"), list_id_map)
        self._save_state()
        self.record_activity("Backup restored", notify=True)

        summary = [
            "Restore complete.",
            "",
            "Lists: %d added, %d updated%s%s" % (
                list_added,
                list_updated,
                ", %d kept as duplicates" % list_kept_both if list_kept_both else "",
                ", %d skipped" % list_skipped if list_skipped else "",
            ),
            "Saved prompts: %d added, %d updated" % (prompts_added, prompts_updated),
            "Hidden items: %d added, %d merged" % (hidden_added, hidden_updated),
            "Folders: %d added, %d updated" % (folders_added, folders_updated),
            "",
            "Trakt syncing is disabled for restored lists until you enable it again.",
        ]
        xbmcgui.Dialog().ok("Backup restored", "\n".join(summary))
        return True

    def backup_menu_interactive(self):
        choice = xbmcgui.Dialog().select(
            "Backup & Restore",
            ["Create backup", "Restore from backup", "Recover previous local state"],
        )
        if choice == 0: return self.export_backup()
        if choice == 1: return self.import_backup()
        if choice == 2: return self.recover_previous_state_interactive()
        return None

    def sync_list_to_trakt(self, list_id, silent=False):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        self._require_trakt_write()
        movies = [m for m in (record.get("movies") or []) if isinstance(m, dict)]
        desired_movies = []
        desired_shows = []
        resolved_movies = []
        unresolved = []
        ids_added = False
        for movie in movies:
            media_type = "show" if str(movie.get("media_type") or "movie") == "show" else "movie"
            resolved = dict(movie)
            ids = dict(resolved.get("ids") or {})
            try:
                trakt_id = int(ids.get("trakt"))
            except (TypeError, ValueError):
                trakt_id = 0
            if not trakt_id and ids.get("tmdb") not in (None, ""):
                matches = self.trakt.search_tmdb(ids.get("tmdb"), media_type)
                match = self._select_media_match(
                    matches, resolved.get("title") or "", self._safe_int(resolved.get("year"), 0), media_type,
                )
                if match:
                    match_ids = dict(match.get("ids") or {})
                    try:
                        trakt_id = int(match_ids.get("trakt"))
                    except (TypeError, ValueError):
                        trakt_id = 0
                    if trakt_id:
                        ids.update({key: value for key, value in match_ids.items() if value not in (None, "")})
                        resolved["ids"] = ids
                        ids_added = True
            if not trakt_id:
                unresolved.append(str(resolved.get("title") or "Unknown item"))
                resolved_movies.append(resolved)
                continue
            if media_type == "show":
                desired_shows.append(trakt_id)
            else:
                desired_movies.append(trakt_id)
            resolved_movies.append(resolved)
        if unresolved:
            raise RuntimeError(
                "Trakt could not identify %d item%s in this list: %s"
                % (len(unresolved), "" if len(unresolved) == 1 else "s", ", ".join(unresolved[:3]))
            )
        if not desired_movies and not desired_shows:
            raise RuntimeError("Find some recommendations for this list before syncing it to Trakt.")

        if ids_added:
            updated_local = dict(record)
            updated_local["movies"] = resolved_movies
            self._store_managed_record(updated_local, record)
            self._save_state()
            record = updated_local

        target = self._resolve_target_list(
            record.get("name") or "My Picks",
            record.get("description") or "Personalised recommendations created by curatr.",
            record,
            silent,
        )
        list_id_remote = (target.get("ids") or {}).get("trakt")
        if not list_id_remote:
            raise RuntimeError("Trakt did not return an ID for the target list.")
        self._sync_list_items(list_id_remote, desired_movies, desired_shows)
        updated = dict(record)
        now = int(time.time())
        updated["trakt_id"] = list_id_remote
        updated["sync_to_trakt"] = True
        updated["trakt_synced_at"] = now
        updated["trakt_refresh_cycle_at"] = now
        updated["trakt_last_attempt_at"] = 0
        self._store_managed_record(updated, record)
        self._save_state()
        if not silent:
            self.record_activity("%s synced to Trakt" % updated.get("name"), notify=True)
        return updated

    def sync_list_to_trakt_interactive(self, list_id):
        """Create or update one Trakt copy, offering connection only on request."""
        if not self._has_oauth():
            connect = xbmcgui.Dialog().yesno(
                self.name,
                "Sync to Trakt needs a connected Trakt account.\n\nConnect Trakt now?",
                nolabel="Not now", yeslabel="Connect Trakt",
            )
            if not connect:
                return None
            self.authenticate_trakt()
            if not self._has_oauth():
                return None
        return self.sync_list_to_trakt(list_id, silent=False)

    def show_privacy_and_data(self):
        """Explain curatr's data flow in plain language from inside Kodi."""
        text = (
            "WHAT STAYS IN KODI\n\n"
            "Your lists, prompts, preferences, artwork choices, hidden items and settings are stored in Kodi's curatr profile. "
            "API keys and account tokens are stored there too, but are excluded from curatr backups.\n\n"
            "KEYWORD MATCHING\n\n"
            "Keyword Matching does not send your request or preferences to an AI provider. TMDB or MDBList may still receive "
            "the catalogue requests needed for filters you choose.\n\n"
            "WHEN YOU USE AI\n\n"
            "curatr sends your prompt and a limited preference summary to the AI service you selected. It does not send your "
            "API keys, account tokens, Kodi file paths or artwork files.\n\n"
            "CONNECTED SERVICES\n\n"
            "Trakt, TMDB and MDBList receive only the requests needed for the features you enable. Each service handles those "
            "requests under its own privacy terms. All connections are optional.\n\n"
            "LOGS AND BACKUPS\n\n"
            "curatr is designed to keep credentials out of backups and redact sensitive request data from its own logs. Before "
            "sharing a Kodi log, you should still check it for personal information added by Kodi or other add-ons.\n\n"
            "YOUR CONTROL\n\n"
            "You can use local lists without Trakt or MDBList, use Keyword Matching without an AI key, and disconnect optional "
            "services whenever you choose."
        )
        xbmcgui.Dialog().textviewer("Privacy & Data", text)

    def choose_menu_background_interactive(self):
        """Choose a theme gradient or custom image without leaving the picker on cancel."""
        addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        current = current_choice(self.addon, self.state)
        entries = background_entries(self.addon, self.state)

        def select(entry):
            if entry["key"] != "custom":
                return entry
            source = xbmcgui.Dialog().browseSingle(
                2, "Choose a background image", "files", ".png|.jpg|.jpeg|.webp",
                defaultt=str(self.state.get("menu_background_source") or ""),
            )
            if not source:
                return None
            try:
                return dict(entry, source=import_custom_background(self.addon, source))
            except (OSError, ValueError, RuntimeError) as exc:
                message = str(exc) if isinstance(exc, ValueError) else "Could not read that image. Please choose another file."
                xbmcgui.Dialog().ok("Menu Background", message)
                return None

        selected = choose_artwork(addon_path, "Menu Background", entries, "fanart", selection_handler=select)
        if not selected:
            return current
        value = str(selected["key"])
        source = str(selected.get("source") or "")
        if value == current and (value != "custom" or source == self.state.get("menu_background_source")):
            return current
        self.state["menu_background_style"] = value
        if value == "custom":
            self.state["menu_background_source"] = source
        self._save_state()
        label = "Custom" if value == "custom" else selected.get("label")
        self.record_activity("Changed menu background to %s" % label, notify=True)
        return value

    def set_list_trakt_sync(self, list_id, enabled):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        updated = dict(record)
        updated["sync_to_trakt"] = bool(enabled)
        if not enabled:
            updated["trakt_refresh_enabled"] = False
            updated["trakt_last_attempt_at"] = 0
        self._store_managed_record(updated, record)
        self._save_state()
        return updated

    def manage_list_interactive(self, list_id):
        while True:
            record = self._managed_record_by_id(list_id)
            if not record:
                raise RuntimeError("That list has already been removed.")
            key = self._record_key(record)
            choice = xbmcgui.Dialog().select(
                record.get("name") or "curatr list",
                [
                    "List settings",
                    "Refresh this list",
                    "Sync to Trakt",
                    "Create Similar List",
                    "Add to Folder",
                    "Artwork",
                    "Save request as a template",
                    "View list details",
                    "Delete this list",
                ],
            )
            if choice < 0:
                return None
            if choice == 0:
                self.list_settings_interactive(key)
            elif choice == 1:
                self.refresh_list(key, silent=False)
            elif choice == 2:
                self.sync_list_to_trakt_interactive(key)
            elif choice == 3:
                return self.create_related_list_interactive(list_id=key)
            elif choice == 4:
                self.add_list_to_widget_folder_interactive(list_id=key)
            elif choice == 5:
                self.list_artwork_interactive(key)
            elif choice == 6:
                self.save_list_prompt_as_template(key)
            elif choice == 7:
                self._view_list_settings(key)
            elif choice == 8:
                if self.delete_list_interactive(key):
                    return None

    def _managed_list_rows(self):
        records = [row for row in self.state.get("ai_lists", []) if isinstance(row, dict)]
        records.sort(key=lambda row: self._safe_int(row.get("updated_at"), 0), reverse=True)
        rows = []
        for row in records:
            key = self._record_key(row)
            if not key:
                continue
            method = "Keyword Matching" if str(row.get("generation_method") or "ai").lower() == "keyword" else "AI"
            content = {
                "movies": "Movies", "shows": "TV Shows", "both": "Movies & TV Shows",
            }.get(str(row.get("content_type") or "movies"), "Movies")
            item_count = len([item for item in row.get("movies", []) if isinstance(item, dict)])
            if not item_count:
                item_count = max(0, self._safe_int(row.get("last_result_count"), 0))
            refresh = "Manual refresh"
            if row.get("regeneration_enabled"):
                refresh = "Auto Refresh: %s" % self._format_interval(
                    self._safe_int(row.get("regeneration_interval_hours"), 24)
                )
            if row.get("sync_to_trakt"):
                if row.get("trakt_refresh_enabled"):
                    trakt = "Trakt: %s" % self._format_interval(
                        self._safe_int(row.get("trakt_refresh_interval_hours"), 24)
                    )
                else:
                    trakt = "Trakt: Manual"
            else:
                trakt = "Kodi only"
            description = str(row.get("description") or "").strip()
            summary_label = "About" if description else ("Request" if method == "Keyword Matching" else "Prompt")
            summary = self._shorten_text(description or row.get("prompt") or "No description", 100)
            rows.append({
                "key": key,
                "label": str(row.get("name") or "curatr list"),
                "detail": "%d item%s  •  %s  •  %s" % (
                    item_count, "" if item_count == 1 else "s", content, method,
                ),
                "status": "%s  •  %s" % (refresh, trakt),
                "summary": "%s: %s" % (summary_label, summary),
                "art": list_art_sources(self.addon, row),
            })
        return rows

    @staticmethod
    def _managed_list_actions(_row):
        return [
            {"key": "settings", "label": "List Settings", "detail": "Appearance, content and behaviour"},
            {"key": "refresh", "label": "Refresh This List", "detail": "Generate an updated set of items"},
            {"key": "sync", "label": "Sync to Trakt", "detail": "Create or update its Trakt copy"},
            {"key": "reference", "label": "Create Similar List", "detail": "Create a separate related list"},
            {"key": "folder", "label": "Add to Folder", "detail": "Place this list in a curatr folder"},
            {"key": "artwork", "label": "Artwork", "detail": "Change its icon or fanart"},
            {"key": "template", "label": "Save Request as Template", "detail": "Reuse this request later"},
            {"key": "details", "label": "View List Details", "detail": "Show the complete saved configuration"},
            {"key": "delete", "label": "Delete This List", "detail": "Choose whether to keep its Trakt copy"},
        ]

    def _managed_list_action(self, list_id, action):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        key = self._record_key(record)
        if action == "settings":
            return self.list_settings_interactive(key)
        if action == "refresh":
            return self.refresh_list(key, silent=False)
        if action == "sync":
            return self.sync_list_to_trakt_interactive(key)
        if action == "reference":
            return self.create_related_list_interactive(list_id=key)
        if action == "folder":
            return self.add_list_to_widget_folder_interactive(list_id=key)
        if action == "artwork":
            return self.list_artwork_interactive(key)
        if action == "template":
            return self.save_list_prompt_as_template(key)
        if action == "details":
            return self._view_list_settings(key)
        if action == "delete":
            return self.delete_list_interactive(key)
        return record

    def _manage_lists_legacy(self):
        records = [row for row in self.state.get("ai_lists", []) if isinstance(row, dict)]
        records.sort(key=lambda row: self._safe_int(row.get("updated_at"), 0), reverse=True)
        if not records:
            self._notify("You do not have any saved lists yet")
            return None
        labels = [
            "%s: %d items" % (
                row.get("name") or "curatr list",
                len([item for item in row.get("movies", []) if isinstance(item, dict)]),
            ) for row in records
        ]
        choice = xbmcgui.Dialog().select("My Lists", labels)
        if choice < 0:
            return None
        return self.manage_list_interactive(self._record_key(records[choice]))

    def manage_lists_interactive(self):
        addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        result = manage_collection(
            addon_path,
            "Manage My Lists",
            "Lists are shown with their artwork, contents and refresh behaviour.",
            "Create List",
            self._managed_list_rows,
            self._managed_list_actions,
            self._managed_list_action,
            self.create_list_interactive,
        )
        if result == "fallback":
            return self._manage_lists_legacy()
        return result

    def update_all(self, silent=False):
        records = list(self.state.get("ai_lists", []))
        if not records:
            if not silent:
                self._notify("You do not have any saved lists yet")
            return {"updated": 0, "failed": 0}

        updated = 0
        failed = 0
        for record in records:
            try:
                self.refresh_list(self._record_key(record), silent=True)
                updated += 1
            except Exception as exc:
                failed += 1
                xbmc.log("curatr list update failed for %s: %s" % (record.get("name"), exc), xbmc.LOGERROR)
                try:
                    self.record_activity(
                        "Could not refresh %s" % (record.get("name") or "curatr list"),
                        level="error", detail=str(exc), notify=False,
                    )
                except Exception:
                    pass

        if not silent:
            if failed:
                self.record_activity(
                    "List refresh finished: %d refreshed, %d failed" % (updated, failed),
                    level="warning", notify=True,
                )
            else:
                self.record_activity("Refreshed %d list(s)" % updated, notify=True)
        return {"updated": updated, "failed": failed}

    def _movie_cache_key(self, title, year, media_type="movie"):
        return "%s|%s|%s" % (media_type, self._normalise_title(title), str(year or ""))

    @staticmethod
    def _compact_movie(movie):
        if not isinstance(movie, dict):
            return {}
        keep = (
            "title", "year", "ids", "overview", "tagline", "runtime", "released",
            "certification", "genres", "rating", "votes", "images", "media_type",
        )
        return {key: movie.get(key) for key in keep if movie.get(key) not in (None, "", [], {})}

    def _keyword_tmdb_item(self, candidate, media_type):
        """Store a local Keyword Matching result without requiring Trakt."""
        tmdb_id = candidate.get("tmdb_id")
        if tmdb_id in (None, ""):
            return None
        images = {}
        poster = self.tmdb.image_url(candidate.get("poster_path"), "w780")
        fanart = self.tmdb.image_url(candidate.get("backdrop_path"), "w1280")
        if poster:
            images["poster"] = {"full": poster}
        if fanart:
            images["fanart"] = {"full": fanart}
        movie = {
            "title": candidate.get("title"),
            "year": self._safe_int(candidate.get("year"), 0),
            "ids": {"tmdb": tmdb_id},
            "overview": candidate.get("overview"),
            "rating": candidate.get("rating"),
            "votes": candidate.get("votes"),
            "images": images,
            "media_type": "show" if media_type == "show" else "movie",
        }
        return self._compact_movie(movie)

    def _movie_cache_lookup(self, title, year, media_type="movie"):
        cache = self.state.get("movie_resolution_cache") or {}
        row = cache.get(self._movie_cache_key(title, year, media_type)) if isinstance(cache, dict) else None
        if not isinstance(row, dict):
            return False, None
        cached_at = self._safe_int(row.get("cached_at"), 0)
        if row.get("miss"):
            if cached_at and time.time() - cached_at < self.MOVIE_MISS_TTL_SECONDS:
                return True, None
            return False, None
        movie = row.get("movie")
        if isinstance(movie, dict):
            return True, self._compact_movie(movie)
        return False, None


    def _trim_movie_cache(self):
        cache = self.state.get("movie_resolution_cache") or {}
        if not isinstance(cache, dict) or len(cache) <= self.MOVIE_CACHE_MAX_ITEMS:
            return
        ordered = sorted(
            cache.items(),
            key=lambda kv: self._safe_int((kv[1] or {}).get("cached_at"), 0),
            reverse=True,
        )
        self.state["movie_resolution_cache"] = dict(ordered[: self.MOVIE_CACHE_KEEP_ITEMS])

    def _cache_movie(self, title, year, movie, media_type="movie"):
        if not isinstance(movie, dict):
            return
        cache = self.state.setdefault("movie_resolution_cache", {})
        key = self._movie_cache_key(title, year, media_type)
        cache[key] = {"movie": self._compact_movie(movie), "cached_at": int(time.time())}
        self._trim_movie_cache()

    def _cache_movie_miss(self, title, year, media_type="movie"):
        cache = self.state.setdefault("movie_resolution_cache", {})
        cache[self._movie_cache_key(title, year, media_type)] = {"miss": True, "cached_at": int(time.time())}
        self._trim_movie_cache()

    @staticmethod
    def _keyword_analysis_key(rules):
        references = {
            "strategy": str((rules or {}).get("strategy") or ""),
            "people": (rules or {}).get("people") or [],
            "movies": (rules or {}).get("reference_movies") or [],
        }
        raw = json.dumps(references, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _keyword_analysis_get(self, key):
        cache = self.state.get("keyword_analysis_cache") or {}
        row = cache.get(str(key)) if isinstance(cache, dict) else None
        if not isinstance(row, dict):
            return None
        cached_at = self._safe_int(row.get("cached_at"), 0)
        analysis = row.get("analysis")
        if not cached_at or time.time() - cached_at > self.KEYWORD_ANALYSIS_MAX_AGE_SECONDS:
            return None
        return analysis if isinstance(analysis, dict) else None

    def _keyword_analysis_put(self, key, analysis):
        cache = dict(self.state.get("keyword_analysis_cache") or {})
        cache[str(key)] = {"cached_at": int(time.time()), "analysis": dict(analysis or {})}
        ordered = sorted(
            cache.items(), key=lambda row: self._safe_int((row[1] or {}).get("cached_at"), 0), reverse=True,
        )
        self.state["keyword_analysis_cache"] = dict(ordered[:self.KEYWORD_ANALYSIS_MAX_ITEMS])
        self._save_state()

    def _keyword_candidate_pool(self, rules, limit):
        strategy = str((rules or {}).get("strategy") or "filtered_discover")
        analysis = {}
        if strategy == "collection" and (rules or {}).get("collection_query"):
            pool, collection = self.tmdb.collection_movies(rules.get("collection_query"), limit=max(100, limit))
            if not collection:
                raise RuntimeError("curatr could not find that movie collection on TMDB.")
            rules["collection_name"] = str(collection.get("name") or rules.get("collection_query"))
            return [row for row in pool if candidate_matches(row, rules)], {"collection": collection}
        external_source = str((rules or {}).get("external_source") or "")
        if external_source:
            if not self.mdblist or not getattr(self.mdblist, "api_key", ""):
                raise RuntimeError(
                    "%s filters need MDBList. Turn on MDBList and add its API key under Connected Accounts."
                    % ((rules or {}).get("external_source_label") or "That rating source")
                )
            chart_limit = self._safe_int((rules or {}).get("external_chart_limit"), 0)
            fetch_limit = max(int(limit or 40), chart_limit or 100)
            cache_key = "%s:%d" % (external_source, fetch_limit)
            cache = self.state.setdefault("mdblist_chart_cache", {})
            cached = cache.get(cache_key) if isinstance(cache, dict) else None
            if isinstance(cached, dict) and time.time() - self._safe_int(cached.get("cached_at"), 0) < 24 * 3600:
                chart_pool = [row for row in cached.get("movies") or [] if isinstance(row, dict)]
            else:
                chart_pool = self.mdblist.catalog_movies(external_source, limit=fetch_limit)
                cache[cache_key] = {"cached_at": int(time.time()), "movies": chart_pool}
                if len(cache) > 8:
                    ordered = sorted(cache.items(), key=lambda row: self._safe_int((row[1] or {}).get("cached_at"), 0), reverse=True)
                    self.state["mdblist_chart_cache"] = dict(ordered[:8])
                self._save_state()
            threshold = float((rules or {}).get("external_rating_min") or 0)
            if threshold:
                chart_pool = [row for row in chart_pool if float(row.get("external_rating") or 0) >= threshold]
            if chart_limit:
                chart_pool = chart_pool[:chart_limit]
            base_rules = dict(rules or {})
            base_rules.update({
                "external_source": "", "external_source_label": "",
                "external_chart_limit": 0, "external_rating_min": 0.0,
            })
            needs_intersection = any(base_rules.get(key) for key in (
                "genres", "themes", "year_min", "year_max", "runtime_min", "runtime_max",
                "rating_min", "language", "country", "people", "reference_movies",
            ))
            if needs_intersection:
                base_pool, analysis = self._keyword_candidate_pool(base_rules, max(limit, min(100, fetch_limit)))
                allowed = {
                    (self._normalise_title(row.get("title")), self._safe_int(row.get("year"), 0))
                    for row in base_pool if isinstance(row, dict)
                }
                chart_pool = [
                    row for row in chart_pool
                    if (self._normalise_title(row.get("title")), self._safe_int(row.get("year"), 0)) in allowed
                ]
            return chart_pool[:max(1, int(limit or 40))], analysis
        if strategy in ("similar_people", "recurring_collaborators"):
            key = self._keyword_analysis_key(rules)
            analysis = self._keyword_analysis_get(key)
            if analysis is None:
                try:
                    analysis = self.tmdb.analyse_people(rules.get("people") or [], film_limit=15, detail_limit=3)
                except CatalogueError as exc:
                    # Keep the list usable if optional deep-detail requests fail:
                    # exact credits are a narrower but still honest fallback.
                    resolved = self.tmdb.resolve_people(rules.get("people") or [], maximum=3)
                    if not resolved:
                        raise
                    fallback = dict(rules); fallback["resolved_people"] = resolved
                    self.record_activity(
                        "Used a simpler Keyword Match while creator analysis was unavailable",
                        level="warning", detail=str(exc), notify=False,
                    )
                    return self.tmdb.discover_movies(fallback, limit=limit), {"resolved_people": resolved}
                if not analysis.get("resolved_people"):
                    raise RuntimeError("curatr could not find the people named in that prompt on TMDB.")
                self._keyword_analysis_put(key, analysis)
            pool = self.tmdb.enriched_discovery_pool(rules, analysis, limit=limit)
        elif strategy == "reference_people":
            key = self._keyword_analysis_key(rules)
            analysis = self._keyword_analysis_get(key) or {}
            resolved = analysis.get("resolved_people") or self.tmdb.resolve_people(rules.get("people") or [], maximum=3)
            if not resolved:
                raise RuntimeError("curatr could not find the people named in that prompt on TMDB.")
            if not analysis.get("resolved_people"):
                analysis = {"resolved_people": resolved}
                self._keyword_analysis_put(key, analysis)
            resolved_rules = dict(rules)
            resolved_rules["resolved_people"] = resolved
            people_pool = self.tmdb.discover_movies(resolved_rules, limit=limit)
            people_ids = {str(row.get("tmdb_id") or "") for row in people_pool if row.get("tmdb_id")}
            reference_pool = self.tmdb.recommendation_pool(rules.get("reference_movies") or [], limit=limit)
            pool = [
                row for row in reference_pool
                if str(row.get("tmdb_id") or "") in people_ids and candidate_matches(row, rules)
            ]
        elif strategy == "exact_people":
            key = self._keyword_analysis_key(rules)
            analysis = self._keyword_analysis_get(key) or {}
            resolved = analysis.get("resolved_people") or self.tmdb.resolve_people(rules.get("people") or [], maximum=3)
            if not resolved:
                raise RuntimeError("curatr could not find the people named in that prompt on TMDB.")
            if not analysis.get("resolved_people"):
                analysis = {"resolved_people": resolved}
                self._keyword_analysis_put(key, analysis)
            resolved_rules = dict(rules)
            resolved_rules["resolved_people"] = resolved
            pool = self.tmdb.discover_movies(resolved_rules, limit=limit)
        elif strategy == "similar_films":
            pool = self.tmdb.recommendation_pool(rules.get("reference_movies") or [], limit=limit)
            pool = [row for row in pool if candidate_matches(row, rules)]
        else:
            pool = self.tmdb.discover_movies(rules, limit=limit)
        return pool, analysis

    def build_similar_preview(self, reference, method="keyword", count=20):
        """Build an unsaved Find Similar result set from one Kodi item."""
        reference = dict(reference or {})
        title = str(reference.get("title") or "").strip()
        if not title:
            raise RuntimeError("curatr could not read the selected title.")
        media_type = "show" if str(reference.get("media_type") or "movie") == "show" else "movie"
        count = max(5, min(50, self._safe_int(count, 20)))
        method = "ai" if str(method or "keyword").lower() == "ai" else "keyword"
        self._require_keyword_catalogue()
        movies = []
        recommendations = []
        if method == "keyword":
            pool = self.tmdb.similar_titles(reference, limit=min(100, count * 3))
            for candidate in pool:
                movie = self._keyword_tmdb_item(candidate, media_type)
                if not movie:
                    continue
                movie["match_reason"] = "Recommended by TMDB from shared catalogue signals"
                movies.append(movie)
                if len(movies) >= count:
                    break
        else:
            self._require_ai()
            content_type = "shows" if media_type == "show" else "movies"
            prompt = (
                "Find %s genuinely similar to %s%s. Match tone, themes, atmosphere, style and creative qualities, "
                "not merely genre. Do not include the reference title."
                % ("TV shows" if media_type == "show" else "films", title, " (%s)" % reference.get("year") if reference.get("year") else "")
            )
            profile = self.state.get("profile") or {}
            context = self._build_recommendation_context(profile, {})
            context["reference_movies"] = [{
                "title": title, "year": self._safe_int(reference.get("year"), 0), "media_type": media_type,
            }]
            result = self.ai.recommend(prompt, context, min(60, count + max(8, count // 3)), content_type=content_type)
            seen = set()
            for item in result.get("items", []):
                item_title = str(item.get("title") or "").strip()
                if not item_title:
                    continue
                year = self._safe_int(item.get("year"), 0)
                match = self.tmdb.search_show(item_title, year) if media_type == "show" else self.tmdb.search_movie(item_title, year)
                if not match:
                    continue
                candidate = self.tmdb._compact_show(match) if media_type == "show" else self.tmdb._compact(match)
                marker = (self._normalise_title(candidate.get("title")), self._safe_int(candidate.get("year"), 0))
                if marker in seen or marker[0] == self._normalise_title(title):
                    continue
                seen.add(marker)
                movie = self._keyword_tmdb_item(candidate, media_type)
                if not movie:
                    continue
                if item.get("reason"):
                    movie["ai_reason"] = str(item.get("reason"))
                movies.append(movie)
                recommendations.append(dict(item))
                if len(movies) >= count:
                    break
        if not movies:
            raise RuntimeError("curatr could not find similar items for that title. Try the other matching method.")
        return {
            "title": title, "year": self._safe_int(reference.get("year"), 0), "media_type": media_type,
            "method": method, "count": count, "movies": movies, "recommendations": recommendations,
            "reference": reference, "created_at": int(time.time()),
        }

    def save_similar_preview_interactive(self, preview):
        """Turn an already generated temporary preview into a normal curatr list."""
        preview = dict(preview or {})
        movies = [dict(row) for row in preview.get("movies", []) if isinstance(row, dict)]
        if not movies:
            raise RuntimeError("That Find Similar preview has expired. Run it again first.")
        source_title = str(preview.get("title") or "this title")
        name = xbmcgui.Dialog().input("Name this list", defaultt="More like %s" % source_title)
        if not name or not str(name).strip():
            return None
        name = str(name).strip()
        if self._managed_record_by_name(name):
            raise RuntimeError("A curatr list already uses that name. Choose a different name.")
        description = xbmcgui.Dialog().input(
            "List description (optional)", defaultt="Recommendations inspired by %s." % source_title,
        )
        method = "ai" if str(preview.get("method") or "keyword") == "ai" else "keyword"
        media_type = "show" if str(preview.get("media_type") or "movie") == "show" else "movie"
        now = int(time.time())
        reference = {"title": source_title, "year": self._safe_int(preview.get("year"), 0), "media_type": media_type}
        record = {
            "local_id": uuid.uuid4().hex, "name": name,
            "prompt": "Find titles similar to %s" % source_title,
            "description": str(description or "").strip(), "count": len(movies),
            "updated_at": now, "local_changed_at": now, "last_result_count": len(movies),
            "movies": movies, "recommendations": list(preview.get("recommendations") or []),
            "generation_method": method, "content_type": "shows" if media_type == "show" else "movies",
            "reference_movies": [reference], "artwork": normalise_list_art({}),
            "sync_to_trakt": bool(self._sync_enabled() and self._has_oauth()),
            "regeneration_enabled": self._default_regeneration_enabled(),
            "regeneration_interval_hours": self._default_regeneration_interval(),
            "regeneration_last_attempt_at": 0,
            "trakt_refresh_enabled": False,
            "trakt_refresh_interval_hours": self._default_trakt_refresh_interval(),
            "trakt_refresh_cycle_at": 0, "trakt_last_attempt_at": 0,
        }
        if method == "keyword":
            record["keyword_rules"] = parse_prompt("similar to %s" % source_title)
            record["keyword_strategy"] = "similar_films"
        self._store_managed_record(record)
        self._save_state()
        if record.get("sync_to_trakt"):
            try:
                record = self.sync_list_to_trakt(record.get("local_id"), silent=True)
            except Exception as exc:
                self.record_activity("%s was saved locally; initial Trakt sync was skipped" % name, level="warning", detail=str(exc), notify=False)
        self.record_activity("Created %s from Find Similar" % name, notify=True)
        return record

    def _generate_keyword_and_write(
        self, name, prompt, count, rules=None, silent=False, managed_record=None,
        description=None, content_type="movies", sync_to_trakt=None, persist=True,
    ):
        """Build and persist a list from deterministic rules without calling an AI provider."""
        self._require_keyword_catalogue()
        count = max(5, min(50, self._safe_int(count, 20)))
        content_type = content_type if content_type in ("movies", "shows", "both") else "movies"
        rules = rules if isinstance(rules, dict) else parse_prompt(prompt)
        if self._safe_int(rules.get("version"), 0) < PARSER_VERSION:
            rules = parse_prompt(prompt)
        if not rules.get("confidence"):
            raise RuntimeError("The saved request no longer contains a clear Keyword Matching filter.")

        profile = self.state.get("profile") or {}
        if self._profile_source_available() and self._profile_is_stale():
            try:
                self.sync_profile(silent=True)
                profile = self.state.get("profile") or profile
            except Exception as exc:
                self.record_activity("Using cached preferences for Keyword Matching", level="warning", detail=str(exc), notify=False)

        pool_limit = min(100, max(40, count * 3))
        history_mode = str(rules.get("history_mode") or "")
        if history_mode in ("stale", "plays") and content_type == "movies":
            watched = [row for row in profile.get("watched", []) if isinstance(row, dict) and row.get("title")]
            now = int(time.time())
            pool = []
            for row in watched:
                if history_mode == "plays":
                    plays = self._safe_int(row.get("playcount"), 0)
                    wanted = self._safe_int(rules.get("history_plays"), 0)
                    comparison = str(rules.get("history_comparison") or "gte")
                    if not ((comparison == "exact" and plays == wanted) or (comparison == "gt" and plays > wanted) or (comparison == "gte" and plays >= wanted)):
                        continue
                else:
                    stamp_text = str(row.get("last_watched_at") or "").strip()
                    try:
                        stamp = int(time.mktime(time.strptime(stamp_text[:19].replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if now - stamp < self._safe_int(rules.get("history_days"), 0) * 86400:
                        continue
                pool.append({"title": row.get("title"), "year": row.get("year"), "tmdb_id": row.get("tmdb_id")})
            analysis = {"history_filter": history_mode}
        else:
            movie_pool, analysis = (self._keyword_candidate_pool(rules, pool_limit) if content_type != "shows" else ([], {}))
            show_specific_supported = not (
                rules.get("people") or rules.get("reference_movies") or rules.get("collection_query")
            )
            show_pool = (
                self.tmdb.discover_shows(rules, limit=pool_limit)
                if content_type != "movies" and show_specific_supported else []
            )
            for row in movie_pool:
                row["media_type"] = "movie"
            if content_type == "both":
                pool = []
                for index in range(max(len(movie_pool), len(show_pool))):
                    if index < len(movie_pool):
                        pool.append(movie_pool[index])
                    if index < len(show_pool):
                        pool.append(show_pool[index])
            else:
                pool = show_pool if content_type == "shows" else movie_pool
        if not pool:
            if content_type == "shows" and not show_specific_supported:
                raise RuntimeError(
                    "Keyword Matching cannot reliably match TV shows from named people, collections or reference films yet. "
                    "Use AI for this request, or use TV filters such as genre, year, rating, country or language."
                )
            raise RuntimeError("Keyword Matching found no items for those filters. Try broadening the request.")
        preference_weights = preferred_genre_ids(profile)
        pool = sorted(
            pool, key=lambda row: score_candidate(row, rules, preference_weights, analysis), reverse=True,
        )

        history_pool = history_mode in ("stale", "plays") and content_type == "movies"
        excluded_ids = set() if history_pool else self._excluded_movie_ids(profile)
        excluded_markers = {"tmdb": set(), "imdb": set(), "title_year": set()} if history_pool else self._excluded_movie_markers(profile)
        excluded_show_ids = self._excluded_show_ids(profile)
        excluded_show_markers = self._excluded_show_markers(profile)
        for row in self.state.get("hidden_movies", []):
            if not isinstance(row, dict):
                continue
            if str(row.get("media_type") or "movie") != "movie":
                continue
            try:
                excluded_ids.add(int(row.get("trakt_id")))
            except (TypeError, ValueError):
                pass
        previous_markers = set()
        if managed_record:
            previous_markers = {
                (str(row.get("media_type") or "movie"), self._normalise_title(row.get("title")), self._safe_int(row.get("year"), 0))
                for row in managed_record.get("movies") or [] if isinstance(row, dict)
            }

        candidates = []
        resolved_ids = set()
        for item in pool:
            title, year = item.get("title", ""), item.get("year")
            media_type = str(item.get("media_type") or "movie").lower()
            marker = (self._normalise_title(title), self._safe_int(year, 0))
            if (media_type, marker[0], marker[1]) in previous_markers and len(pool) > count:
                continue
            if history_pool:
                was_cached, movie = self._movie_cache_lookup(title, year, media_type)
                if not was_cached:
                    try:
                        matches = self.trakt.search_movies(title, year)
                    except TraktError as exc:
                        if exc.status_code == 429 and candidates:
                            break
                        raise
                    movie = self._select_media_match(matches, title, year, media_type)
                    if movie:
                        movie = dict(movie)
                        movie["media_type"] = media_type
                        self._cache_movie(title, year, movie, media_type)
                    else:
                        self._cache_movie_miss(title, year, media_type)
            else:
                movie = self._keyword_tmdb_item(item, media_type)
            if not movie:
                continue
            if history_pool:
                movie_year = self._safe_int(movie.get("year"), 0)
                movie_rating = float(movie.get("rating") or 0)
                movie_runtime = self._safe_int(movie.get("runtime"), 0)
                movie_genres = {str(value).replace("-", " ").casefold() for value in movie.get("genres") or []}
                wanted_genres = {str(value).replace("-", " ").casefold() for value in rules.get("genre_labels") or []}
                if rules.get("year_min") and movie_year < self._safe_int(rules.get("year_min"), 0): continue
                if rules.get("year_max") and movie_year > self._safe_int(rules.get("year_max"), 0): continue
                if rules.get("rating_min") and movie_rating < float(rules.get("rating_min") or 0): continue
                if rules.get("runtime_min") and movie_runtime < self._safe_int(rules.get("runtime_min"), 0): continue
                if rules.get("runtime_max") and movie_runtime > self._safe_int(rules.get("runtime_max"), 0): continue
                if wanted_genres and not wanted_genres.issubset(movie_genres): continue
            ids = movie.get("ids") or {}
            try:
                trakt_id = int(ids.get("trakt"))
            except (TypeError, ValueError):
                trakt_id = 0
            movie_marker = (self._normalise_title(movie.get("title")), self._safe_int(movie.get("year"), 0))
            tmdb_id = str(ids.get("tmdb") or "")
            imdb_id = str(ids.get("imdb") or "").casefold()
            identity = ("tmdb", tmdb_id) if tmdb_id else (("trakt", str(trakt_id)) if trakt_id else ("title", movie_marker))
            if (
                (media_type == "movie" and trakt_id and trakt_id in excluded_ids) or (media_type, identity) in resolved_ids
                or (media_type == "show" and trakt_id and trakt_id in excluded_show_ids)
                or (media_type == "movie" and tmdb_id and tmdb_id in excluded_markers["tmdb"])
                or (media_type == "movie" and imdb_id and imdb_id in excluded_markers["imdb"])
                or (media_type == "movie" and movie_marker in excluded_markers["title_year"])
                or (media_type == "show" and tmdb_id and tmdb_id in excluded_show_markers["tmdb"])
                or (media_type == "show" and imdb_id and imdb_id in excluded_show_markers["imdb"])
                or (media_type == "show" and movie_marker in excluded_show_markers["title_year"])
            ):
                continue
            resolved_ids.add((media_type, identity))
            local_movie = self._compact_movie(movie)
            local_movie["media_type"] = media_type
            reason_labels = {
                "similar_people": "Shares catalogue signals with the referenced creators",
                "recurring_collaborators": "Matches recurring collaborators from the referenced creators",
                "exact_people": "Matches the named actor or director",
                "reference_people": "Matches the referenced film and named actor or director",
                "similar_films": "Related to the referenced films and saved filters",
                "collection": "Part of the selected movie collection",
            }
            local_movie["match_reason"] = reason_labels.get(
                str(rules.get("strategy") or ""), "Matched the saved Keyword Matching filters",
            )
            candidates.append({"movie": local_movie})
            if len(candidates) >= count:
                break
        if not candidates:
            if history_pool:
                raise RuntimeError("Keyword Matching found no films in your viewing history for those filters.")
            raise RuntimeError("Keyword Matching could not find any new unwatched items. Try broader filters or request fewer items.")

        previous = managed_record or {}
        record = dict(previous)
        now = int(time.time())
        record.update({
            "local_id": previous.get("local_id") or uuid.uuid4().hex,
            "name": name, "prompt": prompt,
            "description": str(description).strip() if description is not None else str(previous.get("description") or ""),
            "count": count, "updated_at": now, "local_changed_at": now,
            "last_result_count": len(candidates), "movies": [row["movie"] for row in candidates],
            "recommendations": [], "grounded_candidate_count": len(pool),
            "generation_method": "keyword", "keyword_rules": rules,
            "keyword_strategy": str(rules.get("strategy") or "filtered_discover"),
            "content_type": content_type,
        })
        if sync_to_trakt is not None:
            record["sync_to_trakt"] = bool(sync_to_trakt)
        elif "sync_to_trakt" not in record:
            record["sync_to_trakt"] = bool(self._sync_enabled() and self._has_oauth())
        if "artwork" not in record:
            record["artwork"] = normalise_list_art({})
        if "regeneration_enabled" not in record:
            record["regeneration_enabled"] = self._default_regeneration_enabled()
        if "regeneration_interval_hours" not in record:
            record["regeneration_interval_hours"] = self._default_regeneration_interval()
        record.setdefault("regeneration_last_attempt_at", 0)
        if "trakt_refresh_enabled" not in record:
            record["trakt_refresh_enabled"] = bool(record.get("sync_to_trakt") and self._default_trakt_refresh_enabled())
        record.setdefault("trakt_refresh_interval_hours", self._default_trakt_refresh_interval())
        record.setdefault("trakt_refresh_cycle_at", self._safe_int(record.get("trakt_synced_at"), 0))
        record.setdefault("trakt_last_attempt_at", 0)
        if not persist:
            return record
        self._store_managed_record(record, managed_record)
        self._save_state()

        if not managed_record and record.get("sync_to_trakt") and self._has_oauth():
            try:
                record = self.sync_list_to_trakt(record.get("local_id"), silent=True)
            except Exception as exc:
                self.record_activity("%s created in Kodi; initial Trakt copy was skipped" % name, level="warning", detail=str(exc), notify=False)
        if not silent:
            action = "created" if not managed_record else "refreshed"
            self.record_activity("%s %s locally with %d items using Keyword Matching" % (name, action, len(candidates)), notify=True)
        return record

    def _generate_and_write(
        self, name, prompt, count, silent=False, managed_record=None,
        description=None, reference_movies=None, content_type="movies",
        sync_to_trakt=None, persist=True,
    ):
        self._require_ai()
        count = max(5, min(50, self._safe_int(count, 20)))
        content_type = content_type if content_type in ("movies", "shows", "both") else "movies"

        has_profile_source = self._profile_source_available()
        if has_profile_source and self._profile_is_stale():
            try:
                self.sync_profile(silent=True)
            except Exception as exc:
                if not self.state.get("profile"):
                    raise
                # A stale local profile is more useful than failing the entire
                # recommendation request during a temporary source outage.
                self.record_activity(
                    "Using cached preference profile",
                    level="warning", detail=str(exc), notify=False,
                )
        # Preference history improves personalisation but is not required. New
        # prompt-only users receive recommendations without spending
        # an extra AI request on an empty preference summary.
        profile = self.state.get("profile") or {}
        has_preference_evidence = bool(
            profile.get("ratings") or profile.get("watched") or profile.get("library")
        )
        if has_preference_evidence:
            fingerprint = self._ensure_taste_fingerprint(profile, force=False)
        else:
            fingerprint = {
                "summary": "No preference history is connected; follow the user's current request closely.",
                "core_preferences": [],
                "avoidances": [],
                "director_affinities": [],
                "actor_affinities": [],
                "representative_likes": [],
                "representative_dislikes": [],
                "exploration_directions": [],
            }
        taste_context = self._build_recommendation_context(profile, fingerprint)
        taste_context["recommendation_mode"] = "personalised" if has_preference_evidence else "prompt_only"
        if reference_movies is None and managed_record:
            reference_movies = managed_record.get("reference_movies")
        compact_references = []
        for row in reference_movies or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            year = self._safe_int(row.get("year"), 0)
            if title:
                compact_references.append({"title": title, "year": year, "media_type": str(row.get("media_type") or "movie")})
            if len(compact_references) >= 30:
                break
        if compact_references:
            taste_context["reference_movies"] = compact_references
            taste_context["reference_movies_note"] = (
                "Use these films only as creative reference evidence. Follow the user's instruction, "
                "infer shared qualities, and do not recommend the reference films themselves."
            )
        hidden_rows = [row for row in self.state.get("hidden_movies", []) if isinstance(row, dict)]
        if hidden_rows:
            taste_context["never_recommend"] = [
                {"title": row.get("title"), "year": self._safe_int(row.get("year"), 0), "media_type": str(row.get("media_type") or "movie")}
                for row in hidden_rows[-100:] if row.get("title")
            ]
        if managed_record:
            previous = []
            for movie in managed_record.get("movies") or []:
                if not isinstance(movie, dict):
                    continue
                title = str(movie.get("title") or "").strip()
                year = self._safe_int(movie.get("year"), 0)
                if title and year:
                    previous.append({"title": title, "year": year, "media_type": str(movie.get("media_type") or "movie")})
                if len(previous) >= 50:
                    break
            if previous:
                taste_context["previous_recommendations_to_avoid"] = previous

        grounded_pool = self._grounded_candidate_pool(fingerprint) if content_type != "shows" else []
        if grounded_pool:
            taste_context["verified_candidate_pool"] = grounded_pool
            taste_context["verified_candidate_pool_note"] = (
                "These are optional real-title candidates from enabled catalogue and list services. "
                "Prefer strong matches from this pool, but follow the user's request above all else."
            )

        # Ask for a modest safety margin rather than 60% excess by default.
        # Trakt verification can still discard ambiguous/watched matches, while
        # smaller candidate sets mean fewer title-resolution API requests.
        candidate_count = min(60, count + max(8, count // 3))
        result = self.ai.recommend(prompt, taste_context, candidate_count, content_type=content_type)
        excluded_ids = self._excluded_movie_ids(profile)
        excluded_markers = self._excluded_movie_markers(profile)
        excluded_show_ids = self._excluded_show_ids(profile)
        excluded_show_markers = self._excluded_show_markers(profile)
        for row in self.state.get("hidden_movies", []):
            if not isinstance(row, dict):
                continue
            if str(row.get("media_type") or "movie") != "movie":
                continue
            try:
                excluded_ids.add(int(row.get("trakt_id")))
            except (TypeError, ValueError):
                pass
        candidates = []
        resolved_ids = set()
        reference_markers = {
            (str(row.get("media_type") or "movie"), self._normalise_title(row.get("title")), self._safe_int(row.get("year"), 0))
            for row in compact_references if row.get("title")
        }
        reference_titles = {(marker[0], marker[1]) for marker in reference_markers}

        for item in result.get("items", []):
            title = item.get("title", "")
            year = item.get("year")
            media_type = str(item.get("media_type") or "movie").lower()
            if content_type == "movies" and media_type != "movie":
                continue
            if content_type == "shows" and media_type != "show":
                continue
            if media_type not in ("movie", "show"):
                continue
            was_cached, movie = self._movie_cache_lookup(title, year, media_type)
            if not was_cached:
                try:
                    matches = (
                        self.trakt.search_shows(title, year)
                        if media_type == "show" else self.trakt.search_movies(title, year)
                    )
                except TraktError as exc:
                    if exc.status_code == 429 and candidates:
                        break
                    raise
                movie = self._select_media_match(matches, title, year, media_type)
                if movie:
                    movie = dict(movie)
                    movie["media_type"] = media_type
                    self._cache_movie(title, year, movie, media_type)
                else:
                    self._cache_movie_miss(title, year, media_type)
            if not movie:
                continue
            movie_marker = (
                media_type, self._normalise_title(movie.get("title")), self._safe_int(movie.get("year"), 0)
            )
            if movie_marker in reference_markers or (movie_marker[0], movie_marker[1]) in reference_titles:
                continue
            movie_ids = movie.get("ids") or {}
            trakt_id = movie_ids.get("trakt")
            try:
                trakt_id = int(trakt_id)
            except (TypeError, ValueError):
                continue
            tmdb_id = str(movie_ids.get("tmdb") or "")
            imdb_id = str(movie_ids.get("imdb") or "").casefold()
            if (
                (media_type == "movie" and trakt_id in excluded_ids) or (media_type, trakt_id) in resolved_ids
                or (media_type == "show" and trakt_id in excluded_show_ids)
                or (media_type == "movie" and tmdb_id and tmdb_id in excluded_markers["tmdb"])
                or (media_type == "movie" and imdb_id and imdb_id in excluded_markers["imdb"])
                or (media_type == "movie" and movie_marker[1:] in excluded_markers["title_year"])
                or (media_type == "show" and tmdb_id and tmdb_id in excluded_show_markers["tmdb"])
                or (media_type == "show" and imdb_id and imdb_id in excluded_show_markers["imdb"])
                or (media_type == "show" and movie_marker[1:] in excluded_show_markers["title_year"])
            ):
                continue
            resolved_ids.add((media_type, trakt_id))
            # Preserve the AI reason locally; skins can surface it later without
            # another provider request.
            local_movie = self._compact_movie(movie)
            local_movie["media_type"] = media_type
            if item.get("reason"):
                local_movie["ai_reason"] = str(item.get("reason"))
            candidates.append({"trakt_id": trakt_id, "recommendation": item, "movie": local_movie})
            if len(candidates) >= count:
                break

        if not candidates:
            raise RuntimeError(
                "curatr couldn't find enough new matches this time. Try a broader prompt or ask for fewer items."
            )

        previous = managed_record or {}
        record = dict(previous)
        now = int(time.time())
        record.update({
            "local_id": previous.get("local_id") or uuid.uuid4().hex,
            "name": name,
            "prompt": prompt,
            "description": (
                str(description).strip()
                if description is not None
                else str(previous.get("description") or "")
            ),
            "count": count,
            "updated_at": now,
            "local_changed_at": now,
            "last_result_count": len(candidates),
            "movies": [row["movie"] for row in candidates],
            "recommendations": [row["recommendation"] for row in candidates],
            "grounded_candidate_count": len(grounded_pool),
            "generation_method": "ai",
            "content_type": content_type,
        })
        if compact_references:
            record["reference_movies"] = compact_references
        elif not managed_record:
            record.pop("reference_movies", None)
        if sync_to_trakt is not None:
            record["sync_to_trakt"] = bool(sync_to_trakt)
        elif "sync_to_trakt" not in record:
            # Never leave a brand-new local-only list appearing to wait for a
            # Trakt sync the user did not configure. It can be enabled later.
            record["sync_to_trakt"] = bool(self._sync_enabled() and self._has_oauth())
        if "artwork" not in record:
            record["artwork"] = normalise_list_art({})
        if "regeneration_enabled" not in record:
            record["regeneration_enabled"] = self._default_regeneration_enabled()
        if "regeneration_interval_hours" not in record:
            record["regeneration_interval_hours"] = self._default_regeneration_interval()
        if "regeneration_last_attempt_at" not in record:
            record["regeneration_last_attempt_at"] = 0
        if "trakt_refresh_enabled" not in record:
            record["trakt_refresh_enabled"] = bool(record.get("sync_to_trakt") and self._default_trakt_refresh_enabled())
        if "trakt_refresh_interval_hours" not in record:
            record["trakt_refresh_interval_hours"] = self._default_trakt_refresh_interval()
        if "trakt_refresh_cycle_at" not in record:
            record["trakt_refresh_cycle_at"] = self._safe_int(record.get("trakt_synced_at"), 0)
        if "trakt_last_attempt_at" not in record:
            record["trakt_last_attempt_at"] = 0

        if not persist:
            return record

        self._store_managed_record(record, managed_record)
        self._save_state()

        # AI regeneration is deliberately local-first and independent of Trakt.
        # For a brand-new list only, honour the "sync new lists" default by
        # creating its first Trakt copy when OAuth is available. Future AI
        # regenerations do not touch Trakt unless the separate Trakt schedule
        # (or manual Sync now action) says to do so.
        if not managed_record and record.get("sync_to_trakt") and self._has_oauth():
            try:
                record = self.sync_list_to_trakt(record.get("local_id"), silent=True)
            except Exception as exc:
                self.record_activity(
                    "%s created in Kodi; initial Trakt copy was skipped" % record.get("name"),
                    level="warning", detail=str(exc), notify=False,
                )
        elif not managed_record and record.get("sync_to_trakt") and not self._has_oauth():
            self.record_activity(
                "%s created in Kodi; a Trakt copy needs a connected account" % record.get("name"),
                level="warning", notify=False,
            )

        if not silent:
            action_word = "created" if not managed_record else "refreshed"
            storage = "locally"
            if not managed_record and record.get("trakt_synced_at"):
                storage += " + initially synced to Trakt"
            self.record_activity(
                "%s %s %s with %d items" % (record["name"], action_word, storage, len(candidates)),
                notify=True,
            )
        return record

    def _resolve_target_list(self, name, description, managed_record, silent):
        self._require_trakt_write()
        remote_lists = self.trakt.lists()

        if managed_record and managed_record.get("trakt_id") is not None:
            wanted_id = str(managed_record.get("trakt_id"))
            for item in remote_lists:
                if str((item.get("ids") or {}).get("trakt")) == wanted_id:
                    if (item.get("name") or "").strip() != name.strip():
                        try:
                            self.trakt.update_list(wanted_id, name=name, description=description)
                            item = dict(item)
                            item["name"] = name
                        except Exception:
                            pass
                    return item
            return self.trakt.create_list(name, description or "Personalised recommendations created by curatr.")

        same_name = None
        for item in remote_lists:
            if (item.get("name") or "").strip().casefold() == name.strip().casefold():
                same_name = item
                break

        if same_name:
            # Never silently adopt an unrelated same-name Trakt list: syncing
            # would remove items that are not in this AI list. Background/silent
            # operations therefore create a unique list instead. Interactive
            # sync may explicitly ask the user for permission to adopt it.
            adopt = False
            if not silent:
                adopt = xbmcgui.Dialog().yesno(
                    self.name,
                    "A Trakt list named '%s' already exists.\n\nUse it for this local curatr list?" % name,
                )
            if adopt:
                return same_name
            name = self._unique_list_name(name, remote_lists)

        return self.trakt.create_list(name, description or "Personalised recommendations created by curatr.")

    def _sync_list_movies(self, list_id, desired_ids):
        return self._sync_list_items(list_id, desired_ids, [])

    def _sync_list_items(self, list_id, desired_movies, desired_shows):
        current_items = self.trakt.list_items(list_id, extended=False)
        current_movies, current_shows = set(), set()
        for row in current_items:
            for media_type, bucket in (("movie", current_movies), ("show", current_shows)):
                item = row.get(media_type, {}) if isinstance(row, dict) else {}
                try:
                    bucket.add(int((item.get("ids") or {}).get("trakt")))
                except (AttributeError, TypeError, ValueError):
                    pass

        wanted_movies = set(self.trakt._unique_int_ids(desired_movies))
        wanted_shows = set(self.trakt._unique_int_ids(desired_shows))
        add_movies, remove_movies = wanted_movies - current_movies, current_movies - wanted_movies
        add_shows, remove_shows = wanted_shows - current_shows, current_shows - wanted_shows
        if add_movies: self.trakt.add_movies(list_id, add_movies)
        if remove_movies: self.trakt.remove_movies(list_id, remove_movies)
        if add_shows: self.trakt.add_shows(list_id, add_shows)
        if remove_shows: self.trakt.remove_shows(list_id, remove_shows)

    def _managed_record_by_name(self, name):
        wanted = (name or "").strip().casefold()
        for item in self.state.get("ai_lists", []):
            if isinstance(item, dict) and (item.get("name") or "").strip().casefold() == wanted:
                return item
        return None

    def _store_managed_record(self, record, previous=None):
        if not record.get("local_id"):
            record["local_id"] = uuid.uuid4().hex
        previous_key = self._record_key(previous) if previous else ""
        new_key = self._record_key(record)
        kept = []
        for item in self.state.get("ai_lists", []):
            if not isinstance(item, dict):
                continue
            item_key = self._record_key(item)
            if item_key == new_key or (previous_key and item_key == previous_key):
                continue
            kept.append(item)
        kept.append(record)
        self.state["ai_lists"] = kept

    @staticmethod
    def _unique_list_name(base_name, remote_lists):
        existing = {(item.get("name") or "").strip().casefold() for item in remote_lists}
        candidate = "%s (AI)" % base_name
        if candidate.casefold() not in existing:
            return candidate
        index = 2
        while True:
            candidate = "%s (AI %d)" % (base_name, index)
            if candidate.casefold() not in existing:
                return candidate
            index += 1

    # ---------- Recommendation resolution ----------

    @classmethod
    def _select_movie_match(cls, matches, requested_title, requested_year):
        return cls._select_media_match(matches, requested_title, requested_year, "movie")

    @classmethod
    def _select_media_match(cls, matches, requested_title, requested_year, media_type):
        if not isinstance(matches, list):
            return None
        wanted_title = cls._normalise_title(requested_title)
        try:
            wanted_year = int(requested_year)
        except (TypeError, ValueError):
            wanted_year = None

        exact_year = []
        nearby_year = []
        for result in matches:
            item = result.get(media_type, {}) if isinstance(result, dict) else {}
            if cls._normalise_title(item.get("title")) != wanted_title:
                continue
            try:
                item_year = int(item.get("year"))
            except (TypeError, ValueError):
                item_year = None
            if wanted_year is None or item_year == wanted_year:
                exact_year.append(item)
            elif item_year is not None and abs(item_year - wanted_year) <= 1:
                nearby_year.append(item)
        if exact_year:
            return exact_year[0]
        if nearby_year:
            return nearby_year[0]
        return None

    @staticmethod
    def _normalise_title(value):
        text = unicodedata.normalize("NFKD", str(value or "")).casefold()
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return "".join(ch for ch in text if ch.isalnum())

    @staticmethod
    def _excluded_movie_ids(profile):
        ids = set()
        for bucket in ("watched", "ratings"):
            for item in profile.get(bucket, []):
                try:
                    ids.add(int(item.get("trakt_id")))
                except (AttributeError, TypeError, ValueError):
                    pass
        return ids

    @classmethod
    def _excluded_movie_markers(cls, profile):
        markers = {"tmdb": set(), "imdb": set(), "title_year": set()}
        for bucket in ("watched", "ratings"):
            for item in (profile or {}).get(bucket, []):
                if not isinstance(item, dict):
                    continue
                if item.get("tmdb_id") not in (None, ""):
                    markers["tmdb"].add(str(item.get("tmdb_id")))
                if item.get("imdb_id"):
                    markers["imdb"].add(str(item.get("imdb_id")).casefold())
                title = cls._normalise_title(item.get("title"))
                year = cls._safe_int(item.get("year"), 0)
                if title:
                    markers["title_year"].add((title, year))
        return markers

    @staticmethod
    def _excluded_show_ids(profile):
        ids = set()
        for bucket in ("shows_watched", "show_ratings"):
            for item in (profile or {}).get(bucket, []):
                try:
                    ids.add(int((item.get("ids") or {}).get("trakt")))
                except (AttributeError, TypeError, ValueError):
                    pass
        return ids

    @classmethod
    def _excluded_show_markers(cls, profile):
        markers = {"tmdb": set(), "imdb": set(), "title_year": set()}
        for bucket in ("shows_watched", "show_ratings"):
            for item in (profile or {}).get(bucket, []):
                if not isinstance(item, dict):
                    continue
                ids = item.get("ids") or {}
                if ids.get("tmdb") not in (None, ""):
                    markers["tmdb"].add(str(ids.get("tmdb")))
                if ids.get("imdb"):
                    markers["imdb"].add(str(ids.get("imdb")).casefold())
                title = cls._normalise_title(item.get("title"))
                year = cls._safe_int(item.get("year"), 0)
                if title:
                    markers["title_year"].add((title, year))
        return markers

    # ---------- Compact reusable AI taste fingerprint ----------

    def _ensure_taste_fingerprint(self, profile=None, force=False):
        self._require_ai()
        profile = profile or self.state.get("profile") or {}
        current = self.state.get("taste_fingerprint") or {}
        if current.get("summary") and not force and not self._taste_fingerprint_is_stale():
            return current

        valid_ratings = [
            row for row in profile.get("ratings", [])
            if isinstance(row, dict) and row.get("title") and row.get("rating") is not None
        ]
        watched_count = len([row for row in profile.get("watched", []) if isinstance(row, dict) and row.get("title")])
        library_count = len([row for row in profile.get("library", []) if isinstance(row, dict) and row.get("title")])

        if len(valid_ratings) < 3 and watched_count < 10 and library_count < 20:
            # With very little history there is not enough evidence to justify
            # spending an API call on a pseudo-precise profile. The current
            # request can still drive recommendations while exclusions stay local.
            fingerprint = {
                "summary": "Limited personal rating history; rely mainly on the user's current request until more items are rated.",
                "core_preferences": [],
                "avoidances": [],
                "director_affinities": [],
                "actor_affinities": [],
                "representative_likes": [],
                "representative_dislikes": [],
                "exploration_directions": [],
            }
        else:
            fingerprint = self.ai.build_taste_fingerprint(profile)

        fingerprint["actor_affinities"] = [
            str(item.get("name") or "").strip()
            for item in profile.get("favourite_actors", [])[:10]
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]

        fingerprint["generated_at"] = int(time.time())
        fingerprint["source_signature"] = self._profile_taste_signature(profile)
        fingerprint["source_rating_count"] = len(valid_ratings)
        fingerprint["source_watched_count"] = watched_count
        fingerprint["source_library_count"] = library_count
        fingerprint["provider"] = self.ai.provider_id
        fingerprint["provider_name"] = self.ai.provider_name
        fingerprint["model"] = self.ai.model
        self.state["taste_fingerprint"] = fingerprint
        self._save_state()
        self._update_ai_status_rows()
        return fingerprint

    def _taste_fingerprint_is_stale(self):
        fingerprint = self.state.get("taste_fingerprint") or {}
        if not fingerprint.get("summary"):
            return True
        if str(fingerprint.get("provider") or "openai") != str(getattr(self.ai, "provider_id", "") or ""):
            return True
        if str(fingerprint.get("model") or "") != str(getattr(self.ai, "model", "") or ""):
            return True
        profile = self.state.get("profile") or {}
        if profile and str(fingerprint.get("source_signature") or "") != self._profile_taste_signature(profile):
            return True
        generated_at = self._safe_int(fingerprint.get("generated_at"), 0)
        if not generated_at:
            return True
        hours = self._setting_int("taste_fingerprint_refresh_hours", 168, 24, 720)
        return time.time() - generated_at >= hours * 3600

    @staticmethod
    def _profile_taste_signature(profile):
        ratings = []
        for item in (profile or {}).get("ratings", []):
            if not isinstance(item, dict):
                continue
            ratings.append([
                item.get("title"),
                item.get("year"),
                item.get("trakt_id"),
                item.get("tmdb_id"),
                item.get("imdb_id"),
                item.get("rating"),
                item.get("rating_conflict"),
            ])
        directors = []
        for item in (profile or {}).get("favourite_directors", []):
            if not isinstance(item, dict):
                continue
            directors.append([
                item.get("name"),
                item.get("liked_movies"),
                item.get("average_rating"),
            ])
        actors = []
        for item in (profile or {}).get("favourite_actors", []):
            if not isinstance(item, dict):
                continue
            actors.append([
                item.get("name"),
                item.get("liked_movies"),
                item.get("average_rating"),
            ])
        watched = []
        for item in (profile or {}).get("watched", [])[:100]:
            if isinstance(item, dict):
                watched.append([item.get("title"), item.get("year"), item.get("playcount")])
        library = []
        for item in (profile or {}).get("library", [])[:100]:
            if isinstance(item, dict):
                library.append([item.get("title"), item.get("year")])
        payload = {
            "ratings": ratings,
            "directors": directors,
            "actors": actors,
            "watched": watched,
            "library": library,
            "threshold": (profile or {}).get("liked_rating_threshold"),
            "sources": (profile or {}).get("sources"),
            "mode": (profile or {}).get("preference_history_mode"),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_recommendation_context(profile, fingerprint):
        watched = [item for item in profile.get("watched", []) if isinstance(item, dict)]
        watched.sort(key=lambda item: str(item.get("last_watched_at") or ""), reverse=True)
        recent = []
        seen = set()
        for item in watched:
            title = str(item.get("title") or "").strip()
            try:
                year = int(item.get("year"))
            except (TypeError, ValueError):
                continue
            marker = (title.casefold(), year)
            if not title or marker in seen:
                continue
            seen.add(marker)
            recent.append({"title": title, "year": year})
            if len(recent) >= 35:
                break

        compact_fingerprint = {
            key: fingerprint.get(key)
            for key in (
                "summary",
                "core_preferences",
                "avoidances",
                "director_affinities",
                "actor_affinities",
                "representative_likes",
                "representative_dislikes",
                "exploration_directions",
            )
        }
        recent_shows = []
        for item in sorted(
            [row for row in profile.get("shows_watched", []) if isinstance(row, dict)],
            key=lambda row: str(row.get("last_watched_at") or ""), reverse=True,
        ):
            title = str(item.get("title") or "").strip()
            if title:
                recent_shows.append({"title": title, "year": Curator._safe_int(item.get("year"), 0), "media_type": "show"})
            if len(recent_shows) >= 35:
                break
        show_ratings = [
            {"title": row.get("title"), "year": Curator._safe_int(row.get("year"), 0), "rating": Curator._safe_int(row.get("rating"), 0), "media_type": "show"}
            for row in profile.get("show_ratings", [])[:40]
            if isinstance(row, dict) and row.get("title") and row.get("rating") is not None
        ]
        return {
            "taste_fingerprint": compact_fingerprint,
            "recently_watched_examples_to_avoid": recent,
            "recently_watched_shows_to_avoid": recent_shows,
            "tv_show_rating_examples": show_ratings,
            "local_exclusion_counts": {
                "rated": len(profile.get("ratings", [])),
                "watched": len(profile.get("watched", [])),
                "shows_rated": len(profile.get("show_ratings", [])),
                "shows_watched": len(profile.get("shows_watched", [])),
            },
        }

    def view_taste_fingerprint(self):
        self._require_profile_source()
        self._require_ai()
        if self._profile_is_stale():
            self.sync_profile(silent=True)
        profile = self.state.get("profile") or {}
        fingerprint = self._ensure_taste_fingerprint(profile, force=False)

        def bullet_lines(values):
            return "\n".join("• %s" % value for value in (values or [])) or "Not enough information yet"

        def movie_lines(values):
            rows = []
            for item in values or []:
                if isinstance(item, dict):
                    rows.append("• %s (%s)" % (item.get("title") or "?", item.get("year") or "?"))
            return "\n".join(rows) or "Not enough information yet"

        source_names = [
            "Kodi Library" if value == "kodi" else "Trakt"
            for value in profile.get("sources", []) if value in ("kodi", "trakt")
        ]
        source_text = " + ".join(source_names) or "prompt-only mode"
        conflict_count = self._safe_int(profile.get("conflicting_ratings"), 0)
        data_note = (
            "Built using %d personal ratings, %d watched items and %d Kodi Library titles from %s. "
            "%d conflicting rating%s %s ignored. "
            "Complete watched/rated exclusion data stays local to Kodi and is not sent with every recommendation request."
            % (
                self._safe_int(fingerprint.get("source_rating_count"), 0),
                self._safe_int(fingerprint.get("source_watched_count"), 0),
                self._safe_int(fingerprint.get("source_library_count"), 0), source_text,
                conflict_count, "" if conflict_count == 1 else "s",
                "was" if conflict_count == 1 else "were",
            )
        )

        text = (
            "OVERVIEW\n%s\n\nINTERESTS\n%s\n\nLESS INTERESTED IN\n%s\n\n"
            "DIRECTORS YOU LIKE\n%s\n\nACTORS YOU LIKE\n%s\n\nLIKES\n%s\n\n"
            "DISLIKES\n%s\n\nWORTH EXPLORING\n%s\n\nDATA USED\n%s\n\nCREATED WITH\n%s"
            % (
                fingerprint.get("summary") or "Not enough information yet",
                bullet_lines(fingerprint.get("core_preferences")),
                bullet_lines(fingerprint.get("avoidances")),
                bullet_lines(fingerprint.get("director_affinities")),
                bullet_lines(fingerprint.get("actor_affinities")),
                movie_lines(fingerprint.get("representative_likes")),
                movie_lines(fingerprint.get("representative_dislikes")),
                bullet_lines(fingerprint.get("exploration_directions")),
                data_note,
                "%s / %s" % (fingerprint.get("provider_name") or self.ai.provider_name, fingerprint.get("model") or self.ai.model),
            )
        )
        xbmcgui.Dialog().textviewer("My Preferences", text)

    def show_ai_usage(self):
        usage = self.state.get("ai_usage") or {}
        kinds = usage.get("by_kind") or {}
        providers = usage.get("by_provider") or {}
        lines = [
            "AI requests recorded by this addon: %s" % self._format_int(usage.get("requests", 0)),
            "Input tokens: %s" % self._format_int(usage.get("input_tokens", 0)),
            "Cached input tokens: %s" % self._format_int(usage.get("cached_input_tokens", 0)),
            "Output tokens: %s" % self._format_int(usage.get("output_tokens", 0)),
            "Reasoning/thinking tokens: %s" % self._format_int(usage.get("reasoning_tokens", 0)),
            "Total tokens: %s" % self._format_int(usage.get("total_tokens", 0)),
        ]
        for kind in ("taste_fingerprint", "recommendation"):
            bucket = kinds.get(kind) or {}
            if bucket.get("requests"):
                label = "Preference summary builds" if kind == "taste_fingerprint" else "Recommendation calls"
                lines.append(
                    "%s: %s request(s), %s total tokens"
                    % (label, self._format_int(bucket.get("requests", 0)), self._format_int(bucket.get("total_tokens", 0)))
                )
        if providers:
            lines.append("")
            lines.append("BY PROVIDER")
            for provider_id in sorted(providers):
                bucket = providers.get(provider_id) or {}
                lines.append(
                    "%s: %s request(s), %s total tokens"
                    % (bucket.get("provider_name") or provider_id, self._format_int(bucket.get("requests", 0)), self._format_int(bucket.get("total_tokens", 0)))
                )
        lines.extend([
            "",
            "These counters come from the token-usage metadata returned by the selected AI provider. They are useful "
            "for comparing addon usage. Check your provider's dashboard for billing information.",
        ])
        xbmcgui.Dialog().textviewer("AI Usage", "\n".join(lines))

    # ---------- Background schedules ----------

    def _list_regeneration_due(self, record, now=None):
        if not isinstance(record, dict) or not record.get("regeneration_enabled"):
            return False
        now = int(now or time.time())
        interval = max(1, min(720, self._safe_int(record.get("regeneration_interval_hours"), 24))) * 3600
        last_success = self._safe_int(record.get("updated_at"), 0)
        last_attempt = self._safe_int(record.get("regeneration_last_attempt_at"), 0)
        if last_success and now - last_success < interval:
            return False
        if last_attempt and now - last_attempt < self.AUTO_RETRY_SECONDS:
            return False
        return True

    def _list_trakt_refresh_due(self, record, now=None):
        if not isinstance(record, dict):
            return False
        if not record.get("sync_to_trakt") or not record.get("trakt_refresh_enabled"):
            return False
        if not self._has_oauth():
            return False
        if not (record.get("movies") or []):
            return False
        now = int(now or time.time())
        interval = max(1, min(720, self._safe_int(record.get("trakt_refresh_interval_hours"), 24))) * 3600
        last_cycle = max(
            self._safe_int(record.get("trakt_refresh_cycle_at"), 0),
            self._safe_int(record.get("trakt_synced_at"), 0),
        )
        last_attempt = self._safe_int(record.get("trakt_last_attempt_at"), 0)
        if last_cycle and now - last_cycle < interval:
            return False
        if last_attempt and now - last_attempt < self.AUTO_RETRY_SECONDS:
            return False
        return True


    def auto_update_due(self, now=None):
        now = int(now or time.time())
        records = [row for row in self.state.get("ai_lists", []) if isinstance(row, dict)]
        return any(self._list_regeneration_due(row, now) or self._list_trakt_refresh_due(row, now) for row in records)

    def run_auto_update(self):
        now = int(time.time())
        records = [row for row in self.state.get("ai_lists", []) if isinstance(row, dict)]
        regeneration_due = [row for row in records if self._list_regeneration_due(row, now)]

        regenerated = 0
        regeneration_failed = 0
        for record in regeneration_due:
            key = self._record_key(record)
            attempt = dict(record)
            attempt["regeneration_last_attempt_at"] = now
            self._store_managed_record(attempt, record)
            self._save_state()
            try:
                refreshed = self.refresh_list(key, silent=True)
                refreshed = dict(refreshed)
                refreshed["regeneration_last_attempt_at"] = 0
                self._store_managed_record(refreshed, refreshed)
                self._save_state()
                regenerated += 1
            except Exception as exc:
                regeneration_failed += 1
                xbmc.log(
                    "curatr automatic regeneration failed for %s: %s" % (record.get("name"), exc),
                    xbmc.LOGERROR,
                )
                try:
                    self.record_activity(
                        "Automatic refresh failed for %s" % (record.get("name") or "curatr list"),
                        level="error", detail=str(exc), notify=False,
                    )
                except Exception:
                    pass

        # Reload records after regeneration so a Trakt refresh due in the same
        # service pass receives the newly generated local movie set.
        records = [row for row in self.state.get("ai_lists", []) if isinstance(row, dict)]
        trakt_due = [row for row in records if self._list_trakt_refresh_due(row, now)]
        trakt_synced = 0
        trakt_skipped = 0
        trakt_failed = 0

        for record in trakt_due:
            key = self._record_key(record)
            attempt = dict(record)
            attempt["trakt_last_attempt_at"] = now
            self._store_managed_record(attempt, record)
            self._save_state()
            try:
                current = self._managed_record_by_id(key) or attempt
                local_updated = max(
                    self._safe_int(current.get("updated_at"), 0),
                    self._safe_int(current.get("local_changed_at"), 0),
                )
                last_synced = self._safe_int(current.get("trakt_synced_at"), 0)
                # Avoid spending Trakt requests when nothing locally has changed.
                if current.get("trakt_id") and last_synced and local_updated <= last_synced:
                    skipped = dict(current)
                    skipped["trakt_refresh_cycle_at"] = now
                    skipped["trakt_last_attempt_at"] = 0
                    self._store_managed_record(skipped, current)
                    self._save_state()
                    trakt_skipped += 1
                    continue

                synced = self.sync_list_to_trakt(key, silent=True)
                synced = dict(synced)
                synced["trakt_refresh_cycle_at"] = now
                synced["trakt_last_attempt_at"] = 0
                self._store_managed_record(synced, synced)
                self._save_state()
                trakt_synced += 1
            except Exception as exc:
                trakt_failed += 1
                xbmc.log(
                    "curatr automatic Trakt refresh failed for %s: %s" % (record.get("name"), exc),
                    xbmc.LOGERROR,
                )
                try:
                    self.record_activity(
                        "Automatic Trakt update failed for %s" % (record.get("name") or "curatr list"),
                        level="error", detail=str(exc), notify=False,
                    )
                except Exception:
                    pass

        failures = regeneration_failed + trakt_failed
        if regenerated or trakt_synced or failures:
            parts = []
            if regenerated:
                parts.append("%d list(s) refreshed" % regenerated)
            if trakt_synced:
                parts.append("%d Trakt updated" % trakt_synced)
            if failures:
                parts.append("%d failed" % failures)
            self.record_activity(
                "Background update: " + ", ".join(parts),
                level="warning" if failures else "info",
                notify=True,
                background=True,
            )

        return {
            "regenerated": regenerated,
            "regeneration_failed": regeneration_failed,
            "trakt_synced": trakt_synced,
            "trakt_skipped": trakt_skipped,
            "trakt_failed": trakt_failed,
            "skipped": not regeneration_due and not trakt_due,
        }

    def _require_trakt_write(self):
        if not self.trakt.client_id or not self.trakt.client_secret:
            raise RuntimeError("curatr's Trakt application credentials are unavailable.")
        if not self._has_oauth():
            raise RuntimeError(
                "This list is safe in Kodi, but updating a Trakt copy needs curatr to be connected to Trakt. "
                "You can keep curatr in Kodi-only mode when you do not want it to use a separate Trakt connection."
            )
        self.trakt.ensure_access_token()

    def _require_profile_source(self):
        mode = self._preference_history_mode()
        if mode in ("both", "kodi"):
            return "kodi" if mode == "kodi" else "both"
        if not self.trakt.client_id:
            raise RuntimeError("curatr's Trakt application credentials are unavailable.")
        if self._has_oauth():
            self.trakt.ensure_access_token()
            return "trakt"
        if self._public_username():
            return "trakt"
        raise RuntimeError("Connect Trakt or add a public Trakt username, or choose Kodi Library in Preference History.")

    def _profile_source_available(self):
        mode = self._preference_history_mode()
        return mode in ("both", "kodi") or self._trakt_preference_available()

    def _require_ai(self):
        if not self.ai.api_key:
            raise RuntimeError("Add your %s API key in Settings before creating a list." % getattr(self.ai, "provider_name", "AI"))

    def _require_keyword_catalogue(self):
        if self.tmdb is None or not getattr(self.tmdb, "api_key", ""):
            raise RuntimeError(
                "Keyword Matching needs TMDB for its catalogue. Enable TMDB and add a TMDB API key "
                "under Metadata in Settings; no AI key or linked account is required."
            )

    def _profile_is_stale(self):
        profile = self.state.get("profile") or {}
        if str(profile.get("preference_history_mode") or "") != self._preference_history_mode():
            return True
        synced_at = self._safe_int(profile.get("synced_at"), 0)
        if not synced_at:
            return True
        hours = self._setting_int("profile_refresh_hours", 72, 1, 168)
        return time.time() - synced_at >= hours * 3600

    def _profile_limit(self):
        return self._setting_int("profile_items", 300, 50, 1000)

    def _setting_int(self, setting_id, default, minimum=None, maximum=None):
        value = self._safe_int(self.addon.getSetting(setting_id), default)
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    @staticmethod
    def _safe_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def menu(self):
        """Compact script menu; the video-plugin interface offers the same sections with artwork."""
        while True:
            choice = xbmcgui.Dialog().select(self.name, [
                self._loc(32410, "Lists"),
                self._loc(32411, "Explore"),
                self._loc(32412, "Preferences & Activity"),
                self._loc(32413, "Settings"),
            ])
            if choice < 0:
                return
            if choice == 0:
                sub = xbmcgui.Dialog().select(self._loc(32410, "Lists"), [
                    self._loc(32420, "Create a new list"),
                    self._loc(32421, "Manage my lists"),
                    "Folders",
                    self._loc(32422, "Refresh All Lists"),
                    self._loc(32423, "Backup & Restore"),
                ])
                if sub == 0: self.create_list_interactive()
                elif sub == 1: self.manage_lists_interactive()
                elif sub == 2: self.manage_widget_folders_interactive()
                elif sub == 3: self.update_all()
                elif sub == 4: self.backup_menu_interactive()
            elif choice == 1:
                sub = xbmcgui.Dialog().select(self._loc(32411, "Explore"), [
                    self._loc(32430, "Quick Pick"),
                    self._loc(32431, "Saved Prompts"),
                    self._loc(32432, "Hidden"),
                ])
                if sub == 0: self.quick_pick_interactive()
                elif sub == 1: self.prompt_templates_interactive()
                elif sub == 2: self.manage_hidden_interactive()
            elif choice == 2:
                sub = xbmcgui.Dialog().select(self._loc(32412, "Preferences & Activity"), [
                    self._loc(32440, "Refresh Preferences"),
                    self._loc(32441, "View My Preferences"),
                    self._loc(32442, "AI Usage"),
                    self._loc(32443, "Recent Activity"),
                ])
                if sub == 0: self.sync_profile()
                elif sub == 1: self.view_taste_fingerprint()
                elif sub == 2: self.show_ai_usage()
                elif sub == 3: self.show_activity()
            elif choice == 3:
                self.open_settings()
