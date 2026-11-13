<p align="center">
  <img src="docs/images/koeha.svg" alt="KOEHA logo" width="96" height="96">
</p>

<h1 align="center">KOEHA</h1>

<p align="center">
  <strong>Live Speech &amp; Translation Studio</strong><br>
  <em>Every voice, understood.</em>
</p>

<p align="center">
  Turn system audio and microphone speech into live transcripts,<br>
  translated dialogue, and floating subtitles in one desktop workspace.
</p>

<p align="center">
  <img src="docs/images/compatibility.svg" alt="Windows 11 x64 · Python 3.12–3.13 · Local ASR · MIT license" width="480" height="28">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> &nbsp;·&nbsp;
  <a href="#features">Features</a> &nbsp;·&nbsp;
  <a href="#recognition-models">Models</a> &nbsp;·&nbsp;
  <a href="#screenshots">Screenshots</a> &nbsp;·&nbsp;
  <a href="#development">Development</a>
</p>

<p align="center">
  <img src="docs/images/live.png" alt="KOEHA Live workspace showing audio setup, translation style, and sample captions in Japanese and English" width="1100">
</p>

## Features

| | What you can do |
| :--- | :--- |
| **Live transcription** | Capture Windows system playback or a microphone and recognize speech locally. |
| **Two delivery modes** | See revisable text in Live Preview or output completed segments in Finalized Chunks. |
| **Optional translation** | Save connection profiles for LM Studio, Ollama, or an OpenAI-compatible API. Choose an independent translation style and add scene context or a glossary. |
| **Floating subtitles** | Customize fonts, original/translation colors, text outlines, and background borders in a movable overlay. |
| **Sessions & exports** | Keep local transcript history and export TXT, JSON, SRT, or VTT. |
| **English & Thai** | Choose either interface language and switch between Side by side, Stacked, and Original only transcript layouts. |

Speech recognition runs on your computer after model setup. Translation is optional;
when enabled, recognized text is sent to the API you configure. A local API can keep
translation on the computer too.

## Quick start

### Windows desktop application

Get the installer or portable ZIP from [GitHub Releases](https://github.com/soprice/KOEHA/releases).
See the [third-party notices](docs/THIRD_PARTY_NOTICES.md) for bundled libraries and corresponding sources.

The desktop bundle runs through **KOEHA.exe**, with no separate Python or uv
installation needed to open the app. Keep its `_internal` folder beside the executable.
Double-click **Install-KOEHA.bat** in the extracted bundle to install for your Windows
account and add desktop and Start menu shortcuts. Settings, models, profiles and
history remain in `%LOCALAPPDATA%\KOEHA` when the application is updated.
Uninstall from Windows Settings or run `Uninstall-KOEHA.exe` from the installed
folder. Models, runtimes, settings and history are kept unless you select them
for removal.

To build the bundle from source, run `./scripts/build_desktop.ps1`. It produces
`dist/KOEHA/KOEHA.exe`, `dist/KOEHA-Windows-x64.zip`, and `dist/KOEHA-Setup.exe`. Model weights and inference
engines are not bundled; downloads start only after an explicit action in the app.

### Run from source

Use **Windows 11 x64**, **Python 3.12 or 3.13**, and **uv**.
The first model setup needs internet access and several gigabytes of free space.

```powershell
git clone https://github.com/soprice/KOEHA.git
cd KOEHA
uv sync --locked
uv run koeha
```

You can also double-click **[Start-KOEHA.bat](Start-KOEHA.bat)** to install the
application dependencies and launch the studio.

### Download a model

Open **Models**, choose a recognizer, and click **Download**. KOEHA downloads
the weights and installs the required runtime only after you click. Opening the
app, selecting a model, or starting Live never downloads a missing model automatically.
Wait for **Ready** before starting. Models are listed alphabetically in this page
and in the Live model selector.

### Start a session

1. In **Live**, choose system audio or microphone, your audio device, and the spoken language.
2. Select **Live Preview** or **Finalized Chunks**. Adjust timing under **Settings → Recognition**.
3. To translate, create a connection profile in **Settings → Translation API**. In **Live → Setup**, select the profile and target language, then click **Load models** to choose a model from that server. Choose a translation style below Setup.
4. Click **Start Live**. Use **Floating captions** to open the overlay and **Sessions** to view saved transcripts.

Press `Ctrl+Enter` to start or stop live transcription and `Ctrl+B` to toggle the sidebar.

### Recognition profiles and continuous playback

In **Live → Setup → Recognition**, choose **Speech (dialogue)** (default) or **Music/Vocal (singing)**. Music/Vocal is opt-in and may produce captions for instrumental passages. System-audio loopback now finalizes an active caption when playback pauses, even if the device stops delivering audio; on resume a new caption begins. In sentence mode, the effective hard ceiling remains **6 seconds** (shorter saved limits still apply). The existing **300 ms** context overlap remains enabled to protect words at boundaries. Results can vary with accompanying music; compare both profiles for your recording.

## Delivery modes

| Mode | While speech arrives | When output becomes final |
| :--- | :--- | :--- |
| **Live Preview** | Revisable previews show the growing utterance. | At a speech pause or the maximum sentence length. |
| **Finalized Chunks** | Audio accumulates into a bounded segment. | At the configured chunk length or a speech pause. |

Both modes work with every recognition model below. They use buffered audio
windows; they do not retain native model encoder caches between windows.

## Settings and engine repair

Settings has separate **General**, **Recognition**, **Translation API**, **Translation prompts**, **Sessions**,
**Engines & Frameworks**, and **Hardware** categories. In narrow windows the category
menu becomes a selector above the content.

In **Settings → Engines & Frameworks**, use **Check** to inspect an installed engine
without downloading anything. The report names missing, damaged or mismatched
packages and files. Python engines are checked against pinned package versions and
the hashes in their installed file records; llama.cpp is checked against its pinned
runtime archive. Full checks can take several minutes and can be cancelled.

Use **Repair** after reviewing the results. Repair checks again, replaces only the
affected Python packages or native files, and checks the result. If a runtime is
absent, **Download** installs that runtime without downloading model weights. A
missing native verification archive is fetched only during an explicit repair.
Maintenance is blocked while a transcription session is running. Engines found
to be damaged cannot be used until repaired or checked successfully again.

**Hardware** shows CPU, RAM, NVIDIA GPU/VRAM/driver when detected, and free storage.
It reports actual system information; each inference engine chooses GPU acceleration
automatically.

## Translation connections

In **Settings → Translation API**, click **New profile**, give it a name,
and choose a provider. KOEHA fills in the local server URL; you can change it for
a server on another computer.

| Provider | Default base URL |
| :--- | :--- |
| LM Studio | `http://127.0.0.1:1234/v1` |
| Ollama | `http://127.0.0.1:11434/v1` |
| OpenAI-compatible API | Your server's compatible API base URL |

Start the server and load or download a chat model, then click **Load models**.
Choose a model from the list or type its exact ID. Enter an API key if your server
requires authentication, then click **Save profile**. Choose the connection profile
and target language in **Live** before starting. Profiles can be renamed, edited
or deleted; edits are saved with **Save profile** or **Save settings**.

Profiles keep the provider, URL and model between launches. Each profile has its
own API key in Windows Credential Manager, including profiles sharing one URL;
keys are never written to the settings JSON. Existing connection settings become
the **Default** profile, and its existing key is carried over when you first save.

> [!NOTE]
> **HTTP and HTTPS for translation**
>
> - **HTTP on the same computer** works with an optional API key via `localhost`, `127.0.0.1`, or `[::1]`.
> - **HTTP to another computer**, including LM Studio across a LAN, works with or without an API key. KOEHA displays a warning because any key and translation text travel unencrypted. The warning appears in **Translation API** and in **Live** when translation is enabled.
> - **HTTPS** encrypts the connection and supports API keys.

Both local services use their OpenAI-compatible endpoints. See the
[LM Studio server documentation](https://lmstudio.ai/docs/developer/openai-compat/models)
and [Ollama compatibility documentation](https://docs.ollama.com/api/openai-compatibility)
for server setup. Loading the model list checks access to the server; a successful
translation also requires a chat-capable model.

## Translation prompt profiles

Use **Translation style** below the Live setup panels to select a prompt profile.
Click **Edit prompts**, or open **Settings → Translation prompts**, to create, rename,
edit, delete and save prompt profiles. Each profile has its own system prompt, scene
context and glossary. **New profile** copies all three fields from the selected profile.
Default, Natural dialogue, Faithful translation and Concise subtitles
are provided and can be edited like any other profile.

Prompt profiles are saved independently from connection profiles. **Save translation
prompts** saves the profiles and active selection. Unsaved edits are retained when
switching between profiles.

Use `{target_language}` and `{source_language}` inside a prompt for the languages
selected in Live. Other braces, including literal JSON, remain unchanged. If a prompt
omits `{target_language}`, the selected output language is appended automatically.
Scene context gives the model background for the dialogue. The glossary lists
preferred translations for names and terms, one `original = translation` pair per line.
Both are optional. KOEHA appends the selected profile's context and glossary to its
system prompt and sends the result as the API's `system` message. Recognized
dialogue is sent as a separate `user` message. Choose **Translation style** during
a Live session to apply it to new translation requests. Requests already submitted
finish with the style they started with.

## Caption appearance

Use **Overlay Studio → Caption timing** to choose how floating captions update.
**Immediate** shows the latest caption, including live previews. **Delayed** buffers
finalized captions for the configured number of seconds and shows them in order,
with at least 1.5 seconds per caption. If translation is still pending when a caption
appears, KOEHA shows the original and adds the translation if it arrives while that
caption is visible. You can change timing during a session.

Open **Overlay** to adjust the text and see each change in the preview:

- Choose a font and size, with separate colors for original dialogue and translation.
- Enable **Text outline**, then set each line's outline color and its thickness.
- Customize the background color, opacity, border color, border thickness, and corner radius.
- Start with **Soft Glass**, **Solid**, or **Minimal**, or use **Reset appearance** to restore the defaults.

Click a color swatch to open the picker, or enter a HEX color directly. Appearance
changes apply to the floating captions immediately and are saved automatically.
See the **[caption appearance guide](docs/CAPTION_STYLE.md)** for all controls.

## Recognition models

| Model | Runtime | Language notes |
| :--- | :--- | :--- |
| **Fun-ASR-Nano-2512** | Isolated Transformers environment | Chinese, English, and Japanese. |
| **Nemotron 3.5 ASR Streaming · 0.6B** | Isolated Transformers environment | Language support follows the model's published configuration. |
| **Parakeet TDT-CTC · 0.6B JA** | Isolated NeMo environment | Japanese. |
| **Qwen3 ASR · 1.7B** | Local Vulkan runtime | Multilingual; select the spoken language or automatic detection. |
| **Whisper Large v3** | Isolated Transformers environment | Multilingual transcription, including automatic language detection. |
| **Whisper Large v3 Turbo** | Isolated Transformers environment | Multilingual transcription, including automatic language detection. |

Model downloads use pinned revisions. Python inference environments are separate
from the desktop environment. Qwen requires a working Vulkan driver; Python models
use CUDA when available and otherwise run on CPU.

The Whisper entries use [Large v3](https://huggingface.co/openai/whisper-large-v3)
and [Large v3 Turbo](https://huggingface.co/openai/whisper-large-v3-turbo). Their
selected checkpoints are about 3.09 GB and 1.62 GB, respectively, plus the shared
inference runtime. Each download includes only one checkpoint format. Both work
with Live Preview and Finalized Chunks; translation still uses the selected
translation connection profile.

See **[validation results](docs/VALIDATION.md)** for the tested environment,
real-model checks, and the limits of that validation.

> [!NOTE]
> **Known issues under investigation (singing & musical audio)**
>
> - **Inconsistent VAD on singing vocals** – Silero VAD may fail to distinguish vocals backed by accompaniment or sustained notes from background noise, causing parts of the singing to be dropped before reaching ASR.
> - **Segmentation is not suited for music** – Audio segmentation relies on speech pause timing and fixed duration limits, often cutting vocals mid-word or mid-phrase.
> - **Instrumental accompaniment interferes with recognition** – Musical instruments overlap with vocal frequency bands, causing the model to misrecognize words, skip lyrics, or hallucinate text that was not sung.
> - **ASR models are not designed specifically for singing** – Whisper, Qwen3-ASR, and the other models in KOEHA focus on spoken speech and can struggle with pitch glides, sustained notes, tempo changes, and melody-driven pronunciation.
> - **No vocal separation** – Mixed audio is sent directly to ASR without isolating vocals from background accompaniment prior to transcription.

## Screenshots

<details>
<summary><strong>Explore the Thai interface, Models, Translation prompts, and caption styling</strong></summary>

### Thai interface

![KOEHA Live workspace in Thai showing sample captions in Japanese and Thai](docs/images/live-th.png)

### Model manager

![KOEHA Models page listing all six local recognition models and their download buttons](docs/images/models.png)

### Translation style

![KOEHA Translation prompts with the Concise subtitles profile selected](docs/images/system-prompts.png)

### Caption appearance

![KOEHA caption style inspector with separate text colors, outlines, and a live preview](docs/images/overlay-studio.png)

![KOEHA caption appearance controls in Thai](docs/images/overlay-studio-th.png)

</details>

## Data & preferences

| Data | Storage |
| :--- | :--- |
| Settings, models, runtimes, and session history | `%LOCALAPPDATA%/KOEHA` |
| Translation API keys | Operating system credential store |
| Custom application data directory | Set `KOEHA_DATA_DIR` before launching |

Change the interface language in **Settings**, save, and restart KOEHA to apply it.

## Command line

Prepare a model or transcribe a file without opening the desktop:

```powershell
uv run koeha --prepare qwen3-asr
uv run koeha --transcribe sample.wav --mode chunk --output captions.srt
```

## Development

Install the development tools and run the complete check:

```powershell
uv sync --locked --extra dev
./scripts/check.ps1
```

To exercise real inference in both delivery modes, provide a short Japanese speech sample:

```powershell
uv run python scripts/validate_runtime.py speech.wav --models all --realtime --report runtime-report.json
```

| Reference | Contents |
| :--- | :--- |
| [Architecture](docs/ARCHITECTURE.md) | Module ownership, audio queues, model runtimes, and persistence. |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Installer warnings, missing captions, runtime setup, translation, and GPU checks. |
| [Contributing](CONTRIBUTING.md) | Development workflow, tests, design rules, and pull request checklist. |
| [Security](SECURITY.md) | Vulnerability reporting, data handling, download integrity, and signing status. |
| [Validation](docs/VALIDATION.md) | Automated checks, model replay results, and standalone package validation. |
| [License](LICENSE) | MIT license for the application. Models and dependencies retain their respective licenses. |
| [Model licenses](docs/MODEL_LICENSES.md) | Publisher terms for the pinned model downloads. |

---

<p align="center">
  <strong>KOEHA</strong><br>
  <em>Every voice, understood.</em>
</p>
