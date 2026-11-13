# Contributing to KOEHA

Thanks for helping improve KOEHA. Please keep changes small, reproducible, and compatible with Windows 11 x64.

## Before opening a pull request

1. Search existing issues and pull requests before starting overlapping work.
2. Open an issue to discuss significant changes to audio capture, segmentation, model runtimes, packaging, or the user interface.
3. Create a branch from the current `main`. Do not commit generated model weights, recordings, API keys, virtual environments, or installer binaries.
4. Explain the problem, the proposed behavior, and the evidence for the change.

## Local development

Prerequisites: Windows 11 x64, Python 3.12 or 3.13, and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/soprice/KOEHA.git
cd KOEHA
uv sync --locked --extra dev
./scripts/check.ps1
```

For a desktop packaging change, install the desktop extra and exercise the installer on Windows:

```powershell
uv sync --locked --extra dev --extra desktop
./scripts/build_desktop.ps1
uv run python scripts/check_desktop.py
```

The desktop build needs the Windows .NET Framework C# compiler used by `scripts/build_desktop.py`. The installer lifecycle test creates disposable test directories. Review its behavior before running it.

Pull requests run **Windows CI** and attach the tested installer and portable ZIP.
From `main`, run **Windows release** to test upgrading from the latest published
installer. To publish a new version, bump the app and installer versions, add
`docs/RELEASE_NOTES_vX.Y.Z.md`, and select **publish** when running the workflow.
If you update Qt, Qt for Python, or PyAV, update `scripts/release-sources.json` and
verify that the release includes the matching third-party source archive.

When branch protection is available, require the **validate** job from **Windows
CI** before merging changes to `main`. Use your GitHub-provided noreply address
as the commit email if you want to keep your personal address out of new commits.

## Design rules

- Keep feature code in the appropriate `src/koeha/` package. Read [Architecture](docs/ARCHITECTURE.md) before cross-package refactoring.
- Recognition workers and model runtimes are separate processes; do not import model frameworks into the Qt process.
- Speech is the default recognition profile. Music/Vocal is opt-in; do not silently change the speech baseline.
- Keep preview results replaceable, but never silently discard finalized captions. Preserve audio timing and sample ownership across segmentation changes.
- Do not automatically download model weights or inference runtimes. Model downloads require an explicit user action.
- Preserve user settings, models, history, and unrelated files during install, update, and uninstall.
- Keep API keys in the operating-system credential store, not settings JSON, logs, tests, or screenshots.
- Do not add third-party model or runtime assets to the repository without checking their distribution terms.

## Testing and evidence

Run `./scripts/check.ps1` and describe its result. Add regression tests for behavior changes. UI changes should be checked in English and Thai, including narrow windows where relevant. For WASAPI, GPU, or model-specific changes, clearly distinguish mocked/unit tests from real-hardware checks.

Real ASR benchmarking is optional and should not be run automatically for every PR. When needed, use the documented commands in [Validation](docs/VALIDATION.md) and report the test environment, model, timing, and limitations. Do not commit private recordings, recognizable lyrics, stream titles, or copyrighted test transcripts.

## Pull request checklist

- [ ] Scope and motivation are explained.
- [ ] Tests and Ruff pass, or failures are disclosed.
- [ ] New behavior has a regression test or a documented manual test.
- [ ] Windows installer and data preservation are checked if affected.
- [ ] README, troubleshooting, or architecture documentation is updated if behavior changes.
- [ ] No secrets, private audio, generated binaries, or model weights are included.

Please report suspected security vulnerabilities privately when possible; see [Security](SECURITY.md) rather than posting exploit details in a public issue.
