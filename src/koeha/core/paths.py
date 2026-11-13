import os
from pathlib import Path


def data_root() -> Path:
    override = os.environ.get("KOEHA_DATA_DIR")
    root = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "KOEHA"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()
