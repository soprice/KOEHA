"""Local integrity checks and selective repair of inference engines, never model weights."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from koeha.core.runtime_tools import uv_executable
from koeha.models.catalog import (
    RUNTIME_SHA,
    RUNTIME_TAG,
    VAD_REVISION,
    VAD_SHA,
    WORKER_VERSION,
    Progress,
    RuntimeBusy,
    _run_cancellable,
    backup_path,
    download,
    hashed_lock,
    install_worker_locked,
    is_hash_verified,
    move_aside,
    recover_runtime,
    require_idle_runtime,
    runtime_lock,
    runtime_pip_args,
    sha256,
    trash_path,
    worker_python,
    write_receipt,
)
from koeha.models.runtime_audit import requirements


@dataclass(frozen=True)
class Engine:
    id: str
    name: str
    version: str
    description: str


ENGINES = (
    Engine("llama", "llama.cpp · Vulkan", RUNTIME_TAG, "Qwen3 ASR · GPU acceleration with Vulkan"),
    Engine("nemo", "NeMo · PyTorch CUDA", "2.7.3", "Parakeet · NVIDIA NeMo and PyTorch"),
    Engine("transformers", "Transformers · PyTorch CUDA", "5.19.0", "Fun-ASR, Nemotron and Whisper · shared framework"),
    Engine("vad", "Silero VAD · ONNX", VAD_REVISION[:8], "Shared voice activity detector for all models"),
)


@dataclass
class EngineReport:
    engine_id: str
    issues: list[dict] = field(default_factory=list)
    checked_files: int = 0
    # False for a Python runtime installed before hash-verified installs (or without a receipt).
    # It can be healthy and still run; Repair replaces it with a verified clean install.
    verified: bool = True
    # True when another KOEHA process held the runtime lock, so nothing was checked or written.
    busy: bool = False

    @property
    def healthy(self):
        return not self.issues and not self.busy


def installed(root, engine_id):
    if engine_id == "vad":
        return (root / "vad/silero.onnx").is_file()
    if engine_id == "llama":
        return (root / "runtimes/llama/llama-server.exe").is_file()
    return worker_python(root, engine_id).is_file()


def _notify(progress, task, detail=""):
    progress(Progress("Checking runtime", task, detail))


def _native_manifest(root, cancel):
    archive = root / "downloads" / f"llama-{RUNTIME_TAG}.zip"
    if not archive.is_file() or sha256(archive, cancel) != RUNTIME_SHA:
        return None
    result = {}
    with zipfile.ZipFile(archive) as bundle:
        server = next((n for n in bundle.namelist() if PurePosixPath(n).name == "llama-server.exe"), None)
        if not server:
            raise RuntimeError("Runtime archive contains no llama-server.exe")
        prefix = PurePosixPath(server).parent
        for item in bundle.infolist():
            name = PurePosixPath(item.filename)
            if item.is_dir() or not name.is_relative_to(prefix):
                continue
            relative = name.relative_to(prefix)
            destination = (root / "runtimes/llama" / str(relative)).resolve()
            if not destination.is_relative_to((root / "runtimes/llama").resolve()):
                raise RuntimeError("Unsafe runtime archive path")
            digest = hashlib.sha256()
            with bundle.open(item) as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                    if cancel():
                        raise InterruptedError("Runtime check cancelled")
                    digest.update(chunk)
            result[str(relative)] = (item.filename, digest.hexdigest())
    return result


def _check_engine(root, engine_id, progress=lambda _: None, cancel=lambda: False):
    root = Path(root)
    report = EngineReport(engine_id)
    _notify(progress, engine_id)
    if cancel():
        raise InterruptedError("Runtime check cancelled")
    if engine_id == "vad":
        path = root / "vad/silero.onnx"
        if not path.is_file() or sha256(path, cancel) != VAD_SHA:
            report.issues.append(dict(kind="file", detail="Missing or damaged: vad/silero.onnx"))
        report.checked_files = int(path.is_file())
        return report
    if engine_id == "llama":
        manifest = _native_manifest(root, cancel)
        if manifest is None:
            report.issues.append(dict(kind="manifest", detail="Pinned runtime archive is missing or damaged; repair will fetch it"))
            return report
        for name, (_, expected) in manifest.items():
            path = root / "runtimes/llama" / name
            _notify(progress, name)
            if not path.is_file() or sha256(path, cancel) != expected:
                report.issues.append(dict(kind="file", path=name, detail="Missing or damaged: " + name))
            report.checked_files += 1
        if report.healthy:
            try:
                result = subprocess.run([str(root / "runtimes/llama/llama-server.exe"), "--version"],
                    capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode:
                    report.issues.append(dict(kind="launch", detail="Engine cannot start; check Vulkan drivers and runtime DLLs"))
            except (OSError, subprocess.TimeoutExpired) as error:
                report.issues.append(dict(kind="launch", detail=str(error)))
        return report
    if engine_id not in {"nemo", "transformers"}:
        raise ValueError("Unknown inference engine")
    report.verified = False
    python = worker_python(root, engine_id)
    try:
        # The caller holds runtime_lock. After a crash mid-reinstall, put the last working
        # runtime back (or drop a stale backup) before judging health.
        recover_runtime(root, engine_id)
    except RuntimeError as error:
        report.issues.append(dict(kind="interpreter", detail=str(error)))
        return report
    if not python.is_file():
        report.issues.append(dict(kind="interpreter", detail="Python runtime is missing"))
        return report
    lock = Path(__file__).parent / "runtime_locks" / f"{engine_id}.txt"
    marker = python.parent.parent / "installed.json"
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    if (not isinstance(record, dict) or record.get("version") != WORKER_VERSION
            or record.get("lock_sha256") != sha256(lock)):
        report.issues.append(dict(kind="receipt", detail="Installation receipt is missing or out of date"))
    report.verified = is_hash_verified(engine_id, record)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="koeha-engine-check-") as temp:
        output = Path(temp) / "report.json"
        with (root / f"{engine_id}-check.log").open("w", encoding="utf-8") as log:
            try:
                code = _run_cancellable([str(python), "-I", str(Path(__file__).with_name("runtime_audit.py")),
                    str(lock), str(output), engine_id], log, cancel,
                    lambda elapsed: _notify(progress, "Checking packages and file hashes", f"{int(elapsed)}s"))
                if code or not output.is_file():
                    raise RuntimeError(f"Runtime check failed; see {engine_id}-check.log")
                result = json.loads(output.read_text(encoding="utf-8"))
                report.issues.extend(result["issues"])
                report.checked_files = result["checked_files"]
            except (OSError, ValueError, RuntimeError) as error:
                report.issues.append(dict(kind="interpreter", detail=str(error)))
    return report


PYTHON_ENGINES = {"nemo", "transformers"}


def _environment(root, engine_id):
    return worker_python(Path(root), engine_id).parent.parent


def check_engine(root, engine_id, progress=lambda _: None, cancel=lambda: False):
    """Check an engine and record the result in its health.json.

    A Python runtime is checked while holding its lock. If another KOEHA process holds it
    (installing, checking, repairing or deleting), the report is busy and nothing on disk is
    created or changed: no recovery, no audit, no health.json."""
    if engine_id in PYTHON_ENGINES:
        with ExitStack() as held:
            try:
                held.enter_context(runtime_lock(_environment(root, engine_id)))
            except RuntimeBusy:
                return EngineReport(engine_id, verified=False, busy=True)
            return _check_and_record(root, engine_id, progress, cancel)
    return _check_and_record(root, engine_id, progress, cancel)


def _check_and_record(root, engine_id, progress, cancel):
    report = _check_engine(root, engine_id, progress, cancel)
    folder = Path(root) / ("vad" if engine_id == "vad" else f"runtimes/{engine_id}")
    folder.mkdir(parents=True, exist_ok=True)
    state = folder / "health.json"
    partial = state.with_suffix(".tmp")
    partial.write_text(json.dumps(dict(healthy=report.healthy, issues=report.issues,
                                      checked_files=report.checked_files, verified=report.verified)),
                       encoding="utf-8")
    os.replace(partial, state)
    return report


def repair_engine(root, engine_id, progress=lambda _: None, cancel=lambda: False):
    """Always check first, then change only the packages or files found to be unhealthy.

    A Python runtime that was never hash-verified is rebuilt from scratch instead, even when
    healthy, because a targeted repair would leave its other packages unverified.

    For a Python runtime the lock is held from the first check to the final check, so no other
    KOEHA process can install, repair or delete it in between (raises RuntimeBusy otherwise)."""
    root = Path(root)
    if engine_id in PYTHON_ENGINES:
        with runtime_lock(_environment(root, engine_id)):
            return _repair_python_locked(root, engine_id, progress, cancel)
    before = check_engine(root, engine_id, progress, cancel)
    if before.healthy:
        return before
    def notify(**update):
        progress(Progress("Repairing runtime", update.get("task", engine_id),
                          update.get("detail", ""), update.get("fraction")))
    if engine_id == "vad":
        download(f"https://raw.githubusercontent.com/snakers4/silero-vad/{VAD_REVISION}/"
                 "src/silero_vad/data/silero_vad.onnx", root / "vad/silero.onnx", VAD_SHA, notify, cancel)
    elif engine_id == "llama":
        archive = root / "downloads" / f"llama-{RUNTIME_TAG}.zip"
        download(f"https://github.com/ggml-org/llama.cpp/releases/download/{RUNTIME_TAG}/"
                 f"llama-{RUNTIME_TAG}-bin-win-vulkan-x64.zip", archive, RUNTIME_SHA, notify, cancel)
        manifest = _native_manifest(root, cancel)
        with zipfile.ZipFile(archive) as bundle:
            for name, (source, expected) in manifest.items():
                if cancel():
                    raise InterruptedError("Runtime repair cancelled")
                target = root / "runtimes/llama" / name
                if target.is_file() and sha256(target, cancel) == expected:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_suffix(target.suffix + ".repair")
                with bundle.open(source) as incoming, partial.open("wb") as outgoing:
                    for chunk in iter(lambda: incoming.read(4 * 1024 * 1024), b""):
                        outgoing.write(chunk)
                os.replace(partial, target)
    return check_engine(root, engine_id, progress, cancel)


def _repair_python_locked(root, engine_id, progress, cancel):
    """repair_engine for nemo/transformers; the caller holds runtime_lock throughout."""
    require_idle_runtime(_environment(root, engine_id))
    before = _check_and_record(root, engine_id, progress, cancel)
    if before.healthy and before.verified:
        return before
    def notify(**update):
        progress(Progress("Repairing runtime", update.get("task", engine_id),
                          update.get("detail", ""), update.get("fraction")))
    if not before.verified or any(i["kind"] == "interpreter" for i in before.issues):
        install_worker_locked(root, engine_id, notify, cancel, force=True)
    else:
        lock = Path(__file__).parent / "runtime_locks" / f"{engine_id}.txt"
        expected = requirements(lock)
        packages = sorted({i["package"] for i in before.issues if i.get("package") in expected})
        if packages:
            uv = uv_executable()
            if not uv:
                raise RuntimeError("The runtime installer is missing")
            command = [uv, "pip", "install", "--python", str(worker_python(root, engine_id)),
                       "--constraint", str(hashed_lock(engine_id)), *runtime_pip_args(engine_id)]
            for package in packages:
                command.extend(["--reinstall-package", package, f"{package}=={expected[package]}"])
            with (root / f"{engine_id}-repair.log").open("w", encoding="utf-8") as log:
                if _run_cancellable(command, log, cancel,
                        lambda elapsed: progress(Progress("Repairing runtime", ", ".join(packages), f"{int(elapsed)}s"))):
                    raise RuntimeError(f"Package repair failed; see {engine_id}-repair.log")
        # Reached only for a verified runtime; the reinstalled packages were hash-checked too.
        write_receipt(engine_id, _environment(root, engine_id), hash_verified=True)
    return _check_and_record(root, engine_id, progress, cancel)


def delete_engine(root, engine_id):
    root = Path(root)
    if engine_id == "vad":
        targets = [root / "vad"]
    elif engine_id == "llama":
        targets = [root / "runtimes/llama", root / "downloads" / f"llama-{RUNTIME_TAG}.zip"]
    elif engine_id in PYTHON_ENGINES:
        _delete_python_runtime(root, engine_id)
        return
    else:
        raise ValueError("Unknown inference engine")
    _remove(targets)


def _delete_python_runtime(root, engine_id):
    """Delete under the runtime lock (RuntimeBusy if another KOEHA process holds it).

    The runtime is first moved to <runtime>.deleting by move_aside, which refuses while any
    process runs from it, so an in-use runtime is never half-deleted. The lock file itself is
    kept; removing it could let two processes lock two different files."""
    environment = _environment(root, engine_id)
    with runtime_lock(environment):
        trash = trash_path(environment)
        if trash.exists():
            shutil.rmtree(trash)
        if environment.exists():
            move_aside(environment, trash)  # RuntimeInUse while recognition runs from it
        _remove([trash, backup_path(environment), root / f"{engine_id}-install.log",
                 root / f"{engine_id}-check.log", root / f"{engine_id}-repair.log"])


def _remove(targets):
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
