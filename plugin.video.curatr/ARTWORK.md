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

`rounded_surface_v3.png` shades filled tabs, item rows, panels and colour
swatches. Its short edge bands stay inside the nine-slice borders, with an even
centre and stronger bottom shade. It retains the shared rectangle's alpha and
corner geometry; Kodi supplies each theme's colour. Artwork corner masks
preserve their transparent centres.
`rounded_control_v3.png` includes a fine edge on native button textures.
`rounded_shadow_v3.png` and `rounded_outline_v3.png` provide the soft shadows
and light borders as non-interactive image layers.

`tools/finish_ui_accents.py` rebuilds the complete decorative finish using
Pillow. Each `button_sheen_*_v2.png` is an alpha mask at the original control's
size, so the diagonal spans the whole button instead of being nine-sliced.
`corner_dots_*_v2.png` fades from the left in list/contents windows and from the
bottom-right elsewhere. Foreground controls and all positions of moving buttons
are masked out; unused list space remains patterned while opaque card bodies
protect visible items. `list_sheen_*_v2.png` supplies faint dotted light at both
edges of each card, leaving the centre clear. It is drawn once above the artwork
layers and below the labels, with peak alpha capped at 14/255.
Sheen and dots use the independent `CuratrSheenColour` property through
the image control's colour binding, so unsaved Customise Theme changes preview
live. White is the default. Moving buttons have numbered sheen, focus-border
and shadow images that follow their position and visibility; the Keyword
Matching panel border and shadow follow its runtime height. Existing functional
control IDs, fonts and controller navigation remain in place, with one new
Sheen colour row and the requested contents/source spacing changes.
The build removes unused generated finish masks after regenerating all layouts.
No runtime image library is needed.

The original chip-cap build masks live under `tools/ui_sources/`, alongside the
build tooling rather than runtime media, and are excluded from the install ZIP.

Keyword Matching's panel uses a full-size low-alpha diagonal mask with the same
`CuratrSheenColour` binding. Its numbered layer follows panel resizing. Filter tags
use `chip_sheen_v1.png` with peak alpha 13/255; borderless minus controls omit it.
The source/preview dialog and Dynamic List Settings use generated native XML copies
to avoid reopening the same Kodi window as the manager underneath. They retain the
same geometry and decorative treatment.

`tools/build_action_icons.py` builds the requested menu and keyword-control
PNGs from the retained Font Awesome SVGs. `tools/prepare_ui_layouts.py` limits
action-menu icons to the right-hand lists before decorative layers are regenerated. The
theme editor and left-hand tabs have no icons. Keyword Matching uses finished
chip caps, centres and edge masks without changing the transparent click areas.
Individual minus buttons omit their edge masks while keeping the tag-coloured
focus fill. The add-filter button centres the existing Font Awesome plus PNG
inside its unchanged 60-by-60 click area, using Kodi's proportional fit mode
instead of stretching to match the device's display scaling. Footer actions use the opaque
`CuratrFooterButton` theme property; other controls retain their existing fills.
Keyword Matching's edit toggle uses the `pen-to-square` and `check` PNGs beside
Looking for. The existing button ID 102 handles controller and touch actions;
image 103 follows the editing state. A horizontal group list sizes the heading
to its actual text width; a nested group keeps the 56-by-56 click target and
aspect-fitted icon together in the label row. The 36-by-36 pen icon is raised
four logical pixels; the check is raised by another two for optical alignment.
Both button textures are transparent, without a border, shadow or sheen.
A [conditional zoom](https://kodi.wiki/view/Animating_your_skin) grows the icon
to 112% on focus, preserving the 56-by-56 touch and controller target.
A vertical justified group uses the native request textbox's auto height to
balance the divider for single and wrapped lines. The divider's inset matches
the heading label's inset; spacing uses Kodi's font metrics rather than a
character-count estimate. See the official [textbox](https://kodi.wiki/view/Text_Box)
and [group list](https://kodi.wiki/view/Group_List_Control) control documentation.
`CuratrListCard` and `CuratrListCardFocus` provide dark ordinary cards and
lighter selected cards. Artwork frames and corner masks use the corresponding
card property in each native layout. Card bodies and artwork surrounds have
flat base shading, with one shared dotted sheen above them. The square frame
uses `list_artwork_frame_flat_v2.png`; the existing corner mask keeps its
transparent centre. Separate per-artwork sheen and shade crops are no longer
generated or bundled. Right-hand action labels use their original constant white text colour;
icons retain their existing `CuratrActionTint` disabled state.
`CuratrKeywordBackdrop` keeps the same
theme tint with full opacity to hide underlying dialogs and their buttons.
`CuratrKeywordPanel` uses the same RGB as `CuratrPanel` with full opacity,
including unsaved theme previews.

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
python tools/build_action_icons.py
python tools/finish_ui_accents.py
python tools/release_checks.py
python tools/feature_checks.py
python tools/parser_checks.py
python tools/keyword_pipeline_checks.py
python tools/finish_checks.py
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
