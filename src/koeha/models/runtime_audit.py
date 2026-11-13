"""Standard-library-only integrity audit, executed by each isolated runtime."""
import argparse
import base64
import hashlib
import importlib
import importlib.metadata
import json
import re
from pathlib import Path


def requirements(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, version = line.split("==", 1)
            result[name] = version
    return result


def inspect_packages(expected, distribution=importlib.metadata.distribution):
    issues, checked = [], 0
    for name, version in expected.items():
        try:
            package = distribution(name)
        except importlib.metadata.PackageNotFoundError:
            issues.append(dict(kind="missing", package=name, detail="Package is missing"))
            continue
        if package.version != version:
            issues.append(dict(kind="version", package=name,
                               detail=f"Expected {version}; found {package.version}"))
            continue
        files = package.files
        if not files:
            issues.append(dict(kind="manifest", package=name, detail="Package file manifest is missing"))
            continue
        bad, count = [], 0
        for entry in files:
            if str(entry).endswith((".pyc", ".pyo")):
                continue
            path = Path(package.locate_file(entry))
            checked += 1
            try:
                if not path.is_file():
                    problem = "Missing file: " + str(entry)
                elif entry.hash:
                    digest = hashlib.new(entry.hash.mode)
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                            digest.update(chunk)
                    actual = base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode()
                    problem = "Damaged file: " + str(entry) if actual != entry.hash.value else ""
                else:
                    problem = ""
            except (OSError, ValueError) as error:
                problem = f"Cannot read {entry}: {error}"
            if problem:
                count += 1
                if len(bad) < 8:
                    bad.append(problem)
        if bad:
            issues.append(dict(kind="files", package=name, detail="\n".join(bad), count=count))
    return dict(issues=issues, checked_files=checked)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("runtime", choices=["transformers", "nemo"])
    args = parser.parse_args()
    result = inspect_packages(requirements(args.lock))
    if not result["issues"]:
        modules = ["torch", "torchaudio", "transformers"]
        if args.runtime == "nemo":
            modules.append("nemo.collections.asr.models")
        for module in modules:
            try:
                importlib.import_module(module)
            except Exception as error:
                name = "nemo-toolkit" if module.startswith("nemo.") else re.split(r"[.]", module)[0]
                result["issues"].append(dict(kind="import", package=name, detail=str(error)))
    args.report.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
