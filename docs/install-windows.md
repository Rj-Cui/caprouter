# Windows Installation Guide (beginner-friendly)

CapRouter is a local capability bus: **no internet needed, no pip needed, no registry writes**.
The whole installation is only four steps: **download the ZIP → extract → double-click `install.bat` → see "Installation complete"**.

This document follows the file names in the release package, for example `caprouter-0.7.1.zip` (`<version>` changes with the version).

## Prerequisites

- Windows 10 / 11 (with the built-in PowerShell 5.1 or newer)
- Python 3.11 or newer

First check whether Python is installed: press `Win + R`, type `cmd` and press Enter, then run:

```bat
python --version
```

If you see `Python 3.11.x` or newer, you are good. If it is not installed, install it using either method (**be sure to check
Add python.exe to PATH** during installation):

```bat
winget install Python.Python.3.12
```

Or visit <https://www.python.org/downloads/> to download the installer. After installation, **reopen** the terminal and check again.

## Installation steps

### Step 1: Download the ZIP

Get `caprouter-<version>.zip` and save it to your Downloads folder. Release page:
<https://github.com/Rj-Cui/caprouter/releases> (direct download once the repository is public). If you received it from a chat tool or a cloud drive,
first confirm the download is complete (see "Verifying the installer package" below).

### Step 2: Extract

Right-click the ZIP → "Extract All"; the target directory can be anywhere, for example `<repo>`.

> ⚠️ Do not double-click the install script directly inside the archive; you must fully extract it first.

### Step 3: Double-click install.bat

Open the extracted directory and double-click `install.bat`. If "Windows protected your PC" appears,
click "More info" → "Run anyway".

The script automatically:

1. Finds Python 3.11+;
2. Copies the program to `%LOCALAPPDATA%\CapRouter` (the original ZIP / extracted directory can be deleted afterwards);
3. Creates the `caprouter` command and adds it to the user PATH;
4. Initializes the workspace and demonstrates one capability search.

### Step 4: Confirm the installation is complete

Once you see "CapRouter installation complete", you are done. **Open a new** terminal (PowerShell or CMD) and run:

```bat
caprouter --version
```

If it prints a version number, the installation succeeded.

## Verifying the installer package (optional)

Open PowerShell in the directory containing the ZIP and run:

```powershell
Get-FileHash .\caprouter-0.7.1.zip -Algorithm SHA256
```

Compare the output hash with the SHA256 in the release page/release notes (`RELEASE-NOTES.md`); if they match, you can install with confidence.
The release package also includes a `caprouter-0.7.1.zip.sha256` file whose content format is
"SHA256 lowercase hex + two spaces + file name".

## FAQ

### 1. "Python 3.11 or newer not found" is reported

This means there is no usable Python 3.11+ on the system, or PATH cannot find it.

- Install it with `winget install Python.Python.3.12` above;
- Or install from <https://www.python.org/downloads/> and check **Add python.exe to PATH**;
- After installation, **double-click** `install.bat` again (no need to reboot, but only new terminals inherit PATH).

### 2. PowerShell reports "Cannot load file ... because running scripts is disabled on this system"

You do not need to change the system execution policy. `install.bat` already includes `-ExecutionPolicy Bypass` internally,
**just double-click `install.bat`**.

You may only hit the execution policy block when manually right-clicking `install.ps1` → "Run with PowerShell".
If you really need to run it manually, use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

### 3. Antivirus false positive / installer package blocked

CapRouter's install scripts are plain-text `install.ps1` and `install.bat` with no compiled artifacts,
so you can open them in Notepad and inspect them yourself; copying the program and writing to PATH may trigger heuristic blocks by some security software.

- Choose "Allow to run" / add the extracted directory to the trusted zone;
- First verify the installer package with the SHA256 above to confirm the file has not been tampered with;
- Only obtain release packages from trusted channels; do not allow ZIPs from unknown sources.

## Uninstall

1. Delete `%LOCALAPPDATA%\CapRouter` (the program and the `caprouter` command);
2. Delete the workspace `%USERPROFILE%\CapRouter` as needed (it contains your runtime data; back it up before deleting);
3. Optional: remove the `%LOCALAPPDATA%\CapRouter` entry from the `Path` user variable.

## Related documentation

- `README.md` in the repository root: feature and command overview;
- `docs/`: design notes, roadmap and security hardening records.
