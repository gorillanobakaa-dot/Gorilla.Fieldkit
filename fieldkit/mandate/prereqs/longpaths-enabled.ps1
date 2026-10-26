# MACHINE PREREQUISITE longpaths-enabled (owner, 2026-10-26: "long paths ... the maximum windows ... can allow by
# default"). Run elevated only, through the mandate's prerequisites (one UAC click), never by a session on its own.
#   -Action apply    HKLM\SYSTEM\CurrentControlSet\Control\FileSystem LongPathsEnabled = 1 (DWORD); then verify
#   -Action verify   say the value; change nothing
#   -Action undo     LongPathsEnabled = 0; then verify
# What it changes: long-path-aware programs (python.exe, git with core.longpaths, PowerShell 7) may use paths up to
# 32,767 characters without the \\?\ prefix. Microsoft Learn, "Maximum Path Length Limitation". Nothing else.
param([ValidateSet('apply', 'verify', 'undo')][string]$Action = 'verify')
$ErrorActionPreference = 'Stop'
$key = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
function Say($t) { Write-Host "LONGPATHS: $t" }
function Value { (Get-ItemProperty -LiteralPath $key -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Action -ne 'verify' -and -not $isAdmin) { Say 'FAILED: not elevated'; throw 'not elevated' }
if ($Action -eq 'apply' -and (Value) -ne 1) { Set-ItemProperty -LiteralPath $key -Name LongPathsEnabled -Value 1 -Type DWord }
if ($Action -eq 'undo' -and (Value) -ne 0) { Set-ItemProperty -LiteralPath $key -Name LongPathsEnabled -Value 0 -Type DWord }
$v = Value
if ($Action -eq 'apply' -and $v -ne 1) { Say "FAILED: LongPathsEnabled is '$v' after apply"; throw 'apply failed' }
if ($Action -eq 'undo' -and $v -ne 0) { Say "FAILED: LongPathsEnabled is '$v' after undo"; throw 'undo failed' }
Say "OK ${Action}: LongPathsEnabled = $v"
