# Publish curatr from Termux

This workflow is for Android devices using Termux. The source ZIP updates only
`plugin.video.curatr/` inside `crabbymondays/repo`; existing GitHub Pages,
repository metadata, release ZIPs and Actions workflows remain untouched.

Download `curatr-1.0.1-github-source.zip` to Android's Downloads folder, then
run:

```bash
cd ~
git clone https://github.com/crabbymondays/repo.git curatr-upload
release_tmp="$(mktemp -d)"
unzip -q ~/storage/downloads/curatr-1.0.1-github-source.zip -d "$release_tmp"
rsync -av "$release_tmp/plugin.video.curatr/" ~/curatr-upload/plugin.video.curatr/
cd ~/curatr-upload
git status --short
```

Check that changes are confined to `plugin.video.curatr/`. Then publish them:

```bash
git add plugin.video.curatr
git commit -m "Release curatr 1.0.1"
git push origin main
```

The existing GitHub Action builds the Kodi repository after the source commit
is pushed. Do not use `rsync --delete` at the repository root: it can remove
`.git`, GitHub Pages, Actions and generated repository files.

After GitHub Actions finishes successfully, remove the temporary upload folder:

```bash
cd ~
rm -rf ~/curatr-upload
```
