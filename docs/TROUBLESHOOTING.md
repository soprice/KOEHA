# Troubleshooting KOEHA

This guide covers the current Windows 11 x64 desktop release. Start with the [README](../README.md) for setup and supported recognition models.

## Installer is blocked or Windows displays a warning

Download `KOEHA-Setup.exe` from the official [GitHub Releases](https://github.com/soprice/KOEHA/releases). Verify its SHA-256 hash against the release's `SHA256SUMS.txt`:

```powershell
Get-FileHash .\KOEHA-Setup.exe -Algorithm SHA256
Get-AuthenticodeSignature .\KOEHA-Setup.exe | Format-List Status,SignerCertificate
```

A matching hash alone does **not** mean the installer is code-signed or safe. Check the release's `signature-status.txt`; unsigned builds can trigger Windows warnings. Do not disable Microsoft Defender or SmartScreen to work around a warning. If the download or signature is unexpected, stop and report it.

## KOEHA opens, but no recognition model is ready

Open **Models**, choose a recognizer, and click **Download**. Model weights and inference runtimes are intentionally excluded from the installer and are never downloaded automatically when opening KOEHA or selecting a model. Downloads can require several gigabytes.

If an engine is marked damaged or missing, open **Settings → Engines & Frameworks**, run **Check**, and review the result before using **Repair**. Repairs may require network access; a read-only check does not download weights.

## Live shows no captions

1. Select the correct input source: **System audio** for Windows playback, or **Microphone** for physical input.
2. Confirm the selected device still exists and is receiving audio. Re-select it if Windows audio devices changed.
3. Confirm that the selected recognition model is **Ready** and the spoken language is supported.
4. Try **Speech** for ordinary dialogue. **Music/Vocal** is opt-in for singing; it can also produce false captions from instrumental audio.
5. Check whether audio is audible in the chosen device and whether the Live level indicator responds.

System audio capture uses Windows WASAPI loopback; KOEHA does not currently isolate one individual application from the rest of system playback.

## Captions are late, repeated, or missing words

Use a faster recognition model or **Finalized Chunks** if live previews are too costly. Sentence mode re-decodes the growing utterance, and its effective hard ceiling is six seconds; some forced cuts can duplicate or miss words despite the existing 300 ms overlap. Lowering or raising VAD sensitivity can affect both missed speech and false positives.

For playback pause/resume issues, include the duration of the pause, selected audio device, model, delivery mode, and whether the caption finalized before playback resumed. Hardware pause/resume behavior may vary.

## Translation is missing or fails

1. Start your LM Studio, Ollama, or OpenAI-compatible server.
2. Open **Settings → Translation API**, select the correct connection profile, and use **Load models** to check connectivity.
3. Choose a chat-capable model and select a target language in Live.
4. Verify the server URL and any required API key. Keys are stored in Windows Credential Manager, not `settings.json`.
5. For LM Studio on another computer, enable **Serve on Local Network** in LM Studio's server settings and use the displayed server address with `/v1` in KOEHA. If **Require Authentication** is enabled, enter an API token. HTTP sends the token and translation text unencrypted.

A successful model-list request does not guarantee that the model supports chat completions. ASR continues even when a translation request fails.

If the API server only supports HTTP and requires a key, you can use SSH port forwarding.
For example, if the API listens on port 1234 on the SSH server, run this on the KOEHA computer:

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:12345:127.0.0.1:1234 user@server
```

Keep the SSH session open and set the KOEHA base URL to `http://127.0.0.1:12345/v1`.
Replace `user@server` with your SSH account and host, and port 1234 with the API's port.

## GPU not used, or recognition runs on CPU

The Qwen3-ASR runtime uses a pinned Vulkan build; Python recognition models use their isolated CUDA/PyTorch environments when available, otherwise CPU. GPU detection and display are not the same as verified inference placement. Check the selected model, its runtime health, the GPU driver, and KOEHA's recognition log. Avoid assuming that a GPU shown in the Hardware page is necessarily running the selected model.

## Settings, history, or models seem missing

By default, application data is under `%LOCALAPPDATA%\KOEHA`; `KOEHA_DATA_DIR` overrides this path. Updates should preserve settings and model downloads. Do not delete the data directory during troubleshooting unless you intentionally want to remove those assets.

If settings cannot be parsed, KOEHA can fall back to defaults and attempt to preserve the previous file as `settings.invalid.json`. Back up the directory before making changes.

## Reporting a reproducible bug

Open a GitHub issue with the KOEHA release version, Windows build, selected ASR model, audio source/device, delivery mode, steps to reproduce, expected and actual behavior, and whether it occurs without translation. Attach only sanitized logs. **Do not upload API keys, private audio, copyrighted recordings, identifiable transcripts, or full local paths containing personal information.**

For developers, see [Contributing](../CONTRIBUTING.md) and [Validation](VALIDATION.md).
