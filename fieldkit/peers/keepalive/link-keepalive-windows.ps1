# MACHINE PREREQUISITE link-keepalive-windows (2026-10-26: the cable listener must come back without a person).
# Run elevated only, through the mandate's prerequisites (one UAC click), never by a session on its own.
#   -Action apply    scheduled task "fieldkit-link" for THIS user: at logon, and again every 5 minutes; runs
#                    pythonw -m fieldkit peers link serve from this Fieldkit folder, NOT elevated (RunLevel Limited);
#                    MultipleInstances IgnoreNew (a running listener is left alone), RestartCount 999 every minute
#   -Action verify   say whether the task exists; change nothing
#   -Action undo     remove the task
# Who can act through it: nobody new. It starts the same listener `fieldkit peers link setup` starts (cable address,
# household devices only, signed requests). It opens no port and adds no firewall rule.
param([ValidateSet('apply', 'verify', 'undo')][string]$Action = 'verify', [string]$Python = '')
$ErrorActionPreference = 'Stop'
$name = 'fieldkit-link'
function Say($t) { Write-Host "LINK-KEEPALIVE: $t" }
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
if (-not $Python) {
    foreach ($c in @((Join-Path $root '.venv\Scripts\pythonw.exe'))) { if (Test-Path -LiteralPath $c) { $Python = $c } }
}
if (-not $Python) { $Python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source }
function Present { [bool](Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Action -ne 'verify' -and -not $isAdmin) { Say 'FAILED: not elevated'; throw 'not elevated' }
if ($Action -eq 'apply') {
    if (-not $Python) { Say 'FAILED: no pythonw.exe found'; throw 'no python' }
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $act = New-ScheduledTaskAction -Execute $Python -Argument '-m fieldkit peers link serve' -WorkingDirectory $root
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $user
    $every = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $who = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $act -Trigger @($logon, $every) -Settings $set -Principal $who `
        -Description 'fieldkit cable link listener (keep-alive); fieldkit/peers/keepalive' -Force | Out-Null
}
if ($Action -eq 'undo' -and (Present)) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
$p = Present
if ($Action -eq 'apply' -and -not $p) { Say 'FAILED: task missing after apply'; throw 'apply failed' }
if ($Action -eq 'undo' -and $p) { Say 'FAILED: task still there after undo'; throw 'undo failed' }
Say "OK ${Action}: task $name present = $p (python $Python, root $root)"
