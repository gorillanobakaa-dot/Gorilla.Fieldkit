# Version: 1.0.1 - updated 26-10-23-19-00
<#
.SYNOPSIS
Install Fieldkit on a Windows computer that has no git: the GitHub zip, a private Python
environment in the folder, only the lean packages, then `fieldkit doctor --lean`.

.DESCRIPTION
The Windows twin of install.sh. Every download and every install is asked for first (y/N; anything
but y is no, and no stops the script). Nothing is installed outside the target folder: no PATH change,
no registry, no scheduled task, no shortcut. INSTALLED.txt in the folder lists every download and
install with its size, and the step the script stopped at.

Refused: a target folder that already has files, a target under OneDrive (synced to Microsoft's cloud)
(exit 2); a zip with an entry that would land outside the folder, a zip that is not Fieldkit (exit 3).
Exit 0 ready, 2 bad usage, 3 not ready (a no, a failed download, no Python 3.11+, doctor --lean failed).

Runs in Windows PowerShell 5.1 (the one every Windows 10/11 has):
  powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Target C:\Users\you\Fieldkit

Environment (the same as install.sh): FIELDKIT_ZIP_URL replaces the GitHub address (a file:// zip is
copied, not downloaded); FIELDKIT_SKIP_PIP=1 skips the PyPI step (tests, no network).

.PARAMETER Target
The folder Fieldkit goes into. Created if missing; refused if it has files or is under OneDrive.
.PARAMETER Branch
The GitHub branch whose zip is downloaded (main).
.PARAMETER Yes
Answer yes to every question (only for a run you have already read through).
#>
[CmdletBinding()]
param(
    [string]$Target = (Join-Path $env:USERPROFILE 'Fieldkit'),
    [string]$Branch = 'main',
    [switch]$Yes
)

$MinPython = [version]'3.11'

function Read-Answer([string]$Question) {
    # y/N. Piped input is read too (tests); end of input is no.
    Write-Host "$Question [y/N] " -NoNewline
    if ($Yes) { Write-Host 'y (-Yes)'; return $true }
    if ([Console]::IsInputRedirected) { $line = [Console]::In.ReadLine(); Write-Host $line }
    else { $line = Read-Host }
    return ($null -ne $line) -and ($line.Trim().ToLower() -in @('y', 'yes'))
}

function Test-OneDrivePath([string]$Path) {
    # True when the path is in a OneDrive folder: the OneDrive variables, or any folder named OneDrive*
    # ("OneDrive - Company" for work accounts; Documents moved by Known Folder Move lands there too).
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\') + '\'
    foreach ($name in @('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial')) {
        $root = [Environment]::GetEnvironmentVariable($name)
        if ($root) {
            $r = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
            if ($full.StartsWith($r, [StringComparison]::OrdinalIgnoreCase)) { return $true }
        }
    }
    foreach ($part in $full.Split('\')) {
        if ($part -like 'OneDrive*') { return $true }
    }
    return $false
}

function Test-TargetFolder([string]$Path) {
    # $null when the folder may be used, else the reason it is refused.
    if (-not $Path) { return 'no target folder given' }
    if (-not [IO.Path]::IsPathRooted($Path)) { return "the target must be a full path (C:\...), not '$Path'" }
    if (Test-OneDrivePath $Path) {
        return "$Path is under OneDrive: everything there is copied to Microsoft's cloud. Pick a folder outside it, for example $env:USERPROFILE\Fieldkit"
    }
    if (Test-Path -LiteralPath $Path -PathType Leaf) { return "$Path is a file, not a folder" }
    if (Test-Path -LiteralPath $Path -PathType Container) {
        $first = Get-ChildItem -LiteralPath $Path -Force | Select-Object -First 1
        if ($first) { return "$Path already has files ($($first.Name) ...). Pick an empty or new folder; nothing is overwritten" }
    }
    return $null
}

function Test-ZipFile([string]$Path) {
    # $null when the zip is a Fieldkit zip whose every entry stays inside its one top folder, else the reason.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try { $zip = [IO.Compression.ZipFile]::OpenRead($Path) }
    catch { return "$Path is not a readable zip: $($_.Exception.InnerException.Message)" }
    try {
        $tops = @{}
        $hasMain = $false
        foreach ($e in $zip.Entries) {
            $name = $e.FullName.Replace('\', '/')
            if ($name.StartsWith('/') -or $name -match '^[A-Za-z]:' -or ($name.Split('/') -contains '..')) {
                return "the zip has an entry that would land outside the folder: $($e.FullName)"
            }
            $tops[$name.Split('/')[0]] = $true
            if ($name -match '^[^/]+/fieldkit/__main__\.py$') { $hasMain = $true }
        }
        if ($tops.Count -ne 1) { return "the zip has $($tops.Count) top folders; a GitHub zip has one" }
        if (-not $hasMain) { return 'the zip is not Fieldkit: no fieldkit/__main__.py in it' }
        return $null
    } finally { $zip.Dispose() }
}

function Get-LeanPackages([string]$Folder) {
    # The lean set from the unpacked fieldkit/core/requirements.json ('lean' -> 'packages'), not hard-coded.
    $req = Join-Path $Folder 'fieldkit\core\requirements.json'
    if (Test-Path -LiteralPath $req -PathType Leaf) {
        $pk = @((Get-Content -LiteralPath $req -Raw | ConvertFrom-Json).lean.packages)
        if ($pk.Count -gt 0) { return @{ Packages = $pk; From = $req } }
    }
    return $null   # never a list of our own: the zip says what it needs, or the install stops
}

function Invoke-Quiet([scriptblock]$Block) {
    # A native program's output, or $null when it fails. In 5.1 its stderr under ErrorActionPreference Stop
    # is a terminating error, so the probe runs with Continue and its stderr dropped.
    $ErrorActionPreference = 'Continue'
    try { $out = & $Block 2>$null; if ($LASTEXITCODE -eq 0) { return $out } } catch { }
    return $null
}

function Get-PythonCandidates {
    # Full paths only, never PATH: the py launcher, then python.org installs (per user, then for all users).
    # The Microsoft Store alias in WindowsApps is left out: it opens the Store instead of running Python.
    $list = @()
    foreach ($py in @("$env:SystemRoot\py.exe", "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe")) {
        if (Test-Path -LiteralPath $py -PathType Leaf) {
            # A launcher can point at a Python that is gone (an old user's folder): its error is a miss, not a stop.
            $exe = Invoke-Quiet { & $py -3 -c 'import sys; print(sys.executable)' }
            if ($exe) { $list += "$exe".Trim() }
        }
    }
    foreach ($root in @("$env:LOCALAPPDATA\Programs\Python", $env:ProgramFiles)) {
        if ($root -and (Test-Path -LiteralPath $root)) {
            $list += Get-ChildItem -LiteralPath $root -Directory -Filter 'Python3*' |
                Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' }
        }
    }
    return $list
}

function Find-Python([string[]]$Candidates) {
    # The first candidate that exists, is not the Store alias and is Python 3.11 or later: @{Path; Version} or $null.
    # No double quotes in -c code: Windows PowerShell 5.1 strips them on the way to a native program.
    foreach ($c in $Candidates) {
        if (-not $c -or $c -like '*\WindowsApps\*' -or -not (Test-Path -LiteralPath $c -PathType Leaf)) { continue }
        $v = Invoke-Quiet { & $c -c 'import sys; print(*sys.version_info[:3], sep=chr(46))' }
        if (-not $v) { continue }
        try { $ver = [version]"$v".Trim() } catch { continue }
        if ($ver -ge $MinPython) { return @{ Path = $c; Version = "$ver" } }
    }
    return $null
}

function Get-FolderBytes([string]$Path) {
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if ($sum) { return [long]$sum } else { return [long]0 }
}

function Format-Bytes([long]$Bytes) {
    if ($Bytes -ge 1MB) { return ('{0:N1} MB ({1} bytes)' -f ($Bytes / 1MB), $Bytes) }
    return ('{0:N0} KB ({1} bytes)' -f ($Bytes / 1KB), $Bytes)
}

# --- the record: INSTALLED.txt -------------------------------------------------------------------
$script:Record = New-Object System.Collections.Generic.List[string]
$script:RecordReady = $false

function Add-Record([string]$Line) { $script:Record.Add($Line); Write-Host "  $Line" }

function Save-Record([string]$Outcome) {
    if (-not $script:RecordReady) { return }
    $lines = @('Fieldkit, installed by install.ps1 (no git)',
        "Date: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))",
        "Folder: $Target",
        '') + $script:Record + @('',
        "To remove: delete the folder $Target (nothing outside it was changed; Python itself was already here)",
        "Outcome: $Outcome")
    Set-Content -LiteralPath (Join-Path $Target 'INSTALLED.txt') -Value $lines -Encoding UTF8
}

function Stop-Install([string]$Why) {
    Write-Host ''
    Write-Host "STOPPED: $Why"
    Save-Record "not ready: $Why"
    exit 3
}

# Dot-sourced (the test): the functions above only, nothing runs.
if ($MyInvocation.InvocationName -eq '.') { return }

# --- the steps -----------------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # the progress bar slows Invoke-WebRequest tenfold in 5.1

if ($Branch -notmatch '^[A-Za-z0-9._/-]+$') { Write-Host "bad branch name: $Branch"; exit 2 }
try { $Target = [IO.Path]::GetFullPath($Target) } catch { Write-Host "bad target: $Target"; exit 2 }
$why = Test-TargetFolder $Target
if ($why) { Write-Host "REFUSED: $why"; exit 2 }
$ZipUrl = $env:FIELDKIT_ZIP_URL
if (-not $ZipUrl) { $ZipUrl = "https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/archive/refs/heads/$Branch.zip" }
$isFile = $ZipUrl -like 'file:*'

Write-Host "Fieldkit goes into $Target. Each download and install is asked for first."
$work = Join-Path ([IO.Path]::GetTempPath()) ('fieldkit-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    # 1. the zip
    Write-Host ''
    $zipPath = Join-Path $work 'fieldkit.zip'
    if ($isFile) {
        $src = ([uri]$ZipUrl).LocalPath
        Write-Host "1. Copy the zip from this computer: $src (FIELDKIT_ZIP_URL; nothing downloaded)"
        if (-not (Read-Answer '   Copy it?')) { Write-Host 'STOPPED at step 1: nothing was copied or written.'; exit 3 }
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { Write-Host "no such zip: $src"; exit 2 }
        Copy-Item -LiteralPath $src -Destination $zipPath
    } else {
        Write-Host "1. Download Fieldkit from GitHub: $ZipUrl"
        Write-Host '   GitHub (Microsoft) sees this computer''s address and the time; nothing else is sent.'
        if (-not (Read-Answer '   Download it?')) { Write-Host 'STOPPED at step 1: nothing was downloaded or written.'; exit 3 }
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        try { Invoke-WebRequest -Uri $ZipUrl -OutFile $zipPath -UseBasicParsing }
        catch { Write-Host "STOPPED at step 1: the download failed: $($_.Exception.Message)"; exit 3 }
    }
    $zipBytes = (Get-Item -LiteralPath $zipPath).Length
    # .NET, not Get-FileHash: a Windows PowerShell 5.1 started from PowerShell 7 inherits its module path and cannot
    # load Get-FileHash (the windows-latest CI runner, 2026-10-23); the hash class is always there
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $fs = [System.IO.File]::OpenRead($zipPath)
    try { $zipHash = (-join ($sha.ComputeHash($fs) | ForEach-Object { $_.ToString('x2') })) } finally { $fs.Dispose(); $sha.Dispose() }
    $why = Test-ZipFile $zipPath
    if ($why) { Write-Host "REFUSED: $why"; exit 3 }

    # 2. unpack
    Write-Host ''
    Write-Host "2. Unpack it into $Target ($(Format-Bytes $zipBytes) zip)"
    if (-not (Read-Answer '   Unpack it?')) { Write-Host 'STOPPED at step 2: nothing was written.'; exit 3 }
    $stage = Join-Path $work 'unpacked'
    Expand-Archive -LiteralPath $zipPath -DestinationPath $stage
    $top = Get-ChildItem -LiteralPath $stage -Directory | Select-Object -First 1
    if (-not (Test-Path -LiteralPath $Target)) { New-Item -ItemType Directory -Path $Target | Out-Null }
    Get-ChildItem -LiteralPath $top.FullName -Force | Move-Item -Destination $Target
    $script:RecordReady = $true
    if ($isFile) { Add-Record "Copied (not downloaded): $ZipUrl" } else { Add-Record "Downloaded: $ZipUrl" }
    Add-Record "  size $(Format-Bytes $zipBytes), sha256 $zipHash"
    Add-Record "Unpacked: $Target, $(Format-Bytes (Get-FolderBytes $Target))"

    # 3. Python
    Write-Host ''
    Write-Host "3. Find Python $MinPython or later (by its full path; PATH is not used)"
    $py = Find-Python (Get-PythonCandidates)
    if (-not $py) {
        Stop-Install "no Python $MinPython or later found (py launcher, $env:LOCALAPPDATA\Programs\Python, $env:ProgramFiles). Install it from python.org, then run this again into a new folder"
    }
    Add-Record "Python used: $($py.Path) ($($py.Version)), already on this computer"
    if (Test-Path -LiteralPath (Join-Path $Target 'fieldkit\core\doctor.py')) {
        # the machine and the way in, for the reader; standard library only, informational (as install.sh)
        Push-Location -LiteralPath $Target
        try { Invoke-Quiet { & $py.Path -m fieldkit.core.doctor --probe } | ForEach-Object { Write-Host "   $_" } } finally { Pop-Location }
    }

    # 4. the private environment and the lean packages
    $venv = Join-Path $Target '.venv'
    Write-Host ''
    Write-Host "4a. Create a private Python environment in $venv (no download; only this folder changes)"
    if (-not (Read-Answer '   Create it?')) { Stop-Install 'at step 4a (your no): no environment was created' }
    & $py.Path -m venv $venv
    if ($LASTEXITCODE -ne 0) { Stop-Install "python -m venv failed (exit $LASTEXITCODE)" }
    $vpy = Join-Path $venv 'Scripts\python.exe'
    Add-Record "Created: $venv, $(Format-Bytes (Get-FolderBytes $venv)) (pip from Python's own copy, nothing downloaded)"

    $lean = Get-LeanPackages $Target
    if (-not $lean) { Stop-Install 'the zip has no lean list (fieldkit\core\requirements.json, lean -> packages): this Fieldkit is older than the no-git install' }
    Write-Host ''
    if ($env:FIELDKIT_SKIP_PIP -eq '1') {
        Add-Record "Skipped (FIELDKIT_SKIP_PIP=1): the lean set $($lean.Packages -join ' ') and Fieldkit itself; nothing downloaded"
    } else {
        Write-Host "4b. Download and install from PyPI (the Python Software Foundation's server): $($lean.Packages -join ' ')"
        Write-Host '    then Fieldkit itself from this folder (pip fetches setuptools to build it, in a throwaway build environment).'
        Write-Host '    PyPI sees this computer''s address and the package names. pip''s own version check is switched off.'
        if (-not (Read-Answer '   Download and install them?')) { Stop-Install 'at step 4b (your no): no package was downloaded' }
        $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
        $env:PIP_NO_INPUT = '1'
        & $vpy -m pip install --no-cache-dir @($lean.Packages)
        if ($LASTEXITCODE -ne 0) { Stop-Install "pip install $($lean.Packages -join ' ') failed (exit $LASTEXITCODE)" }
        & $vpy -m pip install --no-cache-dir --no-deps -e $Target
        if ($LASTEXITCODE -ne 0) { Stop-Install "pip install --no-deps -e $Target failed (exit $LASTEXITCODE)" }
        Add-Record "Lean list from: $($lean.From)"
        $names = @($lean.Packages | ForEach-Object { ($_ -split '[<>=!~ ;\[]')[0] })
        $sizes = & $vpy -c @'
import importlib.metadata as m, os, sys
for name in sys.argv[1:]:
    d = m.distribution(name)
    files = [f.locate() for f in (d.files or [])]
    print(d.metadata['Name'], d.version, sum(os.path.getsize(p) for p in files if os.path.isfile(p)))
'@ @names
        foreach ($row in $sizes) {
            $n, $v, $b = "$row".Split(' ')
            Add-Record "Downloaded and installed: $n $v from PyPI, $(Format-Bytes ([long]$b)) installed"
        }
        Add-Record "Installed: Fieldkit itself, editable from $Target (setuptools downloaded into pip's throwaway build environment, not kept)"
    }

    # 5. the check
    Write-Host ''
    Write-Host '5. fieldkit doctor --lean'
    Push-Location -LiteralPath $Target
    try { & $vpy -m fieldkit.core.doctor --lean; $doctor = $LASTEXITCODE } finally { Pop-Location }
    Add-Record "python -m fieldkit.core.doctor --lean: exit $doctor"

    # 6. the record
    if ($doctor -ne 0) { Stop-Install "doctor --lean exit $doctor (each MISS row above has its install line)" }
    Save-Record 'ready; doctor --lean passed'
    Write-Host ''
    Write-Host "6. Written: $(Join-Path $Target 'INSTALLED.txt')"
    Write-Host "Run Fieldkit with: $vpy -m fieldkit (from $Target)"
    exit 0
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
