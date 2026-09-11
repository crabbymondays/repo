"""One versioned menu bundle for square icons and landscape widgets."""

import os


MENU_BUNDLE = "v6"


def menu_source(addon_path, filename, landscape=False):
    filename = os.path.basename(str(filename or ""))
    if filename == "menu_activity.png":
        filename = "menu_usage.png"
    return os.path.join(addon_path, "resources", "media", "menu", MENU_BUNDLE,
                        "landscape" if landscape else "square", filename)


def current_menu_source(addon_path, source):
    """Resolve only local shortcuts to retired menu art; never alter URLs."""
    path = str(source or "").replace("\\", "/")
    if "://" in path and not path.startswith("special://"):
        return source
    for folder, landscape in (("menu_v5", False), ("menu_landscape_v1", True)):
        marker = "/plugin.video.curatr/resources/media/%s/" % folder
        if marker in path:
            filename = path.rsplit(marker, 1)[1]
            if "/" not in filename:
                candidate = menu_source(addon_path, filename, landscape)
                if os.path.isfile(candidate):
                    return candidate
    return source
