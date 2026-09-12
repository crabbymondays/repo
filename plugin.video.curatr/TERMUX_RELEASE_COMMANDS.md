# Publish curatr from Termux

This workflow updates `plugin.video.curatr/` inside `crabbymondays/repo`.
This release also removes retired files and updates outdated repository
wording using the supplied cleanup helper. The existing publishing workflow
builds the current repository packages and metadata after you push.

Keep one clone at `~/curatr-repo`. If it does not exist yet, create it once:

```bash
cd ~
gh auth setup-git
git clone https://github.com/crabbymondays/repo.git curatr-repo
```

For each release, download `curatr-1.0.24-github-source.zip` to Android's
Downloads folder, then run:

```bash
(
set -e
cd ~/curatr-repo
git pull --rebase origin main
release_tmp="$(mktemp -d)"
unzip -oq ~/storage/downloads/curatr-1.0.24-github-source.zip -d "$release_tmp"
test -f "$release_tmp/plugin.video.curatr/addon.xml"
rsync -av --delete --exclude='.git' "$release_tmp/plugin.video.curatr/" plugin.video.curatr/
bash plugin.video.curatr/tools/clean_repository.sh
git status --short
rm -r -- "$release_tmp/plugin.video.curatr"
rmdir -- "$release_tmp"
)
```

The status includes the add-on changes plus the obsolete-file removals,
README, download-page wording, repository description and issue template.
Then publish them:

```bash
(
set -e
cd ~/curatr-repo
git add -- plugin.video.curatr
git commit -m "Release curatr 1.0.24"
git push origin main
)
```

The existing GitHub Action builds the Kodi repository after the source commit
is pushed. The `--delete` target above is only the add-on folder; never run it
against `~/curatr-repo/` itself because that would remove the repository files.

The subshell stops if a command fails. The first block removes only the
temporary extracted copy after a successful sync; the downloaded ZIP and clone
are retained.

The cleanup helper removes only the named retired files. It is safe to run
again and keeps any rewritten README if it no longer contains the old
pre-release placeholder. Existing repository installer paths remain valid.
