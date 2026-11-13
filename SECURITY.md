# Security policy

KOEHA is a Windows desktop application for local speech recognition with optional translation through a user-configured API.

## Supported versions

Security fixes are intended for the latest published release and the current `main` branch. Older releases may not receive fixes. Check [Releases](https://github.com/soprice/KOEHA/releases) for the latest published version.

## Reporting a vulnerability

**Do not publish exploit details, credentials, private recordings, or proof-of-concept payloads in a public issue.**

If GitHub's private vulnerability reporting feature is available for this repository, use the **Security** tab and select **Report a vulnerability**. If that option is not available, contact the repository maintainer through an existing private channel; do not assume that a public issue is confidential. No response-time SLA or dedicated security email is currently documented.

Include the affected version or commit, Windows version, relevant component, reproduction steps, expected versus observed behavior, and impact. Redact API keys, bearer tokens, private URLs, personal data, and transcripts before sharing logs or screenshots.

## Security boundaries

- **Recognition:** ASR runs locally after the selected model and inference runtime have been downloaded.
- **Translation:** When enabled, recognized text, the selected prompt, and limited preceding dialogue context are sent to the configured translation endpoint. Review its privacy policy before selecting a remote provider.
- **Credentials:** Translation API keys are stored in the operating-system credential store. For HTTP endpoints on another computer, KOEHA warns that keys and translation text travel unencrypted. Use HTTPS to encrypt the connection.
- **Downloads:** KOEHA pins model/runtime versions and verifies known checksums. Third-party model and dependency licenses remain separate from KOEHA's MIT license.
- **Installer:** A published executable is not automatically Authenticode-signed. Inspect `signature-status.txt` in a release and verify `SHA256SUMS.txt` before installation. A checksum confirms file integrity relative to the published manifest, not publisher identity.
- **Data:** Settings, downloaded models, runtimes, and session history are stored under `%LOCALAPPDATA%\KOEHA` unless `KOEHA_DATA_DIR` is set.

## Practical precautions

Download releases from the project's GitHub Releases page. Keep Windows and GPU drivers updated. Prefer `127.0.0.1` for local translation servers, and use HTTPS and authentication when connecting to a remote endpoint. Avoid exposing local model APIs directly to untrusted networks.

Security reports are reviewed by the maintainers as capacity permits; this document does not promise a particular response or remediation timeframe.
