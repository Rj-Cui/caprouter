#requires -Version 5.1
<#
    CapRouter release packaging script (Windows / PowerShell 5.1+)

    Run from the repository root:
        powershell -NoProfile -ExecutionPolicy Bypass -File .\release.ps1

    Output (dist\ directory; reruns overwrite same-named artifacts):
        caprouter-<version>.zip          runtime files (allowlist packaging)
        caprouter-<version>.zip.sha256   SHA256 checksum file (sha256sum format)
        RELEASE-NOTES.md                 version, file count, size, SHA256, three install steps

    The version is read from [project] version in pyproject.toml.

    The script packages only allowlisted entries, so .git/, .github/, tests/, eval/, build/,
    *.egg-info/, __pycache__/, dist/, workspace/, .caprouter/ never enter the archive.

    Exit codes: 0 on success, 1 on failure.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Output helpers (English progress messages)
# ---------------------------------------------------------------------------
function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Done([string]$Text) { Write-Host "    $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "    $Text" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# Packaging allowlist: only these top-level entries are packaged, nothing else
# ---------------------------------------------------------------------------
$ManifestEntries = @(
    'caprouter'
    'bin'
    'skills'
    '.claude-plugin'
    '.mcp.json'
    'schemas'
    'docs'
    'examples'
    'eval'
    'tests'
    'mods'
    'commands'
    'hooks'
    'tray'
    'packaging'
    'install.ps1'
    'install.bat'
    'README.md'
    'pyproject.toml'
    'LICENSE'
    'CHANGELOG.md'
    'SECURITY.md'
    'CONTRIBUTING.md'
)

# Conventional excluded directory names (matched exactly per path segment, so
# file names such as docs/tests.md are not affected)
$ExcludedDirNames = @(
    '.git'
    '.github'
    'build'
    'dist'
    'workspace'
    '.caprouter'
    '__pycache__'
)

# Conventional excluded file suffixes
$ExcludedFileSuffixes = @('.pyc', '.pyo')

function Test-IsExcluded([string]$RelativePath) {
    $segments = $RelativePath -split '/'
    foreach ($segment in $segments) {
        if ($ExcludedDirNames -contains $segment) { return $true }
        if ($segment.EndsWith('.egg-info', [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    $leaf = $segments[$segments.Count - 1]
    if ($leaf -eq '.DS_Store' -or $leaf -eq 'Thumbs.db') { return $true }
    foreach ($suffix in $ExcludedFileSuffixes) {
        if ($leaf.EndsWith($suffix, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Get-ProjectVersion([string]$PyprojectPath) {
    if (-not (Test-Path -LiteralPath $PyprojectPath)) {
        throw "pyproject.toml not found: $PyprojectPath"
    }
    $text = [System.IO.File]::ReadAllText($PyprojectPath)
    $match = [regex]::Match($text, '(?m)^\s*version\s*=\s*"([^"]+)"')
    if (-not $match.Success) {
        throw 'Cannot parse version from pyproject.toml (expected version = "x.y.z" under [project])'
    }
    $version = $match.Groups[1].Value.Trim()
    if ($version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+([0-9A-Za-z.+-]*)$') {
        throw "Malformed version in pyproject.toml: $version"
    }
    return $version
}

try {
    Write-Host ''
    Write-Host '  CapRouter release packaging' -ForegroundColor White
    Write-Host '  ------------------' -ForegroundColor DarkGray
    Write-Host ''

    # ------------------------------------------------------------------
    # 1. Locate the repository root (independent of the current directory)
    # ------------------------------------------------------------------
    $scriptDir = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($scriptDir)) {
        $commandPath = $MyInvocation.MyCommand.Path
        if (-not [string]::IsNullOrWhiteSpace($commandPath)) {
            $scriptDir = Split-Path -Parent $commandPath
        }
    }
    if ([string]::IsNullOrWhiteSpace($scriptDir)) {
        throw 'Cannot locate the directory containing release.ps1; run it with -File .\release.ps1.'
    }
    $repoRoot = (Resolve-Path -LiteralPath $scriptDir).Path
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'caprouter'))) {
        throw "release.ps1 must live in the CapRouter repository root (missing caprouter\ directory): $repoRoot"
    }
    $pyprojectPath = Join-Path $repoRoot 'pyproject.toml'
    $distDir = Join-Path $repoRoot 'dist'

    # ------------------------------------------------------------------
    # 2. Read the version
    # ------------------------------------------------------------------
    Write-Step 'Reading the version'
    $version = Get-ProjectVersion $pyprojectPath
    $zipName = "caprouter-$version.zip"
    $shaName = "$zipName.sha256"
    $zipPath = Join-Path $distDir $zipName
    $shaPath = Join-Path $distDir $shaName
    $notesPath = Join-Path $distDir 'RELEASE-NOTES.md'
    Write-Done "Version $version (from pyproject.toml)"

    # ------------------------------------------------------------------
    # 3. Prepare dist\ and clean up old artifacts (idempotent)
    # ------------------------------------------------------------------
    Write-Step "Preparing output directory $distDir"
    if (-not (Test-Path -LiteralPath $distDir)) {
        New-Item -ItemType Directory -Path $distDir -Force | Out-Null
    }
    $staleProducts = @(Get-ChildItem -LiteralPath $distDir -File -Force -ErrorAction SilentlyContinue | Where-Object {
            ($_.Name -like 'caprouter-*.zip*') -or ($_.Name -eq 'RELEASE-NOTES.md')
        })
    foreach ($stale in $staleProducts) {
        Remove-Item -LiteralPath $stale.FullName -Force
    }
    if ($staleProducts.Count -gt 0) {
        Write-Done "Removed $($staleProducts.Count) stale artifact(s) (overwrite mode)"
    } else {
        Write-Done 'Directory ready'
    }

    # ------------------------------------------------------------------
    # 4. Collect files from the allowlist
    # ------------------------------------------------------------------
    Write-Step 'Collecting release files'
    $files = New-Object System.Collections.Generic.List[object]
    $manifestSummary = New-Object System.Collections.Generic.List[object]
    $repoPrefix = $repoRoot.TrimEnd('\') + '\'

    foreach ($entry in $ManifestEntries) {
        $fullPath = Join-Path $repoRoot $entry
        if (-not (Test-Path -LiteralPath $fullPath)) {
            throw "Missing required release entry: $entry (incomplete repository?)"
        }
        $item = Get-Item -LiteralPath $fullPath -Force
        if ($item.PSIsContainer) {
            $found = @(Get-ChildItem -LiteralPath $fullPath -Recurse -File -Force -ErrorAction Stop)
            if ($found.Count -eq 0) {
                throw "Release directory is empty: $entry"
            }
            $added = 0
            foreach ($file in $found) {
                $relative = $file.FullName.Substring($repoPrefix.Length) -replace '\\', '/'
                if (Test-IsExcluded $relative) { continue }
                $files.Add([pscustomobject]@{
                        Path     = $relative
                        FullName = $file.FullName
                    })
                $added++
            }
            if ($added -eq 0) {
                throw "Release directory is empty after filtering: $entry"
            }
            $manifestSummary.Add([pscustomobject]@{ Entry = "$entry/"; Count = $added })
            Write-Done "$entry/: $added file(s)"
        } else {
            $relative = $item.Name
            if (Test-IsExcluded $relative) { continue }
            $files.Add([pscustomobject]@{
                    Path     = $relative
                    FullName = $item.FullName
                })
            $manifestSummary.Add([pscustomobject]@{ Entry = $entry; Count = 1 })
            Write-Done "${entry}: 1 file"
        }
    }

    if ($files.Count -eq 0) {
        throw 'No files were collected; cannot build a release archive'
    }
    $sortedFiles = @($files | Sort-Object -Property Path)
    Write-Done "Total: $($sortedFiles.Count) file(s)"

    # ------------------------------------------------------------------
    # 5. Build the zip (write .tmp first, rename on success to avoid partial output)
    # ------------------------------------------------------------------
    Write-Step "Building archive $zipName"
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $tempZipPath = "$zipPath.tmp"
    if (Test-Path -LiteralPath $tempZipPath) {
        Remove-Item -LiteralPath $tempZipPath -Force
    }
    $zipStream = $null
    $zipArchive = $null
    try {
        $zipStream = [System.IO.File]::Open($tempZipPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::ReadWrite)
        $zipArchive = New-Object -TypeName System.IO.Compression.ZipArchive -ArgumentList @(
            $zipStream,
            [System.IO.Compression.ZipArchiveMode]::Create
        )
        $index = 0
        foreach ($file in $sortedFiles) {
            $null = [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zipArchive,
                $file.FullName,
                $file.Path,
                [System.IO.Compression.CompressionLevel]::Optimal
            )
            $index++
            if (($index % 50) -eq 0) {
                Write-Done "Compressed $index / $($sortedFiles.Count)"
            }
        }
    } finally {
        if ($null -ne $zipArchive) { $zipArchive.Dispose() }
        if ($null -ne $zipStream) { $zipStream.Dispose() }
    }
    Move-Item -LiteralPath $tempZipPath -Destination $zipPath -Force
    Write-Done "Wrote $zipPath"

    # Read-back check: entry count must equal the number of collected files
    $verifyArchive = $null
    $entryCount = -1
    try {
        $verifyArchive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
        $entryCount = $verifyArchive.Entries.Count
    } finally {
        if ($null -ne $verifyArchive) { $verifyArchive.Dispose() }
    }
    if ($entryCount -ne $sortedFiles.Count) {
        throw "Archive entry count mismatch: archive $entryCount, file list $($sortedFiles.Count)"
    }
    Write-Done "Read-back check passed ($entryCount entries)"

    # ------------------------------------------------------------------
    # 6. Compute SHA256 and write .sha256 (lowercase hex + two spaces + file name)
    # ------------------------------------------------------------------
    Write-Step 'Computing SHA256 checksum'
    # .NET keeps this working on CI runners where Get-FileHash may be unavailable
    # (for example when PSModulePath is restricted by the parent process).
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($zipPath)
        try {
            $digest = $sha256.ComputeHash($stream)
        } finally {
            $stream.Dispose()
        }
    } finally {
        $sha256.Dispose()
    }
    $hash = -join ($digest | ForEach-Object { $_.ToString('x2') })
    if ($hash -notmatch '^[0-9a-f]{64}$') {
        throw "Unexpected SHA256 result: $hash"
    }
    $shaText = $hash + '  ' + $zipName + "`n"
    [System.IO.File]::WriteAllText($shaPath, $shaText, (New-Object System.Text.UTF8Encoding($false)))
    Write-Done "SHA256 $hash"
    Write-Done "Wrote $shaPath"

    # ------------------------------------------------------------------
    # 7. Generate RELEASE-NOTES.md
    # ------------------------------------------------------------------
    Write-Step "Writing release notes $notesPath"
    $zipLength = (Get-Item -LiteralPath $zipPath).Length
    $zipMegabytes = [string]::Format(
        [System.Globalization.CultureInfo]::InvariantCulture,
        '{0:0.00}',
        ($zipLength / 1MB)
    )
    $builtAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')

    $notes = New-Object System.Collections.Generic.List[string]
    $notes.Add("# CapRouter $version release bundle")
    $notes.Add('')
    $notes.Add('This directory is generated automatically by `release.ps1`; rerunning overwrites same-named artifacts.')
    $notes.Add('')
    $notes.Add('## Basics')
    $notes.Add('')
    $notes.Add("- Version: $version")
    $notes.Add("- Build time: $builtAt")
    $notes.Add("- File count: $($sortedFiles.Count) file(s)")
    $notes.Add("- Archive size: $zipLength bytes (about $zipMegabytes MB)")
    $notes.Add("- Archive name: $zipName")
    $notes.Add("- SHA256: $hash (lowercase hex)")
    $notes.Add('')
    $notes.Add('## Installation (three steps)')
    $notes.Add('')
    $notes.Add("1. Download/copy ``$zipName`` locally, right-click it and choose Extract All to any directory (for example ``C:\CapRouter``).")
    $notes.Add('2. Open the extracted directory and double-click `install.bat`; if Windows shows "Windows protected your PC", click "More info" then "Run anyway".')
    $notes.Add('3. When you see "CapRouter installation complete", close the window, open a new terminal and run `caprouter --version`; a printed version number means the installation succeeded.')
    $notes.Add('')
    $notes.Add('## Verify SHA256')
    $notes.Add('')
    $notes.Add('Open PowerShell in the directory containing the archive and run:')
    $notes.Add('')
    $notes.Add('```powershell')
    $notes.Add("Get-FileHash .\$zipName -Algorithm SHA256")
    $notes.Add('```')
    $notes.Add('')
    $notes.Add("The output must match the SHA256 above exactly. The sibling checksum file ``$shaName`` uses the format: SHA256 + two spaces + file name.")
    $notes.Add('')
    $notes.Add('## File list (by top-level entry)')
    $notes.Add('')
    foreach ($summaryItem in $manifestSummary) {
        $notes.Add("- $($summaryItem.Entry): $($summaryItem.Count) file(s)")
    }
    $notes.Add('')
    $notes.Add("Total: $($sortedFiles.Count) file(s)")
    $notes.Add('')
    $notes.Add('## Not included')
    $notes.Add('')
    $notes.Add('`.git/`, `.github/`, `tests/`, `eval/`, `build/`, `*.egg-info/`, `__pycache__/`, `dist/`, `workspace/`, `.caprouter/` and other development or runtime directories never enter the release archive.')
    $notes.Add('')
    $notes.Add('See `docs/install-windows.md` for installation details.')
    $notes.Add('')

    # The release notes are opened directly on Windows, so the BOM lets Notepad and
    # PowerShell 5.1 Get-Content detect the encoding correctly (.sha256 is pure ASCII
    # and stays BOM-free).
    $notesText = ($notes -join "`r`n") + "`r`n"
    [System.IO.File]::WriteAllText($notesPath, $notesText, (New-Object System.Text.UTF8Encoding($true)))
    Write-Done "Wrote $notesPath"

    # ------------------------------------------------------------------
    # 8. Summary
    # ------------------------------------------------------------------
    Write-Host ''
    Write-Host '  Release archive generated' -ForegroundColor Green
    Write-Host '  ------------------' -ForegroundColor DarkGray
    Write-Host "  Version:   $version"
    Write-Host "  Archive:   $zipPath"
    Write-Host "  Size:      $zipLength bytes (about $zipMegabytes MB)"
    Write-Host "  Files:     $($sortedFiles.Count)"
    Write-Host "  SHA256:    $hash"
    Write-Host "  Checksum:  $shaPath"
    Write-Host "  Notes:     $notesPath"
    Write-Host ''
    Write-Host "  Re-check hash: Get-FileHash .\dist\$zipName -Algorithm SHA256" -ForegroundColor DarkGray
    Write-Host ''
    exit 0
} catch {
    Write-Host ''
    Write-Host ('Release failed: ' + $_.Exception.Message) -ForegroundColor Red
    if (-not [string]::IsNullOrWhiteSpace($_.InvocationInfo.PositionMessage)) {
        Write-Host ('Error location: ' + $_.InvocationInfo.PositionMessage) -ForegroundColor Yellow
    }
    Write-Host 'Fix the problem and rerun: powershell -NoProfile -ExecutionPolicy Bypass -File .\release.ps1' -ForegroundColor Yellow
    exit 1
}
