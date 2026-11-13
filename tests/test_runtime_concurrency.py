"""Cross-process runtime safety: the per-runtime lock, Delete, Check, targeted Repair and crashes.

"Another KOEHA process" is a real second Python process holding runtime_lock. It reports
"held" and then waits on stdin, so every test controls exactly when the lock is held.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from koeha.models import catalog as models
from koeha.models import engines
from koeha.recognition import recognizer

SRC = str(Path(models.__file__).resolve().parents[2])
HOLD = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
        "from koeha.models.catalog import runtime_lock\n"
        "with runtime_lock(Path(sys.argv[2])):\n"
        "    print('held', flush=True); sys.stdin.readline()")
PROBE = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
         "from koeha.models.catalog import runtime_lock, RuntimeBusy\n"
         "try:\n    with runtime_lock(Path(sys.argv[2])): print('free')\n"
         "except RuntimeBusy: print('busy')")


@contextmanager
def other_process_holds_lock(environment):
    child = subprocess.Popen([sys.executable, "-c", HOLD, SRC, str(environment)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        yield child
    finally:
        if child.poll() is None:
            child.stdin.write("\n")
            child.stdin.flush()
        child.wait(timeout=30)


def other_process_can_lock(environment):
    result = subprocess.run([sys.executable, "-c", PROBE, SRC, str(environment)],
                            capture_output=True, text=True, timeout=60)
    return result.stdout.strip() == "free"


def runtime(root, previous=False):
    environment = models.worker_python(root, "transformers").parent.parent
    (environment / "Scripts").mkdir(parents=True)
    (environment / "Scripts/python.exe").write_text("working interpreter")
    models.write_receipt("transformers", environment, hash_verified=True)
    if previous:
        backup = models.backup_path(environment)
        (backup / "Scripts").mkdir(parents=True)
        (backup / "Scripts/python.exe").write_text("backup interpreter")
    for name in ("install", "check", "repair"):
        (root / f"transformers-{name}.log").write_text(name)
    return environment


def snapshot(root):
    """Every file and folder under the data root with its size and modification time."""
    return {str(p.relative_to(root)): (p.is_dir(), p.stat().st_size if p.is_file() else 0, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


def audit(issues=()):
    def run(command, output, cancel, on_tick=None):
        Path(command[-2]).write_text(json.dumps({"checked_files": 10, "issues": list(issues)}))
        return 0
    return run


# --- Delete ------------------------------------------------------------------

def test_delete_refuses_while_another_process_holds_the_lock(tmp_path):
    environment = runtime(tmp_path, previous=True)
    with other_process_holds_lock(environment):
        before = snapshot(tmp_path)
        with pytest.raises(models.RuntimeBusy):
            engines.delete_engine(tmp_path, "transformers")
        assert snapshot(tmp_path) == before  # runtime, backup, lock file and logs untouched


def test_delete_removes_runtime_backup_and_logs_but_keeps_the_lock_file(tmp_path):
    environment = runtime(tmp_path, previous=True)
    lock_file = environment.with_name("transformers.lock")
    engines.delete_engine(tmp_path, "transformers")
    assert not environment.exists() and not models.backup_path(environment).exists()
    assert not models.trash_path(environment).exists()
    assert not any(tmp_path.glob("transformers-*.log"))
    assert lock_file.is_file() and other_process_can_lock(environment)


@contextmanager
def runtime_process(environment):
    exe = environment / "Scripts/python.exe"
    shutil.copy(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/ping.exe", exe)
    worker = subprocess.Popen([str(exe), "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not models.processes_running_from(models._path_forms(environment)):
            assert time.monotonic() < deadline, "worker process never appeared"
            time.sleep(0.05)
        yield worker
    finally:
        worker.kill()
        worker.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="uses a real Windows process image")
def test_delete_refuses_a_runtime_that_recognition_is_running_from(tmp_path):
    environment = runtime(tmp_path)
    with runtime_process(environment):
        with pytest.raises(models.RuntimeInUse):
            engines.delete_engine(tmp_path, "transformers")
        assert (environment / "Scripts/python.exe").is_file() and (environment / "installed.json").is_file()
        assert not models.trash_path(environment).exists()


def test_delete_undoes_the_move_if_recognition_starts_during_it(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    answers = iter([[], [4242]])  # free before the rename, a worker found right after it
    monkeypatch.setattr(models, "processes_running_from", lambda forms: next(answers))
    with pytest.raises(models.RuntimeInUse):
        engines.delete_engine(tmp_path, "transformers")
    assert (environment / "Scripts/python.exe").read_text() == "working interpreter"
    assert not models.trash_path(environment).exists()


def test_reinstall_refuses_a_runtime_that_recognition_is_running_from(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    monkeypatch.setattr(models, "uv_executable", lambda: "uv")
    monkeypatch.setattr(models, "processes_running_from", lambda forms: [4242])
    def unexpected(*args, **kwargs):
        raise AssertionError("nothing may be installed while the runtime is in use")
    monkeypatch.setattr(models, "_run_cancellable", unexpected)
    with pytest.raises(models.RuntimeInUse):
        models.install_worker(tmp_path, "transformers", force=True)
    assert (environment / "Scripts/python.exe").read_text() == "working interpreter"
    assert not models.backup_path(environment).exists()


def test_restart_after_a_crash_mid_delete_discards_only_the_trash(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    trash = models.trash_path(environment)
    os.replace(environment, trash)  # Delete moved it aside, then the process died
    (trash / "Lib/site-packages/half_deleted").mkdir(parents=True)
    monkeypatch.setattr(engines, "_run_cancellable", audit())
    report = engines.check_engine(tmp_path, "transformers")
    assert not trash.exists()
    assert not report.healthy and report.issues[0]["detail"] == "Python runtime is missing"


# --- Check -------------------------------------------------------------------

def test_check_reports_busy_and_changes_nothing_while_another_process_installs(tmp_path, monkeypatch):
    environment = runtime(tmp_path, previous=True)  # looks like an interrupted reinstall
    (environment / "installed.json").unlink()
    def unexpected(*args, **kwargs):
        raise AssertionError("a busy check must not run the audit")
    monkeypatch.setattr(engines, "_run_cancellable", unexpected)
    with other_process_holds_lock(environment):
        before = snapshot(tmp_path)
        report = engines.check_engine(tmp_path, "transformers")
        assert snapshot(tmp_path) == before  # no recovery, no health.json, no log
    assert report.busy and not report.healthy and not report.issues
    assert models.backup_path(environment).is_dir()


def test_busy_check_does_not_create_a_missing_runtime_folder(tmp_path):
    environment = models.worker_python(tmp_path, "nemo").parent.parent
    with other_process_holds_lock(environment):
        report = engines.check_engine(tmp_path, "nemo")
    assert report.busy and not environment.exists()


def test_check_after_the_other_process_finishes_runs_normally(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    monkeypatch.setattr(engines, "_run_cancellable", audit())
    with other_process_holds_lock(environment):
        assert engines.check_engine(tmp_path, "transformers").busy
    report = engines.check_engine(tmp_path, "transformers")
    assert report.healthy and report.verified and not report.busy
    assert json.loads((environment / "health.json").read_text())["healthy"] is True


def test_engines_panel_shows_busy_and_offers_no_repair(studio):
    panel = studio.engines_panel
    python = models.worker_python(studio.controller.root, "transformers")
    python.parent.mkdir(parents=True)
    python.touch()
    panel.on_report("transformers", engines.EngineReport("transformers", verified=False, busy=True))
    panel.refresh_rows()
    assert "another KOEHA window" in panel.rows["transformers"]["state"].text()
    assert not panel.rows["transformers"]["repair"].isEnabled()
    assert panel.rows["transformers"]["details"].isHidden()


# --- Repair ------------------------------------------------------------------

def test_repair_refuses_while_another_process_holds_the_lock(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    monkeypatch.setattr(engines, "_run_cancellable", audit())
    with other_process_holds_lock(environment):
        before = snapshot(tmp_path)
        with pytest.raises(models.RuntimeBusy):
            engines.repair_engine(tmp_path, "transformers")
        assert snapshot(tmp_path) == before


@pytest.mark.skipif(os.name != "nt", reason="uses a real Windows process image")
def test_targeted_repair_refuses_live_runtime_and_repairs_after_worker_exits(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    package = environment / "Lib/site-packages/numpy/__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("damaged")
    issue = {"kind": "files", "package": "numpy", "detail": "Damaged file: numpy/__init__.py"}
    calls = []

    def run(command, output, cancel, on_tick=None):
        if "pip" in command:
            assert "--require-hashes" in command
            assert command[-2] == "numpy" and command[-1].startswith("numpy==")
            calls.append("pip")
            package.write_text("restored")
            return 0
        calls.append("audit")
        issues = [issue] if package.read_text() == "damaged" else []
        return audit(issues)(command, output, cancel)

    monkeypatch.setattr(engines, "_run_cancellable", run)
    monkeypatch.setattr(engines, "uv_executable", lambda: "uv")
    with models.runtime_lock(environment):
        pass  # Include the persistent lock file in the snapshot.
    with runtime_process(environment) as worker:
        before = snapshot(tmp_path)
        with pytest.raises(models.RuntimeInUse):
            engines.repair_engine(tmp_path, "transformers")
        assert worker.poll() is None
        assert not calls and snapshot(tmp_path) == before
        assert package.read_text() == "damaged"

    report = engines.repair_engine(tmp_path, "transformers")
    assert report.healthy and report.verified
    assert calls == ["audit", "pip", "audit"]
    assert package.read_text() == "restored"


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows runtime lock")
def test_worker_startup_locks_readiness_and_spawn_against_maintenance(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    model = models.by_id("whisper-large-v3-turbo")
    calls = []

    def ready(*_):
        calls.append("ready")
        assert not other_process_can_lock(environment)
        return True

    def spawn(*_, **__):
        calls.append("spawn")
        assert not other_process_can_lock(environment)
        raise OSError("test launch failure")

    monkeypatch.setattr(recognizer, "is_ready", ready)
    monkeypatch.setattr(recognizer, "external_popen", spawn)
    with other_process_holds_lock(environment):
        before = snapshot(tmp_path)
        with pytest.raises(models.RuntimeBusy):
            recognizer.Recognizer(tmp_path, model, "ja")
        assert not calls and snapshot(tmp_path) == before

    with pytest.raises(OSError, match="test launch failure"):
        recognizer.Recognizer(tmp_path, model, "ja")
    assert calls == ["ready", "spawn"]
    assert other_process_can_lock(environment)


def test_targeted_repair_holds_the_lock_from_first_check_through_receipt_and_final_check(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    observed = []
    repaired = []
    def run(command, output, cancel, on_tick=None):
        if "pip" in command:
            observed.append(("pip", other_process_can_lock(environment)))
            # Another KOEHA process tries to reinstall between the repair and its commit.
            attempt = subprocess.run([sys.executable, "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
                "from koeha.models.catalog import install_worker, RuntimeBusy\n"
                "try:\n    install_worker(Path(sys.argv[2]), 'transformers', force=True); print('installed')\n"
                "except RuntimeBusy: print('busy')", SRC, str(tmp_path)],
                capture_output=True, text=True, timeout=60)
            observed.append(("install", attempt.stdout.strip()))
            repaired.append(True)
            return 0
        observed.append(("audit", other_process_can_lock(environment)))
        issues = [] if repaired else [{"kind": "files", "package": "numpy", "detail": "Missing: numpy/core.dll"}]
        return audit(issues)(command, output, cancel)
    real_receipt = engines.write_receipt
    def receipt(*args, **kwargs):
        observed.append(("receipt", other_process_can_lock(environment)))
        return real_receipt(*args, **kwargs)
    monkeypatch.setattr(engines, "_run_cancellable", run)
    monkeypatch.setattr(engines, "uv_executable", lambda: "uv")
    monkeypatch.setattr(engines, "write_receipt", receipt)
    report = engines.repair_engine(tmp_path, "transformers")
    assert report.healthy and report.verified
    assert observed == [("audit", False), ("pip", False), ("install", "busy"),
                        ("receipt", False), ("audit", False)]
    assert other_process_can_lock(environment)  # released afterwards


def test_failed_targeted_repair_releases_the_lock_and_keeps_the_receipt(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    receipt = (environment / "installed.json").read_text()
    def run(command, output, cancel, on_tick=None):
        if "pip" in command:
            raise InterruptedError("Runtime repair cancelled")
        return audit([{"kind": "files", "package": "numpy", "detail": "Missing: numpy/core.dll"}])(
            command, output, cancel)
    monkeypatch.setattr(engines, "_run_cancellable", run)
    monkeypatch.setattr(engines, "uv_executable", lambda: "uv")
    with pytest.raises(InterruptedError):
        engines.repair_engine(tmp_path, "transformers")
    assert (environment / "installed.json").read_text() == receipt
    assert other_process_can_lock(environment)


@pytest.mark.skipif(os.name != "nt", reason="kills a process tree with taskkill")
def test_lock_of_a_killed_process_is_released_and_its_crash_is_recovered(tmp_path, monkeypatch):
    # The killed process was mid-reinstall: the working runtime is the backup, and the new
    # runtime is partial with no receipt.
    environment = models.worker_python(tmp_path, "transformers").parent.parent
    backup = models.backup_path(environment)
    (backup / "Scripts").mkdir(parents=True)
    (backup / "Scripts/python.exe").write_text("backup interpreter")
    models.write_receipt("transformers", backup, hash_verified=True)
    (environment / "Lib").mkdir(parents=True)
    with other_process_holds_lock(environment) as child:
        assert engines.check_engine(tmp_path, "transformers").busy
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(child.pid)], capture_output=True)
        child.wait(timeout=30)
    deadline = time.monotonic() + 10
    while not other_process_can_lock(environment):
        assert time.monotonic() < deadline, "the dead process's lock was never released"
    monkeypatch.setattr(engines, "_run_cancellable", audit())
    report = engines.check_engine(tmp_path, "transformers")
    assert report.healthy and not report.busy
    assert (environment / "Scripts/python.exe").read_text() == "backup interpreter"
    assert not models.backup_path(environment).exists()


# --- Win32 prototypes and move_aside failure handling ---------------------------

windows_only = pytest.mark.skipif(os.name != "nt", reason="Win32 process APIs")


@windows_only
def test_win32_process_apis_declare_pointer_sized_handles():
    import ctypes
    from ctypes import wintypes
    kernel32 = models._kernel32()
    assert ctypes.sizeof(wintypes.HANDLE) == ctypes.sizeof(ctypes.c_void_p)
    assert kernel32.OpenProcess.restype is wintypes.HANDLE
    assert kernel32.OpenProcess.argtypes == [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    assert kernel32.QueryFullProcessImageNameW.argtypes[0] is wintypes.HANDLE
    assert kernel32.CloseHandle.argtypes == [wintypes.HANDLE]
    for name in ("K32EnumProcesses", "QueryFullProcessImageNameW", "CloseHandle"):
        assert getattr(kernel32, name).restype is wintypes.BOOL
    for name in ("GetLongPathNameW", "GetShortPathNameW"):
        assert getattr(kernel32, name).restype is wintypes.DWORD


@windows_only
def test_handle_values_above_32_bits_reach_win32_unchanged():
    """Undeclared, ctypes converts arguments to C int and raises on a value this large."""
    import ctypes
    kernel32 = models._kernel32()
    buffer = ctypes.create_unicode_buffer(260)
    size = ctypes.c_ulong(260)
    ctypes.set_last_error(0)
    assert not kernel32.QueryFullProcessImageNameW((1 << 40) | 4, 0, buffer, ctypes.byref(size))
    assert ctypes.get_last_error() == 6  # ERROR_INVALID_HANDLE: Windows saw the full 64-bit value


@windows_only
def test_open_process_returns_a_usable_handle_for_this_process():
    kernel32 = models._kernel32()
    handle = kernel32.OpenProcess(0x1000, False, os.getpid())
    try:
        assert isinstance(handle, int) and handle > 0
    finally:
        assert kernel32.CloseHandle(handle)
    image = models.process_image(os.getpid())
    assert image and any(os.path.samefile(image, exe) for exe in {sys.executable, sys._base_executable}
                         if os.path.exists(exe))
    assert os.getpid() in models._process_ids()


@windows_only
def test_process_list_is_not_truncated_when_the_buffer_fills(monkeypatch):
    import ctypes
    real = models._kernel32()
    calls = []
    class Kernel32:
        def K32EnumProcesses(self, ids, size, needed):
            capacity = size // 4
            calls.append(capacity)
            count = capacity if len(calls) == 1 else 5000  # first answer exactly fills the buffer
            for index in range(count):
                ids[index] = index + 1
            ctypes.cast(needed, ctypes.POINTER(ctypes.c_ulong))[0] = count * 4
            return True
        def __getattr__(self, name):
            return getattr(real, name)
    monkeypatch.setattr(models, "_kernel32", lambda: Kernel32())
    assert len(models._process_ids()) == 5000
    assert calls == [4096, 8192]


@pytest.mark.parametrize("failure", [OSError("process scan failed"), KeyboardInterrupt()])
def test_move_aside_restores_the_runtime_if_the_second_scan_fails(tmp_path, monkeypatch, failure):
    environment = runtime(tmp_path)
    target = models.trash_path(environment)
    scans = iter([[], failure])
    def scan(forms):
        result = next(scans)
        if isinstance(result, BaseException):
            assert target.is_dir() and not environment.exists()  # the rename really happened
            raise result
        return result
    monkeypatch.setattr(models, "processes_running_from", scan)
    with pytest.raises(type(failure)):
        models.move_aside(environment, target)
    assert (environment / "Scripts/python.exe").read_text() == "working interpreter"
    assert not target.exists()


def test_move_aside_moves_nothing_if_the_first_scan_fails(tmp_path, monkeypatch):
    environment = runtime(tmp_path)
    def scan(forms):
        raise RuntimeError("Could not list running processes")
    monkeypatch.setattr(models, "processes_running_from", scan)
    with pytest.raises(RuntimeError, match="Could not list"):
        models.move_aside(environment, models.trash_path(environment))
    assert environment.is_dir() and not models.trash_path(environment).exists()


def test_delete_keeps_the_runtime_when_the_process_scan_fails_after_the_move(tmp_path, monkeypatch):
    environment = runtime(tmp_path, previous=True)
    scans = iter([[], OSError("process scan failed")])
    def scan(forms):
        result = next(scans)
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(models, "processes_running_from", scan)
    with pytest.raises(OSError, match="process scan failed"):
        engines.delete_engine(tmp_path, "transformers")
    assert (environment / "Scripts/python.exe").read_text() == "working interpreter"
    assert models.backup_path(environment).is_dir()  # nothing after the move ran
    assert not models.trash_path(environment).exists()
    assert other_process_can_lock(environment)  # the lock was released by the failure
