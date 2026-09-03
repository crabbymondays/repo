import hashlib
import json
import os
import time
import unicodedata
import uuid

import xbmc
import xbmcgui
import xbmcvfs

from .ai_factory import create_ai_client
from .art_cache import ArtworkCache
from .artwork_grid import choose_artwork
from .catalogue_clients import CatalogueError, MDBListClient, TMDBClient
from .kodi_library import KodiLibraryError, KodiLibraryReader
from .keyword_confirm import confirm_keyword_rules
from .keyword_matcher import PARSER_VERSION, candidate_matches, format_rules, parse_prompt, preferred_genre_ids, score_candidate
from .list_art import CHOICES as LIST_ART_CHOICES
from .list_art import label as list_art_label
from .list_art import normalise_state as normalise_list_art
from .list_art import summary as list_art_summary
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

    def __init__(self, addon, update_status=True, init_clients=True):
        self.addon = addon
        self.name = addon.getAddonInfo("name") or "curatr"
        self.profile_dir = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
        if not xbmcvfs.exists(self.profile_dir):
            xbmcvfs.mkdirs(self.profile_dir)
        self.state_path = os.path.join(self.profile_dir, "state.json")
        self._dirty_widget_folder_ids = set()
        self._deleted_widget_folder_ids = set()
        self._had_state_file = xbmcvfs.exists(self.state_path)
        self.state = self._load_state()
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
        for path in (self.state_path, backup_path):
            if not xbmcvfs.exists(path):
                continue
            try:
                raw = self._read_text(path)
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict):
                    raise ValueError("state root is not an object")
                if path == backup_path:
                    xbmc.log("curatr recovered state from its safety backup", xbmc.LOGWARNING)
                return data
            except Exception as exc:
                errors.append("%s: %s" % (os.path.basename(path), exc))
        if errors:
            xbmc.log("curatr could not read state: %s" % "; ".join(errors), xbmc.LOGWARNING)
        return {}

    def _merge_concurrent_widget_folders(self):
        """Preserve folder changes made by another curatr process since this instance loaded."""
        if not xbmcvfs.exists(self.state_path):
            return
        try:
            current = json.loads(self._read_text(self.state_path) or "{}")
        except Exception:
            return
        disk_folders = current.get("widget_folders") if isinstance(current, dict) else None
        if not isinstance(disk_folders, list):
            return
        memory_folders = self.state.get("widget_folders")
        if not isinstance(memory_folders, list):
            memory_folders = []

        memory_by_id = {
            str(row.get("id") or ""): row for row in memory_folders
            if isinstance(row, dict) and str(row.get("id") or "")
        }
        merged = []
        seen = set()
        for disk_row in disk_folders:
            if not isinstance(disk_row, dict):
                continue
            folder_id = str(disk_row.get("id") or "")
            if folder_id in self._deleted_widget_folder_ids:
                seen.add(folder_id)
                continue
            memory_row = memory_by_id.get(folder_id)
            if memory_row is not None and folder_id in self._dirty_widget_folder_ids:
                merged.append(memory_row)
            else:
                merged.append(disk_row)
            seen.add(folder_id)
        for memory_row in memory_folders:
            if not isinstance(memory_row, dict):
                continue
            folder_id = str(memory_row.get("id") or "")
            if folder_id and folder_id not in seen and folder_id not in self._deleted_widget_folder_ids:
                merged.append(memory_row)
        self.state["widget_folders"] = merged

    def _save_state(self, merge_disk_folders=True):
        if merge_disk_folders:
            self._merge_concurrent_widget_folders()
        payload = json.dumps(self.state, ensure_ascii=False, separators=(",", ":"))
        temp_path = self.state_path + ".tmp"
        backup_path = self.state_path + ".bak"
        moved_existing = False
        try:
            if xbmcvfs.exists(temp_path):
                xbmcvfs.delete(temp_path)
            self._write_text(temp_path, payload)
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
                    self._dirty_widget_folder_ids.clear()
                    self._deleted_widget_folder_ids.clear()
                    return
            if not xbmcvfs.rename(temp_path, self.state_path):
                self._write_text(self.state_path, payload)
                if xbmcvfs.exists(temp_path):
                    xbmcvfs.delete(temp_path)
            self._dirty_widget_folder_ids.clear()
            self._deleted_widget_folder_ids.clear()
        except Exception:
            if xbmcvfs.exists(temp_path):
                xbmcvfs.delete(temp_path)
            if moved_existing and not xbmcvfs.exists(self.state_path) and xbmcvfs.exists(backup_path):
                xbmcvfs.rename(backup_path, self.state_path)
            raise

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

        valid_list_ids = {self._record_key(row) for row in records if isinstance(row, dict) and self._record_key(row)}
        for folder in folders:
            entries = [
                entry for entry in folder.get("entries", [])
                if entry.get("type") != "curatr_list" or str(entry.get("list_id") or "") in valid_list_ids
            ]
            if entries != folder.get("entries", []):
                folder["entries"] = entries
                changed = True

        if changed:
            try:
                self._save_state(merge_disk_folders=False)
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

    def create_list_interactive(self, preset_prompt=None, preset_name=None, preset_count=None):
        """Create a saved list from a free-form prompt or a supplied preset."""
        prompt = preset_prompt
        if prompt is None:
            prompt = xbmcgui.Dialog().input("What are you in the mood for?")
        if not prompt or not str(prompt).strip():
            return None
        prompt = str(prompt).strip()

        method_choice = xbmcgui.Dialog().select(
            "How should curatr build this list?",
            [
                "Create with AI: best for nuanced requests",
                "Create with Keyword Matching: no AI request",
            ],
        )
        if method_choice < 0:
            return None
        generation_method = "keyword" if method_choice == 1 else "ai"
        type_choice = xbmcgui.Dialog().select(
            "What should this list contain?",
            ["Movies only", "TV Shows only", "Movies & TV Shows"],
        )
        if type_choice < 0:
            return None
        content_type = ("movies", "shows", "both")[type_choice]
        keyword_rules = None
        if generation_method == "ai":
            self._require_ai()
        else:
            self._require_keyword_catalogue()
            keyword_rules = parse_prompt(prompt)
            if content_type == "shows" and (
                keyword_rules.get("people") or keyword_rules.get("reference_movies") or keyword_rules.get("collection_query")
            ):
                xbmcgui.Dialog().ok(
                    self.name,
                    "Keyword Matching cannot reliably match TV shows from named people, collections or references yet. "
                    "Choose AI for this request, or use filters such as genre, year, rating, country or language.",
                )
                return None
            if not keyword_rules.get("confidence"):
                xbmcgui.Dialog().ok(
                    self.name,
                    "Keyword Matching could not find a clear filter in that request.\n\n"
                    "Try details such as genre, decade, rating, runtime, country, language, actor or director. "
                    "For more nuanced requests, choose Create with AI.",
                )
                return None

        default_name = preset_name or "My Picks"
        name = xbmcgui.Dialog().input("Name this list", defaultt=default_name)
        if not name or not str(name).strip():
            return None
        name = str(name).strip()
        managed = self._managed_record_by_name(name)

        description = xbmcgui.Dialog().input(
            "List description (optional)",
            defaultt=str((managed or {}).get("description") or ""),
        )
        description = str(description or "").strip()

        default_count = max(5, min(50, self._safe_int(preset_count, 20)))
        count_text = xbmcgui.Dialog().numeric(0, "How many items? (5-50)", defaultt=str(default_count))
        if not count_text or not str(count_text).strip():
            return None
        try:
            count = max(5, min(50, int(count_text)))
        except (TypeError, ValueError):
            xbmcgui.Dialog().ok(self.name, "Enter a number between 5 and 50.")
            return None

        if generation_method == "keyword":
            footer = "Create '%s' with %d items" % (name, count)
            while True:
                decision = confirm_keyword_rules(
                    xbmcvfs.translatePath(self.addon.getAddonInfo("path")),
                    prompt, keyword_rules, footer,
                )
                if decision == "edit":
                    while True:
                        edited = xbmcgui.Dialog().input("What are you in the mood for?", defaultt=prompt)
                        if not edited or not edited.strip():
                            return None
                        revised = parse_prompt(edited.strip())
                        if revised.get("confidence"):
                            prompt, keyword_rules = edited.strip(), revised
                            break
                        xbmcgui.Dialog().ok(
                            self.name,
                            "curatr couldn't turn enough of that request into reliable filters. "
                            "Try adding a genre, year, actor, director or reference film.",
                        )
                    continue
                if decision != "create":
                    return None
                break
        else:
            summary = "Create '%s' with %d items?" % (name, count)
            if description:
                summary += "\n\nDescription: %s" % self._shorten_text(description, 160)
            summary += "\n\nOne AI recommendation request will be made when you choose Create List."
            try:
                confirmed = xbmcgui.Dialog().yesno(
                    self.name, summary, nolabel="Cancel", yeslabel="Create List",
                )
            except TypeError:
                confirmed = xbmcgui.Dialog().yesno(self.name, summary)
            if not confirmed:
                return None

        self._notify("Finding %d items for %s…" % (count, name))
        if generation_method == "keyword":
            result = self._generate_keyword_and_write(
                name, prompt, count, keyword_rules, managed_record=managed,
                description=description, content_type=content_type,
            )
        else:
            result = self._generate_and_write(
                name, prompt, count, managed_record=managed, description=description,
                content_type=content_type,
            )
        return result

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
        managed = self._managed_record_by_name(name)
        self._notify("Finding fresh picks for %s…" % label)
        result = self._generate_and_write(name, prompt, count, managed_record=managed)
        self.record_activity("Quick Pick refreshed: %s" % label, notify=False)
        return result

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

    def _edit_list_prompt(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        is_keyword = str(record.get("generation_method") or "ai").lower() == "keyword"
        noun = "request" if is_keyword else "prompt"
        current_prompt = str(record.get("prompt") or "Recommend something for me.")
        new_prompt = current_prompt
        while True:
            new_prompt = xbmcgui.Dialog().input("Edit %s" % noun.title(), defaultt=new_prompt)
            if not new_prompt or not new_prompt.strip():
                return record
            new_prompt = new_prompt.strip()
            if not is_keyword:
                break
            rules = parse_prompt(new_prompt)
            if not rules.get("confidence"):
                xbmcgui.Dialog().ok(
                    self.name,
                    "That request does not contain a clear Keyword Matching filter. The existing request was kept.",
                )
                return record
            addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
            decision = confirm_keyword_rules(
                addon_path, new_prompt, rules,
                footer="Save Changes updates this list and refreshes its items.",
                edit_existing=True,
            )
            if decision == "edit":
                continue
            if decision != "create":
                return record
            break
        updated = dict(record)
        updated["prompt"] = new_prompt
        if is_keyword:
            updated["keyword_rules"] = rules
        updated["edited_at"] = int(time.time())
        self._store_managed_record(updated, record)
        self._save_state()
        self.record_activity(
            "Saved %s for %s" % (noun, updated.get("name") or "curatr list"), notify=not is_keyword,
        )
        return self.refresh_list(self._record_key(updated), silent=False) if is_keyword else updated

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

    def _bundled_art_source(self, key, kind, style):
        addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        if kind == "icon":
            folder = "icons_colour_v3" if style == "genre_colours" else "icons_v2"
        else:
            folder = "fanart_mono_v2" if style == "monochrome" else "fanart_v2"
        extension = ".png" if kind == "icon" else ".jpg"
        return os.path.join(addon_path, "resources", "media", "list_art", folder, key + extension)

    def _choose_bundled_art(self, heading, kind):
        if kind == "icon":
            style_choice = xbmcgui.Dialog().select("Icon style", ["White", "Genre Colours"])
            if style_choice < 0:
                return "", ""
            style = "genre_colours" if style_choice == 1 else "white"
            layout = "icon"
        else:
            style_choice = xbmcgui.Dialog().select("Fanart style", ["Genre Colours", "Monochrome"])
            if style_choice < 0:
                return "", ""
            style = "monochrome" if style_choice == 1 else "colour"
            layout = "fanart"
        entries = [
            {
                "key": key,
                "label": label,
                "source": self._bundled_art_source(key, kind, style),
            }
            for key, label in LIST_ART_CHOICES
        ]
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
            "Use the default curatr icon",
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
                    "icon_mode": "custom", "icon_key": "",
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
                "icon_mode": "custom", "icon_source": source,
                "icon_key": "", "icon_label": label,
            })
        elif choice == 4:
            path = xbmcgui.Dialog().browseSingle(2, "Choose a square icon", "files", ".png|.jpg|.jpeg|.webp")
            if not path:
                return record
            art.update({"icon_mode": "custom", "icon_source": str(path), "icon_key": "", "icon_label": "Custom"})
        else:
            art.update({"icon_mode": "default", "icon_key": "", "icon_source": "", "icon_label": ""})
        updated = self._store_list_artwork(record, art)
        self.record_activity("Updated the icon for %s" % (updated.get("name") or "curatr list"), notify=True)
        return updated

    def _fanart_from_list_item(self, record):
        return self._fanart_from_movies(record.get("movies"), "Choose fanart from this list")

    def _fanart_from_movies(self, movies, heading="Choose fanart from contents"):
        movies = [row for row in (movies or []) if isinstance(row, dict)]
        choices = []
        for movie in movies:
            url = ArtworkCache._first_image(movie, "fanart")
            if url:
                choices.append((movie, url))
        if not choices:
            xbmcgui.Dialog().ok(self.name, "No landscape artwork is available from these contents.")
            return "", ""
        choices = choices[:24]
        preview_paths = ArtworkCache(self.addon, workers=6).cache_urls([source for _movie, source in choices], limit=24)
        entries = []
        for movie, source in choices:
            label = "%s%s" % (
                movie.get("title") or "Item artwork",
                " (%s)" % movie.get("year") if movie.get("year") else "",
            )
            entries.append({"label": label, "source": source, "preview_source": preview_paths.get(source) or source})
        selected = choose_artwork(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")),
            heading, entries, "fanart",
        )
        return (selected["source"], selected["label"]) if selected else ("", "")

    def _fanart_from_external_path(self, path):
        path = self._valid_external_plugin_path(path)
        if not path:
            return "", ""
        try:
            directory = self._kodi_json_rpc("Files.GetDirectory", {
                "directory": path, "media": "video",
                "properties": ["title", "year", "thumbnail", "fanart"],
                "limits": {"start": 0, "end": 60},
            })
        except Exception as exc:
            xbmcgui.Dialog().ok(self.name, "This add-on did not make its artwork available to Kodi.\n\n%s" % exc)
            return "", ""
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
            entries.append({"label": label, "source": source})
            if len(entries) >= 30:
                break
        if not entries:
            xbmcgui.Dialog().ok(self.name, "This add-on did not provide any landscape artwork for this path.")
            return "", ""
        previews = ArtworkCache(self.addon, workers=6).cache_urls(
            [entry["source"] for entry in entries if entry["source"].startswith(("http://", "https://", "//"))],
            limit=30,
        )
        for entry in entries:
            entry["preview_source"] = previews.get(entry["source"]) or entry["source"]
        selected = choose_artwork(
            xbmcvfs.translatePath(self.addon.getAddonInfo("path")),
            "Choose fanart from contents", entries, "fanart",
        )
        return (selected["source"], selected["label"]) if selected else ("", "")

    def _fanart_from_person(self):
        if not self.tmdb or not self.tmdb.api_key:
            xbmcgui.Dialog().ok(self.name, "Person artwork needs TMDB to be enabled with an API key under Metadata.")
            return "", ""
        query = xbmcgui.Dialog().input("Search for a director or actor")
        if not query or not query.strip():
            return "", ""
        people = self.tmdb.search_people(query.strip(), limit=20)
        people = [row for row in people if row.get("profile_path")]
        if not people:
            xbmcgui.Dialog().ok(self.name, "No suitable person artwork was found on TMDB.")
            return "", ""
        entries = []
        for person in people:
            known = [str(row.get("title") or row.get("name") or "") for row in person.get("known_for", []) if isinstance(row, dict)]
            source = self.tmdb.image_url(person.get("profile_path"), "h632")
            label = str(person.get("name") or "Person artwork")
            entries.append({
                "label": label,
                "subtitle": ", ".join([value for value in known if value][:2]),
                "source": source,
            })
        preview_paths = ArtworkCache(self.addon, workers=6).cache_urls(
            [entry.get("source") for entry in entries], limit=20,
        )
        for entry in entries:
            entry["preview_source"] = preview_paths.get(entry.get("source")) or entry.get("source")
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
            elif icon_mode == "custom" and art.get("icon_source"):
                art.update({
                    "fanart_mode": "custom", "fanart_key": "",
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

    def _change_list_fanart_style(self, list_id):
        record = self._managed_record_by_id(list_id)
        if not record:
            raise RuntimeError("That list has already been removed.")
        art = normalise_list_art(record.get("artwork"))
        if art.get("fanart_mode") not in ("auto", "bundled"):
            return record
        choice = xbmcgui.Dialog().select("Fanart style", ["Genre colours", "Monochrome"])
        if choice < 0:
            return record
        art["fanart_style"] = "monochrome" if choice == 1 else "colour"
        return self._store_list_artwork(record, art)

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
            art.update({"icon_mode": "bundled", "icon_key": selected["icon_key"], "icon_source": "", "icon_label": "", "icon_style": "white"})
        updated = self._store_list_artwork(record, art)
        self.record_activity("Applied suggested artwork to %s" % (updated.get("name") or "curatr list"), notify=True)
        return updated

    def list_artwork_interactive(self, list_id):
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
        """Edit one property at a time using plain-language labels."""
        while True:
            record = self._managed_record_by_id(list_id)
            if not record:
                raise RuntimeError("That list has already been removed.")

            regen_enabled = bool(record.get("regeneration_enabled"))
            regen_interval = self._safe_int(record.get("regeneration_interval_hours"), self._default_regeneration_interval())
            trakt_refresh_enabled = bool(record.get("trakt_refresh_enabled"))
            trakt_interval = self._safe_int(record.get("trakt_refresh_interval_hours"), self._default_trakt_refresh_interval())
            is_keyword = str(record.get("generation_method") or "ai").lower() == "keyword"
            method_label = "Keyword Matching" if is_keyword else "AI"
            content_type = str(record.get("content_type") or "movies")
            content_label = {"movies": "Movies only", "shows": "TV Shows only", "both": "Movies & TV Shows"}.get(content_type, "Movies only")
            request_label = "Request" if is_keyword else "Prompt"
            refresh_schedule = self._format_interval(regen_interval) if regen_enabled else "Never"
            sync_schedule = self._format_interval(trakt_interval) if record.get("sync_to_trakt") and trakt_refresh_enabled else "Never"

            choices = [
                "List name: %s" % (record.get("name") or "curatr list"),
                "Description: %s" % self._shorten_text(record.get("description") or "Not set", 70),
                "Edit %s: %s" % (request_label, self._shorten_text(record.get("prompt") or "", 70)),
                "Number of items: %d" % self._safe_int(record.get("count"), 20),
                "Creation method: %s" % method_label,
                "Content: %s" % content_label,
                "Refresh This List",
                "Auto Refresh: %s" % refresh_schedule,
                "Sync to Trakt",
                "Auto Sync: %s" % sync_schedule,
                "Save this %s as a template" % request_label.lower(),
                "View list details",
            ]
            choice = xbmcgui.Dialog().select("List settings: %s" % (record.get("name") or "curatr list"), choices)
            if choice < 0:
                return record
            key = self._record_key(record)
            if choice == 0:
                self._edit_list_name(key)
            elif choice == 1:
                self._edit_list_description(key)
            elif choice == 2:
                self._edit_list_prompt(key)
            elif choice == 3:
                self._edit_list_count(key)
            elif choice == 4:
                self._edit_list_generation_method(key)
            elif choice == 5:
                self._edit_list_content_type(key)
            elif choice == 6:
                self.refresh_list(key, silent=False)
            elif choice == 7:
                self._edit_list_refresh_schedule(key)
            elif choice == 8:
                synced = self.sync_list_to_trakt_interactive(key)
                if synced:
                    current = self._managed_record_by_id(key)
                    if current and not current.get("sync_to_trakt"):
                        self.set_list_trakt_sync(key, True)
            elif choice == 9:
                self._edit_list_sync_schedule(key)
            elif choice == 10:
                self.save_list_prompt_as_template(key)
            elif choice == 11:
                self._view_list_settings(key)

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
        backup_path = self.state_path + ".bak"
        if not xbmcvfs.exists(backup_path):
            return None
        try:
            backup = json.loads(self._read_text(backup_path) or "{}")
        except Exception:
            return None
        recovered = next((
            dict(row) for row in (backup.get("widget_folders") or [])
            if isinstance(row, dict) and str(row.get("id") or "") == wanted
        ), None) if isinstance(backup, dict) else None
        if not recovered:
            return None
        self.state["widget_folders"] = self.widget_folders() + [recovered]
        self._dirty_widget_folder_ids.add(wanted)
        self._save_state()
        xbmc.log("curatr restored a missing widget folder from its safety backup", xbmc.LOGWARNING)
        return recovered

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

    def _folder_entry_label(self, entry):
        if not isinstance(entry, dict):
            return "Unknown item"
        if entry.get("type") == "curatr_list":
            record = self._managed_record_by_id(entry.get("list_id"))
            return str((record or {}).get("name") or "Missing curatr list")
        if entry.get("type") == "provider_list":
            return str(entry.get("name") or ("Trakt list" if entry.get("provider") == "trakt" else "MDBList list"))
        return str(entry.get("name") or "External Shortcut")

    def _edit_compact_artwork(self, heading, value, content_fanart=None):
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
                    "Choose a custom image", "Use the default curatr icon",
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
                        art.update({"icon_mode": "custom", "icon_key": "", "icon_source": art.get("fanart_source"), "icon_label": art.get("fanart_label") or "Custom"})
                    elif mode == "default":
                        art.update({"icon_mode": "default", "icon_key": "", "icon_source": "", "icon_label": ""})
                elif selected == 3:
                    source = xbmcgui.Dialog().browseSingle(2, "Choose a square icon", "files", ".png|.jpg|.jpeg|.webp")
                    if source:
                        art.update({"icon_mode": "custom", "icon_key": "", "icon_source": str(source), "icon_label": "Custom"})
                elif selected == 4:
                    art.update({"icon_mode": "default", "icon_key": "", "icon_source": "", "icon_label": ""})
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
                elif mode == "custom" and art.get("icon_source"):
                    art.update({"fanart_mode": "custom", "fanart_key": "", "fanart_source": art.get("icon_source"), "fanart_label": art.get("icon_label") or "Custom"})
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

    def create_widget_folder_interactive(self):
        name = xbmcgui.Dialog().input("Folder name")
        if not name or not str(name).strip():
            return None
        name = str(name).strip()
        if any(self._normalised_restore_name(row.get("name")) == self._normalised_restore_name(name) for row in self.widget_folders()):
            xbmcgui.Dialog().ok(self.name, "A folder already uses that name.")
            return None
        description = xbmcgui.Dialog().input("Folder description (optional)")
        now = int(time.time())
        folder = {
            "id": uuid.uuid4().hex,
            "name": name,
            "description": str(description or "").strip(),
            "artwork": normalise_list_art({}),
            "entries": [],
            "created_at": now,
            "updated_at": now,
        }
        self.state["widget_folders"] = self.widget_folders() + [folder]
        self._dirty_widget_folder_ids.add(folder["id"])
        self._save_state()
        if xbmcgui.Dialog().yesno(self.name, "Folder created. Customise its artwork now?"):
            folder["artwork"] = self._edit_compact_artwork("Folder artwork", folder.get("artwork"))
            folder["updated_at"] = int(time.time())
            self._store_widget_folder(folder, folder)
        self.record_activity("Created folder: %s" % folder["name"], notify=True)
        return folder

    def add_list_to_widget_folder_interactive(self, list_id="", folder_id=""):
        record = self._managed_record_by_id(list_id) if list_id else None
        folders = self.widget_folders()
        if not folders:
            if not xbmcgui.Dialog().yesno(self.name, "Create a folder first?"):
                return None
            created = self.create_widget_folder_interactive()
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
        choice = xbmcgui.Dialog().select("Add to curatr List", [str(row.get("name") or "curatr list") for row in compatible])
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

    def add_provider_list_to_widget_folder_interactive(self, folder_id, provider):
        """Add a lightweight account-list reference without copying its contents."""
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
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
            return folder
        labels = [
            "%s%s" % (
                row.get("name") or (service_name + " list"),
                " (%s items)" % row.get("items") if row.get("items") not in (None, "") else "",
            ) for row in choices
        ]
        selected = xbmcgui.Dialog().select("Add from %s" % service_name, labels)
        if selected < 0:
            return folder
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
            artwork = self._edit_compact_artwork("%s artwork" % name, artwork)
        entry = {
            "id": uuid.uuid4().hex, "type": "provider_list",
            "provider": provider, "provider_list_id": str(chosen.get("id")),
            "name": name, "description": description, "artwork": artwork,
            "item_count": self._safe_int(chosen.get("items"), 0),
        }
        updated = dict(folder)
        updated["entries"] = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)] + [entry]
        updated["updated_at"] = int(time.time())
        updated = self._store_widget_folder(updated, folder)
        try:
            self.linked_provider_list_movies(updated.get("id"), entry.get("id"), force=True)
        except Exception as exc:
            self.record_activity(
                "%s was added, but its contents could not be loaded yet" % name,
                level="warning", detail=str(exc), notify=True,
            )
        self.record_activity("Added %s %s to %s" % (service_name, name, updated.get("name")), notify=True)
        return updated

    @staticmethod
    def _provider_cache_key(provider, provider_list_id):
        return "%s:%s" % (str(provider or "").strip().lower(), str(provider_list_id or "").strip())

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
            movies = [row for row in movies if isinstance(row, dict) and row.get("title")][:250]
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

    def add_external_path_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        method = xbmcgui.Dialog().select("Add an external shortcut", [
            "Browse installed video add-ons", "Enter a plugin path manually",
        ])
        if method < 0:
            return folder
        suggested_name = ""
        suggested_thumbnail = ""
        if method == 0:
            selected = self._browse_external_plugin_path()
            if not selected:
                return folder
            path, suggested_name, suggested_thumbnail = selected
        else:
            path = self._valid_external_plugin_path(xbmcgui.Dialog().input("External plugin path"))
        if not path:
            xbmcgui.Dialog().ok(self.name, "Enter a complete path beginning with plugin://")
            return folder
        existing_paths = {
            str(row.get("path") or "") for row in folder.get("entries", [])
            if isinstance(row, dict) and row.get("type") == "external_path"
        }
        if path in existing_paths:
            xbmcgui.Dialog().ok(self.name, "That page is already in this folder.")
            return folder
        name = xbmcgui.Dialog().input("Shortcut name", defaultt=suggested_name)
        if not name or not str(name).strip():
            return folder
        description = xbmcgui.Dialog().input("Shortcut description (optional)")
        artwork = normalise_list_art({"icon_mode": "default", "fanart_mode": "default"})
        if suggested_thumbnail.startswith(("special://", "/", "image://", "http://", "https://")):
            artwork.update({"icon_mode": "custom", "icon_source": suggested_thumbnail, "icon_label": str(name).strip()})
        if xbmcgui.Dialog().yesno(self.name, "Customise this shortcut's artwork now?"):
            artwork = self._edit_compact_artwork("Shortcut artwork", artwork)
        entry = {
            "id": uuid.uuid4().hex, "type": "external_path",
            "name": str(name).strip(), "description": str(description or "").strip(),
            "path": path, "artwork": artwork,
        }
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

    def import_kodi_favourite_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
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
            return folder
        choice = xbmcgui.Dialog().select("Import from Kodi Favourites", [row[0] for row in favourites])
        if choice < 0:
            return folder
        name, path, thumbnail = favourites[choice]
        artwork = normalise_list_art({"icon_mode": "default", "fanart_mode": "default"})
        if thumbnail.startswith(("special://", "/")) or thumbnail.startswith("image://"):
            artwork.update({"icon_mode": "custom", "icon_source": thumbnail, "icon_label": "Custom"})
        entry = {
            "id": uuid.uuid4().hex, "type": "external_path", "name": name,
            "description": "Imported from Kodi Favourites.", "path": path, "artwork": artwork,
        }
        updated = dict(folder)
        updated["entries"] = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)] + [entry]
        updated["updated_at"] = int(time.time())
        self._store_widget_folder(updated, folder)
        if xbmcgui.Dialog().yesno(self.name, "Favourite imported. Customise its artwork now?"):
            return self.edit_widget_folder_entry_interactive(folder_id, entry["id"])
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

    def _entry_content_fanart(self, folder, entry):
        entry_type = str(entry.get("type") or "")
        if entry_type == "provider_list":
            return lambda: self._fanart_from_movies(
                self.linked_provider_list_movies(folder.get("id"), entry.get("id"))[1],
                "Choose fanart from this list",
            )
        if entry_type == "external_path":
            return lambda: self._fanart_from_external_path(entry.get("path"))
        return None

    def edit_widget_folder_entry_artwork_interactive(self, folder_id, entry_id):
        folder, entry = self._widget_folder_entry(folder_id, entry_id)
        if entry.get("type") == "curatr_list":
            return self.list_artwork_interactive(entry.get("list_id"))
        updated_entry = dict(entry)
        updated_entry["artwork"] = self._edit_compact_artwork(
            "%s artwork" % self._folder_entry_label(entry), entry.get("artwork"),
            content_fanart=self._entry_content_fanart(folder, entry),
        )
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
        updated = dict(folder)
        updated["artwork"] = self._edit_compact_artwork("Folder artwork", folder.get("artwork"))
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

    def edit_widget_folder_entry_interactive(self, folder_id, entry_id):
        folder, entry = self._widget_folder_entry(folder_id, entry_id)
        while True:
            actions = ["Move up", "Move down"]
            if entry.get("type") == "external_path":
                actions = ["Name", "Description", "Plugin path"] + actions
            elif entry.get("type") == "provider_list":
                actions = ["Name", "Description", "Refresh cached items"] + actions
            choice = xbmcgui.Dialog().select(self._folder_entry_label(entry), actions)
            if choice < 0:
                return folder
            entries = [dict(row) for row in folder.get("entries", []) if isinstance(row, dict)]
            index = next((i for i, row in enumerate(entries) if str(row.get("id")) == str(entry_id)), -1)
            if index < 0:
                return folder
            if entry.get("type") == "external_path" and choice < 3:
                updated_entry = dict(entry)
                if choice == 0:
                    value = xbmcgui.Dialog().input("Shortcut name", defaultt=str(entry.get("name") or ""))
                    if value and value.strip(): updated_entry["name"] = value.strip()
                elif choice == 1:
                    value = xbmcgui.Dialog().input("Shortcut description", defaultt=str(entry.get("description") or ""))
                    updated_entry["description"] = str(value or "").strip()
                elif choice == 2:
                    value = self._valid_external_plugin_path(xbmcgui.Dialog().input("Plugin path", defaultt=str(entry.get("path") or "")))
                    if value: updated_entry["path"] = value
                    else: xbmcgui.Dialog().ok(self.name, "Enter a complete path beginning with plugin://")
                entries[index] = updated_entry
                entry = updated_entry
            elif entry.get("type") == "provider_list" and choice < 3:
                updated_entry = dict(entry)
                if choice == 0:
                    value = xbmcgui.Dialog().input("List name", defaultt=str(entry.get("name") or ""))
                    if value and value.strip():
                        updated_entry["name"] = value.strip()
                elif choice == 1:
                    value = xbmcgui.Dialog().input("List description", defaultt=str(entry.get("description") or ""))
                    updated_entry["description"] = str(value or "").strip()
                else:
                    cache_key = self._provider_cache_key(entry.get("provider"), entry.get("provider_list_id"))
                    cache = dict(self.state.get("linked_list_cache") or {})
                    cache.pop(cache_key, None)
                    self.state["linked_list_cache"] = cache
                    self._save_state()
                    xbmcgui.Dialog().notification(self.name, "Linked list will refresh when next opened", xbmcgui.NOTIFICATION_INFO, 3000)
                entries[index] = updated_entry
                entry = updated_entry
            else:
                offset = 3 if entry.get("type") in ("external_path", "provider_list") else 0
                operation = choice - offset
                if operation == 0 and index > 0:
                    entries[index - 1], entries[index] = entries[index], entries[index - 1]
                elif operation == 1 and index < len(entries) - 1:
                    entries[index + 1], entries[index] = entries[index], entries[index + 1]
            updated = dict(folder); updated["entries"] = entries; updated["updated_at"] = int(time.time())
            folder = self._store_widget_folder(updated, folder)

    def manage_widget_folder_interactive(self, folder_id):
        folder = self.widget_folder_by_id(folder_id)
        if not folder:
            raise RuntimeError("That folder no longer exists.")
        while True:
            choices = [
                "Add a curatr list", "Add from Trakt", "Add from MDBList", "Add an external shortcut",
                "Import from Kodi Favourites", "Manage folder items",
                "Folder Name", "Description",
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
                entries = [row for row in folder.get("entries", []) if isinstance(row, dict)]
                if not entries:
                    xbmcgui.Dialog().ok(self.name, "This folder is empty.")
                    continue
                selected = xbmcgui.Dialog().select("Manage folder items", [self._folder_entry_label(row) for row in entries])
                if selected >= 0:
                    folder = self.edit_widget_folder_entry_interactive(folder_id, entries[selected].get("id")) or folder
            elif choice == 6:
                value = xbmcgui.Dialog().input("Folder name", defaultt=str(folder.get("name") or ""))
                if value and value.strip():
                    name = value.strip()
                    duplicate = any(
                        str(row.get("id") or "") != str(folder_id)
                        and self._normalised_restore_name(row.get("name")) == self._normalised_restore_name(name)
                        for row in self.widget_folders()
                    )
                    if duplicate:
                        xbmcgui.Dialog().ok(self.name, "A folder already uses that name.")
                    else:
                        updated = dict(folder); updated["name"] = name; updated["updated_at"] = int(time.time())
                        folder = self._store_widget_folder(updated, folder)
            elif choice == 7:
                value = xbmcgui.Dialog().input("Folder description", defaultt=str(folder.get("description") or ""))
                updated = dict(folder); updated["description"] = str(value or "").strip(); updated["updated_at"] = int(time.time())
                folder = self._store_widget_folder(updated, folder)

    def manage_widget_folders_interactive(self):
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
        choice = xbmcgui.Dialog().select("Backup & Restore", ["Create backup", "Restore from backup"])
        if choice == 0: return self.export_backup()
        if choice == 1: return self.import_backup()
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
        """Choose the global menu background from a visual, skin-neutral grid."""
        addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo("path"))
        media = os.path.join(addon_path, "resources", "media")
        entries = [
            {"key": "0", "label": "Clean", "source": os.path.join(media, "fanart_menu_clean_v4.jpg")},
            {"key": "1", "label": "Deep Blue", "source": os.path.join(media, "background_1_v4.jpg")},
            {"key": "2", "label": "Purple Glow", "source": os.path.join(media, "background_2_v4.jpg")},
            {"key": "3", "label": "Midnight Waves", "source": os.path.join(media, "background_3_v4.jpg")},
        ]
        current = str(
            self.state.get("menu_background_style")
            or self.addon.getSetting("menu_background_style")
            or "0"
        )
        for entry in entries:
            if entry["key"] == current:
                entry["subtitle"] = "Selected"
        selected = choose_artwork(addon_path, "Choose Menu Background", entries, "fanart")
        if not selected:
            return current
        value = str(selected.get("key") or "0")
        self.state["menu_background_style"] = value
        self._save_state()
        self.record_activity("Changed menu background to %s" % selected.get("label"), notify=True)
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
                    "Use list as AI reference",
                    "Add to Folder",
                    "Artwork",
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
                self._view_list_settings(key)
            elif choice == 7:
                if self.delete_list_interactive(key):
                    return None

    def manage_lists_interactive(self):
        records = [row for row in self.state.get("ai_lists", []) if isinstance(row, dict)]
        records.sort(key=lambda row: self._safe_int(row.get("updated_at"), 0), reverse=True)
        if not records:
            self._notify("You do not have any saved lists yet")
            return None
        labels = []
        for row in records:
            method = "Keywords" if str(row.get("generation_method") or "ai").lower() == "keyword" else "AI"
            regen = "%s refresh off" % method
            if row.get("regeneration_enabled"):
                regen = "%s every %dh" % (method, self._safe_int(row.get("regeneration_interval_hours"), 24))
            if row.get("sync_to_trakt"):
                if row.get("trakt_refresh_enabled"):
                    trakt = "Trakt every %dh" % self._safe_int(row.get("trakt_refresh_interval_hours"), 24)
                else:
                    trakt = "Trakt manual"
            else:
                trakt = "Kodi only"
            labels.append(
                "%s: %d items: %s: %s"
                % (row.get("name") or "curatr list", self._safe_int(row.get("count"), 20), regen, trakt)
            )
        choice = xbmcgui.Dialog().select("My Lists", labels)
        if choice < 0:
            return None
        return self.manage_list_interactive(self._record_key(records[choice]))

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
        description=None, content_type="movies", persist=True,
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
        if "sync_to_trakt" not in record:
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
        description=None, reference_movies=None, content_type="movies", persist=True,
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
        if "sync_to_trakt" not in record:
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
                self._loc(32410, "My Lists"),
                self._loc(32411, "Explore"),
                self._loc(32412, "Preferences & Activity"),
                self._loc(32413, "Settings"),
            ])
            if choice < 0:
                return
            if choice == 0:
                sub = xbmcgui.Dialog().select(self._loc(32410, "My Lists"), [
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
