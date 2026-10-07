# Publish curatr 1.0.42 from Termux

Download `curatr-1.0.42-github-source.zip` into Android’s Downloads folder.
Use the existing hosting checkout at `~/curatr-repo`:

```bash
(
set -e
cd ~/curatr-repo
test -z "$(git status --porcelain)" || { echo "This checkout has local changes. Review them before publishing."; exit 1; }
git pull --rebase origin main
curatr_release_tmp="$(mktemp -d)"
unzip -oq ~/storage/downloads/curatr-1.0.42-github-source.zip -d "$curatr_release_tmp"
test -f "$curatr_release_tmp/plugin.video.curatr/addon.xml"
rsync -av --delete --exclude='.git' "$curatr_release_tmp/plugin.video.curatr/" plugin.video.curatr/
bash plugin.video.curatr/tools/clean_repository.sh
git add -A -- plugin.video.curatr
git commit -m "Release curatr 1.0.42"
git pull --rebase origin main
git push origin main
rm -r -- "$curatr_release_tmp/plugin.video.curatr"
rmdir -- "$curatr_release_tmp"
)
```

The existing GitHub Action builds the Kodi repository after the push. This
command does not publish a separate GitHub Release or change the workflow.
The cleanup helper brings the small release-support folders into view when
the checkout is sparse, before cleaning the explicitly named retired files.
It keeps the current repository installer and published download paths.

`rsync --delete` targets only `plugin.video.curatr/`, not the repository root.
Kodi’s installed add-on data is separate and is not touched. If Git reports
uncommitted local work or a rebase conflict, the block stops so that work can be
reviewed instead of overwritten. Do not use a hard reset or force push to bypass
it. The downloaded source ZIP is retained.

For a local Kodi test, install `plugin.video.curatr-1.0.42-install.zip` using
**Add-ons → Install from ZIP file**. Uninstalling or clearing add-on data is not
required.

Version 1.0.42 repairs keyword candidate retrieval and filtering. Try
**Crime, Horror similar to Prisoners** and **Crime or Horror similar to Prisoners**.
The first genre tag must show **Crime and Horror** (the order may vary) and
require both genres; the second accepts either. An empty all-genre result must
explain the difference and suggest editing the tag. Filters are never broadened
automatically. Connector text such as **similar to** should have matching small
gaps before and after it, with no cropped words.

Try **horror by Stephen King** and **films written by Stephen King**. The latter
must show **written by**, and match writing credits rather than unrelated producer
jobs. **Directed by** and **starring** retain their roles. Each separately named
person must have a credit; members of a collective such as the Coen brothers share
one group. Creator/reference combinations check actual credit IDs against TMDB
recommendations, rather than intersecting two limited discover shortlists.

Use a quoted title such as **similar to "War and Peace"**, or **similar to
"The Thing" (1982)**. Title/year resolution must keep them literal. A missing
reference must be explained separately from strict filters. Try **Crime and
thriller without horror or romance**; both exclusions must remain active.
**IMDb 80%** means 8/10; **Rotten Tomatoes 8/10** means 80%.

Try **crime similar to Prisoners under 2 hours**, **British crime in French**,
and **crime similar to Prisoners sorted by release date**. Runtime, country,
language, dates and sorting must stay active. Ordering has an editable/removable
tag. **Crime seen twice** and **crime similar to Prisoners seen twice** must use
saved Trakt/Kodi history through TMDB, without requiring a Trakt lookup for each
film. An empty history snapshot must be explained explicitly.

Preview an empty request, and retry a request after a temporary TMDB failure.
The list settings must reopen with the same name, description, artwork, item
count, filters and Behaviour values. Change Request or remove a filter, then
preview again. Cancel must close without saving a failed list.

Watched/rated/hidden exclusions are applied before filling bounded candidate
pools, allowing later pages to contribute. Hidden titles with only TMDB IDs must
stay hidden, including when watched items are allowed. Refreshes prefer new
matches and reuse previous matches to fill remaining spaces. Reference queries
check at most five pages each, with up to 100 eligible candidates per media type.
Missing metadata checks and history-title lookups each have a 100-request limit;
a limit must be explained clearly, without an unexplained empty list. Exact
genre/credit/recommendation intersections can still legitimately have no overlap.

TV genre names use TMDB's broader categories; runtime filters use its episode
runtime fields. Reference-film, person, collection and rating-source filters
currently require Movies. Shows or mixed requests using those constraints must
explain the restriction rather than silently ignore it.

Refresh schedule stays outside Keyword Matching tags and the Plus menu. Auto
Refresh and Refresh Interval remain in the Behaviour tab. Set a custom interval
(for example 37 hours), edit a keyword filter and save: the interval and On/Off
value must stay unchanged. A newly edited request such as **episodes refreshed
every day** can still seed a daily Behaviour schedule without adding a tag.

Episode source and airing-window tags still have minus/edit controls and can be
restored through Plus. Remove them, save and reopen: they stay removed and the
all-shows/next-30-days defaults are shown. Cancelled edits retain the old filters.

When Plus wraps onto another row, both footer buttons, their borders, sheen and
shadows move below the panel with the same gap. Leaving edit mode shrinks the
panel and moves the buttons back together. Check both touch and D-pad focus.

Dynamic List preview and Back navigation fixes are retained. Add two sources
with known contents, preview their combined items, then close using both the
Close button and Back. The source editor and preview have separate native
window layouts; empty or failing sources explain the problem and keep the draft.

Keyword Matching's panel and filter tags have faint configurable sheen. The
existing layout, dotted list-card finish, borderless pen/check/minus controls,
proportional plus, solid footer buttons and right-hand menu icons are retained.
Check White and a coloured sheen on the Honor and check navigation with the D-pad.

Try **crime films not watched in the last two years** or add **seen twice** using
Viewing history. The tags should show the history filter and preserve it after
editing. The history duration must not become a release-year or release-date
filter. An unrelated description/appearance edit must not offer to refresh the
list simply because parser defaults were upgraded.

For episodes, try **my episodes airing in the next month, update daily and hide
watched items**. Use connected Trakt for the personal calendar, or switch the
source tag to Kodi library shows. Both creation methods save real-calendar rules;
episode refreshes do not ask AI to interpret the request again. Airing windows are
bounded to 93 days, list contents to 50 items. AI interpretation needs a configured
AI provider; Keyword Matching does not. An empty episode window can still be saved.
Upcoming episodes show information until they air; Kodi files play directly, and
compatible player definitions can use a direct episode route or series browser.

Items now has four choices including Episodes, so it uses a picker. On/Off fields
still toggle directly; Creation Method cycles between two values. Refresh Interval,
Auto Sync and other custom-value controls retain their editors. Daily refresh uses
the existing background service while Kodi runs and does not turn on Trakt sync.

The full automated checks cover provider-shaped end-to-end generation, local
filter/exclusion pagination, complete credit groups, HTTP errors, lookup limits,
sorting, semantic parsing, quoted-title matrices, genre exclusions, rating units,
invalid edits, empty-preview retries, history, two-source previews,
episode identity, calendars, watch state, playback routing, syncing, cache reuse,
source/XML/asset references and decorative geometry. Generated finish masks are
pruned automatically; there are no new Kodi runtime dependencies. Actual Kodi
rendering and live account/player integration still need the device checks above.

Validation: 229 automated checks passed (62 release, 68 feature, 39 parser, 38 pipeline, 22 finish).
All Python and XML files parsed, the cleanup shell passed syntax checking,
and artwork/native XML layouts match the validated previous build. Connector
spacing is checked geometrically; device rendering still needs confirmation. Live Kodi
rendering and account/player integration remain device checks.
