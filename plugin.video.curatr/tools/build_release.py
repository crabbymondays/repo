#!/usr/bin/env python3
"""Package the add-on for Kodi and the existing GitHub/Termux update workflow."""

import argparse
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL_EXCLUDES = {"ARTWORK.md", "README.md", "tools"}
TRANSIENT_DIRS = {".git", ".ruff_cache", ".pytest_cache", "__pycache__"}
RETIRED_ART_DIRS = {"menu_v5", "menu_landscape_v1", "keyword_controls_v5"}


def release_files():
    return [
        path for path in sorted(ROOT.rglob("*"))
        if path.is_file()
        and not TRANSIENT_DIRS.intersection(path.relative_to(ROOT).parts)
        and path.suffix not in (".pyc", ".pyo")
        and not (path.relative_to(ROOT).parts[:2] == ("resources", "media")
                 and path.relative_to(ROOT).parts[2] in RETIRED_ART_DIRS)
    ]


def build(output):
    output = output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("Keep release ZIPs outside the add-on directory.")
    manifest = ET.parse(ROOT / "addon.xml").getroot()
    addon_id, version = manifest.attrib["id"], manifest.attrib["version"]
    output.mkdir(parents=True, exist_ok=True)
    files = release_files()
    for name, source in ((f"plugin.video.curatr-{version}-install.zip", False), (f"curatr-{version}-github-source.zip", True)):
        target = output / name
        with tempfile.TemporaryDirectory(prefix=".curatr-package-", dir=output) as staging:
            pending = Path(staging) / name
            with zipfile.ZipFile(pending, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for path in files:
                    relative = path.relative_to(ROOT)
                    if source or relative.parts[0] not in INSTALL_EXCLUDES:
                        archive.write(path, Path(addon_id) / relative)
            with zipfile.ZipFile(pending) as archive:
                if archive.testzip() is not None:
                    raise ValueError(f"Corrupt release archive: {target}")
                info = ET.fromstring(archive.read(f"{addon_id}/addon.xml"))
                assert info.attrib["version"] == version
            os.replace(pending, target)
        print(f"{target}: {target.stat().st_size:,} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    build(parser.parse_args().output)
