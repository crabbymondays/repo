import time

import xbmc
import xbmcaddon

from lib.core import Curator
from lib.view_refresh import list_signature, refresh_if_changed


addon = xbmcaddon.Addon()
monitor = xbmc.Monitor()

# Keep the read-only Trakt status reasonably fresh without spending two API
# requests on every Kodi restart. A successful OAuth status check is cached for
# 24 hours; public/local-first mode needs no startup Trakt request at all.
try:
    startup_state = Curator(addon, update_status=False, init_clients=False)
    if startup_state._has_oauth():
        checked_at = startup_state._safe_int(
            startup_state.state.get("trakt_status_checked_at"), 0
        )
        if not checked_at or time.time() - checked_at >= 24 * 3600:
            Curator(addon, update_status=False).refresh_trakt_status(silent=True)
except Exception as exc:
    xbmc.log("curatr status refresh error: %s" % exc, xbmc.LOGWARNING)

# Check cheaply every five minutes. A fresh Curator picks up settings/state
# changes made by the UI, but update_status=False avoids rewriting Kodi setting
# rows on every idle poll. No AI/Trakt network work is done unless a list is due.
while not monitor.abortRequested():
    try:
        scheduler = Curator(addon, update_status=False, init_clients=False)
        if scheduler.auto_update_due():
            worker = Curator(addon, update_status=False)
            before = list_signature(worker.state)
            worker.run_auto_update()
            # Never reload a user's skin from the background service. Kodi and
            # the skin can update widgets on their normal cycle; interactive
            # list changes still request an immediate guarded reload.
            refresh_if_changed(before, worker.state, reload_skin=False)
    except Exception as exc:
        xbmc.log("curatr service error: %s" % exc, xbmc.LOGERROR)
        try:
            Curator(addon).report_error(
                "Background list schedule failed", detail=str(exc), background=True
            )
        except Exception:
            pass
    if monitor.waitForAbort(300):
        break
