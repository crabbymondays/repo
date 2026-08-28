# curatr beta testing

## Kodi Library and blended preferences (beta14)

- Test **Kodi Library + Trakt**, **Kodi Library Only**, and **Trakt Only** under
  **Settings → Preferences & Data → Preference History**.
- With Trakt disconnected, confirm Kodi Library Only refreshes without asking
  for an account and prompt-only mode still works with an empty library.
- Confirm Kodi access does not change ratings, watched states, files or artwork.
- Rate the same identified movie similarly in Kodi and Trakt and confirm it is
  deduplicated into one blended rating.
- Give the same movie ratings four or more points apart and confirm **View My
  Preferences** reports a conflicting rating that was ignored.
- Confirm watched Kodi movies are excluded from generated recommendations by
  TMDB ID, IMDb ID, or title/year fallback.
- Temporarily make one source unavailable while using Kodi Library + Trakt and
  confirm the available source is retained with a warning in Recent Activity.
- Confirm selected MDBList references and the current prompt continue to
  contribute without creating additional preference-summary requests.

## Artwork grid visibility (beta14 hotfix)

- Check square and landscape artwork grids on both a 16:9 television and a tall/wide phone.
- Confirm the purple focus border stays clear of the image and title.
- Confirm suggested movie artwork shows one title only, without a repeated `list-item fanart` line.
- Under **Choose a curatr icon > Genre Colours**, confirm Thriller displays its eye icon.
- Confirm square icons and portrait people remain centred inside a square frame with labels below it.
- On a non-16:9 display, confirm the purple icon frame remains physically square rather than stretching horizontally.

- Open **Suggested artwork** and **Choose fanart from this list** and confirm
  landscape tiles appear over the semi-transparent dialog.
- Open the curatr icon and person pickers and confirm square tiles still appear.
- Confirm only one grid layout is visible at a time and Back still cancels.

## Linked account lists and AI references (beta13)

- Add one Trakt and one MDBList account list to a Widget Folder.
- Confirm opening either item displays its current films without creating a
  new local or remote list.
- Reopen within 30 minutes and confirm it uses the responsive cached result.
- Use **Refresh linked list** and confirm the provider is queried again.
- Disconnect a provider and confirm a previously cached list still opens.
- Change a linked item's name, description and artwork independently.
- Back up and restore a folder containing both linked-list types.
- Use **Use contents as AI reference** from curatr, Trakt and MDBList sources.
- Cancel each dialog in turn and confirm no AI request or empty list is made.
- Confirm the related list is separate, does not include reference films and
  does not change the original provider list.

## Remote artwork previews (beta13)

- Search for an actor/director and confirm TMDB photos appear in the grid.
- Choose fanart from a list item and confirm film images appear in the grid.
- Open Suggested Artwork and confirm remote person/movie previews appear.
- Confirm Back cancels without changing the current artwork.

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
- Focus list widgets and confirm the summary shows film count, creation method and a relative refresh time
- Confirm list context menus keep Artwork and Delete while List Settings does not repeat them
- Confirm folder context menus contain Artwork, Folder settings and Delete folder without repeating every add action
- For a Trakt/MDBList entry and a compatible external plug-in path, open Artwork and try Choose fanart from contents
- Open Settings > Appearance > Choose Menu Background and confirm the visual landscape grid changes the menu background
- Try `Films by Christopher Nolan with Cillian Murphy in` and confirm separate director and actor tags appear
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
