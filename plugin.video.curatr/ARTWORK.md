# curatr artwork pack

Artwork is bundled with the add-on. The user-facing name and canonical Kodi add-on ID are **curatr** and `plugin.video.curatr`.

## Addon artwork
- `icon.png` — 512×512 PNG.
- `fanart.jpg` — branded 1920×1080 artwork for Kodi's add-on information view.
- `resources/media/fanart_menu_clean_v2.jpg` — 1920×1080 unbranded in-menu fanart with clear space for Kodi labels.
- `resources/media/splash_centered.png` — bright centred 1920×1080 curatr splash artwork matching the main add-on fanart.

## Menu icons
Menu icons live in `resources/media/` as 512×512 transparent PNGs using clean monochrome white artwork.

Files include:
- `menu_my_lists.png`
- `menu_explore.png`
- `menu_taste.png`
- `menu_settings.png`
- `menu_create.png`
- `menu_manage.png`
- `menu_refresh.png`
- `menu_backup.png`
- `menu_quick.png`
- `menu_templates.png`
- `menu_all.png`
- `menu_fresh.png`
- `menu_random.png`
- `menu_hidden.png`
- `menu_sync.png`
- `menu_usage.png`
- `menu_activity.png`
- `menu_trakt.png`
- `menu_list.png`

## Global menu fanart
All navigation items use the same `fanart_menu_clean_v2.jpg`. The abstract
lockup leaves the normal Kodi label area clear. Menu icons remain separate and
are never assigned as `thumb` or fanart, preventing skins from enlarging an
individual glyph into a full-screen background.

## Saved-list artwork

Saved lists have two independent artwork roles:

- `resources/media/list_art/icons_v2/` contains the approved generated 512×512 transparent PNGs.
  Kodi receives these as both `icon` and `thumb`.
- `resources/media/list_art/fanart_v2/` contains generated 1920×1080 colour backgrounds as efficient JPEGs.
- `resources/media/list_art/fanart_mono_v2/` contains matching monochrome backgrounds.
  Kodi receives the selected background as both `fanart` and `landscape`.

The bundled choices are Action, Comedy, Crime, Drama, Horror, Romance, Sci-Fi,
Fantasy, Thriller, Mystery, Western, Documentary, Animation, Mind-Bending,
Superhero, Director and Actor. Versioned folder names prevent Kodi from reusing
cached copies of the earlier programmatically drawn artwork.

List records store icon and fanart modes separately. Manual selections are
preserved across recommendation refreshes; Automatic selections are resolved
from the current list name and prompt. Remote person or film artwork is routed
through the add-on's bounded local artwork cache before Kodi displays it.
