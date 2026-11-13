# KOEHA v0.2.2

## Live translation and floating captions

- Switch **Translation style** during Live. The new style applies to the next translation request.
- Choose **Immediate** or **Delayed** in **Overlay Studio → Caption timing**. Delayed mode buffers finalized captions for the time you set and displays them in order. If a translation is still pending, the original appears first.

## Settings and installation

- **Translation API** stores each profile's API key in Windows Credential Manager. Switching profiles retains their individual keys.
- HTTP API endpoints can use authentication. KOEHA warns when translation text and API keys may travel unencrypted to another machine.
- **Translation prompts** groups the system prompt, scene context, and glossary on one page with a single Save button.
- Windows **Installed apps** shows the estimated size of the installed application after installing or upgrading with this build.

## Windows x64 downloads

- `KOEHA-Setup.exe` — installer. To upgrade, close KOEHA and install to the same folder.
- `KOEHA-Windows-x64.zip` — portable package.
- `SHA256SUMS.txt` — checksums for both downloads.
- `signature-status.txt` — Authenticode status of the installer.
