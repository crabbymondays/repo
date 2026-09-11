# Bundled artwork

The v5 bundle contains 17 coordinated designs in four styles. White icons are
transparent 512 × 512 PNGs; coloured icons use the same shapes over soft matte
gradients. Colour and monochrome fanart are 1920 × 1080 JPEGs with flowing curves
and a white genre icon on the right. Monochrome uses dark grey.

One shared shape mask supplies all four styles. The files have consistent
padding and no baked-in rounded corners: Kodi's existing square and landscape
controls provide the corner treatment and retain the appropriate aspect ratio.

## Rebuild

Genre geometry, palettes and composition live in `tools/build_artwork.py`.
The menu geometry in `tools/build_menu_artwork.py` reuses its mask renderer;
the v6 menu bundle contains padded 512 × 512 icons and 960 × 540 landscape
images. Usage and Activity share one three-bar asset. Menu backgrounds remain
unchanged, and genre artwork does not need rebuilding for a menu-only edit.

On a build machine with Python 3.9+, Cairo, Pillow 9.1+ and CairoSVG installed:

```bash
python tools/build_artwork.py
python tools/build_menu_artwork.py
python tools/release_checks.py
python tools/build_release.py --output /path/to/releases
```

Only Pillow is needed to run the checks. Kodi loads the prebuilt PNG/JPEG files;
the existing repository packager excludes `tools/`, `ARTWORK.md` and `README.md`
from its install ZIP. The source ZIP includes all of them.

Genre keys and saved style values remain unchanged. `lib/list_art.py` resolves
them through the versioned bundle to avoid stale Kodi textures. A read-only
compatibility map handles local paths to retired bundled artwork while leaving
custom files and URLs alone. Change the bundle version in both the renderer and
resolver when replacing artwork again.

The release checks cover parsing, artwork dimensions and transparency, saved
choices, content loading, metadata caching and Kodi-control regressions with
local stubs. Device testing in Kodi is still required for Android/Xbox rendering
and skin-specific scaling.
