"""Pinned model assets and application-owned inference runtimes."""
import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from koeha.core.runtime_tools import external_popen, uv_executable


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    repo: str
    revision: str
    runtime: str
    languages: tuple[str, ...]
    license: str
    size: str


MODELS = tuple(sorted((
    Model("qwen3-asr", "Qwen3 ASR · 1.7B", "ggml-org/Qwen3-ASR-1.7B-GGUF",
          "36a678687ba7d07a74ca70ccb0e36902e005fb80", "llama", (), "Apache-2.0", "2.52 GB"),
    Model("parakeet-ja", "Parakeet TDT-CTC · 0.6B JA", "nvidia/parakeet-tdt_ctc-0.6b-ja",
          "44edb27eea9317daf89333e75eb830db4b1cc298", "nemo", ("ja",), "CC-BY-4.0", "2.5 GB + runtime"),
    Model("fun-asr", "Fun-ASR-Nano-2512", "FunAudioLLM/Fun-ASR-Nano-2512-hf",
          "7ef6cafdc445b5beff0b730cbeb51952c793ca6e", "transformers", ("zh", "en", "ja"), "Apache-2.0", "2 GB + runtime"),
    Model("nemotron", "Nemotron 3.5 ASR Streaming · 0.6B", "nvidia/nemotron-3.5-asr-streaming-0.6b",
          "ea30d66debe3740a08b573244286791d423d6b3e", "transformers", (), "OpenMDW-1.1", "2.5 GB + runtime"),
    Model("whisper-large-v3", "Whisper Large v3", "openai/whisper-large-v3",
          "06f233fe06e710322aca913c1bc4249a0d71fce1", "transformers", (), "Apache-2.0", "3.09 GB + runtime"),
    Model("whisper-large-v3-turbo", "Whisper Large v3 Turbo", "openai/whisper-large-v3-turbo",
          "41f01f3fe87f28c78e2fbf8b568835947dd65ed9", "transformers", (), "MIT", "1.62 GB + runtime"),
), key=lambda model: model.name.casefold()))
WHISPER_ASSETS = {
    "model.safetensors", "config.json", "generation_config.json", "preprocessor_config.json",
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
    "normalizer.json", "vocab.json", "merges.txt",
}
QWEN_FILES = {
    "Qwen3-ASR-1.7B-Q8_0.gguf": "58e22d0532d4eacaf034cfac17a6fed159f37c41390c710186783be439d1fc57",
    "mmproj-Qwen3-ASR-1.7B-Q8_0.gguf": "46c1d533af3f354ceb37ce855dbceff7da7fa7cf1e6a523df3b13440bd164c0d",
}
RUNTIME_TAG = "b12110"
RUNTIME_SHA = "9732a5e48887ca54d0bfe4af4324dc85fd56cd8cda19b52df3542fa6ebe6874f"
VAD_REVISION = "066409f3d9db45a30d21dfcbf9147826ffdd7052"
VAD_SHA = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
WORKER_VERSION = 3
# Every package (including the CUDA torch wheels) is pinned by version and sha256 in
# runtime_locks/<runtime>.hashes.txt, so index order cannot substitute a different file.
# unsafe-best-match is required: the PyTorch index mirrors common packages at only some
# of the locked versions, so first-index cannot resolve the locks.
RUNTIME_LOCKS = Path(__file__).parent / "runtime_locks"
# Source builds would fetch their build backend outside the runtime lock. Transformers is
# wheel-only, so builds are refused. NeMo has four sdist-only packages, so their build
# backend (setuptools) is pinned by hash in build-constraints.hashes.txt instead.
WHEEL_ONLY_RUNTIMES = {"transformers"}


def hashed_lock(runtime):
    return RUNTIME_LOCKS / f"{runtime}.hashes.txt"


def runtime_pip_args(runtime):
    args = ["--extra-index-url", "https://download.pytorch.org/whl/cu128",
            "--index-strategy", "unsafe-best-match", "--require-hashes",
            "--build-constraints", str(RUNTIME_LOCKS / "build-constraints.hashes.txt")]
    return args + (["--no-build"] if runtime in WHEEL_ONLY_RUNTIMES else [])


def write_receipt(runtime, environment, hash_verified):
    """hash_verified is True only when every installed package passed --require-hashes."""
    lock = RUNTIME_LOCKS / f"{runtime}.txt"
    partial = environment / "installed.json.tmp"
    partial.write_text(json.dumps({
        "version": WORKER_VERSION, "lock_sha256": sha256(lock), "hash_verified": hash_verified,
        "hashes_sha256": sha256(hashed_lock(runtime))}), encoding="utf-8")
    os.replace(partial, environment / "installed.json")  # atomic: a crash never leaves half a receipt


def is_hash_verified(runtime, record):
    """Verified only if installed with --require-hashes against the hashed lock shipped now."""
    return (isinstance(record, dict) and record.get("hash_verified") is True
            and record.get("hashes_sha256") == sha256(hashed_lock(runtime)))


def by_id(model_id):
    return next(m for m in MODELS if m.id == model_id)


@dataclass(frozen=True)
class Progress:
    """One setup update. ``fraction`` is None while the length of the work is unknown.

    ``str()`` gives a plain line, so command-line and log callers need no changes.
    """
    phase: str
    task: str = ""
    detail: str = ""
    fraction: float | None = None
    step: int = 0
    steps: int = 0

    def __str__(self):
        head = f"{self.step}/{self.steps} · " if self.steps else ""
        return head + " · ".join(part for part in (self.phase, self.task, self.detail) if part)


SETUP_STEPS = 4


def _reporter(progress, step, phase, interval=.1):
    """Bind updates to one setup step and limit them to about ten per second."""
    last = [-interval]
    def report(task="", detail="", fraction=None, force=False):
        now = time.monotonic()
        if force or now - last[0] >= interval:
            last[0] = now
            progress(Progress(phase, task, detail, fraction, step, SETUP_STEPS))
    return report


def _size(count):
    return f"{count / 1e9:.2f} GB" if count >= 1e9 else f"{count / 1e6:.0f} MB"


def _clock(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{seconds:02}"


def _ignore(**_):
    pass


def sha256(path, cancel=lambda: False, on_chunk=None):
    digest = hashlib.sha256()
    total = path.stat().st_size if on_chunk else 0
    done = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            if cancel():
                raise InterruptedError("Model setup cancelled")
            digest.update(chunk)
            if on_chunk:
                done += len(chunk)
                on_chunk(done, total)
    return digest.hexdigest()


def _verified(path, expected, report, cancel, label):
    def on_chunk(done, total):
        report(task=label, detail=f"Verifying · {_size(done)} / {_size(total)}",
               fraction=done / total if total else None)
    return sha256(path, cancel, on_chunk) == expected


def download(url, target, expected, report=_ignore, cancel=lambda: False, label=None):
    label = label or target.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and (not expected or _verified(target, expected, report, cancel, label)):
        return
    partial = target.with_suffix(target.suffix + ".part")
    report(task=label, detail="Connecting", force=True)
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        count = 0
        began = time.monotonic()
        with partial.open("wb") as stream:
            for chunk in response.iter_bytes(1024 * 1024):
                if cancel():
                    raise InterruptedError("Download cancelled")
                stream.write(chunk)
                count += len(chunk)
                speed = count / max(time.monotonic() - began, 1e-3)
                detail = f"{_size(count)} / {_size(total)}" if total else _size(count)
                if total:
                    detail += f" · {_size(speed)}/s · {_clock((total - count) / speed)} left"
                report(task=label, detail=detail, fraction=count / total if total else None)
    if expected and not _verified(partial, expected, report, cancel, label):
        partial.unlink()
        raise RuntimeError(f"Checksum mismatch: {target.name}")
    os.replace(partial, target)


def worker_python(root: Path, runtime: str):
    return root / "runtimes" / runtime / "Scripts" / "python.exe"


def known_runtime_damage(root, runtime):
    folder = root / ("vad" if runtime == "vad" else f"runtimes/{runtime}")
    path = folder / "health.json"
    if not path.exists():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("healthy") is False
    except (OSError, ValueError, AttributeError):
        return True


def is_ready(root, model):
    folder = root / "models" / model.id
    receipt = folder / "ready.json"
    if known_runtime_damage(root, model.runtime) or known_runtime_damage(root, "vad"):
        return False
    try:
        record = json.loads(receipt.read_text(encoding="utf-8"))
        if record["revision"] != model.revision or not all((folder / f).is_file() for f in record["files"]):
            return False
        runtime = root / "runtimes/llama/llama-server.exe" if model.runtime == "llama" else worker_python(root, model.runtime)
        if model.runtime != "llama":
            installed = json.loads((runtime.parent.parent / "installed.json").read_text(encoding="utf-8"))
            lock = Path(__file__).parent / "runtime_locks" / f"{model.runtime}.txt"
            # Must match the reinstall check in install_worker, or a stale runtime would report ready.
            if (not isinstance(installed, dict) or installed.get("version") != WORKER_VERSION or
                    installed.get("lock_sha256") != sha256(lock)):
                return False
        return runtime.is_file() and (root / "vad/silero.onnx").is_file()
    except (OSError, ValueError, KeyError):
        return False


def has_model_files(root, model):
    model_id = model.id if isinstance(model, Model) else model
    folder = Path(root) / "models" / model_id
    try:
        return folder.is_dir() and any(folder.iterdir())
    except OSError:
        return False


def model_disk_bytes(root, model):
    model_id = model.id if isinstance(model, Model) else model
    folder = Path(root) / "models" / model_id
    if not folder.is_dir():
        return 0
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def delete_model(root, model):
    model_id = model.id if isinstance(model, Model) else model
    folder = Path(root) / "models" / model_id
    if folder.exists():
        shutil.rmtree(folder)


def _model_files(model, entries):
    selected = {}
    for entry in entries:
        name = entry["rfilename"]
        if model.id.startswith("whisper-"):
            # Repositories also contain redundant FP32, Flax and PyTorch copies of the weights.
            include = name in WHISPER_ASSETS
        else:
            extensions = (".json", ".model", ".txt", ".tiktoken", ".jinja", ".jinja2", ".yaml")
            extensions += (".nemo",) if model.runtime == "nemo" else (".safetensors",)
            include = "/" not in name and (name.endswith(extensions) or name.startswith(("LICENSE", "NOTICE")))
        if include:
            selected[name] = entry.get("lfs", {}).get("sha256")
    if model.id.startswith("whisper-") and not WHISPER_ASSETS.issubset(selected):
        raise RuntimeError("Whisper manifest is missing required model or tokenizer files")
    if not any(name.endswith((".safetensors", ".nemo")) for name in selected):
        raise RuntimeError("Model manifest has no supported weights")
    return selected


def prepare(root, model, progress=lambda _: None, cancel=lambda: False):
    """Four steps: voice detector, then runtime and weights (order depends on the runtime), then finishing."""
    for runtime in (model.runtime, "vad"):
        if known_runtime_damage(root, runtime):
            from koeha.models.engines import repair_engine
            result = repair_engine(root, runtime, progress, cancel)
            if not result.healthy:
                raise RuntimeError("Runtime needs attention in Settings → Engines & Frameworks")
    download(f"https://raw.githubusercontent.com/snakers4/silero-vad/{VAD_REVISION}/"
             "src/silero_vad/data/silero_vad.onnx", root / "vad/silero.onnx", VAD_SHA,
             _reporter(progress, 1, "Voice detector"), cancel)
    folder = root / "models" / model.id
    folder.mkdir(parents=True, exist_ok=True)
    if model.runtime == "llama":
        if sys.platform != "win32":
            raise RuntimeError("The bundled local runtime requires Windows x64")
        report = _reporter(progress, 2, "Recognition runtime")
        archive = root / "downloads" / f"llama-{RUNTIME_TAG}.zip"
        download(f"https://github.com/ggml-org/llama.cpp/releases/download/{RUNTIME_TAG}/"
                 f"llama-{RUNTIME_TAG}-bin-win-vulkan-x64.zip", archive, RUNTIME_SHA, report, cancel)
        report(task="Extracting", force=True)
        runtime = root / "runtimes/llama"
        runtime.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                destination = (runtime / member.filename).resolve()
                if not destination.is_relative_to(runtime.resolve()):
                    raise RuntimeError("Unsafe runtime archive path")
            bundle.extractall(runtime)
        # Releases may wrap binaries in a version directory.
        executable = next(runtime.rglob("llama-server.exe"), None)
        if not executable:
            raise RuntimeError("Runtime archive contains no llama-server.exe")
        if executable.parent != runtime:
            for file in executable.parent.iterdir():
                if file.is_file():
                    shutil.copy2(file, runtime / file.name)
        selected = QWEN_FILES
    else:
        _reporter(progress, 2, "Model files")(task="Reading manifest", force=True)
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            response = client.get(f"https://huggingface.co/api/models/{model.repo}/revision/{model.revision}?blobs=true")
            response.raise_for_status()
            selected = _model_files(model, response.json()["siblings"])
    report = _reporter(progress, 3 if model.runtime == "llama" else 2, "Model files")
    for index, (name, digest) in enumerate(selected.items(), 1):
        download(f"https://huggingface.co/{model.repo}/resolve/{model.revision}/{name}",
                 folder / name, digest, report, cancel, f"{name} ({index}/{len(selected)})")
    if model.runtime != "llama":
        install_worker(root, model.runtime, _reporter(progress, 3, "Inference runtime"), cancel)
    if cancel():
        raise InterruptedError("Model setup cancelled")
    _reporter(progress, 4, "Finishing")(force=True)
    record = {"revision": model.revision, "files": selected, "repo": model.repo}
    (folder / "ready.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    progress(Progress("Ready", fraction=1.0, step=SETUP_STEPS, steps=SETUP_STEPS))


def _run_cancellable(command, output, cancel, on_tick=None):
    """Run a setup command, stopping its whole process tree as soon as setup is cancelled.

    ``on_tick`` receives the elapsed seconds about five times per second while the command runs.
    """
    process = external_popen(command, stdout=output, stderr=output,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    began = time.monotonic()
    while True:
        try:
            return process.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            if on_tick:
                on_tick(time.monotonic() - began)
            if cancel():
                _stop(process)
                raise InterruptedError("Model setup cancelled") from None


def _stop(process):
    # uv starts helper processes; terminating only uv would leave them writing into the runtime.
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if process.poll() is None:
        process.kill()
    process.wait()


def install_worker(root, runtime, report=_ignore, cancel=lambda: False, force=False):
    python = worker_python(root, runtime)
    lock = Path(__file__).parent / "runtime_locks" / f"{runtime}.txt"
    marker = python.parent.parent / "installed.json"
    if marker.exists() and python.is_file() and not force:
        try:
            installed = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            installed = {}
        if (isinstance(installed, dict) and installed.get("version") == WORKER_VERSION and
                installed.get("lock_sha256") == sha256(lock)):
            return
    with runtime_lock(python.parent.parent):
        install_worker_locked(root, runtime, report, cancel, force)


def install_worker_locked(root, runtime, report=_ignore, cancel=lambda: False, force=False):
    """install_worker for a caller that already holds runtime_lock for this runtime."""
    python = worker_python(root, runtime)
    uv = uv_executable()
    if not uv:
        raise RuntimeError("Install uv, restart KOEHA and prepare this model again")
    # uv skips packages that are already installed without re-checking their hashes, so a
    # verified install must start from an empty environment. The old one is kept aside as
    # <runtime>.previous until the new one is installed, validated and committed by its receipt.
    environment = python.parent.parent
    previous = backup_path(environment)
    if recover_runtime(root, runtime) == "restored" and not force and _current_receipt(runtime, environment):
        return  # an interrupted reinstall was undone, and the restored runtime is up to date
    if environment.exists():
        move_aside(environment, previous)
    try:
        _install_clean_worker(root, runtime, python, uv, report, cancel)
    except BaseException:
        _rollback(environment, previous)
        raise
    # The receipt is the commit point: from here the new runtime is the working one.
    shutil.rmtree(previous, ignore_errors=True)


def backup_path(environment):
    return environment.with_name(environment.name + ".previous")


def trash_path(environment):
    """Where Delete moves a runtime before removing it; anything here is garbage."""
    return environment.with_name(environment.name + ".deleting")


class RuntimeBusy(RuntimeError):
    """Another KOEHA process holds the runtime lock for maintenance or worker startup."""


class RuntimeInUse(RuntimeError):
    def __init__(self):
        super().__init__("The runtime is in use. Stop recognition that uses it, then try again")


def require_idle_runtime(environment):
    """Refuse maintenance while a worker is running. The caller must hold runtime_lock."""
    if processes_running_from(_path_forms(environment)):
        raise RuntimeInUse()


def move_aside(environment, target):
    """Rename a runtime out of the way, refusing if a process is running from it.

    Windows allows renaming a folder while a program inside it runs, so the rename alone proves
    nothing. Running processes are checked before the rename and again afterwards (a process keeps
    reporting its original path). The rename is undone if a process is found, and also if the
    second check itself fails, so the runtime is never left stranded under the target name."""
    forms = _path_forms(environment)
    if processes_running_from(forms):
        raise RuntimeInUse()
    try:
        os.replace(environment, target)
    except OSError as error:
        raise RuntimeInUse() from error
    try:
        in_use = processes_running_from(forms)
    except BaseException:
        os.replace(target, environment)
        raise
    if in_use:
        os.replace(target, environment)
        raise RuntimeInUse()


@functools.cache
def _kernel32():
    """kernel32 with explicit prototypes. Without them ctypes passes and returns C int, which
    truncates pointer-sized HANDLE values on 64-bit Windows and rejects large ones as arguments.
    A private WinDLL instance keeps these prototypes from leaking into ctypes.windll."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    prototypes = {
        "K32EnumProcesses": (wintypes.BOOL, [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]),
        "OpenProcess": (wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
        "QueryFullProcessImageNameW": (wintypes.BOOL, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                       ctypes.POINTER(wintypes.DWORD)]),
        "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
        "GetLongPathNameW": (wintypes.DWORD, [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]),
        "GetShortPathNameW": (wintypes.DWORD, [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]),
    }
    for name, (restype, argtypes) in prototypes.items():
        function = getattr(kernel32, name)
        function.restype, function.argtypes = restype, argtypes
    return kernel32


_PATH_BUFFER = 32768  # characters; the longest path Windows accepts


def _path_forms(path):
    """The folder as Windows may report it: as given, long form and 8.3 short form."""
    forms = {os.path.normcase(os.path.abspath(path))}
    if os.name == "nt":
        import ctypes
        kernel32 = _kernel32()
        for convert in (kernel32.GetLongPathNameW, kernel32.GetShortPathNameW):
            buffer = ctypes.create_unicode_buffer(_PATH_BUFFER)
            length = convert(str(path), buffer, _PATH_BUFFER)
            if 0 < length < _PATH_BUFFER:  # 0 = failed; >= size = did not fit
                forms.add(os.path.normcase(buffer.value))
    return forms


def _process_ids():
    import ctypes
    from ctypes import wintypes
    kernel32, capacity = _kernel32(), 4096
    while True:
        ids = (wintypes.DWORD * capacity)()
        needed = wintypes.DWORD()
        if not kernel32.K32EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(needed)):
            raise RuntimeError("Could not list running processes to check whether the runtime is in use")
        count = needed.value // ctypes.sizeof(wintypes.DWORD)
        if count < capacity:  # a full buffer may have been truncated, so retry larger
            return list(ids[:count])
        capacity *= 2


def process_image(pid):
    """Full executable path of a running process, or None if it cannot be opened (Windows only)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(_PATH_BUFFER)
        size = wintypes.DWORD(_PATH_BUFFER)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def processes_running_from(forms):
    """Process ids whose executable lies inside one of the folder forms (Windows only).

    KOEHA starts recognition through the runtime's own Scripts\\python.exe launcher, which stays
    alive while its interpreter runs, so this finds every recognition worker of the runtime."""
    if os.name != "nt":
        return []
    prefixes = tuple(form.rstrip("\\") + "\\" for form in forms)
    found = []
    for pid in _process_ids():
        image = process_image(pid)
        if image and os.path.normcase(image).startswith(prefixes):
            found.append(pid)
    return found


@contextmanager
def runtime_lock(environment):
    """Serialize maintenance and worker launch. The OS releases the lock if the holder dies."""
    path = environment.with_name(environment.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeBusy("Another KOEHA window is maintaining this runtime or starting recognition. "
                              "Wait for it to finish, then try again") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def _install_complete(environment):
    """A runtime whose install ran to the end: only a finished, validated install writes this."""
    try:
        record = json.loads((environment / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(record, dict) and record.get("hash_verified") is True
            and (environment / "Scripts" / "python.exe").is_file())


def _current_receipt(runtime, environment):
    try:
        record = json.loads((environment / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(record, dict) and record.get("version") == WORKER_VERSION
            and record.get("lock_sha256") == sha256(RUNTIME_LOCKS / f"{runtime}.txt"))


def _rollback(environment, previous):
    """Remove a new, uncommitted runtime and put the backup back. The backup is only moved,
    never deleted; if the move fails it stays in place for recover_runtime to retry."""
    (environment / "installed.json").unlink(missing_ok=True)  # never leave a committed-looking partial
    shutil.rmtree(environment, ignore_errors=True)
    if previous.exists() and not environment.exists():
        os.replace(previous, environment)


def recover_runtime(root, runtime):
    """Finish or undo a reinstall that was interrupted by a crash, power loss or killed process.

    Call with runtime_lock held. Returns "restored" when the backup was the last working
    runtime and has been put back, "discarded" when a committed install had only its stale
    backup left over, or None when there was nothing to recover."""
    environment = worker_python(root, runtime).parent.parent
    previous = backup_path(environment)
    trash = trash_path(environment)
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)  # an interrupted Delete; never a working runtime
    if not previous.exists():
        return None
    if _install_complete(environment):
        shutil.rmtree(previous)
        return "discarded"
    _rollback(environment, previous)
    if environment.exists() and not previous.exists():
        return "restored"
    raise RuntimeError("Could not restore the previous runtime because files are in use. "
                       "Close KOEHA recognition, then try again")


def _install_clean_worker(root, runtime, python, uv, report, cancel):
    log = root / f"{runtime}-install.log"
    pip_args = runtime_pip_args(runtime)
    with log.open("w", encoding="utf-8") as output:
        # uv prints no machine-readable progress, so each stage shows its name and elapsed time.
        def run(task, command):
            report(task=task, force=True)
            tick = lambda elapsed: report(task=task, detail=f"{_clock(elapsed)} elapsed")  # noqa: E731
            return _run_cancellable(command, output, cancel, tick)
        def install(task, args):
            if run(task, [uv, *args]):
                raise RuntimeError(f"Runtime installation failed. Details: {log}")
        install("Creating environment", ["venv", "--python", "3.12", str(python.parent.parent)])
        hashes = hashed_lock(runtime)
        # Installed packages are not re-verified later, so PyTorch is hash-checked here too.
        install("Downloading PyTorch (about 2.6 GB)", [
            "pip", "install", "--python", str(python), "--constraint", str(hashes),
            "torch==2.11.0+cu128", "torchaudio==2.11.0+cu128", *pip_args])
        install("Installing model libraries", [
            "pip", "install", "--python", str(python), "--requirement", str(hashes), *pip_args])
        check = run("Checking runtime", [str(python), "-c", "import torch, torchaudio; "
                                         "assert torch.__version__.split('+')[0] == '2.11.0'; "
                                         "assert torchaudio.__version__.split('+')[0] == '2.11.0'"])
        if check:
            raise RuntimeError(f"Inference runtime health check failed. Details: {log}")
        # Same audit as Engines → Check: every locked package at its exact version with intact
        # files, plus importing torch, torchaudio, transformers and (for NeMo) the ASR models.
        issues = _validate_runtime(runtime, python, run)
        if issues:
            output.write("Validation issues:\n" + json.dumps(issues, indent=2) + "\n")
            raise RuntimeError(f"The new runtime failed validation, so the previous one was kept. Details: {log}")
    write_receipt(runtime, python.parent.parent, hash_verified=True)


def _validate_runtime(runtime, python, run):
    with tempfile.TemporaryDirectory(prefix="koeha-runtime-validate-") as folder:
        result = Path(folder) / "report.json"
        code = run("Validating runtime", [str(python), "-I", str(Path(__file__).with_name("runtime_audit.py")),
                                          str(RUNTIME_LOCKS / f"{runtime}.txt"), str(result), runtime])
        try:
            report = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [dict(kind="validation", detail=f"Runtime audit did not produce a report (exit code {code})")]
    issues = list(report.get("issues", [])) if isinstance(report, dict) else []
    if code and not issues:
        issues.append(dict(kind="validation", detail=f"Runtime audit exited with code {code}"))
    return issues
