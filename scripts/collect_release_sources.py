"""Build the corresponding-source asset for the Windows release."""

import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts/release-sources.json"
OUTPUT = ROOT / "dist/KOEHA-Third-Party-Source.zip"


def download(entry, staging):
    name = entry["name"]
    if Path(name).name != name:
        raise ValueError(f"Invalid archive filename {name!r}")
    destination = staging / name
    request = urllib.request.Request(entry["url"], headers={"User-Agent": "KOEHA release source collection"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as file:
                shutil.copyfileobj(response, file)
            with destination.open("rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
            if digest != entry["sha256"]:
                raise ValueError(f"SHA-256 mismatch for {name}")
            return destination
        except (OSError, ValueError):
            if attempt == 2:
                raise


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    OUTPUT.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=OUTPUT.parent, prefix="source-staging-") as directory:
        staging = Path(directory)
        staged_zip = staging / OUTPUT.name
        with ThreadPoolExecutor(max_workers=4) as pool, zipfile.ZipFile(staged_zip, "w") as archive:
            archive.write(MANIFEST, MANIFEST.name)
            archive.write(ROOT / "docs/THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
            for path in pool.map(lambda entry: download(entry, staging), manifest["archives"]):
                archive.write(path, path.name)
                print(f"Verified {path.name}", flush=True)
        staged_zip.replace(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
