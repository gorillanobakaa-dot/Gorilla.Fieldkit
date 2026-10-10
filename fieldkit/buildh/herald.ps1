# herald.ps1 - speaks up while a long run goes on, so nobody has to watch a window or wait for an assistant.
#
#   powershell -NoProfile -WindowStyle Hidden -File herald.ps1 -Name "build 30" [-WatchPid <pid>] [-Log <file>]
#
# It runs until the watched process ends, or (with -Log and no -WatchPid) until the log gets its "exit N" line.
#
# Born 2026-10-10. The owner: "the gate must work even when the laptop is on battery but let the user know (maybe just
# a voice message... the battery has dropped to 4 percent, plug your charger in)", after a morning spent waiting on a
# run nobody was reporting on. Windows' own offline voice (System.Speech); nothing is sent anywhere.
#   - on battery, it says so once at 20, 10 and 5 per cent (again after the charger was plugged in and pulled out);
#   - when the watched process ends, it says the run finished, and with -Log, whether it passed (the log's last
#     "exit N" line, or a leak-gate "FINAL_RESULT").
param([int]$WatchPid = 0, [string]$Name = "the run", [string]$Log = "", [int]$Every = 60)

Add-Type -AssemblyName System.Speech
Add-Type -AssemblyName System.Windows.Forms
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer

function Say([string]$text) {
    try { $voice.Speak($text) } catch { }
}

$warned = @{}
function Running {
    if ($WatchPid -gt 0) { return [bool](Get-Process -Id $WatchPid -ErrorAction SilentlyContinue) }
    if (-not $Log) { return $false }
    if (-not (Test-Path $Log)) { return $true }
    $t = Get-Content -Raw -Path $Log -ErrorAction SilentlyContinue
    return -not ($t -match '(?m)^exit -?\d+\s*$')
}

while (Running) {
    $p = [System.Windows.Forms.SystemInformation]::PowerStatus
    $onBattery = ([int]$p.PowerLineStatus -eq 0) -and ([int]$p.BatteryChargeStatus -ne 128)
    if ($onBattery) {
        $pct = [int][Math]::Round($p.BatteryLifePercent * 100)
        foreach ($t in 20, 10, 5) {
            if ($pct -le $t -and -not $warned.ContainsKey($t)) {
                $warned[$t] = $true
                Say "Heads up. The battery is at $pct per cent and $Name is still running. Plug your charger in."
                break
            }
        }
    } else {
        $warned = @{}
    }
    Start-Sleep -Seconds $Every
}

$verdict = "has finished"
# the result file is written just after the process ends: give it up to two minutes
for ($i = 0; $Log -and -not (Test-Path $Log) -and $i -lt 24; $i++) { Start-Sleep -Seconds 5 }
if ($Log -and (Test-Path $Log)) {
    $text = Get-Content -Raw -Path $Log -ErrorAction SilentlyContinue
    if ($text -match '"exit_code"\s*:\s*(-?\d+)') {
        $verdict = if ([int]$Matches[1] -eq 0) { "has finished, and it passed" } else { "has finished, and it failed. Have a look." }
    } elseif ($text -match 'FINAL_RESULT"?\s*[:=]\s*"?(PASS|FAIL)') {
        $verdict = if ($Matches[1] -eq 'PASS') { "has finished, and it passed" } else { "has finished, and it failed. Have a look." }
    } elseif ($text -match '(?m)^exit (-?\d+)\s*$') {
        $all = [regex]::Matches($text, '(?m)^exit (-?\d+)\s*$')
        $code = [int]$all[$all.Count - 1].Groups[1].Value
        $verdict = if ($code -eq 0) { "has finished, and it passed" } else { "has stopped with code $code. Have a look." }
    }
}
Say "$Name $verdict."
