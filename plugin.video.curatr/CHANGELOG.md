# Changelog

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
