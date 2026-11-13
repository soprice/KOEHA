# Security review

## Current release behavior

KOEHA v0.2.3 and later allow API keys over HTTP on the LAN and show a warning in
Settings and Live. Translation requests still reject redirects. See
[Security](../SECURITY.md) for the current transport policy.

For v0.2.4, the Windows bundle switches to PySide6 Essentials and drops Qt
Virtual Keyboard. Releases include third-party license notices and a source
archive. See [Third-party notices](THIRD_PARTY_NOTICES.md).

The runtime update raises Transformers' setuptools pin to 81.0.0 and NeMo's
Hydra pin to 1.3.6. The Parakeet worker checks its exact checkpoint filename
and pinned SHA-256 before invoking NeMo. The older audit below describes the
versions and behavior at the time of PRs #5 and #12.

Runtime upgrade limits observed on Windows x64:

- The current CUDA 12.8 PyTorch and torchaudio pair requires setuptools below
  82. The compatible pair is still needed for GPU recognition.
- Lightning 2.6.6 failed NeMo's import check because the installed NVIDIA
  OneLogger adapter uses an incompatible `save_checkpoint` signature.
- NeMo 2.7.3 requires fsspec 2024.12.0. Upgrading that package requires a
  compatible NeMo upgrade and inference testing.

## Runtime advisory scope

The desktop dependency lock and the isolated recognition runtime locks are
reviewed separately. Runtime pins may remain affected by advisories where
upstream fixes cannot yet be installed with the compatible model stack.
KOEHA downloads model files from pinned revisions, verifies their hashes
when downloading, and disables remote code for Transformers loading. The
Parakeet worker also verifies its checkpoint before each load.

See [Security](../SECURITY.md) for vulnerability reporting and
[Validation](VALIDATION.md) for tested combinations.
