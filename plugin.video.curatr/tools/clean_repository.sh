#!/usr/bin/env bash
# Remove retired release material from the hosting repository.
set -euo pipefail

repo_dir="$(git rev-parse --show-toplevel)"
cd "$repo_dir"
test -f plugin.video.curatr/addon.xml
if ! git cat-file -e HEAD:tools/build_repo.py 2>/dev/null; then
  echo "This checkout is not the Curatr hosting repository (tools/build_repo.py is not tracked)." >&2
  exit 1
fi
# Materialise only the small release-support folders in a sparse checkout.
if [ "$(git config --bool core.sparseCheckout || true)" = true ]; then
  git sparse-checkout add tools repository.curatr .github
  if [ "$(git config --bool core.sparseCheckoutCone || true)" != true ]; then
    git sparse-checkout add '/*.*'
  fi
fi
if [ ! -f tools/build_repo.py ]; then
  git restore --worktree --ignore-skip-worktree-bits -- tools/build_repo.py
fi

git rm --ignore-unmatch -- \
  BETA_TESTING.md \
  curatr-0.23.9-beta25-complete-github.zip \
  plugin.video.curatr-0.23.9-beta25-install.zip \
  tools/generate_keyword_controls.py

if test -f README.md && grep -Fq 'curatr is currently being prepared for its full release.' README.md; then
  cat > README.md <<'README'
# curatr

A Kodi add-on for personalised movie and TV lists, with Keyword Matching,
optional AI generation, Trakt syncing and custom artwork.

Install the repository from [the download page](https://crabbymondays.github.io/repo/),
then install curatr in Kodi to receive updates.

- [Add-on documentation](plugin.video.curatr/README.md)
- [Release notes](plugin.video.curatr/CHANGELOG.md)
- [Publishing from Termux](plugin.video.curatr/TERMUX_RELEASE_COMMANDS.md)
README
fi

if test -f index.html; then
  sed -i 's/receive beta updates/receive updates/g' index.html
fi
if test -f repository.curatr/addon.xml; then
  sed -i 's/curatr beta releases/curatr releases/g' repository.curatr/addon.xml
fi
if test -f .github/ISSUE_TEMPLATE/bug_report.yml; then
  sed -i \
    -e 's/^name: Beta bug report$/name: Bug report/' \
    -e 's/Report a reproducible curatr beta problem/Report a reproducible curatr problem/' \
    -e 's/^title: "\[Beta\] "$/title: "[Bug] "/' \
    -e 's/^labels: \["bug", "beta"\]$/labels: ["bug"]/' \
    .github/ISSUE_TEMPLATE/bug_report.yml
fi

for path in README.md index.html repository.curatr/addon.xml .github/ISSUE_TEMPLATE/bug_report.yml; do
  if test -f "$path"; then
    git add -- "$path"
  fi
done
