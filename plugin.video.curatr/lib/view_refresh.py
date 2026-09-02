"""Keep Kodi directory views and skin widgets in step with local list changes."""

import hashlib
import json

import xbmc


def list_signature(state):
    linked_cache = state.get("linked_list_cache", {}) if isinstance(state, dict) else {}
    linked_summary = {
        str(key): {
            "cached_at": (value or {}).get("cached_at", 0),
            "count": len((value or {}).get("movies", [])) if isinstance((value or {}).get("movies"), list) else 0,
        }
        for key, value in linked_cache.items()
        if isinstance(value, dict)
    } if isinstance(linked_cache, dict) else {}
    records = {
        "ai_lists": state.get("ai_lists", []),
        "hidden_movies": state.get("hidden_movies", []),
        "widget_folders": state.get("widget_folders", []),
        "linked_list_cache": linked_summary,
    } if isinstance(state, dict) else {}
    try:
        payload = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(records)
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def refresh_if_changed(before, state, reload_skin=True):
    """Refresh changed views once without racing a container against the skin."""
    if before == list_signature(state):
        return False
    in_curatr = False
    try:
        plugin_name = str(xbmc.getInfoLabel("Container.PluginName") or "").strip()
        folder_path = str(xbmc.getInfoLabel("Container.FolderPath") or "").strip()
        in_curatr = plugin_name == "plugin.video.curatr" or folder_path.startswith("plugin://plugin.video.curatr")
    except Exception:
        pass
    if in_curatr or not reload_skin:
        try:
            xbmc.executebuiltin("Container.Refresh")
        except Exception:
            pass
        return True
    try:
        if xbmc.getCondVisibility("System.HasModalDialog"):
            return True
    except Exception:
        # Older/unusual bindings may not expose the condition helper. The
        # builtin itself remains protected below.
        pass
    try:
        xbmc.executebuiltin("ReloadSkin()")
    except Exception:
        pass
    return True
