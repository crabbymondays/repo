# curatr

curatr creates personalised Kodi movie lists from natural-language requests,
using the AI provider and optional catalogue accounts selected by the user.

## Beta installation

1. In Kodi File Manager, add `https://crabbymondays.github.io/repo/` as a source.
2. Choose **Add-ons → Install from ZIP file**, open that source and install `repository.curatr-1.0.1.zip`.
3. Choose **Install from repository → curatr Repository → Video add-ons → curatr**.
4. Open curatr Settings, select an AI provider and enter its API key.
5. Add a public Trakt username or connect a Trakt account.

TMDB and MDBList are optional.

## Widget paths

```text
plugin://plugin.video.curatr/?action=lists
plugin://plugin.video.curatr/?action=all
plugin://plugin.video.curatr/?action=fresh
plugin://plugin.video.curatr/?action=random&limit=10
plugin://plugin.video.curatr/?action=folders
```

Each Widget Folder also exposes a stable path using its folder ID. Widget
Folders can mix existing curatr lists, lists from the connected Trakt or
MDBList account, and direct external `plugin://` paths. They store references,
not duplicate lists, and never use AI tokens.

Open a linked provider list once, then choose that page as a skin widget to
show its films directly on Kodi's home screen. Linked-list paths remain stable.
curatr caches their contents for six hours and keeps the last successful copy
available if the provider is temporarily offline.

## Privacy and attribution

curatr sends prompts and preference information to services explicitly enabled by
the user. Keys and account tokens are stored in Kodi's local add-on data and are
excluded from curatr backups.

This product uses the TMDB API but is not endorsed or certified by TMDB.

## Development

Push the unpacked source tree to `crabbymondays/repo`. The included workflow validates and
builds the Kodi repository into `repo/`, commits it to `main`, and creates ZIPs
that Kodi can install.
