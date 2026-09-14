# curatr

curatr is a Kodi 21 add-on for creating and maintaining personalised movie and TV show lists.

## Features

- Natural-language list creation
- Keyword Matching without an AI request
- Editable keyword filters in the list form, retained through preview and creation
- Optional OpenAI, Gemini, Anthropic, OpenRouter and compatible AI services
- Movies, TV shows or mixed lists
- Kodi Library and optional Trakt preference history
- Optional Trakt list syncing
- Custom list artwork and widget folders
- 21 coordinated genre and general artwork symbols, plus blank icon and fanart choices
- 25 shared colours in rainbow order for artwork, menu backgrounds and window accents
- Font Awesome and Curatr menu icons with consistent padding and separate landscape widget artwork
- Unified controller-friendly list and folder management
- A single live-preview artwork editor for list and folder icons and fanart
- Folder contents that can be arranged before a new folder is saved
- Curatr menu/action shortcuts in folders, with Folder Settings on their context menus
- Dynamic Lists combining existing lists and add-on paths, sorted together with duplicate removal
- A visual Customise Theme window with 25 base colours, optional individual accents and a live preview
- Menu backgrounds that match the theme, use a soft gradient colour or use a custom image
- Persistent active-tab highlights, a neutral keyword editing panel, tag-coloured keyword controls and themed footer buttons
- Native Kodi cast, crew, title and rating metadata from TMDB
- Optional cached IMDb, Rotten Tomatoes and Metacritic ratings through MDBList
- Kodi Library-first playback with optional compatible video add-ons
- Configurable Kodi context-menu actions for titles and add-on folders
- Temporary Find Similar poster previews using Keyword Matching or AI
- Local backup, restore and recovery snapshots

## Installation

Install the release ZIP through **Kodi → Add-ons → Install from ZIP file**. Kodi 21 Omega or newer is recommended.

## Setup

Open curatr Settings and add only the services you want to use. TMDB is required for Keyword Matching and title metadata. Trakt and AI services are optional.

Playback is automatic by default. Titles in the Kodi Library use Kodi's built-in player. Other titles can use a compatible installed video add-on after running **Set Up Installed Video Add-ons** under Playback.

## Theme and backgrounds

Open **Settings → Appearance → Customise Theme**. Choose a base swatch to apply
one coordinated colour, then turn on **Custom colours** to choose Highlight,
Secondary highlight and Background tint individually. Save applies the preview;
Cancel keeps the previous appearance. Existing presets retain their colours as
custom combinations until you choose a new base colour.

Choose **Menu background** in the Customise Theme sidebar to use Match Theme,
one of the colour swatches or Custom Image. A small image shows the selected
background. Save Changes applies both the theme and background; Cancel keeps
the saved appearance. Swatches have no labels underneath them. The same 25
colours are used in the genre artwork picker.

Buttons, tabs and item rows have a subtle light-to-dark surface treatment that
follows the selected theme.

## Dynamic Lists

Open **Lists → Dynamic Lists → Create Dynamic List**. Add Curatr lists, external
add-on directory paths, or linked Trakt/MDBList lists. Reorder the sources, then
choose Recently watched, Recently added, Title or Source order. Date and title
sorts support ascending and descending order. Preview the
combined contents before saving. A Dynamic List can be added to a Curatr folder
or used directly as a widget path.

Sorting applies to the combined contents. Duplicate media IDs are matched by
media type, with season and episode numbers kept distinct. Without a reliable
ID, only identical source URLs are treated as duplicates. External playback
and folder URLs are preserved; Curatr does not send them through another player.

Enable **Alternate sources** to take one item from each path in turn:
Path 1 → Path 2 → Path 3 → repeat. With Source order, this applies to all items.
With a date sort, dated items are sorted first and only the undated items
alternate afterwards. Empty or exhausted sources are skipped and duplicates
are removed before alternating. The toggle is disabled for Title sorting.

Dates must be supplied by the source add-on. Items with no relevant date follow
dated items in source order unless Alternate sources is enabled; **Source Information** on the context menu
explains missing dates or a source that is unavailable. Opening a series still
uses its source add-on's episode browser. Add-ons must expose their directory
through Kodi's Files.GetDirectory API. No directory crawler or next-page requests
are used: up to 2,000 items per path, 250 per linked provider list, 16 sources and
1,000 output items are supported.

Curatr lists are read from the latest saved state. External sources share a
60-second cache across lists and widgets. Kodi playback/library notifications
invalidate that cache; otherwise it refreshes on the next read after expiry.
A failed source keeps its last successful results with a short retry delay.
Other add-ons do not expose a universal content-change notification, and the
skin controls when an already visible widget asks for its next listing. There
are no user refresh intervals to configure and no AI or Keyword Matching stage.

Dynamic List Settings shows a refresh summary. **Source Information** on each
item's context menu explains the refresh behaviour and any source warnings.
Previews and widgets preserve source artwork and use cached TMDB posters and
backdrops where available to fill gaps. Existing episode artwork, playback
paths and resume information remain attached to the source item.

Dynamic List definitions and folder shortcuts are included in backups and
recovery snapshots. Source-result caches are disposable and are not exported.

## Folder shortcuts

In folder Contents, choose **Add Item → Add Curatr Shortcut** for Settings,
Quick Pick, saved prompts, picks, Create List, or Add Item to This Folder.
Action shortcuts open only when selected; probing them as a widget directory
does not run the action. **Folder Settings** on a contained item's context menu
opens its parent Curatr folder.

In standard Kodi widgets, action shortcuts have an explicit click target, so
Create a New List and Quick Pick open their dialogs from the home screen.
Closing a dialog returns to the screen underneath it. Opening a preview from
home retains that return destination; editing a preview within Videos replaces
its results without adding repeated entries to Back history.

## Review scores

TMDB's own score comes directly from TMDB, and Trakt-backed items retain their
Trakt community score. These do not require an MDBList key. IMDb, Rotten
Tomatoes and Metacritic scores are separate ratings from those services.

Connect MDBList in Settings for IMDb, Metacritic, Rotten Tomatoes critics and
Rotten Tomatoes audience (popcorn) scores. Scores are provided through Kodi's
video metadata and supported skin properties; the skin determines which logos
and scores are shown. A provider may not have every score for every title.

Successful external scores are cached for seven days. Missing or incomplete
responses retry after six hours, and rate-limit failures pause requests while
retaining available scores. Changing the MDBList API key allows an immediate
retry. Dynamic Lists use the same display cache as ordinary Curatr lists.

## Privacy

Settings, cached metadata, lists and API credentials are stored in Kodi's local add-on profile. curatr sends requests only to services the user enables. Curatr’s configured API keys and Trakt tokens are excluded from backups. External source paths are included, so review backups before sharing them.

## License

Curatr code is MIT licensed. See [LICENSE.txt](LICENSE.txt). The selected Font
Awesome icons are licensed under CC BY 4.0; attribution and modification details
are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Development

The source ZIP includes reusable artwork components, editable genre geometry,
pinned Font Awesome SVGs, canonical menu PNGs and release checks. See
[ARTWORK.md](ARTWORK.md) for build and validation commands. These
checks use local Kodi stubs; testing on Android and Xbox remains necessary.
