"""Menu background choices, shared gradient colouring and local custom images."""

import hashlib
import os
import tempfile

import xbmcvfs

from .bundled_art import BUNDLE as BACKGROUND_VERSION, components, _read_component, write_png
from .menu_art import menu_source
from .ui_theme import BACKGROUND_COLOURS, background_colour


MAX_CUSTOM_BYTES = 12 * 1024 * 1024
_COLOUR_KEYS = {key for key, _label in BACKGROUND_COLOURS}
_LEGACY = {"0": "theme", "1": "deep_blue", "2": "deep_violet", "3": "slate", "neutral": "grey"}

def current_choice(addon, state=None):
    state = state if isinstance(state, dict) else {}
    value = state.get("menu_background_style")
    if value in (None, ""):
        value = addon.getSetting("menu_background_style")
    value = str(value if value not in (None, "") else "theme")
    value = _LEGACY.get(value, value)
    return value if value in _COLOUR_KEYS or value in ("theme", "custom") else "theme"


def appearance_signature(addon, state=None):
    state = state if isinstance(state, dict) else {}
    choice = current_choice(addon, state)
    if choice == "custom":
        source = str(state.get("menu_background_source") or "")
        if source and xbmcvfs.exists(source):
            return choice, source
    return choice, background_colour(addon, choice)


def _gradient_source(addon, colour, preview=False):
    root = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    key = background_colour(addon, colour)
    _symbol, source, _palette = components(root, "blank", "fanart", "colour", key)
    if not preview:
        return source
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    if not profile:
        return source
    target = os.path.join(profile, "menu_backgrounds", BACKGROUND_VERSION, key + "-preview.png")
    if os.path.isfile(target):
        return target
    width, height, channels, pixels = _read_component(source)
    if channels != 3:
        raise ValueError("The shared background must be RGB")
    # Pick one pixel in each 3 x 3 area for the 640 x 360 picker thumbnail.
    # Channel slicing runs in C and avoids a Python loop over every pixel.
    small_width, small_height = (width + 2) // 3, (height + 2) // 3
    thumbnail = bytearray(small_width * small_height * channels)
    for channel in range(channels):
        thumbnail[channel::channels] = b"".join(
            pixels[row * width * channels + channel:(row + 1) * width * channels:3 * channels]
            for row in range(0, height, 3)
        )
    write_png(target, small_width, small_height, channels, thumbnail)
    return target


def background_source(addon, state=None, choice=None, preview=False):
    state = state if isinstance(state, dict) else {}
    choice = current_choice(addon, state) if choice is None else choice
    if choice == "custom":
        source = str(state.get("menu_background_source") or "")
        if source and xbmcvfs.exists(source):
            return source
        choice = "theme"
    try:
        return _gradient_source(addon, choice, preview)
    except (OSError, ValueError):
        return ""


def background_entries(addon, state=None):
    state = state if isinstance(state, dict) else {}
    current = current_choice(addon, state)
    entries = [{"key": key, "label": label,
                "source": background_source(addon, choice=key, preview=True), "selected": key == current}
               for key, label in (("theme", "Match theme"),) + BACKGROUND_COLOURS]
    custom = str(state.get("menu_background_source") or "")
    root = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    entries.append({"key": "custom", "label": "Custom…", "selected": current == "custom",
                    "source": custom if custom and xbmcvfs.exists(custom)
                    else menu_source(root, "menu_widget_folders.png", landscape=True)})
    return entries


def import_custom_background(addon, source):
    """Copy the chosen image atomically; a deleted download must not break it."""
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    if not profile:
        raise ValueError("Could not access Curatr's data folder.")
    folder = os.path.join(profile, "menu_backgrounds", "custom")
    os.makedirs(folder, exist_ok=True)
    reader = None
    pending = ""
    try:
        reader = xbmcvfs.File(source, "rb")
        first = bytes(reader.readBytes(64 * 1024))
        if first.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = ".png"
        elif first.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"
        elif first.startswith(b"RIFF") and first[8:12] == b"WEBP":
            extension = ".webp"
        else:
            raise ValueError("Choose a PNG, JPG or WebP image.")
        digest = hashlib.sha256()
        total = 0
        handle, pending = tempfile.mkstemp(prefix=".background-", suffix=".tmp", dir=folder)
        with os.fdopen(handle, "wb") as writer:
            chunk = first
            while chunk:
                total += len(chunk)
                if total > MAX_CUSTOM_BYTES:
                    raise ValueError("Choose an image smaller than 12 MB.")
                digest.update(chunk)
                writer.write(chunk)
                chunk = bytes(reader.readBytes(64 * 1024))
        target = os.path.join(folder, digest.hexdigest() + extension)
        if not os.path.isfile(target):
            os.replace(pending, target)
        return target
    finally:
        if reader is not None:
            reader.close()
        if pending and os.path.exists(pending):
            os.remove(pending)
