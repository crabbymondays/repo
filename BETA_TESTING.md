# curatr beta testing

## API key import (beta12)

- Put a test credential alone on one line in a `.txt` file.
- Import it from AI Service, Metadata, or Connected Accounts as appropriate.
- Confirm cancelling the file browser changes nothing.
- Confirm an existing key is not replaced without approval.
- Confirm **Keep File** leaves the source untouched.
- Confirm **Delete File** removes it where the source is writable.
- Confirm malformed, multi-line, empty and oversized files are rejected without
  exposing their contents in the error message or Kodi log.

## Prompt-only mode

1. Leave both the Trakt connection and public Trakt username empty.
2. Configure only an AI provider and API key.
3. Create a new list and confirm curatr does not ask for Trakt.
4. Confirm the list is saved locally and Trakt Sync is off.
5. Refresh the list and confirm it remains usable without Trakt.

## Artwork grid

1. Open List settings, then Artwork.
2. Open curatr Icons and test both White and Genre Colours grids.
3. Select an image and confirm it applies immediately; press Back and confirm nothing changes.
4. Test Genre Colours and Monochrome fanart grids.
5. With TMDB enabled, test both the person and movie-fanart grids.
6. Repeat with Widget Folder or external-shortcut artwork.

Please test on Kodi 21 Omega where possible.

- Fresh install from the curatr repository
- AI setup and list creation
- List rename, description, prompt, artwork, refresh and deletion
- Home-screen widget refresh after every list change
- Create, edit, reorder and delete Widget Folders
- Confirm Widget Folders and Create a Widget Folder use their dedicated icons
- Add curatr lists and direct external `plugin://` shortcuts to folders
- Browse an installed video add-on, move through its folders and use Choose This Path
- Confirm Back moves up a browser level and manual/Favourites fallbacks still work
- Edit a folder, return to Widget Folders and confirm every row appears exactly once
- Point a skin widget at all folders and at one specific folder
- Confirm deleting a folder never deletes its referenced lists
- Kodi-only lists and optional Trakt synchronisation
- Backup, duplicate-aware restore and restored-list safety
- Backup and restore Widget Folders, ordering and external paths
- Restart Kodi and confirm scheduled refresh behaviour

When reporting a problem, include Kodi version, platform, curatr version,
selected AI provider, what you expected, what happened, and reproducible steps.
Never post API keys, Trakt codes, tokens, or an unreviewed Kodi log publicly.
