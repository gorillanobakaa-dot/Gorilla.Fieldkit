# MACHINE PREREQUISITE telemetry-firewall-task (owner, 2026-11-16: "UAC should never appear again", after the Firefox 160
# build gate stopped on 5 open vctip.exe firewall rules). Run elevated only, through the mandate's prerequisites (ONE UAC
# click, the first time), never by a session on its own.
#   -Action apply    copy THIS script to C:\ProgramData\Gorilla (writable by SYSTEM and Administrators only) and register the
#                    scheduled task fieldkit-telemetry-firewall (SYSTEM; at startup, at logon, every hour) that runs it with -Action run
#   -Action run      for every vctip.exe (Microsoft's compiler telemetry uploader) under Visual Studio and every user's
#                    .mozbuild\vs: add an OUTBOUND BLOCK rule named 'Gorilla telemetry block: <path>' when it is missing. Nothing else
#   -Action verify   say whether the task exists; change nothing
#   -Action undo     remove the task and the copied script (the firewall rules stay: they only block telemetry)
# Why a task: a new MSVC version unpacks a new vctip.exe; without this the gate waits for a person to click Yes again.
# Why a protected copy: a SYSTEM task must never run a file a normal user could rewrite.
param([ValidateSet('apply', 'run', 'verify', 'undo')][string]$Action = 'verify')
$ErrorActionPreference = 'Stop'
$task = 'fieldkit-telemetry-firewall'
$dir = Join-Path $env:ProgramData 'Gorilla'
$copy = Join-Path $dir 'telemetry-firewall.ps1'
function Say($t) { Write-Host "TELFW: $t" }
function Roots {
  $r = @("${env:ProgramFiles(x86)}\Microsoft Visual Studio", "$env:ProgramFiles\Microsoft Visual Studio")
  Get-ChildItem 'C:\Users' -Directory -ErrorAction SilentlyContinue | ForEach-Object { $r += (Join-Path $_.FullName '.mozbuild\vs') }
  $r | Where-Object { Test-Path -LiteralPath $_ }
}
switch ($Action) {
  'run' {
    $n = 0
    foreach ($root in Roots) {
      Get-ChildItem -LiteralPath $root -Recurse -Filter vctip.exe -File -ErrorAction SilentlyContinue | ForEach-Object {
        $name = 'Gorilla telemetry block: ' + $_.FullName
        if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
          New-NetFirewallRule -DisplayName $name -Direction Outbound -Program $_.FullName -Action Block -Profile Any | Out-Null
          $n++
        }
      }
    }
    Say "run: $n rule(s) added"
  }
  'apply' {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Copy-Item -LiteralPath $PSCommandPath -Destination $copy -Force
    & icacls.exe $dir /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'Users:(OI)(CI)RX' | Out-Null
    $act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$copy`" -Action run"
    $trg = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn),
             (New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)))
    $pri = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $task -Action $act -Trigger $trg -Principal $pri -Force | Out-Null
    Start-ScheduledTask -TaskName $task
    Say "apply: task $task registered, copy at $copy"
  }
  'undo' {
    if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $task -Confirm:$false }
    if (Test-Path -LiteralPath $copy) { Remove-Item -LiteralPath $copy -Force }
    Say 'undo: task and copy removed'
  }
  'verify' {
    if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) { Say 'task present' } else { Say 'task missing'; exit 3 }
  }
}
