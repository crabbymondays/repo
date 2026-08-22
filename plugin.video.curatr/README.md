# curatr

**curatr** is a Kodi movie-list curator that turns natural-language prompts into personalised recommendations shaped by your preferences. Lists live locally in Kodi by default and can optionally be copied to Trakt.

## 0.15.2 generated artwork update

The bundled per-list artwork now uses the approved image-generated designs: 17 transparent square icons, 17 matching colour landscapes and 17 matching monochrome landscapes. Versioned artwork paths prevent Kodi from displaying the earlier programmatically drawn images from its texture cache.

## 0.15.1 artwork update

Kodi's main add-on page keeps the centred curatr artwork. Inside the add-on, all menu entries use a separate right-aligned version of the same design, leaving the left side clear for skin labels and navigation. The menu image has its own filename so Kodi does not reuse an older cached background.

## What it does

- Create movie lists from prompts such as “smart 90s thrillers with great direction, no obvious blockbusters”.
- Blend each prompt with a reusable preference summary built from your Trakt ratings and watch history.
- Exclude watched, rated and manually hidden movies.
- Refresh each list with AI on its own schedule.
- Optionally update a copy of a list on Trakt on a separate schedule.
- Use Quick Pick moods and Saved Prompts.
- Browse All Picks, Latest Picks and a stable daily Surprise Me selection.
- Expose widget-friendly Kodi plugin views.
- Give each saved list independent square-icon and landscape-fanart choices.
- Back up local lists, prompts and hidden-movie choices.

## Requirements

- Kodi 21 (Omega) or newer is the primary target.
- Your own API key for at least one supported AI service: OpenAI, Google Gemini, Anthropic Claude, OpenRouter, or an advanced OpenAI-compatible endpoint.
- Trakt is optional. A public Trakt username can provide read-only preference data. Full Trakt authorisation is only needed if curatr itself should write list copies to Trakt.

curatr does **not** ship with an AI-provider API key. Its Trakt application credentials are bundled so users can link their own Trakt account with the Device Code flow.

## Installation from the curatr repository

The public-testing release is intended to be distributed from the curatr Kodi repository rather than the official Kodi repository. Add the repository source published by the project maintainer, install the repository add-on ZIP, then install **curatr** from that repository.

When updating, install the newer version normally; Kodi keeps the addon profile, settings, local lists and tokens unless the addon data is explicitly removed.

## First setup

On a new installation curatr offers to open Settings once.

1. Choose an AI service. **OpenAI**, **Gemini** and **Claude** are the simplest direct options; **OpenRouter** provides many model families through one account.
2. Enter your own API key and model ID.
3. For personalised recommendations, either enter a public Trakt username or configure a full Trakt connection.
4. Leave **Save new lists to Trakt by default** off if you only want Kodi-local lists.
5. Create your first list from **My Lists → Create a New List**.

Existing users upgrading from an older curatr build are not shown the onboarding prompt.

## Choosing an AI service

- **OpenAI:** enter an OpenAI API key and model ID.
- **Google Gemini:** enter a Gemini API key and model ID.
- **Anthropic Claude:** enter an Anthropic API key. The default is `claude-sonnet-5` and can be changed if your account uses another model.
- **OpenRouter:** enter an OpenRouter API key and a complete model ID such as `anthropic/claude-sonnet-5`. OpenRouter billing and availability apply.
- **Custom / OpenAI-compatible:** advanced option for a trusted hosted provider or local server. Enter its API base URL, API key and exact model ID. The server must support non-streaming Chat Completions and JSON-schema structured output.

Only the selected AI service is contacted. Switching service does not delete lists or preference data, although curatr may rebuild its compact preference summary with the newly selected model when needed.

All provider key/model sections remain visible in Settings. This is intentional: some Kodi platforms do not refresh conditionally hidden settings immediately after the provider selector changes. Configure the section for the service you selected; credentials entered for the other services are left unused.

## Metadata and linked accounts

These services are optional. Trakt and MDBList appear under **Linked Accounts**; TMDB appears under **Metadata**.

### TMDB metadata

1. Create your personal TMDB API credential.
2. In **Settings → Metadata**, turn on **Improve matching with TMDB**.
3. Paste either a TMDB v3 API key or v4 read-access token.
4. Set a two-letter region such as `GB` and choose **Check TMDB**.

curatr caches a compact candidate pool related to films that fit your preferences. Cache reuse keeps requests low. If TMDB is temporarily unavailable, curatr records a warning in Kodi's log and continues with the normal AI + Trakt workflow.

### MDBList account lists

1. Open **Settings → Linked Accounts → MDBList**.
2. Turn on **Use films from MDBList** and enter your MDBList API key.
3. Choose **Choose my MDBList lists** and select up to eight movie lists.
4. Choose **Check MDBList**.

Selected lists are cached for six hours, combined and de-duplicated before a compact candidate pool is sent to the AI. A public MDBList link remains available as an optional fallback. MDBList is read-only: final list copies continue to use Trakt.

## Per-list artwork

Open **List settings → Artwork** to change the square icon and landscape fanart independently.

- **Automatic** chooses bundled artwork from the list name and prompt.
- **Choose a curatr icon/fanart** offers Action, Comedy, Crime, Drama, Horror, Romance, Sci-Fi, Fantasy, Thriller, Mystery, Western, Documentary, Animation, Mind-Bending, Superhero, Director and Actor designs.
- **Fanart style** switches bundled backgrounds between genre colours and monochrome.
- **Choose fanart from this list** uses artwork already attached to one of its films.
- **Search for a director or actor** uses TMDB when it is configured.
- **Custom image** lets Kodi browse for a user-supplied image.

Manual choices survive list regeneration. Automatic artwork follows later name or prompt edits. Kodi receives the square asset as both `icon` and `thumb`, and the 16:9 asset as both `landscape` and `fanart`, allowing skins and widgets to select the format they support.

## Trakt modes

### Kodi-only
No Trakt write connection is needed. curatr stores its generated lists locally.

### Public profile (read-only)
Enter a public Trakt username. curatr can use public ratings and watch history as preference data without writing anything to the account.

### Full Trakt connection
Choose **Connect / Reconnect Trakt** and authorize curatr with the QR/device code if you want curatr to create or update Trakt list copies.

AI refresh and Trakt update schedules are independent: an AI refresh changes the local recommendations; a Trakt update only copies the current local list to Trakt.

## Privacy and local data

curatr stores its state in Kodi's addon profile. AI API keys and Trakt login tokens live in Kodi settings/state on the device. The backup feature deliberately excludes AI API keys, Trakt login tokens and remote Trakt list IDs.

When curatr calls the AI provider you configured, it sends the recommendation prompt together with compact preference information and optional grounded candidate records needed to create recommendations. Your selected provider's terms and privacy policy apply. API keys for every AI and movie-source service are excluded from curatr backups and activity messages.

## Backup and restore

**My Lists → Backup & Restore** exports local list definitions, prompts, hidden choices and related curator data to JSON. Use this before major changes or when moving to another Kodi installation. Secrets are not included.

## Widget/plugin paths

Useful paths include:

```text
plugin://plugin.video.curatr/?action=all
plugin://plugin.video.curatr/?action=fresh
plugin://plugin.video.curatr/?action=random&limit=10
```

Individual saved lists are also exposed through the plugin interface. Playback is handed to the configured Kodi movie add-on when supported.

## Troubleshooting

- **No recommendations:** verify the selected AI provider and key, and make sure a Trakt profile source is configured if you want personalised results.
- **Trakt list copy will not update:** full Trakt authorisation is required for writes. A public username is read-only.
- **Artwork seems stale after an update:** restart Kodi once so the skin can refresh cached artwork references.
- **Background update failed:** check **Preferences & Activity → Recent Activity** for the stored error.
- **TMDB or MDBList check fails:** check the relevant key and selected lists, then try again. Leaving either service disabled returns curatr to its normal recommendation path.
- **Custom AI endpoint fails:** confirm that the URL includes `http://` or `https://`, the model ID is exact, and the server supports OpenAI-style structured Chat Completions.

## Licence

MIT. See `LICENSE.txt`.

## Third-party services

curatr is an independent community addon and is not affiliated with or endorsed by Trakt, TMDB, MDBList, OpenAI, Google, Anthropic, OpenRouter, Kodi or any playback addon it can hand off to.

This product uses the TMDB API but is not endorsed or certified by TMDB.

## 0.14.0

- Added native Anthropic Claude support.
- Added OpenRouter and custom OpenAI-compatible AI services.
- Added optional cached TMDB candidate grounding.
- Added optional MDBList candidate lists from a supplied list URL.
- Added connection tests, localised settings and failure-safe fallbacks.

## 0.14.1

- Removed logo and wordmark overlays from the background shown inside curatr.
- Kept branded artwork for the splash and Kodi add-on information page.
- Made every AI provider configuration section immediately available without reopening Settings.

## 0.15.0

- Added independent per-list square icons and landscape fanart.
- Added 17 uniform bundled artwork choices in colour and monochrome styles.
- Added automatic genre/person-type artwork suggestions, list-item fanart, TMDB person search and custom images.
- Added MDBList account browsing with multi-select, caching and de-duplication.
- Reorganised services into the simpler **Linked Accounts** and **Metadata** settings sections.


## 0.13.3

- Backup restore is now duplicate-aware across devices.
- Lists with matching local IDs update automatically.
- Same-name list conflicts offer Replace, Keep both, or Skip.
- Saved prompts are merged by ID/name instead of replacing the whole collection.
- Hidden-movie choices are merged and de-duplicated instead of replacing the whole collection.
- Restore now shows a summary of what was added, updated, kept, or skipped.

## 0.13.2

- Connect / Reconnect Trakt is available directly from the Trakt settings section.
- Managed lists can be deleted locally, or locally and from Trakt when a synced copy exists.
- Visible product naming is standardised on **curatr**. The legacy technical addon ID is retained so existing installations update in place.
