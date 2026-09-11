# Publish curatr from Termux

This workflow is for Android devices using Termux. The source ZIP updates only
`plugin.video.curatr/` inside `crabbymondays/repo`; existing GitHub Pages,
repository metadata, release ZIPs and Actions workflows remain untouched.

Keep one clone at `~/curatr-repo`. If it does not exist yet, create it once:

```bash
cd ~
gh auth setup-git
git clone https://github.com/crabbymondays/repo.git curatr-repo
```

For each release, download `curatr-1.0.19-github-source.zip` to Android's
Downloads folder, then run:

```bash
(
set -e
cd ~/curatr-repo
git pull --rebase origin main
release_tmp="$(mktemp -d)"
unzip -oq ~/storage/downloads/curatr-1.0.19-github-source.zip -d "$release_tmp"
test -f "$release_tmp/plugin.video.curatr/addon.xml"
rsync -av --delete --exclude='.git' "$release_tmp/plugin.video.curatr/" plugin.video.curatr/
git status --short
rm -r -- "$release_tmp/plugin.video.curatr"
rmdir -- "$release_tmp"
)
```

Check that changes are confined to `plugin.video.curatr/`. Then publish them:

```bash
(
set -e
cd ~/curatr-repo
git add -- plugin.video.curatr
git commit -m "Release curatr 1.0.19"
git push origin main
)
```

The existing GitHub Action builds the Kodi repository after the source commit
is pushed. The `--delete` target above is only the add-on folder; never run it
against `~/curatr-repo/` itself because that would remove the repository files.

The subshell stops if a command fails. The first block removes only the
temporary extracted copy after a successful sync; the downloaded ZIP and clone
are retained.
