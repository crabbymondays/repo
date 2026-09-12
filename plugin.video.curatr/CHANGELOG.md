# Changelog

## 1.0.24

- Applied the new add-on icon with a fresh asset filename and removed the old add-on information-page fanart pending a replacement.
- Unified artwork, menu-background and window colour choices into one 25-colour palette. Colours follow rainbow order, with Slate and Grey at the end; Deep Blue, Deep Violet, Red, Cyan, Teal, Green and Pink are available throughout.
- Added a final Blank tile to coloured icons and fanart. It reuses the selected background without creating another set of assets.
- Removed Monochrome from the fanart picker. Grey is available in the colour row; saved monochrome artwork remains readable.
- Removed light mode, its setting, layouts and rendering branches. Existing light-mode preferences are ignored so custom windows open normally after upgrading.
- Matched artwork and folder selection borders to the main highlight colour, including the active selectors above them.
- Changed Find Similar refreshes and method switches to replace the current directory listing. Back returns to the preceding screen rather than stepping through previous result sets.
- Reused the exact fanart background files for menu backgrounds, removed superseded artwork and the separate menu-gradient builder, and retained compatibility for saved references to older assets.
- Included a Termux cleanup helper for the hosting repository's old testing guide, release archives and unused control generator. It updates the repository description, download page, issue template and README to remove obsolete pre-release wording.

## 1.0.23

- Added Match theme, eleven soft gradient colours and a final Custom… tile to the menu background picker. Match theme follows the interface background tint; gradient choices retain their hue in light mode.
- Preserved explicitly saved backgrounds from earlier releases and selected the active background when opening the picker.
- Reused the flowing fanart geometry in one shared greyscale template. Python applies colours and caches completed images; picker previews use smaller images and require no extra Kodi dependency.
- Copied custom PNG, JPG and WebP backgrounds into Curatr's data folder, so removing the original download does not break the selection. Cancelling the file browser returns to the picker without saving changes.
- Refreshed an open Curatr directory after a background or matching theme change, without reloading the skin or moving to the home screen. List and folder storage is unchanged.

## 1.0.22

- Restored tag-coloured focus for keyword remove/edit controls, tightened the gap between minus and label, and kept Plus neutral in light and dark modes.
- Moved Edit Filters / Done beside Looking for with normal-weight text, a transparent resting background and a neutral focus state.
- Used Save for keyword edits within an existing list, while retaining Use Filters during new-list configuration. Cancelling the parent settings window still leaves the saved list untouched.
- Renamed the main My Lists section to Lists and Browse My Lists to My Lists. Centred the browse magnifier inside its badge and retained consistent badge alignment with Create a New List.
- Renamed preview actions to Refresh Results and Switch Method. Their descriptions identify the method that will be used; switching keeps the same reference item.
- Added the selected Font Awesome shuffle, heart-pulse, fingerprint and file-lines designs for Switch Method, Preferences & Activity, View My Preferences and Add To New List respectively.
- Versioned the square and landscape menu bundle together, removed the superseded menu files and retained compatibility with saved links to older bundled artwork.

## 1.0.21

- Applied the selected Font Awesome Classic Solid icons to Privacy, Hidden, AI Usage, Recent Activity, Refresh, Latest Picks, Surprise Me, Saved Prompts, Quick Pick, All Picks, Backup & Restore and folder actions. Browse My Lists and Create a List share a page design, padding and aligned circular badges.
- Added List, Folder, Movie and TV Show artwork alongside the 17 existing genre and people symbols. Every built-in symbol supports 18 background colours for square artwork and fanart, with a single row of colour swatches in the artwork picker.
- Reused shared backgrounds and transparent shapes in the picker. Finished images are composed on demand using Python's standard library and cached for Kodi skins and widgets; no new Kodi dependency is required.
- Softened the Comedy and Drama mask corners, aligned Animation's star and corrected the rocket's wing symmetry. Kept monochrome fanart dark grey.
- Kept selected tabs coloured behind native Kodi dialogs with control-owned colours. Made the keyword panel charcoal in dark mode and light grey in light mode, retained coloured tags, replaced the old transparent hit texture and widened Edit Filters / Done.
- Strengthened custom-window headings and bottom action labels, with more space between item titles and details across the list and folder interfaces.
- Focused the first item in Manage Contents on opening, retained its focus border and kept its action pane in sync while navigating. Omitted deleted local-list references from folder views and counts; Manage Folders now labels those lists as local.
- Versioned both artwork bundles, removed superseded files and retained compatibility with saved artwork choices and old bundled paths. Stored lists, folders and recovery data are not migrated or reset.

## 1.0.20

- Restored the original menu artwork, including My Lists, Usage, Activity and folder actions. Retained the 1.0.19 Browse My Lists, Hidden and Backup & Restore designs.
- Matched every menu glyph to the genre icons' 340-pixel visible bounds on a 512-pixel square canvas, preserving proportions and centred padding. Updated landscape widget images from the same shapes.
- Versioned the menu bundle again and omitted the superseded v6 images and unused redesign geometry from release packages. Local shortcuts to retired menu paths resolve to the current bundle.
- Preserved the keyword editor, active-tab highlights, themes and other behaviour from 1.0.19.

## 1.0.19

- Restyled menu icons to match the coordinated genre family, retaining the existing concepts and the approved My Lists, Browse My Lists, three-bar Usage/Activity, file-based Backup & Restore and folder-management designs.
- Versioned square and 16:9 menu artwork together, with consistent transparent padding. Removed superseded menu artwork and resolved local shortcuts to those files through the new bundle.
- Kept active list, folder and artwork selectors highlighted while focus moves into their contents, with readable labels in light and dark themes.
- Restored the keyword chip editor when editing a Keyword Matching request in List Settings. Preview and Create share confirmation when filters have not already been reviewed.
- Preserved manually edited filters through previews, creation and unrelated settings changes. Cancel leaves the draft and saved list untouched; changing filters offers a refresh even when the request text is unchanged.
- Replaced 31 fixed-colour keyword control images with shared, runtime-tinted shapes and native focus conditions. Kept controller navigation and touch actions on the same editing path.
- Added focused regression checks and removed the unused legacy prompt-edit handler. No new Kodi runtime dependencies or user-data migrations.

## 1.0.18

- Replaced all 17 genre and people icons with a consistent white silhouette set, including the brain, rocket, open book, handcuffs and comedy mask with one pair of eyes.
- Added coordinated soft gradient square artwork and flowing-curve 16:9 fanart. Monochrome uses a dedicated dark grey palette instead of near-black.
- Versioned the complete genre bundle and removed 68 superseded images. Existing genre/style choices and local shortcuts to old bundled images resolve to the replacement artwork without rewriting user data.
- Restored Contents in artwork settings for Curatr and linked Trakt/MDBList lists, including links being added to a new folder. Missing backdrops load on demand through the metadata cache, with bounded requests and duplicate filtering.
- Made Left/Right at artwork-grid edges focus Save Changes; Up returns to the grid. Both light and dark layouts use the same navigation.
- Included editable artwork geometry and a build-only renderer in the source package; no new Kodi dependencies or runtime image generation.

## 1.0.17

- Restored the full custom Folder Settings window on Android by isolating optional content-control failures instead of dropping into the basic fallback menu.
- Kept the right-hand actions visible in Manage My Lists and Manage Folders, updating them as the highlighted item changes and allowing direct left/right navigation.
- Restored the subtler artwork corner treatment while retaining aligned masks, clean white-icon previews and fixed square/landscape proportions.
- Widened the artwork style control so Monochrome is shown in full.
- Renamed AI-reference actions to Create Similar List throughout list and folder menus.

## 1.0.16

- Stabilised Folder Settings on Android by isolating unsupported control calls and retaining a standard-Kodi fallback if the custom window cannot initialise.
- Kept list and folder items navigable while their action pane is open, so another item can be selected without backing out first.
- Made Quick Pick safe to expose through home-screen widgets without allowing widget probing to open its dialog unexpectedly.
- Kept each preset's background tint visible in light mode instead of flattening every theme to white.
- Added cached TMDB ratings and optional bulk IMDb, Rotten Tomatoes audience/critic and Metacritic ratings through an existing MDBList connection.
- Put enabled Curatr actions directly into Curatr title context menus and reserved the separate Curatr submenu for items outside the add-on.
- Rebalanced the Create a New List glyph inside its square canvas and replaced the superseded square and landscape filenames.
- Corrected the artwork editor's square preview backing, white frame, inset icon, rounded masks and 16:9 corner layers without adding duplicate textures.
- Widened Match Fanart so its full label remains visible and preserved fixed artwork proportions across Kodi's supported display scaling.

## 1.0.15

- Added coordinated Violet, Ocean, Emerald and Amber themes, an optional light mode, and independent custom highlight, secondary highlight and background-tint choices.
- Kept Violet's two accents within the violet family and generated every window surface from one runtime palette instead of duplicate colour-specific skin files.
- Repaired the blank Folder Settings Appearance and Contents tabs and restored the pre-save content grid and controller navigation.
- Corrected the fingerprint's inner spacing and removed the duplicate upper eye marks from the white Comedy icon.
- Versioned the fingerprint, branching-preview and white genre-icon artwork so Kodi reloads the corrected images, while removing their superseded copies.
- Made Match Fanart display in full, strengthened the icon-preview frame and aligned each preview layer around the same centre.
- Corrected the landscape corner mask, reduced the fanart corner radius and made card and mask colours identical in normal and focused states.
- Centralised bundled-artwork path resolution and removed an unused import, an unreachable metadata helper, an unused splash implementation and unreferenced menu artwork.

## 1.0.14

- Kept square icons and 16:9 fanart proportional across 4:3, 16:10, 16:9, 18:9, 19.5:9, 20:9 and 21:9 displays.
- Made each artwork image, rounded mask, card and focus frame use the same centred scaling rule so the layers remain aligned on ultrawide phones.
- Replaced the square-backed landscape masks with native 16:9 RGBA assets and corrected the landscape thumbnail centres.
- Checked the custom artwork layouts at HD, Full HD and 4K resolutions, including the Honor screenshot dimensions.

## 1.0.13

- Replaced separate coloured PNG files with Kodi-native `colordiffuse` styling applied to the established RGBA rounded shape.
- Restored Violet, Ocean, Emerald and Amber highlights and focus borders without depending on device-specific coloured texture decoding.
- Added a thin rounded white frame around the left-hand icon preview.
- Removed the now-unused coloured theme texture files.

## 1.0.12

- Restored the coloured focus highlights and selection borders by loading each interface theme from a complete static Kodi skin instead of constructing texture paths at runtime.
- Kept Violet as the dependable default while retaining the Ocean, Emerald and Amber appearance presets.
- Rounded both square icons and landscape fanart in the artwork grids and the combined left-hand preview.
- Matched corner masks to their surrounding panels so rounding no longer produces conspicuous dark corner marks.

## 1.0.11

- Centred action labels vertically after removing their secondary descriptions.
- Simplified the artwork editor so Curatr artwork opens first, with Automatic available through the single Reset to Automatic action.
- Removed the redundant default Curatr icon choice and shortened the colour-style label throughout the interface.
- Widened artwork source controls, removed clutter from person results and corrected square and landscape focus borders, preview spacing and label placement.
- Removed the unnecessary border around the left-hand icon preview and kept artwork proportions stable through Kodi's 1080p coordinate scaling.
- Added Violet, Ocean, Emerald and Amber highlight presets under Appearance for Curatr's custom windows.
- Added a visual Contents tab to folder creation, including Add Item, reordering, item editing and removal before Create Folder is selected.
- Corrected custom-window navigation order for reliable D-pad movement.

## 1.0.10

- Added one unified artwork editor for icons and fanart, with a combined live preview and temporary changes until Save Changes is selected.
- Added direct source controls for Automatic, curatr artwork, matching, contents, people, custom files and the default artwork where applicable.
- Kept square and landscape choices in correctly proportioned visual grids with their relevant colour-style controls.
- Connected the editor to list creation, saved lists, folders, linked lists, add-on paths and imported Kodi Favourites.
- Simplified list and folder action panes to action names only, with concise Select an item and Choose an action states.
- Removed the second focus treatment from list and folder artwork so only the complete row or tile receives focus.
- Replaced the matching-method artwork with a clean branching-path icon without AI-specific decoration.

## 1.0.9

- Added a unified Manage My Lists window with vertical artwork-led rows and clear list details.
- Added the same visual management style for curatr folders.
- Replaced the folder-content selector with a controller-friendly icon grid and a final Add Item tile.
- Added direct Move Up, Move Down, Move to Front and Move to Back actions for folder contents.
- Kept curatr, Trakt, MDBList, add-on path and Kodi Favourite sources available from the new add flow.
- Retained native-dialog fallbacks if a custom window is unavailable on a Kodi installation.

## 1.0.8

- Gave square icons and landscape fanart clearly different card shapes and focus colours.
- Kept square previews at 1:1 and changed landscape previews to a true 16:9 image area.
- Used format-specific nine-slice textures so corner radii and focus frames scale cleanly without stretching artwork.

## 1.0.7

- Added subtle rounded clipping to square artwork and landscape fanart previews.
- Matched the thumbnail corners to both normal and focused card backgrounds.
- Rounded the landscape artwork cards and focus borders to match the other custom windows.

## 1.0.6

- Replaced the old one-setting-at-a-time list editor with the tabbed List Settings window populated from the saved list.
- Added Save Changes and Cancel actions, with an optional refresh prompt when recommendation settings change.
- Replaced the old folder editor with the custom Folder Settings window and a dedicated Contents entry.
- Kept list details, templates and destructive actions outside the settings forms.

## 1.0.5

- Enabled the tabbed Advanced List Creation window by default and added an Appearance setting for switching back to the simpler step-by-step flow.
- Added a per-list Sync to Trakt choice under Behaviour.
- Added a matching Folder Settings window for name, description, artwork and adding contents after creation.
- Added subtle corner rounding to custom settings, keyword and artwork windows.

## 1.0.4

- Added a dedicated tabbed List Settings window with separate Appearance, Content and Behaviour sections.
- Kept Preview, Create and Cancel in a fixed action bar instead of mixing them with configuration rows.
- Added temporary poster previews that can be edited, discarded or saved without generating the results a second time.
- Changed Quick Pick to open a temporary preview instead of overwriting a saved list with the same name.
- Prevented an empty live state file from hiding recoverable lists and folders after an update.
- Made obsolete list and folder widget paths close quietly without repeated missing-item messages.
- Stopped artwork changes from automatically reloading the full skin and returning to Kodi's home screen.
- Added dedicated 16:9 artwork for Curatr menu entries used in landscape widgets.
- Corrected the Comedy genre artwork and made automatic genre icons follow the selected colour style.

## 1.0.3

- Internal package superseded by 1.0.4 so Kodi reliably applies the updated List Settings interface.

## 1.0.2

- Prevented overlapping Kodi plugin and background-service saves from overwriting newer state.
- Added row-level merging for lists, folders, saved prompts and hidden items.
- Protected other settings and account state from stale background writes.
- Preserved the existing 1.0.1 safety backup before any upgrade migration can rotate it.
- Added a compact cumulative recovery snapshot that retains recoverable local content.
- Added **Recover previous local state** under **Backup & Restore**; recovery only adds missing content and keeps current content unchanged.

## 1.0.1

- Prevented background updates from overwriting newer folder changes.
- Added missing-folder recovery from the last valid state snapshot.
- Stopped obsolete widget paths from repeatedly displaying a missing-item dialog.
- Polished Find Similar actions with clearer labels, consistent PNG artwork and a more useful action order.
- Moved context-menu controls into Appearance settings.
- Added item counts, providers and refresh status to linked Trakt and MDBList folder entries.

## 1.0.0

- First stable curatr release.
- Added personalised movie, TV show and mixed-list creation.
- Added Keyword Matching and optional multi-provider AI recommendations.
- Added Kodi Library and Trakt preference sources.
- Added optional Trakt list syncing and automatic refresh schedules.
- Added list management, artwork, folders, external shortcuts and backups.
- Added Xbox-friendly D-pad navigation and dialog handling.
- Added native Kodi cast, crew and title metadata with a persistent TMDB cache.
- Added Kodi Library-first playback and compatible installed video add-on support.
- Added automatic community player-list fallback and clearer playback setup.
- Added configurable Kodi context-menu actions for movies, TV shows and add-on folders.
- Added temporary Find Similar poster previews with explicit list creation.
- Completed the public-release wording and code-quality pass.
