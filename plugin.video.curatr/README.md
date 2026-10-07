# curatr

curatr is a Kodi 21 add-on for creating and maintaining personalised movie, TV show and episode lists.

## Features

- Natural-language list creation
- Keyword Matching without an AI request
- Editable keyword filters in the list form, retained through preview and creation
- Optional OpenAI, Gemini, Anthropic, OpenRouter and compatible AI services
- Movies, TV shows, mixed lists and episode airing calendars
- Saved airing windows, watched-item rules and refresh schedules shared by AI and Keyword Matching
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
- Smooth diagonal button sheen and fading background corner dots, with an independent sheen colour
- Menu backgrounds that match the theme, use a soft gradient colour or use a custom image
- Persistent active-tab highlights, a themed keyword editing panel, tag-coloured keyword controls and solid footer buttons
- Native Kodi cast, crew, title and rating metadata from TMDB
- Optional cached IMDb, Rotten Tomatoes and Metacritic ratings through MDBList
- Kodi Library-first playback with optional compatible video add-ons
- Configurable Kodi context-menu actions for titles and add-on folders
- Temporary Find Similar poster previews using Keyword Matching or AI
- Local backup, restore and recovery snapshots

## Installation

Install the release ZIP through **Kodi → Add-ons → Install from ZIP file**. Kodi 21 Omega or newer is recommended.

## Setup

Open curatr Settings and add only the services you want to use. Movie/show Keyword Matching uses TMDB. Episode lists use Trakt calendars; TMDB artwork is optional for them. Connect Trakt for your personal airing calendar, or use Kodi library shows with the global calendar. AI services are optional.

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

Buttons, tabs and item rows have subtle edge gradients and light borders that
follow the selected theme, with soft shadows behind panels and fixed controls.
The established 1.0.27 layout, fonts and artwork picker behaviour are preserved,
with extra room around contents labels and centred two-line source rows. The
decorative layers do not add interactive controls or actions. Small Font Awesome
action-menu icons appear in the right-hand menus; editor tabs stay text-only.

The smooth sheen fades from the bottom-right corner of buttons and colour
swatches, with a soft sheen behind the artwork preview. List cards have faint
dotted light fading inward from their top and bottom edges. Selected list, folder and
source cards use the lighter fill; other cards use the darker fill. Artwork
surrounds match the card's fill in each selection state.
In list and contents views, dots fade from the left
and remain visible in empty space; opaque cards keep them clear of item text.
Other views keep the quiet bottom-right background pattern.
Open **Settings → Appearance → Customise Theme → Sheen colour** to choose
White (the default), a palette colour or a custom colour such as `#B8DFFF`.
Swatch changes preview immediately without saving. This colour tints the finish
independently of the theme and background. Save Changes applies it; Cancel
restores the saved appearance. Selected controls gain a light border on focus.
Save, Cancel and other footer actions use solid themed backgrounds, with the
same sheen and shadows. Keyword Matching minus buttons and tag text use a borderless focus
fill; the plus icon is centred independently of the active skin's font and
fits proportionally across different screen shapes.
Beside **Looking for**, the pen icon starts filter editing and changes to a
check icon to finish. The icon follows the label's actual text width in the
active skin. The borderless icon sits slightly higher and grows gently on focus.
The request's
wrapped text height controls the spacing around the divider, keeping it
midway between the request and the Looking for label.
Right-hand action labels retain their original solid text colour.
Keyword Matching's panel follows the chosen theme colour. It uses an opaque backdrop so
buttons in the underlying menu do not show through. Closing it reveals the
same menu without navigating away. The panel and filter tags have a faint diagonal
sheen using the chosen Sheen colour; tag shading is lighter than button shading.

In List Settings, Creation Method switches between AI and Keyword Matching.
Items has a picker for its four choices: Movies, TV Shows, Movies & TV Shows,
and Episodes. On/Off fields, including Auto Refresh and Sync to
Trakt, toggle directly. Refresh Interval and Auto Sync retain their pickers;
custom refresh intervals and Number of Items retain their numeric editors.
Save Changes applies the draft, and Cancel discards it.

## Keyword requests and empty previews

Creator and reference clauses stay separate. For example, **films by Stephen
King similar to Chucky** means films with the creator's credits that also occur
in TMDB's recommendations for the reference. **Directed by**, **written by** and
**starring** retain their explicit roles; plain **films by** uses creator credits.
Cast and director clauses can appear in either order. Each named person must have
the requested credit; a collective reference combines its members. Exact matching
checks complete credit responses, not just a top-ranked discover shortlist.
Short requests such as **horror by Stephen King** or **crime by a named creator**
also retain both the catalogue filter and the creator; "films" or "movies" is
optional. The same rule applies to themes, language and country wording.

Put ambiguous names or titles in quotes: **similar to "War and Peace"**, or
**similar to "The Thing" (1982)**. Quoted text is kept literal and cannot become
a genre, country, viewing-history or schedule instruction. Up to three people
and three reference films are supported. Reference films contribute one combined
recommendation pool; creator/cast constraints intersect that pool. Keyword
Matching supports flat genre AND/OR groups, not arbitrary nested Boolean queries.
**Crime, Horror** and **Crime and Horror** require both genres and display as
**Crime and Horror**. **Crime or Horror** accepts either. These constraints also
apply to the reference recommendation pool; the request is never broadened
silently. Themes influence ranking rather than guaranteeing a plot detail.
**Sorted by release date**, **sorted by rating** and **sorted by popularity**
produce editable ordering tags and control the final local ranking.

**Crime and thriller without horror or romance** keeps both exclusions.
Percentage ratings use the selected provider's scale: **IMDb 80%** becomes 8/10,
and **Rotten Tomatoes 8/10** becomes 80%. **In French** sets a language, while
**British films in French** displays separate country and language constraints.
Release years after/before a year exclude that year. Runtime ranges display
both ends. Conflicting bounds are rejected, and unrecognised filter edits retain
the previous value; the minus button removes a filter.

If Keyword Matching finds no items during Preview List or Create List, the
complete list draft stays open. Catalogue errors and invalid filters also retain
the draft. Empty-result explanations distinguish no source recommendations,
strict genre combinations, creator/reference intersections and local exclusions.
Change Request, remove a filter or include watched items and try again. Hidden
items remain excluded, using provider IDs when available and title/year otherwise.
Refreshes prefer new matches and reuse previous matches to fill remaining spaces.

Recommendation searches check at most five pages per reference (up to three
references); discovery queries check at most five pages. Local filters and
exclusions are applied before retaining up to 100 eligible candidates per media
type. Missing metadata is fetched only when a filter needs it, with at most 100
movie-detail checks or history-title lookups per search and bounded session
caches. A lookup limit produces an explicit explanation rather than an unexplained
empty list. No search promises to exhaust the catalogue. A familiar creator and
reference can still have zero overlap in TMDB's recommendation data.

Ordinary genre/year/rating/country/language/runtime TV discovery is supported;
TV genre names are mapped to TMDB's broader TV categories (for example Horror
and Thriller use Mystery). People, reference-film, collection and rating-source
filters currently require Movies; mixed requests with these filters explain the
restriction instead of silently dropping TV constraints. TV runtime filters use
TMDB's episode-runtime discovery fields.

## Watch-history filters

Keyword Matching understands **unwatched**, **not watched in the last two years**,
**last seen six weeks ago**, **watched before**, **seen twice**, **watched at least
three times** and **including watched items**. Use the plus button and choose
**Viewing history** to add one, or click an existing history tag to edit it.
Unrecognised history input keeps the existing filter and shows examples.
Movie history searches use the saved Trakt/Kodi snapshot and resolve catalogue
metadata through TMDB; a Trakt connection is not required for Kodi history.
History constraints remain active when combined with creators, references or
other filters. An empty/missing history snapshot is explained explicitly.

History uses the source selected under **Preferences & Activity**. A stale or
play-count filter selects previously watched items; unwatched excludes them.
Counts and last-watched filtering currently apply to Movies or Episodes.
Whole-series lists cannot infer episode-level counts. Hidden titles stay excluded
when watched titles are allowed. Movie history uses the synced preference profile;
episode history reads shared Kodi/Trakt snapshots.

## Episode lists and saved instructions

For example, request **"make me a list of my episodes airing in the next month,
update daily and hide watched items"**. AI interprets supported operational choices
once. Keyword Matching recognises the same wording without an AI request. Both
save the source, date window, watched-item rule and schedule. The calendar supplies
real episode IDs and airing dates; daily episode refreshes do not call AI again.

**My episodes** uses Trakt's calendar of watched/watchlisted shows, including
watchlisted individual episodes. Other source choices are **Kodi library shows**
and **all shows**. Click the source tag to cycle those three choices. A past/next
number of days or weeks, the current month and the next calendar month are supported.
**In the next month** means the next 30 days; **during next calendar month** means
that complete month. Exact dates are displayed in the saved instructions. Date
windows are bounded to 93 days and lists to 50 items.

Source and date-window tags use the same minus, text-edit and plus
controls as other keyword filters. The plus menu restores removed tags; a new
source starts with Trakt and its label cycles the three choices. Removing the
source uses the all-shows calendar; removing the date window uses the next 30
days. These defaults appear beneath the tags and removed tags stay removed after
saving. Auto Refresh and its interval are edited in **Behaviour**; they are not
keyword filter tags or plus-menu options. **Refreshed every day** and **updated
daily** can seed a daily schedule from a new request. Editing keyword filters
preserves the refresh settings chosen in Behaviour.

Episode filters support genre, genre alternatives/exclusions, airing year, rating,
runtime, language, country and watch history. Creator, reference-title, franchise
and mood filters for episodes show a clear unsupported-filter message. Movie/show
recommendations retain their existing AI and Keyword Matching behaviour, with
optional release-date windows. Personal calendar sources apply to Episodes.

Auto Refresh uses the existing service while Kodi is running. **Update daily**
sets a 24-hour interval; the service checks due lists every five minutes. Manual
changes in Behaviour take precedence over the initially interpreted schedule.
Refresh does not enable Trakt syncing. Sync to Trakt and its custom schedule remain
separate settings. Empty episode windows can be saved for future refreshes; source
failures keep the previous list. Newly watched episodes are hidden when the list
or a Dynamic List using it is reopened.

Kodi library files play directly. Compatible player definitions with a
`play_episode` route receive parent show IDs and season/episode numbers. Players
with only `open_show` open their series browser. Future episodes open information
until their air time; metadata and Trakt sync use the episode's own IDs.

## Dynamic Lists

Open **Lists → Dynamic Lists → Create Dynamic List**. Add Curatr lists, external
add-on directory paths, or linked Trakt/MDBList lists. Reorder the sources, then
choose Recently watched, Recently added, Title or Source order. Date and title
sorts support ascending and descending order. Preview the
combined contents before saving. A Dynamic List can be added to a Curatr folder
or used directly as a widget path.

The source editor and preview use their own native dialogs. Preview has a visible
Close action and accepts Back from either pane. Empty sources or provider failures
are explained without replacing the preview with the empty Dynamic Lists manager;
your selected sources remain in the draft.

Sorting applies to the combined contents. Duplicate media IDs are matched by
media type, with season and episode numbers kept distinct. Without a reliable
ID, only identical source URLs are treated as duplicates. External playback
and folder URLs are preserved; Curatr does not send them through another player.

Enable **Alternating sources** to take one item from each path in turn:
Path 1 → Path 2 → Path 3 → repeat. With Source order, this applies to all items.
With a date sort, dated items are sorted first and only the undated items
alternate afterwards. Empty or exhausted sources are skipped and duplicates
are removed before alternating. The toggle is disabled for Title sorting.

Dates must be supplied by the source add-on. Items with no relevant date follow
dated items in source order unless Alternating sources is enabled; **Source Information** on the context menu
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
Run `python tools/parser_checks.py` for the dedicated parser, filter-edit and
empty-preview recovery regressions. Run `python tools/keyword_pipeline_checks.py`
for end-to-end generation, provider-shaped pagination, role credits, history,
exclusions, lookup limits, sorting and draft-error recovery. These suites use
deterministic fixtures and no live accounts or AI requests.
