# OWNER STEP (Fieldkit scratch, owner decision 2026-10-26, verbatim: "Defender exclusion folder for scratch. Go.")
#
# A SECURITY SETTING: run by the owner only, elevated (one UAC click through elevate.py). No session sets it.
#   -Action apply    add ONE path exclusion to Microsoft Defender: the scratch root, nothing else; then verify
#   -Action verify   say whether the exclusion is present; change nothing
#   -Action undo     remove that one path exclusion; then verify it is gone
# Never: a process exclusion, an extension exclusion, any other path, any other Defender setting.
#
# The cost the owner accepted: nothing inside the scratch root is scanned by Defender. So nothing is ever executed
# from there (Fieldkit puts only test copies, fixtures, transfers and probe output in it), and every transfer that
# lands there is checked against its sha256. Any program running as you could also drop files there unscanned; the
# rule above is what keeps that from mattering. Undo is one click: undo.ps1.
#
# Fails closed: a root that is not a short top-level folder on a local drive, that does not exist, or that is a
# junction/symlink is refused; a Defender list that changed in any other way than the one path is reported as FAILED.
# Writes its result to state\scratch\exclusion.json in the Fieldkit folder (read by `fieldkit scratch doctor`, which
# cannot list exclusions without admin).
param(
  [ValidateSet('apply', 'verify', 'undo')][string]$Action = 'verify',
  [string]$Root = 'C:\fk-scratch',
  [string]$Proof = (Join-Path $HOME 'Documents\Scripts\Fieldkit\state\scratch\exclusion.json')
)
$ErrorActionPreference = 'Stop'

function Say($t) { Write-Host "SCRATCH-EXCLUSION: $t" }
function Fail($t) { Say "FAILED: $t"; throw "scratch exclusion: $t" }
function Prefs { $p = Get-MpPreference; @{ Path = @($p.ExclusionPath | Where-Object { $_ }); Process = @($p.ExclusionProcess | Where-Object { $_ }); Extension = @($p.ExclusionExtension | Where-Object { $_ }) } }
function Has($prefs) { @($prefs.Path | Where-Object { $_.TrimEnd('\') -ieq $Root }).Count -gt 0 }
function WriteProof($present) {
  $dir = Split-Path -Parent $Proof
  if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $o = [ordered]@{ root = $Root; present = [bool]$present; action = $Action; at = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); by = 'owner script scratch-exclusion.ps1' }
  ($o | ConvertTo-Json) | Set-Content -LiteralPath $Proof -Encoding UTF8
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Fail 'not elevated: run it through elevate.py (one UAC click)' }

$Root = $Root.TrimEnd('\')
if ($Root -notmatch '^[A-Za-z]:\\[A-Za-z0-9._-]{2,40}$') { Fail "root '$Root' is not a short top-level folder on a drive (like C:\fk-scratch): refused" }
$name = Split-Path -Leaf $Root
foreach ($sys in @('Windows', 'Program Files', 'Program Files (x86)', 'ProgramData', 'Users', 'Recovery', 'System Volume Information', 'Boot', 'PerfLogs')) {
  if ($name -ieq $sys) { Fail "root '$Root' is a system folder: refused" }
}

$before = Prefs
if ($Action -eq 'apply') {
  if (-not (Test-Path -LiteralPath $Root -PathType Container)) { Fail "root '$Root' does not exist: run 'fieldkit scratch new' once as yourself first" }
  $item = Get-Item -LiteralPath $Root -Force
  if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { Fail "root '$Root' is a junction or link: refused" }
  if (Has $before) { Say "already present: $Root" }
  else { Add-MpPreference -ExclusionPath $Root; Say "added path exclusion: $Root" }
} elseif ($Action -eq 'undo') {
  if (Has $before) { Remove-MpPreference -ExclusionPath $Root; Say "removed path exclusion: $Root" }
  else { Say "not present, nothing to remove: $Root" }
}

$after = Prefs
$present = Has $after
# exactly one path may change (ours); process and extension exclusions never
$otherBefore = @($before.Path | Where-Object { $_.TrimEnd('\') -ine $Root }) | Sort-Object
$otherAfter = @($after.Path | Where-Object { $_.TrimEnd('\') -ine $Root }) | Sort-Object
if ((@($otherBefore) -join '|') -ne (@($otherAfter) -join '|')) { Fail 'other exclusion paths changed during this run' }
if ((@($before.Process) -join '|') -ne (@($after.Process) -join '|')) { Fail 'process exclusions changed during this run' }
if ((@($before.Extension) -join '|') -ne (@($after.Extension) -join '|')) { Fail 'extension exclusions changed during this run' }
if ($Action -eq 'apply' -and -not $present) { Fail "the exclusion is not in Get-MpPreference after Add-MpPreference (tamper protection or a policy?)" }
if ($Action -eq 'undo' -and $present) { Fail 'the exclusion is still there after Remove-MpPreference' }
WriteProof $present
Say ("OK {0}: {1} is {2}; {3} other path exclusion(s), {4} process, {5} extension (unchanged)" -f $Action, $Root, $(if ($present) { 'EXCLUDED' } else { 'NOT excluded' }), @($otherAfter).Count, @($after.Process).Count, @($after.Extension).Count)
