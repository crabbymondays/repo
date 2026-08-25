# curatr

API credentials can be entered normally in Kodi Settings or imported from a
small one-line `.txt`/`.key` file using Kodi's own file browser. Imported keys
stay in Kodi's local add-on settings and are excluded from curatr backups.

curatr creates personalised Kodi movie lists from natural-language requests.
Users can choose lightweight Keyword Matching or an optional AI provider.

## Beta installation

1. In Kodi File Manager, add `https://crabbymondays.github.io/repo/` as a source.
2. Choose **Add-ons → Install from ZIP file**, open that source and install `repository.curatr-1.0.1.zip`.
3. Choose **Install from repository → curatr Repository → Video add-ons → curatr**.
4. Create a local list with Keyword Matching, or configure an AI provider for
   more nuanced requests.
5. curatr can use the local Kodi movie library for preferences. Optionally add
   Trakt for additional rating/history data and list syncing.

Trakt, TMDB and MDBList are optional. Without Trakt, curatr can combine the
user's prompt with local Kodi Library preferences and store lists locally.
Keyword Matching needs TMDB for catalogue discovery but does not use an AI key.
When MDBList is connected, Keyword Matching can also understand IMDb, Rotten
Tomatoes and MDBList rating/ranking phrases.

## Widget paths

```text
plugin://plugin.video.curatr/?action=lists
plugin://plugin.video.curatr/?action=all
plugin://plugin.video.curatr/?action=fresh
plugin://plugin.video.curatr/?action=random&limit=10
plugin://plugin.video.curatr/?action=folders
```

Each Widget Folder also exposes a stable path using its folder ID. Widget
Folders reference existing curatr lists, linked Trakt/MDBList account lists and
direct external `plugin://` paths;
they do not duplicate movie data or use AI tokens.

## Privacy and attribution

curatr sends prompts and preference information to services explicitly enabled by
the user. Keys and account tokens are stored in Kodi's local add-on data and are
excluded from curatr backups.

A plain-language **Privacy & Data** page is available inside curatr Settings.

This product uses the TMDB API but is not endorsed or certified by TMDB.

## Development

Push the unpacked source tree to `crabbymondays/repo`. The included workflow validates and
builds the Kodi repository into `repo/`, commits it to `main`, and creates ZIPs
that Kodi can install.
