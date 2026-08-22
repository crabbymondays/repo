#!/usr/bin/env python3
import hashlib
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "repo"
ADDONS = (ROOT / "repository.curatr", ROOT / "plugin.video.curatr")


def version(addon_dir):
    return ET.parse(addon_dir / "addon.xml").getroot().attrib["version"]


def zip_addon(addon_dir):
    target_dir = OUTPUT / addon_dir.name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{addon_dir.name}-{version(addon_dir)}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(addon_dir.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
                archive.write(path, Path(addon_dir.name) / path.relative_to(addon_dir))


def publish_assets(addon_dir):
    """Publish metadata artwork beside the ZIP for Kodi repository browsing."""
    root = ET.parse(addon_dir / "addon.xml").getroot()
    target_dir = OUTPUT / addon_dir.name
    for node in root.findall("./extension[@point='xbmc.addon.metadata']/assets/*"):
        relative = Path((node.text or "").strip())
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            continue
        source = addon_dir / relative
        if not source.is_file():
            raise FileNotFoundError(f"Missing metadata asset: {source}")
        destination = target_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def main():
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir()
    roots = []
    for addon_dir in ADDONS:
        ET.parse(addon_dir / "addon.xml")
        zip_addon(addon_dir)
        publish_assets(addon_dir)
        roots.append(ET.parse(addon_dir / "addon.xml").getroot())
    repository_filename = f"repository.curatr-{version(ROOT / 'repository.curatr')}.zip"
    shutil.copy2(
        OUTPUT / "repository.curatr" / repository_filename,
        OUTPUT / repository_filename,
    )
    # Kodi's HTTP ZIP browser expects the bootstrap installer beside the
    # project's root index, matching established GitHub Pages Kodi repos.
    shutil.copy2(
        OUTPUT / "repository.curatr" / repository_filename,
        ROOT / repository_filename,
    )
    document = ET.Element("addons")
    for root in roots:
        document.append(root)
    ET.indent(document, space="    ")
    payload = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(document, encoding="utf-8") + b"\n"
    (OUTPUT / "addons.xml").write_bytes(payload)
    (OUTPUT / "addons.xml.md5").write_text(hashlib.md5(payload).hexdigest(), encoding="ascii")
    (OUTPUT / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>curatr repository</title></head>"
        "<body><h1>curatr repository</h1>"
        f"<a href='{repository_filename}'>{repository_filename}</a>"
        "</body></html>\n",
        encoding="utf-8",
    )
    (OUTPUT / "repository.curatr" / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>curatr repository</title></head>"
        "<body>"
        f"<a href='repository.curatr-{version(ROOT / 'repository.curatr')}.zip'>"
        f"repository.curatr-{version(ROOT / 'repository.curatr')}.zip</a>"
        "</body></html>\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
