# KOEHA v0.2.0 — Live Speech & Translation Studio

KOEHA v0.2.0 brings optional singing-oriented recognition, more reliable Windows loopback captions, and clearer recognition timing. Windows 11 x64 is supported.

## Highlights

- **Speech / Music-Vocal profiles:** Speech remains the default; select Music/Vocal (3b) in Live → Setup → Recognition for singing and voices masked by music. Instrumental passages may still trigger false captions.
- **Playback pause handling:** System-audio loopback produces elapsed silence during idle capture so pending captions can finalize while the player is paused. Playback resumes under a new caption ID.
- **Sentence limit clarity:** Recognition settings disclose the effective six-second sentence ceiling without changing stored preferences. The existing 300 ms forced-cut overlap remains because removing it increased missing words in singing tests.
- **Installer preservation:** Includes the standalone installer and portable Windows bundle, with existing install/uninstall, data preservation, and optional signing support.

## Validation

- 201 automated tests and Ruff reported passing on the development Windows workstation.
- Speech and Music/Vocal default segmentation parity reported across 14,650 test windows.
- Actual Windows WASAPI loopback was previously exercised on an RTX 5080. The later idle-loopback fix passed automated tests; short-pause hardware confirmation of that fix remains recommended.

## Known limitations

- Music/Vocal can caption instrumental audio; recognition accuracy depends on the mix.
- Sentence-mode hard cuts may still duplicate some words. Adaptive token budgets and quiet-boundary splitting remain experimental, not enabled by default.
- Windows OS sleep/suspend lasting more than 10 seconds can affect wall-time caption alignment, unlike normal playback pauses.
- Offline model weights and inference runtimes are not bundled. Download only the models you need from KOEHA's Models page after installation.

## Windows downloads

- **KOEHA-Setup.exe** — Windows x64 installer
- **KOEHA-Windows-x64.zip** — portable desktop bundle
- **SHA256SUMS.txt** — SHA-256 hashes for both downloads
- **signature-status.txt** — installer Authenticode verification status at build time

The installer is **not claimed to be code-signed** unless the signature status explicitly reports a valid signature. Windows may warn about unsigned software.

This release stays a **draft** until the repository owner manually publishes it.
