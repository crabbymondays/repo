"""Curatr folder shortcuts: directory targets and explicitly selected actions."""

import uuid
import xbmcgui

# key, title, route/command, target, icon. No user-supplied executable commands.
SHORTCUTS = (
    ("add_item", "Add Item to This Folder", "folder", "add_item", "menu_add_folder.png"),
    ("create_list", "Create a New List", "command", "create", "menu_create_v2.png"),
    ("create_folder", "Create a Folder", "command", "folder_create", "menu_add_folder.png"),
    ("settings", "Settings", "command", "settings", "menu_settings.png"),
    ("my_lists", "My Lists", "route", "lists", "menu_list.png"),
    ("folders", "Folders", "route", "folders", "menu_widget_folders.png"),
    ("dynamic_lists", "Dynamic Lists", "route", "dynamic_lists", "menu_dynamic.png"),
    ("all_picks", "All Picks", "route", "all", "menu_all.png"),
    ("latest_picks", "Latest Picks", "route", "fresh", "menu_fresh.png"),
    ("surprise", "Surprise Me", "route", "random", "menu_random.png"),
    ("quick_pick", "Quick Pick", "command", "quick", "menu_quick.png"),
    ("saved_prompts", "Saved Prompts", "command", "templates", "menu_templates.png"),
    ("preferences", "Preferences & Activity", "route", "taste_activity", "menu_taste_v3.png"),
    ("folder_settings", "Folder Settings", "folder", "settings", "menu_manage_folders.png"),
)
BY_KEY = {row[0]: row for row in SHORTCUTS}


def choose(existing=()):
    used = {e.get("shortcut") for e in existing if isinstance(e, dict)}
    options = [row for row in SHORTCUTS if row[0] not in used]
    if not options:
        xbmcgui.Dialog().ok("Curatr Shortcuts", "All shortcuts are already in this folder.")
        return None
    selected = xbmcgui.Dialog().select("Add Curatr Shortcut", [row[1] for row in options])
    if selected < 0:
        return None
    row = options[selected]
    return {"id": uuid.uuid4().hex, "type": "curatr_action", "shortcut": row[0], "name": row[1]}
