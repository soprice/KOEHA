import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from koeha.models import catalog as models
from koeha.models.catalog import (
    MODELS,
    WORKER_VERSION,
    download,
    hashed_lock,
    is_ready,
    sha256,
    worker_python,
)
from koeha.models.runtime_audit import requirements


def test_download_verifies_bytes_before_publishing(tmp_path, monkeypatch):
    content = b"verified model bytes"
    digest = hashlib.sha256(content).hexdigest()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)))
    monkeypatch.setattr("koeha.models.catalog.httpx.stream", client.stream)
    output = tmp_path / "model.bin"
    download("https://models.example/model", output, digest)
    assert output.read_bytes() == content
    assert not output.with_suffix(".bin.part").exists()
    output.unlink()
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        download("https://models.example/model", output, "0" * 64)
    assert not output.exists()
    client.close()


def test_model_is_not_ready_with_only_weights(tmp_path):
    model = models.by_id("qwen3-asr")
    folder = tmp_path / "models" / model.id
    folder.mkdir(parents=True)
    (folder / "weights.gguf").touch()
    assert not is_ready(tmp_path, model)
    (folder / "ready.json").write_text(json.dumps({"revision": model.revision, "files": ["weights.gguf"]}))
    assert not is_ready(tmp_path, model)
    (tmp_path / "runtimes/llama").mkdir(parents=True)
    (tmp_path / "runtimes/llama/llama-server.exe").touch()
    (tmp_path / "vad").mkdir()
    (tmp_path / "vad/silero.onnx").touch()
    assert is_ready(tmp_path, model)
    (folder / "weights.gguf").unlink()
    assert not is_ready(tmp_path, model)


def test_worker_runtime_from_an_older_installer_is_not_ready(tmp_path):
    model = next(m for m in MODELS if m.runtime != "llama")
    folder = tmp_path / "models" / model.id
    folder.mkdir(parents=True)
    (folder / "model.safetensors").touch()
    (folder / "ready.json").write_text(json.dumps({"revision": model.revision, "files": ["model.safetensors"]}))
    python = worker_python(tmp_path, model.runtime)
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / "vad").mkdir()
    (tmp_path / "vad/silero.onnx").touch()
    lock = sha256(Path(models.__file__).parent / "runtime_locks" / f"{model.runtime}.txt")
    marker = python.parent.parent / "installed.json"
    marker.write_text(json.dumps({"version": WORKER_VERSION - 1, "lock_sha256": lock}))
    assert not is_ready(tmp_path, model)
    marker.write_text(json.dumps({"version": WORKER_VERSION, "lock_sha256": lock}))
    assert is_ready(tmp_path, model)


def test_hashing_stops_when_setup_is_cancelled(tmp_path):
    path = tmp_path / "weights.bin"
    path.write_bytes(b"x" * 1024)
    with pytest.raises(InterruptedError):
        sha256(path, cancel=lambda: True)


@pytest.mark.skipif(sys.platform != "win32", reason="process tree termination uses taskkill")
def test_cancelled_setup_command_stops_its_whole_process_tree(tmp_path):
    output = (tmp_path / "install.log").open("w+", encoding="utf-8")
    script = ("import subprocess, sys, time; "
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              "print(child.pid, flush=True); time.sleep(60)")
    began = time.monotonic()
    def cancel():
        return time.monotonic() - began > 1.5
    with pytest.raises(InterruptedError):
        models._run_cancellable([sys.executable, "-c", script], output, cancel)
    assert time.monotonic() - began < 10
    output.seek(0)
    child = output.read().split()[0]
    output.close()
    listing = subprocess.run(["tasklist", "/FI", f"PID eq {child}", "/NH"], capture_output=True, text=True).stdout
    assert child not in listing


def test_cancelled_runtime_install_is_not_marked_installed(tmp_path, monkeypatch):
    monkeypatch.setattr("koeha.models.catalog.shutil.which", lambda _: "uv")
    def cancelled(*_):
        raise InterruptedError("Model setup cancelled")
    monkeypatch.setattr("koeha.models.catalog._run_cancellable", cancelled)
    with pytest.raises(InterruptedError):
        models.install_worker(tmp_path, "transformers", cancel=lambda: True)
    assert not (worker_python(tmp_path, "transformers").parent.parent / "installed.json").exists()


def test_download_reports_fraction_speed_and_verification(tmp_path, monkeypatch):
    content = b"x" * (3 * 1024 * 1024 + 7)
    digest = hashlib.sha256(content).hexdigest()
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=content, headers={"content-length": str(len(content))})))
    monkeypatch.setattr("koeha.models.catalog.httpx.stream", client.stream)
    updates = []
    download("https://models.example/model", tmp_path / "model.bin", digest,
             lambda **update: updates.append(update), label="model.bin (1/2)")
    client.close()
    fractions = [u["fraction"] for u in updates if u.get("fraction") is not None and "Verifying" not in u["detail"]]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert any("left" in u["detail"] for u in updates)
    assert updates[-1]["detail"].startswith("Verifying") and updates[-1]["fraction"] == 1.0
    assert all(u["task"] == "model.bin (1/2)" for u in updates)


def test_progress_lines_stay_readable_for_the_command_line():
    update = models.Progress("Model files", "model.bin (1/2)", "1 MB / 2 MB", .5, 2, 4)
    assert str(update) == "2/4 · Model files · model.bin (1/2) · 1 MB / 2 MB"
    seen = []
    report = models._reporter(seen.append, 3, "Inference runtime", interval=60)
    report(task="first")
    report(task="throttled")
    report(task="forced", force=True)
    assert [u.task for u in seen] == ["first", "forced"]
    assert seen[0].step == 3 and seen[0].steps == models.SETUP_STEPS


def test_long_setup_commands_report_elapsed_time(tmp_path):
    ticks = []
    with (tmp_path / "log.txt").open("w") as output:
        code = models._run_cancellable([sys.executable, "-c", "import time; time.sleep(.7)"], output,
                                       lambda: False, ticks.append)
    assert code == 0
    assert ticks and ticks == sorted(ticks)


def test_model_registry_is_alphabetical_and_includes_both_whisper_variants():
    assert [m.name for m in MODELS] == sorted((m.name for m in MODELS), key=str.casefold)
    assert {m.id for m in MODELS if m.id.startswith("whisper-")} == {
        "whisper-large-v3", "whisper-large-v3-turbo"}


@pytest.mark.parametrize("model_id", ["whisper-large-v3", "whisper-large-v3-turbo"])
def test_whisper_setup_uses_only_one_checkpoint_and_pinned_assets(tmp_path, monkeypatch, model_id):
    model = models.by_id(model_id)
    entries = [{"rfilename": name, "lfs": {"sha256": "test-digest"}}
               for name in models.WHISPER_ASSETS]
    entries.extend({"rfilename": name} for name in ["model.fp32-00001-of-00002.safetensors",
                   "model.fp32-00002-of-00002.safetensors", "model.safetensors.index.fp32.json",
                   "pytorch_model.bin", "flax_model.msgpack", "onnx/model.onnx"])
    requested, downloads, installs = [], [], []
    def manifest(request):
        requested.append(str(request.url))
        return httpx.Response(200, json={"siblings": entries})
    client = httpx.Client(transport=httpx.MockTransport(manifest))
    monkeypatch.setattr(models.httpx, "Client", lambda **_: client)
    def download_fixture(url, target, expected, *args):
        downloads.append((url, target.name, expected))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"test fixture")
    monkeypatch.setattr(models, "download", download_fixture)
    monkeypatch.setattr(models, "install_worker", lambda root, runtime, *args: installs.append(runtime))
    updates = []
    models.prepare(tmp_path, model, updates.append)
    assert requested == [f"https://huggingface.co/api/models/{model.repo}/revision/{model.revision}?blobs=true"]
    model_downloads = downloads[1:]  # the shared VAD is a separate setup asset
    assert {name for _, name, _ in model_downloads} == models.WHISPER_ASSETS
    assert all(f"/resolve/{model.revision}/" in url for url, _, _ in model_downloads)
    assert all(digest == "test-digest" for _, _, digest in model_downloads)
    assert installs == ["transformers"]
    receipt = json.loads((tmp_path / "models" / model_id / "ready.json").read_text())
    assert receipt["revision"] == model.revision
    assert set(receipt["files"]) == models.WHISPER_ASSETS
    assert updates[-1].fraction == 1


def test_incomplete_whisper_manifest_is_rejected_before_download():
    with pytest.raises(RuntimeError, match="missing required"):
        models._model_files(models.by_id("whisper-large-v3"), [{"rfilename": "model.safetensors"}])


def test_delete_model_removes_downloaded_files_and_resets_readiness(tmp_path):
    model = models.by_id("qwen3-asr")
    folder = tmp_path / "models" / model.id
    assert not models.has_model_files(tmp_path, model)
    assert models.model_disk_bytes(tmp_path, model) == 0
    folder.mkdir(parents=True)
    (folder / "weights.gguf").write_bytes(b"a" * 2048)
    (folder / "ready.json").write_text(json.dumps({"revision": model.revision, "files": ["weights.gguf"]}))
    (tmp_path / "runtimes/llama").mkdir(parents=True)
    (tmp_path / "runtimes/llama/llama-server.exe").touch()
    (tmp_path / "vad").mkdir()
    (tmp_path / "vad/silero.onnx").touch()
    assert models.has_model_files(tmp_path, model)
    assert models.model_disk_bytes(tmp_path, model) > 2048
    assert is_ready(tmp_path, model)
    models.delete_model(tmp_path, model)
    assert not folder.exists()
    assert not models.has_model_files(tmp_path, model)
    assert models.model_disk_bytes(tmp_path, model) == 0
    assert not is_ready(tmp_path, model)
    assert (tmp_path / "runtimes/llama/llama-server.exe").is_file()


@pytest.mark.parametrize("runtime", ["transformers", "nemo"])
def test_hashed_runtime_lock_matches_the_version_lock(runtime):
    lock = Path(models.__file__).parent / "runtime_locks" / f"{runtime}.txt"
    pins, hashes, current = {}, {}, None
    for line in hashed_lock(runtime).read_text(encoding="utf-8").splitlines():
        line = line.strip().rstrip(" \\")
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash=sha256:"):
            assert len(line.removeprefix("--hash=sha256:")) == 64
            hashes[current] += 1
        else:
            name, version = line.split("==", 1)
            current = name.lower()
            pins[current], hashes[current] = version, 0
    expected = {name.lower(): version for name, version in requirements(lock).items()}
    assert pins == expected
    assert all(hashes.values()), "every locked package must carry at least one sha256"
    assert pins["torch"] == "2.11.0+cu128" and pins["torchaudio"] == "2.11.0+cu128"


def _fake_uv(commands, fail_on=None, cancel_on=None, audit_issues=()):
    """Stands in for uv and the runtime audit; the audit report lists audit_issues."""
    def run(command, *args, **kwargs):
        commands.append(command)
        if cancel_on and cancel_on in command:
            raise InterruptedError("Model setup cancelled")
        if command[1:2] == ["venv"]:
            (Path(command[-1]) / "Scripts").mkdir(parents=True)
            (Path(command[-1]) / "Scripts/python.exe").write_text("new interpreter")
        if "-I" in command and str(command[2]).endswith("runtime_audit.py"):
            Path(command[-2]).write_text(json.dumps({"checked_files": 1, "issues": list(audit_issues)}))
        return 1 if fail_on and fail_on in command else 0
    return run


def test_runtime_install_verifies_every_package_hash(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr("koeha.models.catalog.uv_executable", lambda: "uv")
    monkeypatch.setattr("koeha.models.catalog._run_cancellable", _fake_uv(commands))
    models.install_worker(tmp_path, "transformers")
    installs = [c for c in commands if c[1:3] == ["pip", "install"]]
    assert len(installs) == 2
    for command in installs:
        assert "--require-hashes" in command and "--index-url" not in command
        assert command[command.index("--index-strategy") + 1] == "unsafe-best-match"
        assert command[command.index("--build-constraints") + 1].endswith("build-constraints.hashes.txt")
        assert "--no-build" in command  # the Transformers runtime is wheel-only
        assert any(arg.endswith("transformers.hashes.txt") for arg in command)
    assert "torch==2.11.0+cu128" in installs[0] and "--constraint" in installs[0]
    receipt = json.loads((worker_python(tmp_path, "transformers").parent.parent / "installed.json").read_text())
    assert receipt["hash_verified"] is True and receipt["hashes_sha256"] == sha256(hashed_lock("transformers"))


def test_nemo_install_allows_only_hash_pinned_source_builds():
    args = models.runtime_pip_args("nemo")
    assert "--no-build" not in args and "--require-hashes" in args
    assert args[args.index("--build-constraints") + 1].endswith("build-constraints.hashes.txt")


def test_build_constraints_pin_every_build_backend_by_hash():
    lines = (models.RUNTIME_LOCKS / "build-constraints.hashes.txt").read_text(encoding="utf-8").splitlines()
    pins = [line.split(" ")[0] for line in lines if line and line[0].isalpha()]
    assert pins == ["setuptools==84.2.1"]
    assert any(line.strip().startswith("--hash=sha256:") for line in lines)
    source = (models.RUNTIME_LOCKS / "build-constraints.txt").read_text(encoding="utf-8")
    assert [line for line in source.splitlines() if line and not line.startswith("#")] == pins


def test_reinstall_starts_from_an_empty_environment_and_drops_the_old_one(tmp_path, monkeypatch):
    environment = worker_python(tmp_path, "transformers").parent.parent
    (environment / "Lib/site-packages/unverified").mkdir(parents=True)
    (environment / "Scripts").mkdir()
    (environment / "Scripts/python.exe").write_text("legacy interpreter")
    commands = []
    monkeypatch.setattr("koeha.models.catalog.uv_executable", lambda: "uv")
    monkeypatch.setattr("koeha.models.catalog._run_cancellable", _fake_uv(commands))
    models.install_worker(tmp_path, "transformers", force=True)
    assert not (environment / "Lib/site-packages/unverified").exists()
    assert (environment / "Scripts/python.exe").read_text() == "new interpreter"
    assert not environment.with_name("transformers.previous").exists()
    assert "--allow-existing" not in next(c for c in commands if c[1:2] == ["venv"])


@pytest.mark.parametrize("failure", ["fail", "cancel"])
def test_failed_reinstall_restores_the_previous_runtime(tmp_path, monkeypatch, failure):
    environment = worker_python(tmp_path, "transformers").parent.parent
    (environment / "Scripts").mkdir(parents=True)
    (environment / "Scripts/python.exe").write_text("legacy interpreter")
    (environment / "installed.json").write_text('{"legacy": true}')
    run = (_fake_uv([], fail_on="--requirement") if failure == "fail"
           else _fake_uv([], cancel_on="--requirement"))
    monkeypatch.setattr("koeha.models.catalog.uv_executable", lambda: "uv")
    monkeypatch.setattr("koeha.models.catalog._run_cancellable", run)
    with pytest.raises((RuntimeError, InterruptedError)):
        models.install_worker(tmp_path, "transformers", force=True)
    assert (environment / "Scripts/python.exe").read_text() == "legacy interpreter"
    assert (environment / "installed.json").read_text() == '{"legacy": true}'
    assert not environment.with_name("transformers.previous").exists()


def test_reinstall_refuses_to_touch_a_runtime_that_is_in_use(tmp_path, monkeypatch):
    environment = worker_python(tmp_path, "transformers").parent.parent
    (environment / "Scripts").mkdir(parents=True)
    (environment / "Scripts/python.exe").write_text("running interpreter")
    monkeypatch.setattr("koeha.models.catalog.uv_executable", lambda: "uv")
    def locked(source, target):
        raise PermissionError("in use")
    monkeypatch.setattr("koeha.models.catalog.os.replace", locked)
    def unexpected(*args, **kwargs):
        raise AssertionError("nothing may run while the runtime is in use")
    monkeypatch.setattr("koeha.models.catalog._run_cancellable", unexpected)
    with pytest.raises(RuntimeError, match="in use"):
        models.install_worker(tmp_path, "transformers", force=True)
    assert (environment / "Scripts/python.exe").read_text() == "running interpreter"



def _working_runtime(root, interpreter="old interpreter"):
    environment = worker_python(root, "transformers").parent.parent
    (environment / "Scripts").mkdir(parents=True)
    (environment / "Scripts/python.exe").write_text(interpreter)
    models.write_receipt("transformers", environment, hash_verified=True)
    return environment


def _install(tmp_path, monkeypatch, commands, **fake):
    monkeypatch.setattr("koeha.models.catalog.uv_executable", lambda: "uv")
    monkeypatch.setattr("koeha.models.catalog._run_cancellable", _fake_uv(commands, **fake))
    models.install_worker(tmp_path, "transformers", force=True)


def test_new_runtime_is_fully_audited_before_the_backup_is_deleted(tmp_path, monkeypatch):
    environment = _working_runtime(tmp_path)
    commands, seen = [], []
    real_rmtree = models.shutil.rmtree
    def watch(path, *args, **kwargs):
        seen.append((Path(path).name, any("runtime_audit.py" in str(a) for c in commands for a in c)))
        return real_rmtree(path, *args, **kwargs)
    monkeypatch.setattr(models.shutil, "rmtree", watch)
    _install(tmp_path, monkeypatch, commands)
    audit = next(c for c in commands if str(c[2]).endswith("runtime_audit.py"))
    assert audit[0].endswith("python.exe") and audit[1] == "-I" and audit[-1] == "transformers"
    assert audit[3].endswith("transformers.txt")  # every locked package, version and file hash
    assert ("transformers.previous", True) in seen  # backup removed only after the audit ran
    assert (environment / "Scripts/python.exe").read_text() == "new interpreter"


@pytest.mark.parametrize("issue", [
    {"kind": "import", "package": "transformers", "detail": "No module named 'transformers'"},
    {"kind": "version", "package": "numpy", "detail": "Expected 2.5.4; found 2.0.0"},
    {"kind": "files", "package": "torch", "detail": "Missing file: torch/lib/c10.dll"},
])
def test_failed_validation_restores_the_previous_runtime(tmp_path, monkeypatch, issue):
    environment = _working_runtime(tmp_path)
    before = (environment / "installed.json").read_text()
    with pytest.raises(RuntimeError, match="failed validation"):
        _install(tmp_path, monkeypatch, [], audit_issues=[issue])
    assert (environment / "Scripts/python.exe").read_text() == "old interpreter"
    assert (environment / "installed.json").read_text() == before
    assert not environment.with_name("transformers.previous").exists()
    assert issue["detail"] in (tmp_path / "transformers-install.log").read_text()


def test_audit_that_crashes_without_a_report_fails_validation(tmp_path, monkeypatch):
    environment = _working_runtime(tmp_path)
    def run(command, *args, **kwargs):
        if command[1:2] == ["venv"]:
            (Path(command[-1]) / "Scripts").mkdir(parents=True)
            (Path(command[-1]) / "Scripts/python.exe").write_text("new interpreter")
        return 3 if "-I" in command else 0
    monkeypatch.setattr("koeha.models.catalog.uv_executable", lambda: "uv")
    monkeypatch.setattr("koeha.models.catalog._run_cancellable", run)
    with pytest.raises(RuntimeError, match="failed validation"):
        models.install_worker(tmp_path, "transformers", force=True)
    assert (environment / "Scripts/python.exe").read_text() == "old interpreter"


# Crash points: the process dies (power loss, killed) so no rollback code runs. On restart,
# recover_runtime must use .previous only when it is the last working runtime.

def _crashed(root, state):
    environment = _working_runtime(root)
    previous = environment.with_name("transformers.previous")
    os.replace(environment, previous)  # step 1 of a reinstall: old runtime moved aside
    if state == "before new venv":
        pass
    elif state == "mid install":
        (environment / "Lib/site-packages/half_downloaded").mkdir(parents=True)
        (environment / "Scripts").mkdir()
        (environment / "Scripts/python.exe").write_text("new interpreter")
    elif state == "half-written receipt":
        (environment / "Scripts").mkdir(parents=True)
        (environment / "Scripts/python.exe").write_text("new interpreter")
        (environment / "installed.json.tmp").write_text('{"hash_ver')
    elif state == "mid rollback":
        (environment / "Lib").mkdir(parents=True)  # partially deleted new runtime, receipt gone
    elif state == "committed":
        (environment / "Scripts").mkdir(parents=True)
        (environment / "Scripts/python.exe").write_text("new interpreter")
        models.write_receipt("transformers", environment, hash_verified=True)
    return environment, previous


@pytest.mark.parametrize("state", ["before new venv", "mid install", "half-written receipt", "mid rollback"])
def test_restart_after_an_interrupted_install_restores_the_backup(tmp_path, state):
    environment, previous = _crashed(tmp_path, state)
    with models.runtime_lock(environment):
        assert models.recover_runtime(tmp_path, "transformers") == "restored"
    assert (environment / "Scripts/python.exe").read_text() == "old interpreter"
    assert models.is_hash_verified("transformers", json.loads((environment / "installed.json").read_text()))
    assert not previous.exists() and not (environment / "Lib/site-packages/half_downloaded").exists()


def test_restart_after_a_committed_install_discards_only_the_stale_backup(tmp_path):
    environment, previous = _crashed(tmp_path, "committed")
    with models.runtime_lock(environment):
        assert models.recover_runtime(tmp_path, "transformers") == "discarded"
    assert (environment / "Scripts/python.exe").read_text() == "new interpreter"
    assert not previous.exists()


def test_prepare_after_a_crash_restores_without_downloading(tmp_path, monkeypatch):
    environment, previous = _crashed(tmp_path, "mid install")
    monkeypatch.setattr("koeha.models.catalog.uv_executable", lambda: "uv")
    def unexpected(*args, **kwargs):
        raise AssertionError("the restored runtime is current; nothing should be installed")
    monkeypatch.setattr("koeha.models.catalog._run_cancellable", unexpected)
    models.install_worker(tmp_path, "transformers")
    assert (environment / "Scripts/python.exe").read_text() == "old interpreter"
    assert not previous.exists()


def test_forced_reinstall_after_a_crash_never_deletes_the_backup_first(tmp_path, monkeypatch):
    environment, previous = _crashed(tmp_path, "mid install")
    deleted = []
    real_rmtree = models.shutil.rmtree
    monkeypatch.setattr(models.shutil, "rmtree",
                        lambda path, *a, **k: deleted.append(Path(path).name) or real_rmtree(path, *a, **k))
    with pytest.raises(RuntimeError, match="failed validation"):
        _install(tmp_path, monkeypatch, [], audit_issues=[{"kind": "import", "detail": "broken"}])
    assert "transformers.previous" not in deleted
    assert (environment / "Scripts/python.exe").read_text() == "old interpreter"


def test_restore_that_cannot_remove_the_partial_runtime_keeps_the_backup(tmp_path, monkeypatch):
    environment, previous = _crashed(tmp_path, "mid install")
    monkeypatch.setattr(models.shutil, "rmtree", lambda *args, **kwargs: None)  # files locked
    with models.runtime_lock(environment), pytest.raises(RuntimeError, match="in use"):
        models.recover_runtime(tmp_path, "transformers")
    assert (previous / "Scripts/python.exe").read_text() == "old interpreter"


def test_second_window_cannot_install_while_the_first_holds_the_lock(tmp_path, monkeypatch):
    environment, previous = _crashed(tmp_path, "mid install")  # looks interrupted, but is live
    monkeypatch.setattr("koeha.models.catalog.uv_executable", lambda: "uv")
    with models.runtime_lock(environment), pytest.raises(RuntimeError, match="(?i)another KOEHA window"):
        models.install_worker(tmp_path, "transformers", force=True)
    assert previous.is_dir() and (environment / "Lib/site-packages/half_downloaded").is_dir()
