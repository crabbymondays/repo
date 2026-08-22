"""Keep Kodi directory views and skin widgets in step with local list changes."""

import hashlib
import json

import xbmc


def list_signature(state):
    records = {
        "ai_lists": state.get("ai_lists", []),
        "hidden_movies": state.get("hidden_movies", []),
        "widget_folders": state.get("widget_folders", []),
    } if isinstance(state, dict) else {}
    try:
        payload = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(records)
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def refresh_if_changed(before, state, reload_skin=True):
    """Refresh changed views, with a guarded full reload for interactive edits."""
    if before == list_signature(state):
        return False
    try:
        xbmc.executebuiltin("Container.Refresh")
    except Exception:
        pass
    if not reload_skin:
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
