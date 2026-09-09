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

For each release, download `curatr-1.0.17-github-source.zip` to Android's
Downloads folder, then run:

```bash
cd ~/curatr-repo
git pull --rebase origin main
release_tmp="$(mktemp -d)"
unzip -oq ~/storage/downloads/curatr-1.0.17-github-source.zip -d "$release_tmp"
rsync -av --delete "$release_tmp/plugin.video.curatr/" ~/curatr-repo/plugin.video.curatr/
git status --short
```

Check that changes are confined to `plugin.video.curatr/`. Then publish them:

```bash
git add plugin.video.curatr
git commit -m "Release curatr 1.0.17"
git push origin main
```

The existing GitHub Action builds the Kodi repository after the source commit
is pushed. The `--delete` target above is only the add-on folder; never run it
against `~/curatr-repo/` itself because that would remove the repository files.

After the push, remove only the temporary extraction folder:

```bash
rm -rf "$release_tmp"
```
