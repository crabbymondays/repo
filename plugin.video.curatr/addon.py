import sys

import xbmc
import xbmcaddon
import xbmcgui

from lib.core import Curator
from lib.splash import show_splash
from lib.view_refresh import list_signature, refresh_if_changed


def main():
    addon = xbmcaddon.Addon()
    action = sys.argv[1] if len(sys.argv) > 1 else "menu"
    argument = sys.argv[2] if len(sys.argv) > 2 else ""
    curator = None
    try:
        curator = Curator(addon)
        before = list_signature(curator.state)
        if action == "auth":
            curator.authenticate_trakt()
        elif action == "create":
            curator.create_list_interactive()
        elif action == "quick":
            curator.quick_pick_interactive()
        elif action == "templates":
            curator.prompt_templates_interactive()
        elif action == "hidden":
            curator.manage_hidden_interactive()
        elif action == "backup":
            curator.backup_menu_interactive()
        elif action == "update":
            curator.update_all()
        elif action == "manage":
            curator.manage_lists_interactive()
        elif action == "refresh_list":
            curator.refresh_list(argument)
        elif action == "edit_list":
            curator.edit_list_interactive(argument)
        elif action == "delete_list":
            curator.delete_list_interactive(argument)
        elif action == "sync":
            curator.sync_profile()
        elif action == "settings":
            curator.open_settings()
        elif action == "status":
            curator.refresh_trakt_status(silent=False)
        elif action == "test_tmdb":
            curator.test_tmdb_interactive()
        elif action == "test_mdblist":
            curator.test_mdblist_interactive()
        elif action == "choose_mdblist_lists":
            curator.choose_mdblist_lists_interactive()
        elif action == "taste":
            curator.view_taste_fingerprint()
        elif action == "usage":
            curator.show_ai_usage()
        elif action == "activity":
            curator.show_activity()
        else:
            show_splash(addon.getAddonInfo("path"), duration_ms=1600)
            curator.maybe_show_first_run()
            curator.menu()
        refresh_if_changed(before, curator.state)
    except Exception as exc:
        xbmc.log("curatr error: %s" % exc, xbmc.LOGERROR)
        if curator is not None:
            try:
                curator.report_error("Addon action failed", detail=str(exc))
            except Exception:
                pass
        prefix = addon.getLocalizedString(32490) or "Error"
        xbmcgui.Dialog().ok(addon.getAddonInfo("name"), "%s: %s" % (prefix, exc))


if __name__ == "__main__":
    main()
