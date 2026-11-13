"""Build a windowed, self-contained Windows application and per-user installer bundle."""
import contextlib
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import distribution, version
from pathlib import Path

import PySide6.QtCore


def bundle_notices(root: Path, output: Path) -> None:
    notices = root / "docs"
    shutil.copy2(notices / "THIRD_PARTY_NOTICES.md", output)
    shutil.copytree(notices / "licenses", output / "licenses")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", output / "licenses/Python-LICENSE.txt")
    manifest_path = root / "scripts/release-sources.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if PySide6.QtCore.qVersion() != manifest["qt_version"]:
        raise SystemExit("Update the Qt source archive before building this Qt version.")
    for name, locked in manifest["wheel_versions"].items():
        if version(name) != locked:
            raise SystemExit(f"Update the source archive for {name} before building.")
    shutil.copy2(manifest_path, output)

    wheels = (
        "av", "certifi", "h11", "httpcore", "httpx", "idna", "keyring", "numpy",
        "onnxruntime", "pyaudiowpatch", "pyside6-essentials", "shiboken6",
    )
    for name in wheels:
        wheel = distribution(name)
        for file in wheel.files or ():
            directory, *parts = file.parts
            if not directory.endswith(".dist-info") or not parts:
                continue
            if parts[0] != "licenses" and not parts[0].upper().startswith(("LICENSE", "NOTICE", "COPYING")):
                continue
            source = wheel.locate_file(file)
            if source.is_file():
                target = output.joinpath("licenses", "wheels", name, *parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)


def sign_executable(exe_path: Path) -> None:
    pfx = os.environ.get("KOEHA_SIGN_PFX", "").strip()
    thumbprint = os.environ.get("KOEHA_SIGN_THUMBPRINT", "").strip()
    timestamp = os.environ.get("KOEHA_SIGN_TIMESTAMP", "http://timestamp.digicert.com").strip()
    if not pfx and not thumbprint:
        return
    if pfx:
        script = (
            "$pwd = ConvertTo-SecureString -String $env:KOEHA_SIGN_PASSWORD -AsPlainText -Force; "
            "$cert = Get-PfxCertificate -FilePath $env:KOEHA_SIGN_PFX -Password $pwd; "
            "Set-AuthenticodeSignature -FilePath $args[0] -Certificate $cert "
            "-HashAlgorithm SHA256 -TimestampServer $args[1] | Out-Null"
        )
    else:
        script = (
            "$cert = Get-ChildItem Cert:\\CurrentUser\\My\\$env:KOEHA_SIGN_THUMBPRINT; "
            "Set-AuthenticodeSignature -FilePath $args[0] -Certificate $cert "
            "-HashAlgorithm SHA256 -TimestampServer $args[1] | Out-Null"
        )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script,
         str(exe_path), timestamp],
        check=True,
    )


def build_uninstaller_exe(root: Path, output_dir: Path, inventory: Path, windows: Path,
                          environment: dict[str, str]) -> Path:
    csc = windows / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not csc.is_file():
        raise SystemExit(f"C# compiler not found at {csc}")
    uninstaller_exe = output_dir / "Uninstall-KOEHA.exe"
    uninstaller_exe.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(csc), "/nologo", "/target:winexe", "/platform:x64", "/optimize+", f"/out:{uninstaller_exe}",
        f"/win32icon:{root / 'src/koeha/ui/assets/koeha.ico'}",
        f"/win32manifest:{root / 'scripts/setup.manifest'}",
        f"/resource:{inventory},KOEHA-files.txt",
        "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll",
        "/r:System.Windows.Forms.dll", "/r:System.IO.Compression.dll",
        "/r:System.IO.Compression.FileSystem.dll", str(root / "scripts/setup_stub.cs"),
    ], cwd=root, check=True, env=environment)
    sign_executable(uninstaller_exe)
    return uninstaller_exe


def build_setup_exe(root: Path, archive: Path, inventory: Path, windows: Path,
                    environment: dict[str, str]) -> Path:
    csc = windows / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not csc.is_file():
        raise SystemExit(f"C# compiler not found at {csc}")
    old_stub = root / "build/KOEHA-Setup-stub.exe"
    with contextlib.suppress(OSError):
        old_stub.unlink()
    setup_exe = root / "dist/KOEHA-Setup.exe"
    setup_exe.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(csc), "/nologo", "/target:winexe", "/platform:x64", "/optimize+", f"/out:{setup_exe}",
        f"/win32icon:{root / 'src/koeha/ui/assets/koeha.ico'}",
        f"/win32manifest:{root / 'scripts/setup.manifest'}",
        f"/resource:{archive},KOEHA.zip",
        f"/resource:{inventory},KOEHA-files.txt",
        "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll",
        "/r:System.Windows.Forms.dll", "/r:System.IO.Compression.dll",
        "/r:System.IO.Compression.FileSystem.dll", str(root / "scripts/setup_stub.cs"),
    ], cwd=root, check=True, env=environment)
    sign_executable(setup_exe)
    return setup_exe


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows desktop application on Windows")
    root = Path(__file__).resolve().parent.parent
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required to build the bundled runtime installer")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
               "--name", "KOEHA", "--icon", str(root / "src/koeha/ui/assets/koeha.ico"),
               "--paths", str(root / "src"), "--specpath", str(root / "build"),
               "--distpath", str(root / "dist"), "--workpath", str(root / "build/desktop"),
               "--hidden-import", "keyring.backends.Windows", "--collect-all", "onnxruntime",
               "--collect-all", "av", "--add-binary", f"{uv}{os.pathsep}tools"]
    for source, destination in [
        (root / "src/koeha/ui/assets", "koeha/ui/assets"),
        (root / "src/koeha/models/runtime_locks", "koeha/models/runtime_locks"),
        (root / "src/koeha/recognition/worker.py", "koeha/recognition"),
        (root / "src/koeha/models/runtime_audit.py", "koeha/models"),
    ]:
        command.extend(["--add-data", f"{source}{os.pathsep}{destination}"])
    command.append(str(root / "scripts/desktop_entry.py"))
    environment = os.environ.copy()
    # Keep unrelated application DLLs (for example another ICU build) out of dependency discovery.
    windows = Path(os.environ["WINDIR"])
    environment["PATH"] = os.pathsep.join(map(str, [Path(sys.executable).parent, Path(sys.base_prefix),
        Path(sys.base_prefix) / "DLLs", windows / "System32", windows]))
    subprocess.run(command, cwd=root, check=True, env=environment)
    output = root / "dist/KOEHA"
    # PyInstaller's QtGui hook collects input-context plugins even for a QWidget-only app.
    for path in (output / "_internal/PySide6").rglob("*"):
        if path.is_file() and any("virtualkeyboard" in part.lower() for part in path.relative_to(output).parts):
            path.unlink()
    bundle_notices(root, output)
    sign_executable(output / "KOEHA.exe")
    shutil.copy2(root / "scripts/Install-KOEHA.ps1", output)
    shutil.copy2(root / "scripts/Install-KOEHA.bat", output)
    shutil.copy2(root / "LICENSE", output)
    (output / "README.txt").write_text(
        "KOEHA for Windows x64\n\nDouble-click KOEHA.exe to run without installing.\n"
        "Keep the _internal folder next to KOEHA.exe.\n"
        "For Start menu and desktop shortcuts, double-click Install-KOEHA.bat.\n"
        "Settings and models stay in %LOCALAPPDATA%\\KOEHA.\n"
        "Model and engine downloads start only when requested in the app.\n"
        "See THIRD_PARTY_NOTICES.md for Qt, FFmpeg, license terms and corresponding sources.\n", encoding="utf-8")
    inventory = root / "build/KOEHA-files.txt"
    files = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    files.add("Uninstall-KOEHA.exe")
    inventory.write_text("\n".join(sorted(files)) + "\n", encoding="utf-8")
    build_uninstaller_exe(root, output, inventory, windows, environment)
    archive = Path(shutil.make_archive(str(root / "dist/KOEHA-Windows-x64"), "zip", root / "dist", "KOEHA"))
    setup_exe = build_setup_exe(root, archive, inventory, windows, environment)
    print(archive)
    print(setup_exe)


if __name__ == "__main__":
    main()
