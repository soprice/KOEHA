# Validation

## Windows and runtime checks · v0.2.4

The local Windows check passed 302 tests and Ruff. The installer lifecycle
check covered setup, update, uninstall, and data preservation. The portable
ZIP contained the notices and source manifest and omitted Qt Virtual Keyboard.
The release source collection verified all 23 archive hashes.

On Windows 11 with an NVIDIA RTX 5080, Fun-ASR, Nemotron and Parakeet loaded
with the updated runtime locks and produced finalized text in both delivery
modes using the [Japanese pronunciation sample](https://commons.wikimedia.org/wiki/File:Ja-LessonIntroKonPron-practice1.ogg).
This replay checked inference and caption delivery. It did not measure
transcription accuracy or exercise physical audio capture.

## Windows checks · v0.2.3

The Windows release workflow for v0.2.3 passed 301 tests and Ruff, built the installer and portable ZIP, and verified
setup, upgrade, uninstall, and user-data preservation. Translation tests cover
HTTP with API keys over a LAN, the warning shown by the app, profile-specific
model discovery, and switching prompt profiles during a session. Overlay tests
cover clearing both immediate and delayed captions when Live stops.

The v0.2.3 installer reported `NotSigned`. Consult `signature-status.txt` and
`SHA256SUMS.txt` in the [current release](https://github.com/soprice/KOEHA/releases/latest)
for signature status and package integrity.

## Initial runtime replay · 2026-11-05

Validated on Windows 11 x64 with an NVIDIA RTX 5080, 2026-11-05.

- 26 automated tests passed: audio stop, segmentation, queue pressure, decoder
  failure, file pacing, download integrity, model readiness, translation ordering,
  Unicode exports, navigation, settings, caption outlines, color selection and persistence.
- Real inference passed for all four models in both sentence and chunk mode, with
  a 7.224-second Japanese speech sample replayed at normal speed. Sentence mode
  delivered 5–6 previews and one final line; chunk mode delivered two final lines.
- Both Python runtimes reported Torch/torchaudio `2.11.0+cu128` with CUDA available.
- A wheel installed into a separate environment opened the UI from an empty working
  directory. Packaged logos, icons and inference locks were present.
- Native Windows UI captures covered all six pages in English and Thai, plus narrow
  window layouts. The overlay, navigation and transcript layouts were also exercised
  by UI tests.
- Idle WASAPI loopback capture stopped when requested without an incoming buffer.

Archived machine-readable results: [runtime](validation/runtime.json) and
[environment](validation/environment.json). The environment snapshot records
19 tests for that capture.

## Caption appearance · 2026-11-06

Seven additional checks cover rendered outline pixels, invalid color input,
cancelled color selection, settings compatibility, multilingual wrapping, autosave
and reopening, and consistent preset/reset behavior in preview and floating captions.
The application was visually inspected on Windows, including a custom HEX text
color reflected in the preview and a saved appearance status. English, Thai and
narrow-window screenshots were inspected. The installed wheel was checked separately
for the appearance editor and packaged indicator artwork.

This is a functional check, not an accuracy benchmark or a prolonged live-session
test. External translation APIs were checked using deterministic HTTP fixtures;
no paid API call was made. Physical microphone speech, device hot-plug and per-app
audio isolation were not validated. Capture supports system output and microphone,
not selecting an individual application.

## Windows desktop and engine maintenance · 2026-11-06

The windowed PyInstaller build includes the UI dependencies, icons, standalone uv,
worker scripts and runtime locks. Its executable is checked from an unrelated working
directory using the screenshot option. The per-user installer script is syntax checked.
The installed-program and desktop shortcut actions remain user initiated.

Settings categories are rendered in English and Thai at normal and narrow window sizes.
Read-only checks against existing local assets passed for Silero VAD (1 file),
llama.cpp (53 files), and Transformers (24,008 package files plus framework imports).
Selective repair is tested with missing files and damaged-package fixtures; those checks
do not download model weights or reinstall the user's healthy runtimes.

The Whisper generation path is checked offline using a small randomly initialized
Whisper network in the existing inference runtime. Its supported decoding calls and
language selection pass without downloading pretrained weights. This is not a speech
accuracy test for the full Large v3 or Turbo checkpoints.

## Interface snapshot · 2026-11-07

Ruff and all 106 automated tests passed on Windows. The README captures show Live
and Overlay in English and Thai, all six Models entries, and the System prompts
editor. The Live and Overlay captures use sample captions and a demonstration
translation profile.

## Build and verification commands

```powershell
./scripts/check.ps1
uv sync --locked --extra dev --extra desktop
uv run --no-sync python scripts/build_desktop.py
uv run --no-sync python scripts/check_desktop.py
Get-AuthenticodeSignature -LiteralPath dist/KOEHA-Setup.exe
Get-FileHash -Algorithm SHA256 -LiteralPath dist/KOEHA-Setup.exe,dist/KOEHA-Windows-x64.zip
uv run python scripts/validate_runtime.py speech.wav --models all --realtime --report runtime-report.json
```

The runtime report records the sample hash and timings without bundling recordings
or recognized text. Models and native runtimes are downloaded separately.
