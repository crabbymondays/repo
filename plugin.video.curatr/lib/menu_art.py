"""One versioned menu bundle for square icons and landscape widgets."""

import os


MENU_BUNDLE = "v10"
ADDON_ICON = "icon_v4.png"


def menu_source(addon_path, filename, landscape=False):
    filename = os.path.basename(str(filename or ""))
    if filename == "menu_manage_folders.png":
        filename = "menu_manage.png"
    return os.path.join(addon_path, "resources", "media", "menu", MENU_BUNDLE,
                        "landscape" if landscape else "square", filename)


def current_menu_source(addon_path, source):
    """Resolve only local shortcuts to retired menu art; never alter URLs."""
    path = str(source or "").replace("\\", "/")
    if "://" in path and not path.startswith("special://"):
        return source
    for folder, landscape in (
        ("menu_v5", False), ("menu_landscape_v1", True),
        ("menu/v6/square", False), ("menu/v6/landscape", True),
        ("menu/v7/square", False), ("menu/v7/landscape", True),
        ("menu/v8/square", False), ("menu/v8/landscape", True),
        ("menu/v9/square", False), ("menu/v9/landscape", True),
    ):
        marker = "/plugin.video.curatr/resources/media/%s/" % folder
        if marker in path:
            filename = path.rsplit(marker, 1)[1]
            if "/" not in filename:
                candidate = menu_source(addon_path, filename, landscape)
                if os.path.isfile(candidate):
                    return candidate
    return source
