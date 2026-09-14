# Publish curatr 1.0.27 from Termux

Download `curatr-1.0.27-github-source.zip` into Android’s Downloads folder.
Use the existing hosting checkout at `~/curatr-repo`:

```bash
(
set -e
cd ~/curatr-repo
test -z "$(git status --porcelain)" || { echo "This checkout has local changes. Review them before publishing."; exit 1; }
git pull --rebase origin main
curatr_release_tmp="$(mktemp -d)"
unzip -oq ~/storage/downloads/curatr-1.0.27-github-source.zip -d "$curatr_release_tmp"
test -f "$curatr_release_tmp/plugin.video.curatr/addon.xml"
rsync -av --delete --exclude='.git' "$curatr_release_tmp/plugin.video.curatr/" plugin.video.curatr/
bash plugin.video.curatr/tools/clean_repository.sh
git add -A -- plugin.video.curatr
git commit -m "Release curatr 1.0.27"
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

For a local Kodi test, install `plugin.video.curatr-1.0.27-install.zip` using
**Add-ons → Install from ZIP file**. Uninstalling or clearing add-on data is not
required.
