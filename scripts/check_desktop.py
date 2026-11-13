"""Exercise the built Windows setup and uninstaller in a disposable directory."""

import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path


def main():
    setup = Path(__file__).resolve().parents[1] / "dist/KOEHA-Setup.exe"
    if not setup.is_file():
        raise SystemExit("Build the desktop package before running this check.")

    with zipfile.ZipFile(setup.with_name("KOEHA-Windows-x64.zip")) as archive:
        names = archive.namelist()
        assert "KOEHA/THIRD_PARTY_NOTICES.md" in names
        assert "KOEHA/licenses/LGPL-3.0-only.txt" in names
        assert "KOEHA/licenses/Python-LICENSE.txt" in names
        assert "KOEHA/release-sources.json" in names
        assert not any("virtualkeyboard" in name.lower() for name in names)

    with tempfile.TemporaryDirectory(prefix="koeha-setup-") as workspace:
        root = Path(workspace)
        target = root / "Shared folder & 100%"
        data = root / "Data"
        target.mkdir()
        data.mkdir()
        env = dict(os.environ, KOEHA_DATA_DIR=str(data))
        options = ["--silent", "--no-shortcuts", f"--target={target}"]

        collision = target / "README.txt"
        collision.write_text("Existing readme", encoding="utf-8")
        assert subprocess.run([setup, *options], env=env, timeout=120).returncode != 0
        assert collision.read_text(encoding="utf-8") == "Existing readme"
        assert not (target / "KOEHA.exe").exists()
        collision.unlink()

        unrelated = target / "_internal" / "personal.txt"
        unrelated.parent.mkdir()
        unrelated.write_text("Keep this", encoding="utf-8")
        settings = data / "settings.json"
        settings.write_text("{}", encoding="utf-8")
        exported = data / "export.txt"
        exported.write_text("Keep this too", encoding="utf-8")

        subprocess.run([setup, *options], env=env, check=True, timeout=120)
        retired_dlls = [
            target / "_internal/PySide6/Qt6VirtualKeyboard.dll",
            target / "_internal/PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
        ]
        for dll in retired_dlls:
            dll.parent.mkdir(parents=True, exist_ok=True)
            dll.write_bytes(b"old bundled library")
        subprocess.run([setup, *options], env=env, check=True, timeout=120)
        uninstaller = target / "Uninstall-KOEHA.exe"
        assert (target / "KOEHA.exe").is_file()
        assert (target / "THIRD_PARTY_NOTICES.md").is_file()
        assert all(not dll.exists() for dll in retired_dlls)
        assert not any("virtualkeyboard" in path.name.lower() for path in (target / "_internal/PySide6").rglob("*"))
        subprocess.run([uninstaller, *options], env=env, check=True, timeout=120)
        deadline = time.monotonic() + 45
        while uninstaller.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not uninstaller.exists(), "The uninstaller did not finish removing its own executable"
        assert not (target / "KOEHA.exe").exists()
        assert not (target / "README.txt").exists()
        assert unrelated.read_text(encoding="utf-8") == "Keep this"
        assert settings.is_file()

        subprocess.run([setup, *options], env=env, check=True, timeout=120)
        subprocess.run([setup, "--uninstall", "--remove-data", *options], env=env, check=True, timeout=120)
        assert not uninstaller.exists()
        assert not settings.exists()
        assert unrelated.is_file() and exported.is_file()
    print("Setup, update, uninstall, and data preservation passed.")


if __name__ == "__main__":
    main()
