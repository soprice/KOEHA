using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("KOEHA Setup & Uninstaller")]
[assembly: AssemblyDescription("KOEHA Live Speech & Translation Studio Installer and Uninstaller")]
[assembly: AssemblyCompany("KOEHA")]
[assembly: AssemblyProduct("KOEHA")]
[assembly: AssemblyVersion("0.2.4.0")]
[assembly: AssemblyFileVersion("0.2.4.0")]

namespace KoehaSetup
{
    internal sealed class SubStream : Stream
    {
        private readonly Stream _inner;
        private readonly long _offset;
        private readonly long _length;

        public SubStream(Stream inner, long offset, long length)
        {
            _inner = inner;
            _offset = offset;
            _length = length;
            _inner.Position = _offset;
        }

        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return true; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { return _length; } }

        public override long Position
        {
            get { return _inner.Position - _offset; }
            set { Seek(value, SeekOrigin.Begin); }
        }

        public override void Flush() { }

        public override int Read(byte[] buffer, int offset, int count)
        {
            long remaining = _length - Position;
            if (remaining <= 0) return 0;
            if (count > remaining) count = (int)remaining;
            return _inner.Read(buffer, offset, count);
        }

        public override long Seek(long offset, SeekOrigin origin)
        {
            long target;
            if (origin == SeekOrigin.Begin) target = offset;
            else if (origin == SeekOrigin.Current) target = Position + offset;
            else target = _length + offset;

            if (target < 0) target = 0;
            if (target > _length) target = _length;
            _inner.Position = _offset + target;
            return target;
        }

        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
    }

    internal static class InstallerCore
    {
        public const string UninstallRegistrySubKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\KOEHA";
        private static readonly byte[] Magic = Encoding.ASCII.GetBytes("KOEHASFX");

        public static string DefaultTargetDir()
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.GetFullPath(Path.Combine(localAppData, "Programs", "KOEHA"));
        }

        public static string DefaultDataDir()
        {
            string overrideDir = Environment.GetEnvironmentVariable("KOEHA_DATA_DIR");
            if (!string.IsNullOrEmpty(overrideDir))
            {
                return Path.GetFullPath(overrideDir);
            }
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.GetFullPath(Path.Combine(localAppData, "KOEHA"));
        }

        public static string ResolveInstalledTargetDir(string exePath)
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallRegistrySubKey))
                {
                    if (key != null)
                    {
                        string regLoc = key.GetValue("InstallLocation") as string;
                        if (!string.IsNullOrEmpty(regLoc))
                        {
                            return Path.GetFullPath(regLoc);
                        }
                    }
                }
            }
            catch
            {
            }

            if (!string.IsNullOrEmpty(exePath))
            {
                string exeDir = Path.GetDirectoryName(exePath);
                if (!string.IsNullOrEmpty(exeDir) && File.Exists(Path.Combine(exeDir, "KOEHA.exe")))
                {
                    return Path.GetFullPath(exeDir);
                }
            }
            return DefaultTargetDir();
        }

        public static Stream OpenPayload(string exePath)
        {
            Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("KOEHA.zip");
            if (resource != null)
            {
                return resource;
            }
            FileStream fs = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (fs.Length < 24)
            {
                fs.Dispose();
                throw new InvalidDataException("Setup payload is missing.");
            }
            fs.Seek(-16, SeekOrigin.End);
            byte[] footer = new byte[16];
            int read = fs.Read(footer, 0, 16);
            if (read != 16)
            {
                fs.Dispose();
                throw new InvalidDataException("Could not read setup footer.");
            }
            for (int i = 0; i < 8; i++)
            {
                if (footer[8 + i] != Magic[i])
                {
                    fs.Dispose();
                    throw new InvalidDataException("Setup payload signature is invalid.");
                }
            }
            long zipLength = BitConverter.ToInt64(footer, 0);
            long zipOffset = fs.Length - 16 - zipLength;
            if (zipLength <= 0 || zipOffset <= 0)
            {
                fs.Dispose();
                throw new InvalidDataException("Setup payload offset is invalid.");
            }
            return new SubStream(fs, zipOffset, zipLength);
        }

        public static bool IsTargetRunning(string targetDir)
        {
            string exePath = Path.GetFullPath(Path.Combine(targetDir, "KOEHA.exe"));
            Process[] procs = Process.GetProcessesByName("KOEHA");
            foreach (Process p in procs)
            {
                try
                {
                    if (p.MainModule != null &&
                        string.Equals(Path.GetFullPath(p.MainModule.FileName), exePath, StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
                catch
                {
                }
            }
            return false;
        }

        public static void Install(
            string exePath,
            string targetDir,
            bool createDesktopShortcut,
            bool createStartMenuShortcut,
            bool registerUninstall,
            Action<int, int, string> onProgress)
        {
            targetDir = Path.GetFullPath(targetDir);
            if (IsTargetRunning(targetDir))
            {
                throw new InvalidOperationException("Close the running KOEHA application before updating it.");
            }

            if (UninstallerCore.HasReparsePoint(targetDir))
            {
                throw new IOException("Setup cannot write through a link: " + targetDir);
            }
            Directory.CreateDirectory(targetDir);
            bool updating = File.Exists(Path.Combine(targetDir, "KOEHA.exe")) &&
                Directory.Exists(Path.Combine(targetDir, "_internal"));
            long installedBytes = 0;
            using (Stream payload = OpenPayload(exePath))
            using (ZipArchive archive = new ZipArchive(payload, ZipArchiveMode.Read))
            {
                List<ZipArchiveEntry> files = new List<ZipArchiveEntry>();
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    if (!string.IsNullOrEmpty(entry.Name))
                    {
                        files.Add(entry);
                    }
                }
                if (files.Count == 0)
                {
                    throw new InvalidDataException("Setup archive contains no files.");
                }

                string targetRootWithSep = targetDir.EndsWith(Path.DirectorySeparatorChar.ToString())
                    ? targetDir
                    : targetDir + Path.DirectorySeparatorChar;
                List<string> destinations = new List<string>();

                foreach (ZipArchiveEntry entry in files)
                {
                    string relPath = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                    if (relPath.StartsWith("KOEHA" + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                    {
                        relPath = relPath.Substring(6);
                    }
                    string destPath = Path.GetFullPath(Path.Combine(targetDir, relPath));
                    if (!destPath.StartsWith(targetRootWithSep, StringComparison.OrdinalIgnoreCase))
                    {
                        throw new InvalidDataException("Invalid path in setup archive: " + entry.FullName);
                    }
                    if (UninstallerCore.HasReparsePoint(destPath))
                    {
                        throw new IOException("Setup cannot write through a link: " + destPath);
                    }
                    if (!updating && File.Exists(destPath))
                    {
                        throw new IOException("Choose another installation folder. This file already exists: " + destPath);
                    }
                    destinations.Add(destPath);
                }

                for (int i = 0; i < files.Count; i++)
                {
                    string destPath = destinations[i];
                    string parentDir = Path.GetDirectoryName(destPath);
                    if (!string.IsNullOrEmpty(parentDir))
                    {
                        Directory.CreateDirectory(parentDir);
                    }
                    if (onProgress != null)
                    {
                        onProgress(i + 1, files.Count, files[i].FullName);
                    }
                    files[i].ExtractToFile(destPath, updating);
                    installedBytes += files[i].Length;
                }
            }

            string installedExe = Path.Combine(targetDir, "KOEHA.exe");
            string internalDir = Path.Combine(targetDir, "_internal");
            if (!File.Exists(installedExe) || !Directory.Exists(internalDir))
            {
                throw new InvalidDataException("Installation verification failed: KOEHA.exe or _internal is missing.");
            }
            if (updating)
            {
                // Older KOEHA bundles installed these two files. Remove only the retired components.
                foreach (string relative in new string[] {
                    @"_internal\PySide6\Qt6VirtualKeyboard.dll",
                    @"_internal\PySide6\plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll"
                })
                {
                    string retired = Path.Combine(targetDir, relative);
                    if (File.Exists(retired) && !UninstallerCore.HasReparsePoint(retired))
                    {
                        File.Delete(retired);
                    }
                }
            }

            string uninstallerExe = Path.Combine(targetDir, "Uninstall-KOEHA.exe");
            if (!File.Exists(uninstallerExe) && !string.IsNullOrEmpty(exePath) && File.Exists(exePath))
            {
                try
                {
                    File.Copy(exePath, uninstallerExe, true);
                    installedBytes += new FileInfo(uninstallerExe).Length;
                }
                catch
                {
                }
            }

            if (createDesktopShortcut)
            {
                CreateShortcut(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), installedExe, targetDir);
            }
            if (createStartMenuShortcut)
            {
                CreateShortcut(Environment.GetFolderPath(Environment.SpecialFolder.Programs), installedExe, targetDir);
            }
            if (registerUninstall)
            {
                RegisterUninstallEntry(targetDir, installedExe, uninstallerExe, installedBytes);
            }
        }

        private static void CreateShortcut(string folder, string targetExe, string workingDir)
        {
            if (string.IsNullOrEmpty(folder) || !Directory.Exists(folder)) return;
            string shortcutPath = Path.Combine(folder, "KOEHA.lnk");
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null) return;
            object shell = Activator.CreateInstance(shellType);
            object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { shortcutPath });
            Type scType = shortcut.GetType();
            scType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetExe });
            scType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workingDir });
            scType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { targetExe + ",0" });
            scType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { "KOEHA - Live Speech & Translation Studio" });
            scType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
        }

        private static void RegisterUninstallEntry(string targetDir, string installedExe, string uninstallerExe, long installedBytes)
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallRegistrySubKey))
                {
                    if (key == null) return;
                    key.SetValue("DisplayName", "KOEHA");
                    key.SetValue("DisplayVersion", "0.2.4");
                    key.SetValue("Publisher", "KOEHA");
                    key.SetValue("DisplayIcon", installedExe);
                    key.SetValue("InstallLocation", targetDir);
                    int estimatedSize = (int)Math.Min(int.MaxValue, (installedBytes + 1023) / 1024);
                    key.SetValue("EstimatedSize", estimatedSize, RegistryValueKind.DWord);
                    key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                    key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    string uninstallCmd = string.Format("\"{0}\" --uninstall \"--target={1}\"", uninstallerExe, targetDir);
                    string quietUninstallCmd = string.Format("\"{0}\" --uninstall --silent \"--target={1}\"", uninstallerExe, targetDir);
                    key.SetValue("UninstallString", uninstallCmd);
                    key.SetValue("QuietUninstallString", quietUninstallCmd);
                }
            }
            catch
            {
            }
        }
    }

    internal sealed class UninstallInventory
    {
        public string TargetDir;
        public string DataDir;
        public long AppBytes;
        public long ModelsBytes;
        public long RuntimesBytes;
        public long UserDataBytes;
        public List<string> DetectedModels = new List<string>();
        public List<string> DetectedRuntimes = new List<string>();
        public bool HasUserData;
        public bool SizesComputed;

        private static readonly Dictionary<string, string> KnownModelNames = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            { "fun-asr", "Fun-ASR-Nano-2512" },
            { "nemotron", "Nemotron 3.5 ASR · 0.6B" },
            { "parakeet-ja", "Parakeet TDT-CTC · 0.6B JA" },
            { "qwen3-asr", "Qwen3 ASR · 1.7B" },
            { "whisper-large-v3", "Whisper Large v3" },
            { "whisper-large-v3-turbo", "Whisper Large v3 Turbo" }
        };

        private static readonly Dictionary<string, string> KnownRuntimeNames = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            { "llama", "llama.cpp (Vulkan)" },
            { "nemo", "NeMo (PyTorch CUDA)" },
            { "transformers", "Transformers (PyTorch CUDA)" }
        };

        public static string FormatBytes(long bytes)
        {
            if (bytes >= 1000000000L)
            {
                return string.Format(CultureInfo.InvariantCulture, "{0:0.00} GB", bytes / 1e9);
            }
            if (bytes >= 1000000L)
            {
                return string.Format(CultureInfo.InvariantCulture, "{0:0.0} MB", bytes / 1e6);
            }
            if (bytes > 0L)
            {
                return string.Format(CultureInfo.InvariantCulture, "{0:0} KB", Math.Max(1.0, bytes / 1e3));
            }
            return "0 MB";
        }

        public static UninstallInventory InspectQuick(string targetDir, string dataDir)
        {
            UninstallInventory inv = new UninstallInventory();
            inv.TargetDir = Path.GetFullPath(targetDir);
            inv.DataDir = Path.GetFullPath(dataDir);

            string modelsDir = Path.Combine(inv.DataDir, "models");
            if (Directory.Exists(modelsDir))
            {
                try
                {
                    string[] subdirs = Directory.GetDirectories(modelsDir);
                    Array.Sort(subdirs, StringComparer.OrdinalIgnoreCase);
                    foreach (string dir in subdirs)
                    {
                        if (DirectoryHasAnyFile(dir))
                        {
                            string id = Path.GetFileName(dir);
                            string friendly;
                            if (!KnownModelNames.TryGetValue(id, out friendly))
                            {
                                friendly = id;
                            }
                            inv.DetectedModels.Add(friendly);
                        }
                    }
                }
                catch
                {
                }
            }

            string runtimesDir = Path.Combine(inv.DataDir, "runtimes");
            if (Directory.Exists(runtimesDir))
            {
                try
                {
                    string[] subdirs = Directory.GetDirectories(runtimesDir);
                    Array.Sort(subdirs, StringComparer.OrdinalIgnoreCase);
                    foreach (string dir in subdirs)
                    {
                        if (DirectoryHasAnyFile(dir))
                        {
                            string id = Path.GetFileName(dir);
                            string friendly;
                            if (!KnownRuntimeNames.TryGetValue(id, out friendly))
                            {
                                friendly = id;
                            }
                            inv.DetectedRuntimes.Add(friendly);
                        }
                    }
                }
                catch
                {
                }
            }

            string vadDir = Path.Combine(inv.DataDir, "vad");
            if (Directory.Exists(vadDir) && DirectoryHasAnyFile(vadDir))
            {
                inv.DetectedRuntimes.Add("Silero VAD (ONNX)");
            }

            string downloadsDir = Path.Combine(inv.DataDir, "downloads");
            if (Directory.Exists(downloadsDir) && DirectoryHasAnyFile(downloadsDir))
            {
                inv.DetectedRuntimes.Add("Cached runtime archives");
            }

            if (Directory.Exists(inv.DataDir))
            {
                try
                {
                    foreach (string file in Directory.GetFiles(inv.DataDir))
                    {
                        string name = Path.GetFileName(file);
                        if (IsUserDataFile(name))
                        {
                            inv.HasUserData = true;
                            break;
                        }
                    }
                }
                catch
                {
                }
            }

            return inv;
        }

        public static void ComputeSizes(UninstallInventory inv)
        {
            inv.AppBytes = 0;
            foreach (string file in UninstallerCore.GetAppFiles(inv.TargetDir))
            {
                if (!UninstallerCore.HasReparsePoint(file)) inv.AppBytes += SafeFileLength(file);
            }
            inv.ModelsBytes = SafeDirectoryBytes(Path.Combine(inv.DataDir, "models"));
            long runtimes = SafeDirectoryBytes(Path.Combine(inv.DataDir, "runtimes"))
                          + SafeDirectoryBytes(Path.Combine(inv.DataDir, "vad"))
                          + SafeDirectoryBytes(Path.Combine(inv.DataDir, "downloads"));
            long userData = 0;
            if (Directory.Exists(inv.DataDir))
            {
                try
                {
                    foreach (string file in Directory.GetFiles(inv.DataDir))
                    {
                        string name = Path.GetFileName(file);
                        long len = SafeFileLength(file);
                        if (IsRuntimeLogFile(name))
                        {
                            runtimes += len;
                        }
                        else if (IsUserDataFile(name))
                        {
                            userData += len;
                        }
                    }
                }
                catch
                {
                }
            }
            inv.RuntimesBytes = runtimes;
            inv.UserDataBytes = userData;
            inv.SizesComputed = true;
        }

        public static bool IsRuntimeLogFile(string fileName)
        {
            if (string.IsNullOrEmpty(fileName)) return false;
            return fileName.EndsWith("-install.log", StringComparison.OrdinalIgnoreCase)
                || fileName.EndsWith("-check.log", StringComparison.OrdinalIgnoreCase)
                || fileName.EndsWith("-repair.log", StringComparison.OrdinalIgnoreCase)
                || string.Equals(fileName, "recognition.log", StringComparison.OrdinalIgnoreCase);
        }

        public static bool IsUserDataFile(string name)
        {
            return string.Equals(name, "settings.json", StringComparison.OrdinalIgnoreCase)
                || string.Equals(name, "history.sqlite3", StringComparison.OrdinalIgnoreCase)
                || string.Equals(name, "history.sqlite3-wal", StringComparison.OrdinalIgnoreCase)
                || string.Equals(name, "history.sqlite3-shm", StringComparison.OrdinalIgnoreCase)
                || string.Equals(name, "history.sqlite3-journal", StringComparison.OrdinalIgnoreCase);
        }

        private static bool DirectoryHasAnyFile(string dir)
        {
            try
            {
                foreach (string file in UninstallerCore.EnumerateFilesWithoutLinks(dir))
                {
                    if (!string.IsNullOrEmpty(file))
                    {
                        return true;
                    }
                }
                return false;
            }
            catch
            {
                return false;
            }
        }

        private static long SafeFileLength(string file)
        {
            try
            {
                return new FileInfo(file).Length;
            }
            catch
            {
                return 0L;
            }
        }

        private static long SafeDirectoryBytes(string dir)
        {
            if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return 0L;
            long total = 0L;
            try
            {
                foreach (string file in UninstallerCore.EnumerateFilesWithoutLinks(dir))
                {
                    try
                    {
                        total += SafeFileLength(file);
                    }
                    catch
                    {
                    }
                }
            }
            catch
            {
            }
            return total;
        }
    }

    internal static class UninstallerCore
    {
        public static bool HasReparsePoint(string path)
        {
            for (string current = path; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
            {
                try
                {
                    if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) return true;
                }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
            }
            return false;
        }

        public static IEnumerable<string> GetAppFiles(string targetDir)
        {
            string root = Path.GetFullPath(targetDir);
            string prefix = root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            Stream inventory = Assembly.GetExecutingAssembly().GetManifestResourceStream("KOEHA-files.txt");
            if (inventory == null) throw new InvalidDataException("The KOEHA file inventory is missing.");
            using (StreamReader reader = new StreamReader(inventory))
            {
                string relativePath;
                while ((relativePath = reader.ReadLine()) != null)
                {
                    string file = Path.GetFullPath(Path.Combine(root, relativePath));
                    if (!file.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) || file.Length == prefix.Length)
                    {
                        throw new InvalidDataException("Invalid path in KOEHA file inventory.");
                    }
                    yield return file;
                }
            }
        }

        public static IEnumerable<string> EnumerateFilesWithoutLinks(string directory)
        {
            if (!Directory.Exists(directory) || HasReparsePoint(directory)) yield break;
            foreach (string file in Directory.EnumerateFiles(directory))
            {
                if (!HasReparsePoint(file)) yield return file;
            }
            foreach (string child in Directory.EnumerateDirectories(directory))
            {
                foreach (string file in EnumerateFilesWithoutLinks(child)) yield return file;
            }
        }

        public static void Uninstall(
            string currentExePath,
            string targetDir,
            string dataDir,
            bool removeModels,
            bool removeRuntimes,
            bool removeUserData,
            bool removeShortcutsAndRegistry,
            Action<int, int, string> onProgress)
        {
            targetDir = Path.GetFullPath(targetDir);
            dataDir = Path.GetFullPath(dataDir);
            currentExePath = Path.GetFullPath(currentExePath);
            if (HasReparsePoint(targetDir))
            {
                throw new IOException("KOEHA cannot uninstall through a link: " + targetDir);
            }
            if (InstallerCore.IsTargetRunning(targetDir))
            {
                throw new InvalidOperationException("Close the running KOEHA application before uninstalling it.");
            }

            List<string> filesToDelete = new List<string>(GetAppFiles(targetDir));
            HashSet<string> dirsToClean = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string file in filesToDelete)
            {
                AddParentDirectories(file, targetDir, dirsToClean);
            }

            if ((removeModels || removeRuntimes || removeUserData) && Directory.Exists(dataDir) && !HasReparsePoint(dataDir))
            {
                List<string> ownedDirs = new List<string>();
                if (removeModels) ownedDirs.Add("models");
                if (removeRuntimes) ownedDirs.AddRange(new string[] { "runtimes", "vad", "downloads" });
                foreach (string name in ownedDirs)
                {
                    foreach (string file in EnumerateFilesWithoutLinks(Path.Combine(dataDir, name)))
                    {
                        filesToDelete.Add(file);
                        AddParentDirectories(file, dataDir, dirsToClean);
                    }
                }
                foreach (string file in Directory.EnumerateFiles(dataDir))
                {
                    string name = Path.GetFileName(file);
                    if ((removeRuntimes && UninstallInventory.IsRuntimeLogFile(name)) ||
                        (removeUserData && UninstallInventory.IsUserDataFile(name)))
                    {
                        filesToDelete.Add(file);
                    }
                }
            }

            int total = Math.Max(1, filesToDelete.Count + 1);
            for (int i = 0; i < filesToDelete.Count; i++)
            {
                string file = filesToDelete[i];
                if (onProgress != null && (i % 15 == 0 || i == filesToDelete.Count - 1))
                {
                    onProgress(i + 1, total, Path.GetFileName(file));
                }
                if (!string.Equals(file, currentExePath, StringComparison.OrdinalIgnoreCase)) DeleteFile(file);
            }

            List<string> orderedDirs = new List<string>(dirsToClean);
            orderedDirs.Sort(delegate(string a, string b) { return b.Length.CompareTo(a.Length); });
            foreach (string dir in orderedDirs) DeleteEmptyDirectory(dir);

            if (removeShortcutsAndRegistry)
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(InstallerCore.UninstallRegistrySubKey))
                {
                    string registered = key == null ? null : key.GetValue("InstallLocation") as string;
                    if (string.Equals(registered, targetDir, StringComparison.OrdinalIgnoreCase))
                    {
                        DeleteFile(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "KOEHA.lnk"));
                        DeleteFile(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "KOEHA.lnk"));
                        Registry.CurrentUser.DeleteSubKey(InstallerCore.UninstallRegistrySubKey, false);
                    }
                }
            }

            if (onProgress != null) onProgress(total, total, "Finished");
        }

        public static void ScheduleSelfDeleteIfNeeded(string currentExePath, string targetDir)
        {
            string installedUninstaller = Path.GetFullPath(Path.Combine(targetDir, "Uninstall-KOEHA.exe"));
            if (!string.Equals(Path.GetFullPath(currentExePath), installedUninstaller, StringComparison.OrdinalIgnoreCase) ||
                HasReparsePoint(installedUninstaller))
            {
                return;
            }

            // Pass paths as data and wait for Windows to release the running executable.
            string script = @"Wait-Process -Id ([int]$env:KOEHA_UNINSTALL_PID) -ErrorAction SilentlyContinue
$exe = $env:KOEHA_UNINSTALL_EXE
for ($path = $exe; $path; $path = [IO.Path]::GetDirectoryName($path)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { exit 1 }
}
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        if (Test-Path -LiteralPath $exe) {
            Remove-Item -LiteralPath $exe -Force -ErrorAction Stop
        }
        break
    } catch {
        if ($attempt -eq 30) { throw }
        Start-Sleep -Seconds 1
    }
}
try { [IO.Directory]::Delete([IO.Path]::GetDirectoryName($exe), $false) } catch {}";
            string powershell = Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe");
            string arguments = "-NoProfile -NonInteractive -EncodedCommand " +
                Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
            ProcessStartInfo psi = new ProcessStartInfo(powershell, arguments);
            psi.CreateNoWindow = true;
            psi.UseShellExecute = false;
            psi.WorkingDirectory = Path.GetTempPath();
            psi.EnvironmentVariables["KOEHA_UNINSTALL_PID"] = Process.GetCurrentProcess().Id.ToString();
            psi.EnvironmentVariables["KOEHA_UNINSTALL_EXE"] = installedUninstaller;
            using (Process cleanup = Process.Start(psi)) { }
        }

        private static void AddParentDirectories(string file, string root, HashSet<string> directories)
        {
            string prefix = root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            for (string dir = Path.GetDirectoryName(file);
                 dir != null && dir.StartsWith(prefix, StringComparison.OrdinalIgnoreCase);
                 dir = Path.GetDirectoryName(dir))
            {
                directories.Add(dir);
            }
        }

        private static void DeleteFile(string path)
        {
            if (!File.Exists(path) || HasReparsePoint(path)) return;
            FileAttributes attrs = File.GetAttributes(path);
            if ((attrs & FileAttributes.ReadOnly) != 0) File.SetAttributes(path, attrs & ~FileAttributes.ReadOnly);
            File.Delete(path);
        }

        private static void DeleteEmptyDirectory(string dir)
        {
            if (!Directory.Exists(dir) || HasReparsePoint(dir)) return;
            using (IEnumerator<string> entries = Directory.EnumerateFileSystemEntries(dir).GetEnumerator())
            {
                if (entries.MoveNext()) return;
            }
            Directory.Delete(dir, false);
        }
    }

    internal sealed class SetupForm : Form
    {
        private readonly string _exePath;
        private readonly TextBox _pathBox;
        private readonly Button _browseButton;
        private readonly CheckBox _desktopCheck;
        private readonly CheckBox _startMenuCheck;
        private readonly CheckBox _launchCheck;
        private readonly ProgressBar _progressBar;
        private readonly Label _statusLabel;
        private readonly Button _installButton;
        private readonly Button _cancelButton;
        private bool _installed;
        private bool _installing;

        public SetupForm(string exePath, string initialTargetDir)
        {
            _exePath = exePath;
            Text = "KOEHA Setup";
            ClientSize = new Size(520, 316);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(18, 22, 31);
            ForeColor = Color.FromArgb(236, 240, 248);
            Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);

            try
            {
                Icon = Icon.ExtractAssociatedIcon(exePath);
            }
            catch
            {
            }

            Label titleLabel = new Label();
            titleLabel.Text = "KOEHA · Live Speech & Translation Studio";
            titleLabel.UseMnemonic = false;
            titleLabel.Font = new Font("Segoe UI Semibold", 13f, FontStyle.Bold);
            titleLabel.AutoSize = true;
            titleLabel.Location = new Point(24, 20);
            Controls.Add(titleLabel);

            Label subtitleLabel = new Label();
            subtitleLabel.Text = "Install KOEHA v0.2.4 for your Windows account. Models and settings stay in LocalAppData\\KOEHA.";
            subtitleLabel.UseMnemonic = false;
            subtitleLabel.ForeColor = Color.FromArgb(168, 178, 196);
            subtitleLabel.AutoSize = false;
            subtitleLabel.Size = new Size(472, 36);
            subtitleLabel.Location = new Point(24, 50);
            Controls.Add(subtitleLabel);

            Label folderLabel = new Label();
            folderLabel.Text = "Installation folder:";
            folderLabel.AutoSize = true;
            folderLabel.Location = new Point(24, 92);
            Controls.Add(folderLabel);

            _pathBox = new TextBox();
            _pathBox.Text = initialTargetDir;
            _pathBox.Location = new Point(24, 114);
            _pathBox.Size = new Size(378, 25);
            _pathBox.BackColor = Color.FromArgb(28, 34, 46);
            _pathBox.ForeColor = Color.FromArgb(236, 240, 248);
            _pathBox.BorderStyle = BorderStyle.FixedSingle;
            Controls.Add(_pathBox);

            _browseButton = new Button();
            _browseButton.Text = "Browse...";
            _browseButton.Size = new Size(86, 25);
            _browseButton.Location = new Point(410, 114);
            _browseButton.FlatStyle = FlatStyle.Flat;
            _browseButton.BackColor = Color.FromArgb(38, 46, 62);
            _browseButton.ForeColor = Color.FromArgb(220, 226, 238);
            _browseButton.Click += OnBrowseClick;
            Controls.Add(_browseButton);

            _desktopCheck = new CheckBox();
            _desktopCheck.Text = "Create Desktop shortcut";
            _desktopCheck.Checked = true;
            _desktopCheck.AutoSize = true;
            _desktopCheck.Location = new Point(24, 150);
            Controls.Add(_desktopCheck);

            _startMenuCheck = new CheckBox();
            _startMenuCheck.Text = "Create Start Menu shortcut";
            _startMenuCheck.Checked = true;
            _startMenuCheck.AutoSize = true;
            _startMenuCheck.Location = new Point(250, 150);
            Controls.Add(_startMenuCheck);

            _launchCheck = new CheckBox();
            _launchCheck.Text = "Launch KOEHA when installation completes";
            _launchCheck.Checked = true;
            _launchCheck.AutoSize = true;
            _launchCheck.Location = new Point(24, 178);
            Controls.Add(_launchCheck);

            _progressBar = new ProgressBar();
            _progressBar.Location = new Point(24, 214);
            _progressBar.Size = new Size(472, 18);
            _progressBar.Minimum = 0;
            _progressBar.Maximum = 1000;
            Controls.Add(_progressBar);

            _statusLabel = new Label();
            _statusLabel.Text = "Ready to install.";
            _statusLabel.UseMnemonic = false;
            _statusLabel.ForeColor = Color.FromArgb(168, 178, 196);
            _statusLabel.AutoSize = false;
            _statusLabel.Size = new Size(260, 24);
            _statusLabel.Location = new Point(24, 260);
            Controls.Add(_statusLabel);

            _installButton = new Button();
            _installButton.Text = "Install";
            _installButton.Size = new Size(96, 32);
            _installButton.Location = new Point(296, 254);
            _installButton.FlatStyle = FlatStyle.Flat;
            _installButton.BackColor = Color.FromArgb(59, 130, 246);
            _installButton.ForeColor = Color.White;
            _installButton.Click += OnInstallClick;
            Controls.Add(_installButton);

            _cancelButton = new Button();
            _cancelButton.Text = "Cancel";
            _cancelButton.Size = new Size(92, 32);
            _cancelButton.Location = new Point(404, 254);
            _cancelButton.FlatStyle = FlatStyle.Flat;
            _cancelButton.BackColor = Color.FromArgb(38, 46, 62);
            _cancelButton.ForeColor = Color.FromArgb(220, 226, 238);
            _cancelButton.Click += delegate { if (!_installing) Close(); };
            Controls.Add(_cancelButton);

            AcceptButton = _installButton;
            CancelButton = _cancelButton;
            ActiveControl = _installButton;
        }

        private void OnBrowseClick(object sender, EventArgs e)
        {
            if (_installing) return;
            using (FolderBrowserDialog dialog = new FolderBrowserDialog())
            {
                dialog.Description = "Choose the installation folder for KOEHA:";
                dialog.SelectedPath = _pathBox.Text.Trim();
                if (dialog.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(dialog.SelectedPath))
                {
                    _pathBox.Text = dialog.SelectedPath;
                }
            }
        }

        private void OnInstallClick(object sender, EventArgs e)
        {
            if (_installed)
            {
                if (_launchCheck.Checked)
                {
                    string exe = Path.Combine(_pathBox.Text.Trim(), "KOEHA.exe");
                    if (File.Exists(exe))
                    {
                        ProcessStartInfo psi = new ProcessStartInfo(exe);
                        psi.WorkingDirectory = Path.GetDirectoryName(exe);
                        Process.Start(psi);
                    }
                }
                Close();
                return;
            }

            string targetDir = _pathBox.Text.Trim();
            if (string.IsNullOrEmpty(targetDir))
            {
                MessageBox.Show(this, "Please choose an installation folder.", "KOEHA Setup", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            _installing = true;
            _installButton.Enabled = false;
            _cancelButton.Enabled = false;
            _browseButton.Enabled = false;
            _pathBox.ReadOnly = true;
            _desktopCheck.Enabled = false;
            _startMenuCheck.Enabled = false;
            bool makeDesktop = _desktopCheck.Checked;
            bool makeStart = _startMenuCheck.Checked;

            Thread worker = new Thread(delegate()
            {
                try
                {
                    InstallerCore.Install(_exePath, targetDir, makeDesktop, makeStart, true, delegate(int current, int total, string relPath)
                    {
                        int permille = total > 0 ? (int)((current * 1000L) / total) : 0;
                        BeginInvoke((MethodInvoker)delegate
                        {
                            _progressBar.Value = Math.Min(1000, Math.Max(0, permille));
                            _statusLabel.Text = string.Format("Installing ({0}/{1})...", current, total);
                        });
                    });
                    BeginInvoke((MethodInvoker)delegate
                    {
                        _installing = false;
                        _installed = true;
                        _progressBar.Value = 1000;
                        _statusLabel.Text = "Installation complete.";
                        _installButton.Text = "Finish";
                        _installButton.Enabled = true;
                        _cancelButton.Text = "Close";
                        _cancelButton.Enabled = true;
                    });
                }
                catch (Exception ex)
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        _installing = false;
                        _statusLabel.Text = "Installation failed.";
                        _installButton.Enabled = true;
                        _cancelButton.Enabled = true;
                        _browseButton.Enabled = true;
                        _pathBox.ReadOnly = false;
                        _desktopCheck.Enabled = true;
                        _startMenuCheck.Enabled = true;
                        MessageBox.Show(this, ex.Message, "KOEHA Setup Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    });
                }
            });
            worker.IsBackground = true;
            worker.Start();
        }
    }

    internal sealed class UninstallForm : Form
    {
        private readonly string _exePath;
        private readonly string _targetDir;
        private readonly string _dataDir;
        private readonly UninstallInventory _inventory;
        private readonly Label _appLabel;
        private readonly CheckBox _removeModelsCheck;
        private readonly CheckBox _removeRuntimesCheck;
        private readonly CheckBox _removeUserDataCheck;
        private readonly Label _modelsDetailLabel;
        private readonly Label _runtimesDetailLabel;
        private readonly Label _totalSpaceLabel;
        private readonly ProgressBar _progressBar;
        private readonly Label _statusLabel;
        private readonly Button _uninstallButton;
        private readonly Button _cancelButton;
        private bool _uninstalling;
        private bool _uninstalled;

        public UninstallForm(string exePath, string targetDir, string dataDir, bool syncScan)
        {
            _exePath = exePath;
            _targetDir = Path.GetFullPath(targetDir);
            _dataDir = Path.GetFullPath(dataDir);
            _inventory = UninstallInventory.InspectQuick(_targetDir, _dataDir);
            if (syncScan)
            {
                UninstallInventory.ComputeSizes(_inventory);
            }

            Text = "KOEHA Uninstaller";
            ClientSize = new Size(560, 476);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(18, 22, 31);
            ForeColor = Color.FromArgb(236, 240, 248);
            Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);

            try
            {
                Icon = Icon.ExtractAssociatedIcon(exePath);
            }
            catch
            {
            }

            Label titleLabel = new Label();
            titleLabel.Text = "Uninstall KOEHA · Live Speech & Translation Studio";
            titleLabel.UseMnemonic = false;
            titleLabel.Font = new Font("Segoe UI Semibold", 13f, FontStyle.Bold);
            titleLabel.AutoSize = true;
            titleLabel.Location = new Point(24, 18);
            Controls.Add(titleLabel);

            Label subtitleLabel = new Label();
            subtitleLabel.Text = "Remove KOEHA and clean up downloaded AI models, engines, and runtime files.";
            subtitleLabel.UseMnemonic = false;
            subtitleLabel.ForeColor = Color.FromArgb(168, 178, 196);
            subtitleLabel.AutoSize = false;
            subtitleLabel.Size = new Size(512, 22);
            subtitleLabel.Location = new Point(24, 46);
            Controls.Add(subtitleLabel);

            Panel warningBanner = new Panel();
            warningBanner.Location = new Point(24, 72);
            warningBanner.Size = new Size(512, 46);
            warningBanner.BackColor = Color.FromArgb(44, 32, 24);
            warningBanner.BorderStyle = BorderStyle.FixedSingle;

            Label warningLabel = new Label();
            warningLabel.Text = "Notice: KOEHA downloaded AI models and inference runtimes in LocalAppData\\KOEHA. They are selected for removal by default below.";
            warningLabel.UseMnemonic = false;
            warningLabel.ForeColor = Color.FromArgb(252, 211, 130);
            warningLabel.Font = new Font("Segoe UI", 8.75f, FontStyle.Regular);
            warningLabel.Location = new Point(10, 6);
            warningLabel.Size = new Size(490, 34);
            warningBanner.Controls.Add(warningLabel);
            Controls.Add(warningBanner);

            Panel cardPanel = new Panel();
            cardPanel.Location = new Point(24, 128);
            cardPanel.Size = new Size(512, 236);
            cardPanel.BackColor = Color.FromArgb(24, 30, 42);
            cardPanel.BorderStyle = BorderStyle.FixedSingle;

            Font boldCheckFont = new Font("Segoe UI Semibold", 9.5f, FontStyle.Bold);
            Font detailFont = new Font("Segoe UI", 8.5f, FontStyle.Regular);
            Color mutedColor = Color.FromArgb(148, 163, 184);

            _appLabel = new Label();
            _appLabel.Text = "✓  KOEHA Application & Shortcuts";
            _appLabel.UseMnemonic = false;
            _appLabel.Font = boldCheckFont;
            _appLabel.ForeColor = Color.FromArgb(236, 240, 248);
            _appLabel.AutoSize = true;
            _appLabel.Location = new Point(14, 12);
            cardPanel.Controls.Add(_appLabel);

            Label appDetail = new Label();
            appDetail.Text = "Folder: " + _targetDir;
            appDetail.UseMnemonic = false;
            appDetail.Font = detailFont;
            appDetail.ForeColor = mutedColor;
            appDetail.AutoSize = false;
            appDetail.Size = new Size(464, 18);
            appDetail.Location = new Point(34, 32);
            cardPanel.Controls.Add(appDetail);

            _removeModelsCheck = new CheckBox();
            _removeModelsCheck.Text = "Delete downloaded AI models (models)";
            _removeModelsCheck.UseMnemonic = false;
            _removeModelsCheck.Font = boldCheckFont;
            _removeModelsCheck.Checked = false;
            _removeModelsCheck.AutoSize = true;
            _removeModelsCheck.Location = new Point(14, 58);
            _removeModelsCheck.CheckedChanged += delegate { UpdateSummaryLabels(); };
            cardPanel.Controls.Add(_removeModelsCheck);

            _modelsDetailLabel = new Label();
            _modelsDetailLabel.UseMnemonic = false;
            _modelsDetailLabel.Font = detailFont;
            _modelsDetailLabel.ForeColor = mutedColor;
            _modelsDetailLabel.AutoSize = false;
            _modelsDetailLabel.Size = new Size(464, 32);
            _modelsDetailLabel.Location = new Point(34, 79);
            cardPanel.Controls.Add(_modelsDetailLabel);

            _removeRuntimesCheck = new CheckBox();
            _removeRuntimesCheck.Text = "Delete inference engines & runtimes (engine / runtime / VAD)";
            _removeRuntimesCheck.UseMnemonic = false;
            _removeRuntimesCheck.Font = boldCheckFont;
            _removeRuntimesCheck.Checked = false;
            _removeRuntimesCheck.AutoSize = true;
            _removeRuntimesCheck.Location = new Point(14, 118);
            _removeRuntimesCheck.CheckedChanged += delegate { UpdateSummaryLabels(); };
            cardPanel.Controls.Add(_removeRuntimesCheck);

            _runtimesDetailLabel = new Label();
            _runtimesDetailLabel.UseMnemonic = false;
            _runtimesDetailLabel.Font = detailFont;
            _runtimesDetailLabel.ForeColor = mutedColor;
            _runtimesDetailLabel.AutoSize = false;
            _runtimesDetailLabel.Size = new Size(464, 32);
            _runtimesDetailLabel.Location = new Point(34, 139);
            cardPanel.Controls.Add(_runtimesDetailLabel);

            _removeUserDataCheck = new CheckBox();
            _removeUserDataCheck.Text = "Delete user settings, profiles & session history";
            _removeUserDataCheck.UseMnemonic = false;
            _removeUserDataCheck.Font = boldCheckFont;
            _removeUserDataCheck.Checked = false;
            _removeUserDataCheck.AutoSize = true;
            _removeUserDataCheck.Location = new Point(14, 178);
            _removeUserDataCheck.CheckedChanged += delegate { UpdateSummaryLabels(); };
            cardPanel.Controls.Add(_removeUserDataCheck);

            Label userDataDetail = new Label();
            userDataDetail.Text = "Settings (settings.json), translation profiles & transcripts (history.sqlite3)";
            userDataDetail.UseMnemonic = false;
            userDataDetail.Font = detailFont;
            userDataDetail.ForeColor = mutedColor;
            userDataDetail.AutoSize = false;
            userDataDetail.Size = new Size(464, 18);
            userDataDetail.Location = new Point(34, 199);
            cardPanel.Controls.Add(userDataDetail);

            Controls.Add(cardPanel);

            _totalSpaceLabel = new Label();
            _totalSpaceLabel.UseMnemonic = false;
            _totalSpaceLabel.Font = new Font("Segoe UI Semibold", 9.5f, FontStyle.Bold);
            _totalSpaceLabel.ForeColor = Color.FromArgb(165, 229, 198);
            _totalSpaceLabel.AutoSize = false;
            _totalSpaceLabel.Size = new Size(512, 20);
            _totalSpaceLabel.Location = new Point(24, 374);
            Controls.Add(_totalSpaceLabel);

            _progressBar = new ProgressBar();
            _progressBar.Location = new Point(24, 400);
            _progressBar.Size = new Size(512, 16);
            _progressBar.Minimum = 0;
            _progressBar.Maximum = 1000;
            Controls.Add(_progressBar);

            _statusLabel = new Label();
            _statusLabel.Text = "Ready to uninstall.";
            _statusLabel.UseMnemonic = false;
            _statusLabel.ForeColor = Color.FromArgb(168, 178, 196);
            _statusLabel.AutoSize = false;
            _statusLabel.Size = new Size(300, 24);
            _statusLabel.Location = new Point(24, 430);
            Controls.Add(_statusLabel);

            _uninstallButton = new Button();
            _uninstallButton.Text = "Uninstall";
            _uninstallButton.Size = new Size(100, 32);
            _uninstallButton.Location = new Point(336, 426);
            _uninstallButton.FlatStyle = FlatStyle.Flat;
            _uninstallButton.BackColor = Color.FromArgb(185, 56, 64);
            _uninstallButton.ForeColor = Color.White;
            _uninstallButton.Click += OnUninstallClick;
            Controls.Add(_uninstallButton);

            _cancelButton = new Button();
            _cancelButton.Text = "Cancel";
            _cancelButton.Size = new Size(90, 32);
            _cancelButton.Location = new Point(446, 426);
            _cancelButton.FlatStyle = FlatStyle.Flat;
            _cancelButton.BackColor = Color.FromArgb(38, 46, 62);
            _cancelButton.ForeColor = Color.FromArgb(220, 226, 238);
            _cancelButton.Click += delegate { if (!_uninstalling) Close(); };
            Controls.Add(_cancelButton);

            AcceptButton = _uninstallButton;
            CancelButton = _cancelButton;
            ActiveControl = _uninstallButton;

            UpdateSummaryLabels();
            if (!syncScan)
            {
                StartBackgroundSizeScan();
            }
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            base.OnFormClosed(e);
            if (_uninstalled)
            {
                UninstallerCore.ScheduleSelfDeleteIfNeeded(_exePath, _targetDir);
            }
        }

        private void StartBackgroundSizeScan()
        {
            Thread scanner = new Thread(delegate()
            {
                UninstallInventory.ComputeSizes(_inventory);
                try
                {
                    if (IsHandleCreated && !IsDisposed)
                    {
                        BeginInvoke((MethodInvoker)delegate
                        {
                            UpdateSummaryLabels();
                        });
                    }
                }
                catch
                {
                }
            });
            scanner.IsBackground = true;
            scanner.Start();
        }

        private void UpdateSummaryLabels()
        {
            string modelsFound = _inventory.DetectedModels.Count > 0
                ? "Detected: " + string.Join(", ", _inventory.DetectedModels.ToArray())
                : "No downloaded models found in LocalAppData\\KOEHA\\models";
            _modelsDetailLabel.Text = modelsFound;

            string runtimesFound = _inventory.DetectedRuntimes.Count > 0
                ? "Detected: " + string.Join(", ", _inventory.DetectedRuntimes.ToArray())
                : "No inference engines or runtimes found in LocalAppData\\KOEHA\\runtimes";
            _runtimesDetailLabel.Text = runtimesFound;

            if (_inventory.SizesComputed)
            {
                _appLabel.Text = string.Format("✓  KOEHA Application & Shortcuts ({0})", UninstallInventory.FormatBytes(_inventory.AppBytes));
                _removeModelsCheck.Text = string.Format("Delete downloaded AI models ({0})", UninstallInventory.FormatBytes(_inventory.ModelsBytes));
                _removeRuntimesCheck.Text = string.Format("Delete inference engines & runtimes ({0})", UninstallInventory.FormatBytes(_inventory.RuntimesBytes));
                _removeUserDataCheck.Text = string.Format("Delete user settings, profiles & session history ({0})", UninstallInventory.FormatBytes(_inventory.UserDataBytes));

                long total = _inventory.AppBytes;
                if (_removeModelsCheck.Checked) total += _inventory.ModelsBytes;
                if (_removeRuntimesCheck.Checked) total += _inventory.RuntimesBytes;
                if (_removeUserDataCheck.Checked) total += _inventory.UserDataBytes;
                _totalSpaceLabel.Text = "Total disk space to be freed: " + UninstallInventory.FormatBytes(total);
            }
            else
            {
                _totalSpaceLabel.Text = "Calculating disk space used by models and runtimes...";
            }
        }

        private void OnUninstallClick(object sender, EventArgs e)
        {
            if (_uninstalled)
            {
                Close();
                return;
            }

            bool rmModels = _removeModelsCheck.Checked;
            bool rmRuntimes = _removeRuntimesCheck.Checked;
            bool rmUserData = _removeUserDataCheck.Checked;

            _uninstalling = true;
            _uninstallButton.Enabled = false;
            _cancelButton.Enabled = false;
            _removeModelsCheck.Enabled = false;
            _removeRuntimesCheck.Enabled = false;
            _removeUserDataCheck.Enabled = false;
            _statusLabel.Text = "Stopping running processes and removing files...";

            Thread worker = new Thread(delegate()
            {
                try
                {
                    UninstallerCore.Uninstall(
                        _exePath,
                        _targetDir,
                        _dataDir,
                        rmModels,
                        rmRuntimes,
                        rmUserData,
                        true,
                        delegate(int current, int total, string item)
                        {
                            int permille = total > 0 ? (int)((current * 1000L) / total) : 0;
                            BeginInvoke((MethodInvoker)delegate
                            {
                                _progressBar.Value = Math.Min(1000, Math.Max(0, permille));
                                _statusLabel.Text = string.Format("Removing ({0}/{1}): {2}", current, total, item);
                            });
                        });
                    BeginInvoke((MethodInvoker)delegate
                    {
                        _uninstalling = false;
                        _uninstalled = true;
                        _progressBar.Value = 1000;
                        _statusLabel.Text = "KOEHA has been uninstalled.";
                        _uninstallButton.Text = "Finish";
                        _uninstallButton.BackColor = Color.FromArgb(59, 130, 246);
                        _uninstallButton.Enabled = true;
                        _cancelButton.Text = "Close";
                        _cancelButton.Enabled = true;
                    });
                }
                catch (Exception ex)
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        _uninstalling = false;
                        _statusLabel.Text = "Uninstall encountered an error.";
                        _uninstallButton.Enabled = true;
                        _cancelButton.Enabled = true;
                        _removeModelsCheck.Enabled = true;
                        _removeRuntimesCheck.Enabled = true;
                        _removeUserDataCheck.Enabled = true;
                        MessageBox.Show(this, ex.Message, "KOEHA Uninstaller Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    });
                }
            });
            worker.IsBackground = true;
            worker.Start();
        }
    }

    internal static class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            string exePath = Assembly.GetExecutingAssembly().Location;
            string exeName = Path.GetFileNameWithoutExtension(exePath) ?? "";
            bool uninstallMode = exeName.StartsWith("Uninstall", StringComparison.OrdinalIgnoreCase);
            string targetDir = null;
            string dataDir = InstallerCore.DefaultDataDir();
            bool silent = false;
            bool createShortcuts = true;
            bool removeModels = false;
            bool removeRuntimes = false;
            bool removeUserData = false;
            string screenshotPath = null;

            foreach (string arg in args)
            {
                if (string.Equals(arg, "/U", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(arg, "/UNINSTALL", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(arg, "--uninstall", StringComparison.OrdinalIgnoreCase))
                {
                    uninstallMode = true;
                }
                else if (string.Equals(arg, "/S", StringComparison.OrdinalIgnoreCase) ||
                         string.Equals(arg, "/SILENT", StringComparison.OrdinalIgnoreCase) ||
                         string.Equals(arg, "/VERYSILENT", StringComparison.OrdinalIgnoreCase) ||
                         string.Equals(arg, "--silent", StringComparison.OrdinalIgnoreCase))
                {
                    silent = true;
                }
                else if (string.Equals(arg, "--no-shortcuts", StringComparison.OrdinalIgnoreCase))
                {
                    createShortcuts = false;
                }
                else if (string.Equals(arg, "--remove-models", StringComparison.OrdinalIgnoreCase))
                {
                    removeModels = true;
                }
                else if (string.Equals(arg, "--remove-runtimes", StringComparison.OrdinalIgnoreCase))
                {
                    removeRuntimes = true;
                }
                else if (string.Equals(arg, "--remove-data", StringComparison.OrdinalIgnoreCase))
                {
                    removeUserData = true;
                }
                else if (string.Equals(arg, "--keep-models", StringComparison.OrdinalIgnoreCase))
                {
                    removeModels = false;
                }
                else if (string.Equals(arg, "--keep-runtimes", StringComparison.OrdinalIgnoreCase))
                {
                    removeRuntimes = false;
                }
                else if (string.Equals(arg, "--keep-settings", StringComparison.OrdinalIgnoreCase) ||
                         string.Equals(arg, "--keep-data", StringComparison.OrdinalIgnoreCase))
                {
                    removeUserData = false;
                }
                else if (arg.StartsWith("/D=", StringComparison.OrdinalIgnoreCase))
                {
                    targetDir = arg.Substring(3).Trim('"');
                }
                else if (arg.StartsWith("--target=", StringComparison.OrdinalIgnoreCase))
                {
                    targetDir = arg.Substring(9).Trim('"');
                }
                else if (arg.StartsWith("--data-dir=", StringComparison.OrdinalIgnoreCase))
                {
                    dataDir = arg.Substring(11).Trim('"');
                }
                else if (arg.StartsWith("--screenshot=", StringComparison.OrdinalIgnoreCase))
                {
                    screenshotPath = arg.Substring(13).Trim('"');
                }
            }

            if (string.IsNullOrEmpty(targetDir))
            {
                targetDir = uninstallMode ? InstallerCore.ResolveInstalledTargetDir(exePath) : InstallerCore.DefaultTargetDir();
            }

            if (!string.IsNullOrEmpty(screenshotPath))
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                using (Form form = uninstallMode
                    ? (Form)new UninstallForm(exePath, targetDir, dataDir, true)
                    : (Form)new SetupForm(exePath, targetDir))
                {
                    form.Show();
                    form.Refresh();
                    Application.DoEvents();
                    using (Bitmap bmp = new Bitmap(form.Width, form.Height))
                    {
                        form.DrawToBitmap(bmp, new Rectangle(0, 0, form.Width, form.Height));
                        bmp.Save(screenshotPath, System.Drawing.Imaging.ImageFormat.Png);
                    }
                }
                return 0;
            }

            if (uninstallMode)
            {
                if (silent)
                {
                    try
                    {
                        UninstallerCore.Uninstall(exePath, targetDir, dataDir, removeModels, removeRuntimes, removeUserData, createShortcuts, null);
                        UninstallerCore.ScheduleSelfDeleteIfNeeded(exePath, targetDir);
                        return 0;
                    }
                    catch (Exception ex)
                    {
                        Console.Error.WriteLine(ex.Message);
                        return 1;
                    }
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new UninstallForm(exePath, targetDir, dataDir, false));
                return 0;
            }

            if (silent)
            {
                try
                {
                    InstallerCore.Install(exePath, targetDir, createShortcuts, createShortcuts, createShortcuts, null);
                    return 0;
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine(ex.Message);
                    return 1;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm(exePath, targetDir));
            return 0;
        }
    }
}
