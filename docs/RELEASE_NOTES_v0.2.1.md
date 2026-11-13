# KOEHA v0.2.1 — Security & Stability Patch

This patch release includes security-hardening changes, without enabling experimental ASR tuning. Windows 11 x64.

## Security and reliability fixes

- **Translation credentials:** Prevent sending API keys to non-loopback HTTP endpoints, including at profile save time. Authenticated remote servers must use HTTPS. Unauthenticated LAN HTTP remains available; loopback requests bypass system proxies.
- **Verified runtime packages:** Pin SHA-256 hashes for CUDA PyTorch, Transformers/NeMo dependencies and NeMo's source-build backend. Reuse cached packages only after hash verification.
- **Legacy runtime migration:** Identify installs made before hash verification and offer a clean, verifiable Repair. A working older runtime stays usable until the user chooses Repair.
- **Safe maintenance:** Block targeted Repair, clean reinstall and Delete while recognition is using the runtime. Coordinate recognition startup with maintenance across multiple KOEHA windows.
- **Crash and interruption recovery:** Keep a restorable runtime backup until a fresh install passes integrity and import checks; recover interrupted installs and deletes safely.

## Validation

- Windows CI for PR #5: **281 tests passed**, Ruff passed.
- RTX 5080 integration validation of the PR source: Whisper Large v3 Turbo and Parakeet JA each produced two offline captions on CUDA; CUDA tensor checks, hash-pinned installs, legacy Repair and crash recovery passed.
- The previously reproducible live-targeted-Repair bug is fixed: Repair reports `RuntimeInUse` without changing packages, receipt or logs while Whisper is decoding. Repair succeeds and restores the original package SHA-256 after the worker stops.
- Release build additionally requires locked application dependencies, full Windows tests, Ruff, installer lifecycle checks, a real **v0.2.0 → v0.2.1** upgrade retaining settings/models, and SHA-256 verification of staged assets.

## Compatibility and limitations

- Existing user settings, histories and downloaded model data remain under `%LOCALAPPDATA%\KOEHA`. Model weights and CUDA runtimes are not included in the installer.
- Some existing runtime installations may appear **Unverified**; run Engines & Frameworks → Check, then Repair if prompted. Do not run Repair during recognition.
- Remote HTTP profiles with API keys now fail validation; switch to HTTPS or remove the key.
- Existing dependency vulnerability advisories (including torch/NeMo ecosystem packages) remain under separate review. Runtime package hash verification does not itself fix upstream vulnerabilities.
- LM Studio/Ollama integration was not available for the GPU retest; real HTTP socket tests covered loopback, LAN and blocked remote HTTP-with-key behavior.
- The installer may be unsigned. Check `signature-status.txt`; Windows SmartScreen may show a warning for unsigned builds.

## Windows downloads

- `KOEHA-Setup.exe` — Windows x64 installer
- `KOEHA-Windows-x64.zip` — portable package
- `SHA256SUMS.txt` — expected hashes of both downloads
- `signature-status.txt` — Authenticode status recorded during the build

The v0.2.0 release and tag remain available; this is a new patch release.
