import base64
import hashlib
import json
import os
import zipfile
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace

from koeha.models import catalog as models
from koeha.models import engines
from koeha.models.runtime_audit import inspect_packages


def test_package_audit_names_missing_corrupt_and_wrong_version_files(tmp_path):
    class Entry(str):
        hash = SimpleNamespace(mode="sha256", value=base64.urlsafe_b64encode(
            hashlib.sha256(b"healthy").digest()).rstrip(b"=").decode())
    (tmp_path / "healthy.py").write_bytes(b"healthy")
    (tmp_path / "damaged.py").write_bytes(b"bad")
    packages = {
        "good": SimpleNamespace(version="1", files=[Entry("healthy.py")], locate_file=lambda p: tmp_path / p),
        "bad": SimpleNamespace(version="1", files=[Entry("damaged.py"), Entry("missing.dll")],
                               locate_file=lambda p: tmp_path / p),
        "old": SimpleNamespace(version="0"),
    }
    def distribution(name):
        if name not in packages:
            raise PackageNotFoundError(name)
        return packages[name]
    report = inspect_packages(dict(good="1", bad="1", old="1", absent="1"), distribution)
    assert report["checked_files"] == 3
    assert {i["package"] for i in report["issues"]} == {"bad", "old", "absent"}
    bad = next(i for i in report["issues"] if i["package"] == "bad")
    assert "damaged.py" in bad["detail"] and "missing.dll" in bad["detail"] and bad["count"] == 2


def test_python_engine_repair_reinstalls_only_the_damaged_package(tmp_path, monkeypatch):
    python = models.worker_python(tmp_path, "transformers")
    python.parent.mkdir(parents=True)
    python.touch()
    models.write_receipt("transformers", python.parent.parent, hash_verified=True)
    commands, repaired = [], []
    def run(command, output, cancel, on_tick=None):
        commands.append(command)
        if "pip" in command:
            repaired.append(True)
        else:
            Path(command[-2]).write_text(json.dumps({"checked_files": 100, "issues": [] if repaired else [
                {"kind": "files", "package": "numpy", "detail": "Missing: numpy/core.dll"}]}))
        return 0
    monkeypatch.setattr(engines, "_run_cancellable", run)
    monkeypatch.setattr(engines, "uv_executable", lambda: "bundled-uv.exe")
    report = engines.repair_engine(tmp_path, "transformers")
    assert report.healthy and report.checked_files == 100
    install = next(command for command in commands if "pip" in command)
    assert "numpy==2.5.4" in install
    assert install.count("--reinstall-package") == 1
    assert "--require-hashes" in install and "--no-build" in install
    assert json.loads((python.parent.parent / "installed.json").read_text())["hash_verified"] is True
    assert install[install.index("--constraint") + 1].endswith("transformers.hashes.txt")
    assert not any(arg.startswith("torch==") for arg in install)
    assert json.loads((python.parent.parent / "health.json").read_text())["healthy"]
    assert not (tmp_path / "models").exists()


def test_healthy_engine_repair_never_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "check_engine", lambda *args: engines.EngineReport("vad", checked_files=1))
    def unexpected(*args, **kwargs):
        raise AssertionError("A healthy runtime must not download anything")
    monkeypatch.setattr(engines, "download", unexpected)
    assert engines.repair_engine(tmp_path, "vad").healthy


def test_native_engine_repairs_only_missing_or_damaged_files(tmp_path, monkeypatch):
    archive = tmp_path / "downloads" / f"llama-{models.RUNTIME_TAG}.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("llama-server.exe", b"healthy executable")
        bundle.writestr("ggml.dll", b"healthy library")
    monkeypatch.setattr(engines, "RUNTIME_SHA", models.sha256(archive))
    folder = tmp_path / "runtimes/llama"
    folder.mkdir(parents=True)
    executable = folder / "llama-server.exe"
    executable.write_bytes(b"healthy executable")
    original = executable.stat().st_mtime_ns
    monkeypatch.setattr(engines.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    before = engines.check_engine(tmp_path, "llama")
    assert len(before.issues) == 1 and before.issues[0]["path"] == "ggml.dll"
    def cached(url, target, expected, *args):
        assert target == archive and models.sha256(target) == expected
    monkeypatch.setattr(engines, "download", cached)
    after = engines.repair_engine(tmp_path, "llama")
    assert after.healthy and (folder / "ggml.dll").read_bytes() == b"healthy library"
    assert executable.stat().st_mtime_ns == original
    assert not (tmp_path / "models").exists()


def test_vad_check_detects_corruption_without_a_download(tmp_path, monkeypatch):
    folder = tmp_path / "vad"
    folder.mkdir()
    (folder / "silero.onnx").write_bytes(b"incomplete")
    report = engines.check_engine(tmp_path, "vad")
    assert not report.healthy
    assert models.known_runtime_damage(tmp_path, "vad")


def test_runtime_ui_exposes_check_results_and_blocks_healthy_repairs(studio):
    panel = studio.engines_panel
    panel.on_busy("transformers", True)
    assert not panel.rows["llama"]["check"].isEnabled()
    assert not panel.rows["transformers"]["cancel"].isHidden()
    panel.on_report("transformers", engines.EngineReport("transformers", [{
        "kind": "missing", "package": "torch", "detail": "Package is missing"}]))
    panel.on_busy("transformers", False)
    assert panel.rows["transformers"]["repair"].isEnabled()
    assert "torch" in panel.rows["transformers"]["details"].toPlainText()
    panel.on_report("transformers", engines.EngineReport("transformers", checked_files=123))
    python = models.worker_python(studio.controller.root, "transformers")
    python.parent.mkdir(parents=True)
    python.touch()
    panel.on_busy("transformers", False)
    assert "123" in panel.rows["transformers"]["state"].text()
    assert not panel.rows["transformers"]["repair"].isEnabled()


def test_settings_categories_are_separate_and_keep_edits(studio):
    sections = studio.settings_sections
    assert sections.pages.count() == 7
    studio.navigate(4)
    sections.select(1)
    studio.chunk_size.setValue(5)
    sections.select_title("Translation prompts")
    studio.prompt_profiles.context.setPlainText("dialogue")
    sections.select(0)
    assert studio.save_settings()
    assert studio.controller.settings.chunk_seconds == 5
    assert studio.controller.settings.active_prompt_profile.context == "dialogue"


def test_known_runtime_damage_routes_models_to_engine_repair(studio, monkeypatch):
    folder = studio.controller.root / "runtimes/transformers"
    folder.mkdir(parents=True)
    (folder / "health.json").write_text(json.dumps({"healthy": False}))
    requested = []
    monkeypatch.setattr(studio.controller, "check_runtime", lambda engine, repair=False: requested.append((engine, repair)))
    studio.refresh_models()
    assert studio.model_controls["whisper-large-v3"][0].text() == "Repair runtime"
    studio.model_controls["whisper-large-v3"][0].click()
    assert requested == [("transformers", True)]
    assert studio.pages.currentIndex() == 4
    assert studio.settings_sections.titles[studio.settings_sections.pages.currentIndex()] == "Engines & Frameworks"


def test_maintenance_cannot_overlap_a_live_session(studio):
    errors = []
    studio.controller.error.connect(errors.append)
    studio.controller.thread = SimpleNamespace(is_alive=lambda: True)
    studio.controller.check_runtime("vad", repair=True)
    assert not studio.controller.jobs and errors
    studio.controller.thread = None
    studio.controller.jobs["engine:vad"] = SimpleNamespace(set=lambda: None)
    studio.controller.start()
    assert studio.controller.thread is None and "maintenance" in errors[-1]
    studio.controller.jobs.clear()


def test_engine_deletion_removes_runtime_and_updates_panel(studio, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    root = studio.controller.root
    (root / "runtimes/llama").mkdir(parents=True, exist_ok=True)
    (root / "runtimes/llama/llama-server.exe").write_bytes(b"binary")
    (root / "downloads").mkdir(parents=True, exist_ok=True)
    (root / "downloads" / f"llama-{models.RUNTIME_TAG}.zip").write_bytes(b"zip")
    panel = studio.engines_panel
    panel.refresh_rows()
    assert not panel.rows["llama"]["delete"].isHidden()
    assert panel.rows["llama"]["repair"].text() == "Repair"
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    panel.rows["llama"]["delete"].click()
    assert not (root / "runtimes/llama").exists()
    assert not (root / "downloads" / f"llama-{models.RUNTIME_TAG}.zip").exists()
    assert panel.rows["llama"]["delete"].isHidden()
    assert panel.rows["llama"]["repair"].text() == "Download"
    assert panel.rows["llama"]["state"].text() == "Not installed"


def _legacy_runtime(root, verified):
    python = models.worker_python(root, "transformers")
    python.parent.mkdir(parents=True)
    python.write_text("legacy interpreter")
    if verified:
        models.write_receipt("transformers", python.parent.parent, hash_verified=True)
    else:
        lock = Path(models.__file__).parent / "runtime_locks/transformers.txt"
        (python.parent.parent / "installed.json").write_text(json.dumps(
            {"version": models.WORKER_VERSION, "lock_sha256": models.sha256(lock)}))
    return python


def _healthy_audit(command, output, cancel, on_tick=None):
    Path(command[-2]).write_text(json.dumps({"checked_files": 100, "issues": []}))
    return 0


def test_legacy_runtime_is_healthy_but_reported_unverified(tmp_path, monkeypatch):
    _legacy_runtime(tmp_path, verified=False)
    monkeypatch.setattr(engines, "_run_cancellable", _healthy_audit)
    report = engines.check_engine(tmp_path, "transformers")
    assert report.healthy and not report.verified
    health = json.loads((tmp_path / "runtimes/transformers/health.json").read_text())
    assert health["healthy"] is True and health["verified"] is False
    assert not models.known_runtime_damage(tmp_path, "transformers")  # still usable


def test_repair_rebuilds_a_healthy_legacy_runtime_from_verified_packages(tmp_path, monkeypatch):
    _legacy_runtime(tmp_path, verified=False)
    monkeypatch.setattr(engines, "_run_cancellable", _healthy_audit)
    installs = []
    def rebuild(root, runtime, report, cancel, force=False):
        installs.append((runtime, force))
        models.write_receipt(runtime, models.worker_python(root, runtime).parent.parent, hash_verified=True)
    monkeypatch.setattr(engines, "install_worker_locked", rebuild)
    report = engines.repair_engine(tmp_path, "transformers")
    assert installs == [("transformers", True)]
    assert report.healthy and report.verified


def test_repair_leaves_a_healthy_verified_runtime_alone(tmp_path, monkeypatch):
    _legacy_runtime(tmp_path, verified=True)
    monkeypatch.setattr(engines, "_run_cancellable", _healthy_audit)
    def unexpected(*args, **kwargs):
        raise AssertionError("A verified healthy runtime must not be reinstalled")
    monkeypatch.setattr(engines, "install_worker_locked", unexpected)
    report = engines.repair_engine(tmp_path, "transformers")
    assert report.healthy and report.verified


def test_damaged_legacy_runtime_gets_a_clean_reinstall_not_a_package_patch(tmp_path, monkeypatch):
    _legacy_runtime(tmp_path, verified=False)
    def damaged(command, output, cancel, on_tick=None):
        assert "pip" not in command, "a targeted package repair would leave the rest unverified"
        Path(command[-2]).write_text(json.dumps({"checked_files": 100, "issues": [
            {"kind": "files", "package": "numpy", "detail": "Missing: numpy/core.dll"}]}))
        return 0
    monkeypatch.setattr(engines, "_run_cancellable", damaged)
    installs = []
    monkeypatch.setattr(engines, "install_worker_locked", lambda root, runtime, *args, **kwargs: installs.append(runtime))
    engines.repair_engine(tmp_path, "transformers")
    assert installs == ["transformers"]


def test_engines_panel_offers_repair_for_an_unverified_runtime(studio):
    panel = studio.engines_panel
    python = models.worker_python(studio.controller.root, "transformers")
    python.parent.mkdir(parents=True)
    python.touch()
    panel.on_report("transformers", engines.EngineReport("transformers", checked_files=50, verified=False))
    panel.refresh_rows()
    assert "hash verification" in panel.rows["transformers"]["state"].text()
    assert panel.rows["transformers"]["repair"].isEnabled()
    panel.on_report("transformers", engines.EngineReport("transformers", checked_files=50))
    panel.refresh_rows()
    assert "hash verification" not in panel.rows["transformers"]["state"].text()
    assert not panel.rows["transformers"]["repair"].isEnabled()


def test_receipt_from_an_older_hashed_lock_is_not_verified(tmp_path, monkeypatch):
    python = _legacy_runtime(tmp_path, verified=True)
    receipt = python.parent.parent / "installed.json"
    record = json.loads(receipt.read_text())
    monkeypatch.setattr(engines, "_run_cancellable", _healthy_audit)
    assert engines.check_engine(tmp_path, "transformers").verified
    record["hashes_sha256"] = "0" * 64  # installed against a different hashed lock
    receipt.write_text(json.dumps(record))
    report = engines.check_engine(tmp_path, "transformers")
    assert report.healthy and not report.verified
    del record["hashes_sha256"]
    receipt.write_text(json.dumps(record))
    assert not engines.check_engine(tmp_path, "transformers").verified


def test_check_restores_a_runtime_after_a_crash_mid_reinstall(tmp_path, monkeypatch):
    """Restart after the process died while the new runtime was half-built."""
    python = _legacy_runtime(tmp_path, verified=True)
    environment = python.parent.parent
    os.replace(environment, environment.with_name("transformers.previous"))
    (environment / "Lib/site-packages").mkdir(parents=True)  # partial new install, no receipt
    monkeypatch.setattr(engines, "_run_cancellable", _healthy_audit)
    report = engines.check_engine(tmp_path, "transformers")
    assert report.healthy and report.verified
    assert python.read_text() == "legacy interpreter"
    assert not environment.with_name("transformers.previous").exists()
