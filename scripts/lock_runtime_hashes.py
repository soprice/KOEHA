"""Regenerate the hash-pinned files in src/koeha/models/runtime_locks.

Run after editing a runtime lock or build-constraints.txt:
    uv run python scripts/lock_runtime_hashes.py
Install and repair use the *.hashes.txt files with --require-hashes and --build-constraints.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LOCKS = Path(__file__).resolve().parents[1] / "src/koeha/models/runtime_locks"
TARGET = ["--python-version", "3.12", "--python-platform", "x86_64-pc-windows-msvc",
          "--generate-hashes", "--no-header", "--no-annotate", "--quiet"]
PYTORCH = ["--extra-index-url", "https://download.pytorch.org/whl/cu128", "--index-strategy", "unsafe-best-match"]
# Build dependencies come from PyPI only; the runtime locks also need the CUDA wheels.
SOURCES = {"transformers.txt": PYTORCH, "nemo.txt": PYTORCH, "build-constraints.txt": []}


def main():
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv was not found on PATH")
    for name, index in SOURCES.items():
        lock = LOCKS / name
        target = lock.with_name(lock.stem + ".hashes.txt")
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / target.name
            subprocess.run([uv, "pip", "compile", *index, *TARGET, str(lock), "-o", str(output)], check=True)
            body = output.read_text(encoding="utf-8")
        target.write_text(f"# Generated from {lock.name} by scripts/lock_runtime_hashes.py; do not edit.\n"
                          + body, encoding="utf-8", newline="\n")
        print(f"Wrote {target.relative_to(LOCKS.parents[3])}")


if __name__ == "__main__":
    main()
