"""Keep Kodi directory views and skin widgets in step with local list changes."""

import hashlib
import json

import xbmc


def list_signature(state):
    records = state.get("ai_lists", []) if isinstance(state, dict) else []
    try:
        payload = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(records)
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def refresh_if_changed(before, state):
    """Refresh the active view and force home widgets to query curatr again."""
    if before == list_signature(state):
        return False
    try:
        xbmc.executebuiltin("Container.Refresh")
    except Exception:
        pass
    try:
        xbmc.executebuiltin("ReloadSkin()")
    except Exception:
        pass
    return True
