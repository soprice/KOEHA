# KOEHA v0.2.5

- FloaSub now offers Smart Dual, Committed Only, and Live Single playback.
  Smart Dual shows finalized subtitles alongside live recognition and optional
  draft translations. Overlay Studio adds controls for translation wait,
  reading time, sync delay, auto-hide, and fit-to-text width.
- Application audio captures the selected application's process tree on
  Windows build 20348 or later. System audio and microphone remain available.
- Final translations run in order. Draft and final requests can reference up
  to four earlier finalized source subtitles. Translation connection profiles
  support Temperature and Top P, with a Server default option for each.
- Live draft toggles invalidate pending drafts. On Stop, unfinished final
  translations receive an ordered cancellation after the grace period.
- Existing settings migrate automatically, including legacy overlay timing.

## Windows x64 downloads

- `KOEHA-Setup.exe` installs or upgrades KOEHA. Close KOEHA before upgrading.
- `KOEHA-Windows-x64.zip` is the portable bundle. Extract it into a fresh folder
  when upgrading a portable copy.
- `KOEHA-Third-Party-Source.zip` contains corresponding source archives and
  build recipes for the bundled libraries.
- `SHA256SUMS.txt` lists the package checksums. `signature-status.txt` reports
  the installer's Authenticode status.

See the [FloaSub guide](FLOASUB.md) and [third-party notices](THIRD_PARTY_NOTICES.md).
