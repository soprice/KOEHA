# KOEHA v0.2.4

- Windows downloads include third-party license notices. Qt Virtual Keyboard
  has been removed from the bundle.
- The Parakeet worker verifies the SHA-256 of its pinned checkpoint before
  loading it.
- Updated the isolated recognition runtimes and regenerated their hash locks.
  Existing model weights are kept when repairing the runtimes.
- Updated the Live and Translation prompts screenshots.

## Windows x64 downloads

- `KOEHA-Setup.exe` installs or upgrades KOEHA. Close KOEHA before upgrading.
- `KOEHA-Windows-x64.zip` is the portable bundle. Extract it into a fresh folder
  when upgrading a portable copy.
- `KOEHA-Third-Party-Source.zip` contains corresponding source archives and
  build recipes for Qt, Qt for Python, and the FFmpeg libraries shipped with PyAV.
- `SHA256SUMS.txt` lists the package checksums. `signature-status.txt` reports
  the installer's Authenticode status.

[Third-party notices](https://github.com/soprice/KOEHA/blob/v0.2.4/docs/THIRD_PARTY_NOTICES.md)
