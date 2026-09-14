# Bundled artwork

## Artwork picker

The v7 bundle contains 21 symbols: 17 genre and people designs, plus List,
Folder, Movie and TV Show. The final Blank tile uses only the selected
background. White icons stay transparent; fanart uses the colour row, which
includes Grey instead of a separate Monochrome option.

`lib/colours.py` defines all 25 palettes and their rainbow order, shared by
artwork, menu backgrounds and window settings. Original genre colours keep
their existing keys and tones. The deeper and additional UI colours are also
available for every genre. Neutral shades appear at the end of the row.

The bundle has 92 components: 21 transparent square symbols, 21 transparent
landscape symbols, and 25 shared backgrounds in each aspect ratio. Square
images are 512 × 512; landscape images are 1920 × 1080. The picker layers each
symbol over the selected background with matching bounds and aspect-ratio rules.
Blank tiles return the shared background path directly; no extra images or
copies of every symbol/colour combination are bundled.

Kodi skins and widgets need a single image path. `lib/bundled_art.py` composes
each requested combination once into the add-on profile and reuses that cached
PNG. This small compositor uses only Python's standard library, reads only our
bundled unfiltered PNG components, keeps at most two decoded components in
memory and writes completed images atomically. It does not process custom or
downloaded artwork. Cache paths include the artwork bundle version.

`tools/build_artwork.py` contains the original genre geometry and renders the
components. Stable genre keys live in `lib/bundled_art.py`; palettes live in `lib/colours.py`.
The masks, animation star and symmetrical rocket wings retain their current
shapes. Corners remain a Kodi-control
treatment rather than being baked into each image.

## Menu artwork

The v10 menu bundle combines the selected Font Awesome Classic Solid icons,
the retained Curatr designs and matching Browse/Create page badges. The badges
share the same circle centre and size; the magnifier is centred with room around
its handle. Every glyph fits within 340 pixels on a
512 × 512 transparent canvas, matching the genre icons' visible bounds. The
960 × 540 widget images reuse those shapes with centred padding. The selected
shuffle, heart-pulse, fingerprint and file-lines designs have separate menu
mappings for switching method, preferences/activity, viewing preferences and
saving preview results. Dynamic Lists uses the Classic Solid layer-group glyph.

`tools/build_menu_artwork.py` reads the pinned Font Awesome SVGs and generates
the two page badges. For retained menu designs, the supplied square PNGs are
the canonical source; no duplicate master image is needed. The renderer also
accepts a source directory through `--source`.

Font Awesome Free 7.0.1 sources are in `tools/icon_sources/fontawesome-7.0.1/`.
See `THIRD_PARTY_NOTICES.md` for attribution and licences. SVGs and the artwork
build dependencies are not required by Kodi.

## Menu backgrounds and add-on branding

The Menu Background section of Customise Theme shows the same 25 colours as
small unlabelled swatches, with a compact rounded preview card and Match Theme and Custom
Image buttons.
Match Theme follows the interface background tint. Menu backgrounds use the
exact shared fanart files. Swatches use the shared rounded texture with a colour
diffuse; there are no generated thumbnail or per-colour UI image files.

Customise Theme uses that same swatch window for a base colour and optional
individual highlights/background tint. Selected tab backdrops have no
texture-level diffuse colour: Kodi must use the control's programmatic tint.

`rounded_surface_v2.png` shades filled buttons, tabs, item rows, panels and colour
swatches. Its short edge bands stay inside the nine-slice borders, with an even
centre and stronger bottom shade. It retains the shared rectangle's alpha and
corner geometry; Kodi supplies each theme's colour. Artwork masks stay flat.
`tools/build_ui_surface.py` rebuilds the surface and the preview's rounded alpha
mask using Pillow. Neither requires a runtime image-processing dependency.

Custom PNG, JPEG and WebP images up to 12 MiB are copied atomically into the
add-on profile. Identical imports reuse the same copy; the original file can
then be moved or deleted. Generated caches and user images are not bundled.
Old numeric menu-background choices resolve to the corresponding shared
colour choices without rewriting saved lists or folders.

The supplied add-on icon is kept unchanged as `icon_v4.png`, referenced explicitly in
`addon.xml` to avoid Kodi reusing its old icon texture. There is no duplicate
root icon or add-on information-page fanart. The in-add-on menu backgrounds
remain available independently.

## Rebuild and verify

Use Python 3.9+, Cairo, Pillow 9.1+ and CairoSVG for the artwork builders:

```bash
python tools/build_artwork.py
python tools/build_menu_artwork.py
python tools/build_ui_surface.py
python tools/release_checks.py
python tools/feature_checks.py
python tools/build_release.py --output /path/to/releases
```

Only Pillow is needed in addition to Python to run the checks. The release
packager excludes `tools/`, `ARTWORK.md` and `README.md` from the install ZIP;
the source ZIP includes them. Both packages include third-party attribution. The source includes
`tools/clean_repository.sh` for the existing Termux publishing workflow.

Stable genre keys and existing style values remain supported. `lib/list_art.py`
resolves saved choices and local paths to retired artwork through the current
bundle, leaving custom files and URLs alone. Replace the bundle version when
changing components again to prevent stale Kodi textures or composed images.

The checks cover source/XML parsing, geometry, transparency, pixel-correct
composition, cache reuse, saved colours, cancellation, folder navigation,
deleted references, keyword editing, menu backgrounds, custom-image imports,
directory refreshes and metadata behaviour using Kodi stubs.
Actual Android/Xbox rendering and skin-specific font/scaling behaviour still
need testing in Kodi.
