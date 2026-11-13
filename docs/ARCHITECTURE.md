# Architecture

The desktop and inference processes have separate dependencies. The desktop uses
Qt, NumPy, ONNX Runtime and HTTP; model-specific Python environments live in the
application data directory. No inference module is imported into the Qt process.

## Code layout

`src/koeha` is split into feature packages. Each package covers one feature area and has
an `__init__.py` without imports, so importing one feature never loads another. Imports are absolute: `from koeha.<package>.<module> import name`.

| Package | Modules | Responsibility |
| --- | --- | --- |
| `core` | `paths`, `settings`, `events`, `i18n`, `runtime_tools` | Data directory, settings model, caption/audio events, interface language, external process launch |
| `app` | `controller` | Session ownership, background jobs and UI events |
| `audio` | `capture`, `vad`, `segmentation`, `pipeline` | WASAPI loopback, microphone, media decoding, speech detection, audio windows, recognition scheduling |
| `recognition` | `recognizer`, `worker` | Local HTTP inference and isolated Python inference |
| `models` | `catalog`, `engines`, `runtime_audit`, `runtime_locks/`, `engines_panel`, `page` | Pinned manifests, checksummed weights, runtime setup, integrity checks, Models page |
| `translation` | `translator`, `connection_profiles`, `prompt_profiles`, `connections_panel`, `prompt_profiles_panel`, `controls` | Translation requests, provider and prompt profiles, their panels and Live selectors |
| `overlay` | `captions`, `studio`, `color_control`, `page` | Caption rendering, floating window, Overlay Studio page |
| `history` | `store`, `page` | SQLite sessions, subtitle export, Sessions page |
| `live` | `page` | Live page: setup controls, session state and transcript view |
| `preferences` | `sections`, `hardware`, `page` | Settings page, category navigation, hardware inventory |
| `ui` | `main_window`, `components`, `widgets`, `theme`, `icons`, `motion`, `sidebar`, `assets/` | Window shell and the shared Qt kit |

### Working in parallel

- **Pages are mixins.** `ui/main_window.py` is only the shell (frame, navigation,
  banner, activity bar, close handling). Each page is a mixin in its feature
  package (`LivePage`, `ModelsPage`, `OverlayPage`, `SessionsPage`, `SettingsPage`,
  `TranslationControls`). A page change touches that feature's `page.py` only.
- **Dependency direction.** Features may import `core` and the shared `ui` kit
  (`components`, `widgets`, `theme`, `icons`, `motion`). Only `ui/main_window.py`
  and `app/controller.py` compose several features. `core.settings` imports the
  profile dataclasses from `translation`; keep those two modules free of other
  imports.
- **Isolated scripts.** `recognition/worker.py` and `models/runtime_audit.py` run
  inside engine runtimes, not the desktop process. They must not import `koeha`.
  The desktop build bundles them by path (`scripts/build_desktop.py`).
- **Shared hotspots.** `core/settings.py` (new fields and validation),
  `core/i18n.py` (Thai strings) and `ui/main_window.py` (registering a page) are
  edited by several features. Keep those edits small and merge them first.

## Delivery

Sentence mode re-decodes a growing speech window at the preview interval. A preview
may change. A speech pause or maximum duration finalizes the window. Repeated
prefixes are shown separately from speculative text. Chunk mode sends only finalized
windows, at the configured length or speech pause. These are buffered recognition
modes; the application does not retain native model encoder caches between windows.

Capture and recognition run independently. Preview work may be replaced by newer
work. Final windows remain queued; a realtime overflow stops the session with an
explicit error. File input waits for queue space. Stop ends capture, flushes the
last speech window, drains final recognition and finishes translation before the
session becomes idle.

Final translations run one at a time in submission order. Caption IDs preserve audio
order in the UI, history and exports. A draft cannot overwrite a final caption. History writes happen
before the UI notification, and a translation failure preserves the original text.
The visible session cache is bounded; saved history retains the complete session.

## Data and execution

`KOEHA_DATA_DIR` overrides the standard application data directory. It contains
settings, history, downloaded models, runtime executables and setup logs. API keys
live in the operating system credential store. Recognition is local. Translation
sends text only to the configured API. No automatic provider fallback is used.

Translation profiles store names, provider types, base URLs and model IDs in settings.
The active profile supplies the translation connection. LM Studio and Ollama use
their OpenAI-compatible APIs, with provider-specific local URL defaults. Model
discovery runs on a background thread with a bounded HTTP timeout; results are
discarded when the profile or its connection fields have changed. Credentials are
scoped by profile ID, and the legacy URL-scoped credential is migrated on first save.

System prompt profiles have a separate active ID and collection from connection
profiles. Their save action leaves connection drafts and credentials untouched.
The translator resolves the selected profile at session creation, replaces
the supported language placeholders, and appends the profile's scene context and glossary.
Legacy settings with shared scene context and glossary copy those fields into each
profile when loaded. The translator sends the result as the system message and
labels earlier source subtitles as reference. The final user message is the subtitle
to translate. Whisper uses scene context as a recognition hint at session start.
Qwen3 ASR receives audio and the selected recognition language.

Model setup installs a matched CUDA Torch/torchaudio pair and the bundled inference
dependency lock, then checks imports. NeMo uses Transformers 4.57.6 in its own
environment; the other Python models use Transformers 5.19.0. Qwen uses a pinned
Vulkan runtime and requires a working Vulkan driver. Models are prepared explicitly
from the Models page; setup downloads can be several gigabytes.

The model registry is sorted by display name for both the manager and Live selector.
Opening the application, changing the selected model and starting recognition never
prepare or download a missing model. Users explicitly start downloads from Models
or the CLI `--prepare` command. Whisper Large v3 and Large v3 Turbo share the
Transformers runtime and load only local files. Setup selects one safetensors
checkpoint and its tokenizer/processor assets, excluding duplicate FP32, PyTorch
and Flax weights. Whisper uses transcription mode with the chosen language or
automatic detection, FP16 on CUDA and FP32 on CPU.

Engine checks run separately from transcription and never download model weights.
Python audits run inside the isolated interpreter and check pinned package versions,
wheel RECORD file hashes, and framework imports. Repairs reinstall only the affected
locked distributions. Native repairs compare against the checksum-verified runtime
archive and replace only missing or changed files. Health reports block known-broken
engines, and maintenance cannot overlap a live session or model setup.

The PyInstaller desktop bundle includes the UI dependencies, assets, external worker
scripts, lock files and the standalone uv installer. Inference runtimes and models
stay in the existing application data directory. External Python workers launch with
the bundle's DLL search modifications removed to avoid mixing framework libraries.

Caption appearance uses the same cached glyph paths for the embedded preview and
floating window. Fill colors, rounded text strokes, panel transparency and borders
are drawn independently. Appearance edits update the window immediately and save
after a short debounce; they preserve the recognition configuration and upgrade
existing settings with defaults for newly added fields.
