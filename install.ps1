#requires -Version 5.1
<#
    CapRouter one-click installer (Windows)

    For users who would rather not touch a command line:
      1. Download/extract the CapRouter directory
      2. Double-click install.bat
      3. When you see "installation complete" you are done; afterwards type caprouter in any terminal

    Features:
      - No pip / no network / no registry writes (except the optional user PATH)
      - Finds Python 3.11+ automatically; prints a winget hint when none is found
      - Copies the program to %LOCALAPPDATA%\CapRouter, so the repository can be deleted afterwards
      - Creates a workspace and runs init plus one search demo
      - With -Dsh, also generates the DeepSeek Harness plugin directory

    Parameters:
      -InstallDir  Install directory (default %LOCALAPPDATA%\CapRouter)
      -Workspace   Workspace directory (default %USERPROFILE%\CapRouter\workspace)
      -Dsh         Also integrate with DeepSeek Harness
      -NoPath      Do not modify the user PATH
      -SkipBootstrap  Install only, do not initialize the workspace
      -Force       Overwrite an existing installation
      -Yes         Do not prompt, use defaults everywhere
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'CapRouter'),
    [string]$Workspace = (Join-Path $env:USERPROFILE 'CapRouter\workspace'),
    [switch]$Dsh,
    [switch]$NoPath,
    [switch]$SkipBootstrap,
    [switch]$Force,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "    $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "    $Text" -ForegroundColor Yellow }

function Resolve-Python {
    $candidates = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { $candidates += [pscustomobject]@{ Exe = $py.Source; Pre = @('-3') } }
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += [pscustomobject]@{ Exe = $cmd.Source; Pre = @() } }
    }
    foreach ($candidate in $candidates) {
        try {
            $probeArgs = @($candidate.Pre) + @('-c', 'import sys; print(sys.version_info[0], sys.version_info[1])')
            $out = & $candidate.Exe @probeArgs 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $last = ($out | Select-Object -Last 1).ToString().Trim()
                $parts = $last -split '\s+'
                if ($parts.Count -ge 2) {
                    $major = [int]$parts[0]
                    $minor = [int]$parts[1]
                    if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                        return [pscustomobject]@{
                            Exe     = $candidate.Exe
                            Pre     = $candidate.Pre
                            Version = "$major.$minor"
                        }
                    }
                    Write-Warn2 "Skipping Python $major.$minor (3.11+ required): $($candidate.Exe)"
                }
            }
        } catch {
            continue
        }
    }
    return $null
}

try {
    Write-Host ''
    Write-Host '  CapRouter one-click install' -ForegroundColor White
    Write-Host '  ------------------' -ForegroundColor DarkGray
    Write-Host ''

    Write-Step 'Checking for Python 3.11+'
    $python = Resolve-Python
    if ($null -eq $python) {
        Write-Host ''
        Write-Host 'Python 3.11 or newer was not found.' -ForegroundColor Red
        Write-Host 'Install Python in any of the following ways, then double-click install.bat again:'
        Write-Host '    winget install Python.Python.3.12'
        Write-Host '    or download it from https://www.python.org/downloads/ (tick "Add python.exe to PATH" during setup)'
        Write-Host ''
        exit 1
    }
    Write-Ok "Python $($python.Version): $($python.Exe)"

    if (-not (Test-Path (Join-Path $RepoRoot 'caprouter'))) {
        throw "The installer must live in the CapRouter repository root (missing caprouter\ directory): $RepoRoot"
    }

    Write-Step "Preparing install directory $InstallDir"
    if (Test-Path $InstallDir) {
        if (-not $Force) {
            Write-Warn2 'Directory already exists; upgrading in place (workspace and config kept)'
        }
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $appDir = Join-Path $InstallDir 'app'
    $appReplaced = $true
    if (Test-Path $appDir) {
        try {
            Remove-Item -Recurse -Force $appDir
        } catch {
            $appReplaced = $false
        }
    }
    if (-not $appReplaced) {
        # A host (for example DSH) may still be running the old copy. Keep the
        # old directory and install side-by-side; the launcher is rewritten below.
        $appDir = Join-Path $InstallDir ('app-' + (Get-Date -Format 'yyyyMMddHHmmss'))
        Write-Warn2 ('Old directory is in use (a host may be running it); using a new directory: ' + $appDir)
        Write-Warn2 'Afterwards, remove the old plugin and add the new one in the host to finish the upgrade'
    }
    New-Item -ItemType Directory -Force -Path $appDir | Out-Null
    foreach ($name in @('caprouter', 'bin', 'skills', '.claude-plugin', 'hooks', 'commands', 'mods', '.mcp.json')) {
        $source = Join-Path $RepoRoot $name
        if (Test-Path $source) {
            Copy-Item -Recurse -Force $source (Join-Path $appDir $name)
        }
    }
    Get-ChildItem $appDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    $binScript = Join-Path $appDir 'bin\caprouter.py'
    if (-not (Test-Path $binScript)) { throw "Copy failed: $binScript does not exist" }
    Write-Ok "Program copied to $appDir"
    # Best-effort prune: keep the current app dir plus at most two previous
    # side-by-side copies created by an upgrade while a host was connected.
    try {
        $oldAppDirs = Get-ChildItem $InstallDir -Directory -Filter 'app-*' |
            Sort-Object Name -Descending | Select-Object -Skip 2
        foreach ($oldAppDir in $oldAppDirs) {
            try { Remove-Item -Recurse -Force $oldAppDir.FullName } catch { }
        }
    } catch { }

    Write-Step 'Creating the caprouter command'
    $launcher = Join-Path $InstallDir 'caprouter.cmd'
    $preArgs = ''
    if ($python.Pre.Count -gt 0) { $preArgs = ($python.Pre -join ' ') + ' ' }
    $launcherText = @(
        '@echo off',
        'chcp 65001 >nul',
        ('"' + $python.Exe + '" ' + $preArgs + '"' + $binScript + '" %*')
    ) -join "`r`n"
    [System.IO.File]::WriteAllText(
        $launcher,
        $launcherText + "`r`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
    Write-Ok $launcher

    if (-not $NoPath) {
        Write-Step 'Adding the install directory to the user PATH'
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if ($null -eq $userPath) { $userPath = '' }
        $parts = @($userPath.Split(';') | Where-Object { $_ -ne '' })
        if ($parts -notcontains $InstallDir) {
            $newPath = (($parts + $InstallDir) -join ';')
            [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
            Write-Ok 'User PATH updated (takes effect in new terminals)'
        } else {
            Write-Ok 'Already present in PATH, skipping'
        }
        if (-not (($env:Path.Split(';')) -contains $InstallDir)) {
            $env:Path = $env:Path + ';' + $InstallDir
        }
    } else {
        Write-Warn2 'PATH left unchanged as requested'
    }

    $summary = @()
    $summary += "Command:   $launcher"
    $summary += "Program:   $appDir"
    $summary += "Workspace: $Workspace"

    if (-not $SkipBootstrap) {
        Write-Step 'Initializing the workspace (about 1 second)'
        New-Item -ItemType Directory -Force -Path $Workspace | Out-Null
        & $python.Exe @($python.Pre + @($binScript, 'init', '--workspace', $Workspace, '--yes'))
        if ($LASTEXITCODE -ne 0) { throw "init failed (exit $LASTEXITCODE)" }
        Write-Ok 'Workspace ready'

        Write-Step 'Running a capability search demo'
        & $python.Exe @($python.Pre + @(
            $binScript, 'capability', 'search',
            '--workspace', $Workspace,
            '--need', 'Convert this PDF to Word'
        ))
        if ($LASTEXITCODE -ne 0) { Write-Warn2 'Search demo failed, but the installation itself is complete' }
    } else {
        Write-Warn2 'Skipping initialization as requested'
    }

    if (-not $Dsh -and -not $Yes) {
        if (Test-Path (Join-Path $env:USERPROFILE '.dsh')) {
            $answer = Read-Host 'DeepSeek Harness detected. Integrate with it as well (generate plugin directory + sync skills)? [Y/n]'
            if ($answer -eq '' -or $answer -match '^[Yy]') { $Dsh = $true }
        }
    }

    if ($Dsh) {
        Write-Step 'Integrating with DeepSeek Harness'
        $dshArgs = @(
            $binScript, 'setup',
            '--host', 'deepseek-harness',
            '--write',
            '--link-skills',
            '--workspace', $Workspace
        )
        if ($Force) { $dshArgs += '--force' }
        & $python.Exe @($python.Pre + $dshArgs)
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 'DSH plugin generation failed; CapRouter itself still works'
        } else {
            Write-Ok 'Plugin directory generated; add it once in DSH as shown below'
        }
        $summary += "DSH:    plugin directory generated (DSH -> Plugins -> Add local plugin)"
    }

    Write-Host ''
    Write-Host '  CapRouter installation complete' -ForegroundColor Green
    Write-Host '  ------------------' -ForegroundColor DarkGray
    foreach ($line in $summary) { Write-Host "  $line" }
    Write-Host ''
    Write-Host '  Use it directly next time (open a new terminal):'
    Write-Host ('    caprouter capability search --workspace "' + $Workspace + '" --need "Convert this PDF to Word"')
    Write-Host ('    caprouter status --workspace "' + $Workspace + '"')
    Write-Host ''
    exit 0
} catch {
    Write-Host ''
    Write-Host ('Installation failed: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Screenshot this screen when reporting the issue; or run manually: python bin\caprouter.py --version' -ForegroundColor Yellow
    exit 1
}
