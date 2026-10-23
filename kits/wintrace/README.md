# wintrace

Inspects your own Windows computer for signs of intrusion, read-only, using only Windows' own APIs and tools. It
gives you the Linux incident-response reflexes that Windows lacks. Nothing is sent anywhere, and nothing is stopped,
killed, disabled or changed. Every command ends with one `VERDICT:` line and one `NEXT:` line. Exit 3 means a finding.

```
python -I wintrace.py doctor
python -I wintrace.py whodraws --over claude.exe          # which windows of OTHER processes lie over it
python -I wintrace.py whodraws --at 960 500               # which windows sit at a screen point
python -I wintrace.py modules PID                         # every DLL/EXE/.node in the process and its signer
python -I wintrace.py ss --listen                         # ss -tulnp + signer + the services in each process
python -I wintrace.py svc PID                             # the services in a process (svchost groups)
python -I wintrace.py fuser PATH                          # who holds a file open (Restart Manager)
python -I wintrace.py opensnoop --seconds 30 --pid PID --yes   # files opened; one UAC click
```

| Linux | wintrace | Windows API used |
|---|---|---|
| `ss -tulnp` | `ss` | psutil (GetExtendedTcpTable), Get-AuthenticodeSignature, Win32_Service.ProcessId |
| `fuser` / `lsof FILE` | `fuser` | Restart Manager: RmStartSession, RmRegisterResources, RmGetList (never RmShutdown) |
| `/proc/PID/maps` | `modules` | psutil memory_maps, Get-AuthenticodeSignature (catalog signatures included) |
| `systemctl status PID` | `svc` | Win32_Service |
| `xdotool` / `xprop` | `whodraws` | EnumWindows, GetWindowThreadProcessId, QueryFullProcessImageName, DWM frame and cloaking |
| `opensnoop-bpf` | `opensnoop` | ETW Microsoft-Windows-Kernel-File (logman), tracerpt to CSV, QueryDosDevice |

| File | What it is |
|---|---|
| `wintrace.py` | the tool (stdlib + psutil) |
| `testdata/opens-kernel-file.csv` | 99 real file-open events and the 2 header rows from a 10 s trace (2026-10-22), user names removed |
| `mutations.yaml` | the rules `fieldkit mutate` breaks on purpose; all 23 must be caught |

To test: `python -m pytest -q tests/test_wintrace.py`. The step-by-step procedure is the recipe
`inspect-a-suspicious-window-or-process-on-windows`.
