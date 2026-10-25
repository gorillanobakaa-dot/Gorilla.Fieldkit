"""The tool router (2026-10-18): hand-rolled scans are refused with the harness tool's command; ordinary commands
and the tools themselves pass; an override must say why."""
import io
import json

import pytest

from fieldkit.autopilot import toolrouter as tr


@pytest.mark.parametrize("cmd,route", [
    ('Get-ChildItem -Path "C:\\Program Files" -Recurse -Filter vctip.exe', "recursive-file-search"),
    ("gci C:\\ -r -fi *.log", "recursive-file-search"),
    ("dir C:\\Users /s /b vctip.exe", "recursive-file-search"),
    ("find / -name vctip.exe 2>/dev/null", "recursive-file-search"),
    ("where /r C:\\ vctip.exe", "recursive-file-search"),
    ("du -sh ~/Documents/*", "folder-size"),
    ("robocopy C:\\a C:\\b /L /E /BYTES", "folder-size"),
    ("(Get-ChildItem x -File | Measure-Object -Property Length -Sum).Sum", "folder-size"),
    ("findstr /s /i telemetry *.py", "text-search-in-tree"),
])
def test_hand_rolled_scans_are_routed_to_the_tool(cmd, route):
    r = tr.route(cmd)
    assert r and r["route"] == route and "fieldkit" in r["example"]


@pytest.mark.parametrize("cmd", [
    "git status --short",
    "ls -la /c/Users",
    "Get-ChildItem C:\\gfobj\\dist\\*.zip",
    "python -m fieldkit size find --name vctip.exe --type f C:/",
    "find . -maxdepth 1",
    "Get-ChildItem -Recurse x  # toolrouter-ok: a 3-file fixture folder in a test",
])
def test_ordinary_commands_and_the_tools_pass(cmd):
    assert tr.route(cmd) is None


def test_a_heredoc_body_is_data_but_the_command_around_it_is_judged():
    doc = "cat >> GEMINI.md <<'EOF'\nNever run Get-ChildItem -Recurse or du -sh by hand.\nEOF\necho done"
    assert tr.route(doc) is None
    assert tr.route(doc + "\nGet-ChildItem C:\\ -Recurse -Filter x") is not None


def test_python_with_backslashes_in_a_heredoc_goes_to_the_write_tool():
    bad = "python - <<'EOF'\nimport re\np = re.compile(r'\\s+')\nEOF"
    assert tr.route(bad)["route"] == "code-with-backslashes-in-a-heredoc"
    assert tr.route("python - <<'EOF'\nprint(1 + 1)\nEOF") is None          # no backslash: nothing to mangle
    assert tr.route("cat >> notes.md <<'EOF'\npath C:\\x\\y\nEOF") is None   # text, not code


# -- 2026-10-19: the hand-rolled habits each Fieldkit tool replaces. Every row: commands it must refuse, and the
#    look-alikes it must let through (a router that refuses ordinary work is switched off, then it teaches nothing).
HABITS = {
    "release-sums-by-hand": (
        ["sha256sum *.deb *.exe > SHA256SUMS-v0.1.152.txt",
         "python -m fieldkit size hash a b | python -c \"...\" > SHA256SUMS-v0.1.152.txt",
         "Get-FileHash x.exe >> 'SHA256SUMS.txt'"],
        ["sha256sum -c SHA256SUMS-v0.1.152.txt --ignore-missing", "cat SHA256SUMS-v0.1.152.txt",
         "python -m fieldkit release table SHA256SUMS-v0.1.152.txt"]),
    "packages-by-hand": (
        ["nfpm package -f nfpm.yaml -p deb -t out.deb", "dpkg-deb --build pkg out.deb", "dpkg-deb -b pkg",
         "rpmbuild -bb gorilla.spec"],
        ["nfpm --version", "dpkg-deb -c x.deb", "dpkg-deb --info x.deb", "rpm -qlp x.rpm",
         "python -m fieldkit pipeline run gorilla-opencode"]),
    "release-check-by-hand": (
        ["gh release download v0.1.152 -R o/r -D dl", "gh workflow run linux-install.yml -R o/r -f tag=v1"],
        ["gh release view v0.1.152 -R o/r", "gh workflow run arch-package.yml -f tag=v1",
         "python -m fieldkit release verify v0.1.152 --repo o/r"]),
    "lmstudio-log-by-hand": (
        ["grep -n 'eval time' ~/.cache/lm-studio/server-logs/2026-10/2026-10-25.1.log",
         "tail -50 /c/Users/x/.cache/lm-studio/server-logs/2026-10/2026-10-25.1.log",
         "Get-Content $HOME\\.cache\\lm-studio\\server-logs\\2026-10\\a.log -Tail 40"],
        ["python -m fieldkit lmstudio-log --day 2026-10-25",
         "python -m fieldkit lmstudio-log ~/.cache/lm-studio/server-logs/2026-10/2026-10-25.1.log",
         "ls ~/.cache/lm-studio/server-logs/"]),
    "hash-by-hand": (
        ["Get-FileHash C:\\gfobj\\dist\\x.exe -Algorithm SHA256",
         "certutil -hashfile setup.exe SHA256",
         "sha256sum firefox.tar.xz"],
        ["sha256sum -c SHA256SUMS", "sha256sum --check SHA256SUMS.txt", "python -m fieldkit size hash x.exe --json"]),
    "permanent-delete": (
        ["Remove-Item C:\\Build.Work\\157.0-clean -Recurse -Force",
         "rm -rf ~/Documents/Gorilla.firefox/old",
         "rmdir /s /q D:\\Backups\\old",
         "del /s /q C:\\gfobj\\*.obj"],
        ["Remove-Item $env:TEMP\\fk-test -Recurse -Force",
         "rm -rf /tmp/fk-build",
         "Remove-Item D:\\profile\\AppData\\Local\\Temp\\claude\\scratchpad\\a -Recurse",
         "git rm -r --cached state/",
         "Remove-Item notes.txt",
         "python -m fieldkit size recycle D:\\old --json"]),
    "tree-copy-by-hand": (
        ["robocopy C:\\src D:\\dst /E /COPYALL", "xcopy C:\\src D:\\dst /s /e /h"],
        ["robocopy C:\\a C:\\b /L /E /BYTES", "Copy-Item notes.txt D:\\backup\\", "xcopy a.txt b.txt"]),
    "office-by-hand": (
        ['python -c "import docx; print(docx.Document(\'a.docx\').paragraphs)"',
         'python -c "from openpyxl import load_workbook"',
         "pdftotext report.pdf -"],
        ["python -m fieldkit office read report.docx", "Get-Content notes.md", "pip show python-docx"]),
    "network-diagnosis-by-hand": (
        ["ipconfig /all", "netsh wlan show interfaces", "Test-NetConnection 1.1.1.1 -Port 443", "tracert 8.8.8.8",
         "nmcli dev status"],
        ["ipconfig", "Get-NetAdapter | Select-Object Name,Status", "python -m fieldkit net route"]),
    "icon-cache-by-hand": (
        ["ie4uinit.exe -show", "Remove-Item $HOME\\AppData\\Local\\IconCache.db -Force",
         "del %LOCALAPPDATA%\\Microsoft\\Windows\\Explorer\\iconcache_32.db"],
        ["python -m fieldkit icons cache", "Get-ChildItem *.ico"]),
    "kill-by-name": (
        ["taskkill /F /IM firefox.exe", "Stop-Process -Name WhatsApp -Force", "pkill -f gorilla", "killall firefox"],
        ["Stop-Process -Id 12345", "taskkill /PID 12345 /T", "Get-Process -Name firefox"]),
    "download-by-hand": (
        ["Invoke-WebRequest https://example.org/x.zip -OutFile x.zip", "iwr https://example.org/a",
         "curl -L -o x.tar.gz https://example.org/x.tar.gz", "curl.exe --output a.bin https://example.org/a.bin",
         "wget https://example.org/x.iso", "bitsadmin /transfer j https://example.org/a C:\\a"],
        ["curl -s http://127.0.0.1:1234/v1/models", "git fetch origin", "gh release view v1 -R o/r"]),
    "registry-edit-by-hand": (
        ["reg add HKLM\\SOFTWARE\\Policies\\X /v Y /t REG_DWORD /d 0 /f",
         "Set-ItemProperty -Path HKCU:\\Software\\X -Name Y -Value 0",
         "reg delete HKCU\\Software\\X /f"],
        ["reg query HKLM\\SOFTWARE\\X", "Get-ItemProperty HKCU:\\Software\\X", "python -m fieldkit win11 run copilot preview"]),
    "sleep-to-wait": (
        ["Start-Sleep -Seconds 60", "Start-Sleep 30", "while true; do ls; sleep 30; done"],
        ["Start-Sleep -Milliseconds 300", "Start-Sleep -Seconds 2", "sleep 1",
         'python -m fieldkit wait --until "git log -1" --matches abc --timeout 600']),
}


@pytest.mark.parametrize("route,cmd", [(r, c) for r, (hits, _) in HABITS.items() for c in hits])
def test_each_habit_is_routed_to_its_tool(route, cmd):
    r = tr.route(cmd)
    assert r and r["route"] == route, (cmd, r)
    assert r["use"] and r["example"]


@pytest.mark.parametrize("route,cmd", [(r, c) for r, (_, ok) in HABITS.items() for c in ok])
def test_each_habit_lets_its_look_alike_through(route, cmd):
    r = tr.route(cmd)
    assert r is None or r["route"] != route, (cmd, r)


def test_the_delete_exemption_holds_for_each_scratch_place_on_its_own():
    # each marker alone (2026-10-19: a test whose path held two markers let a broken exemption through, mutate showed)
    for cmd in ["rm -rf /c/Users/x/.claude/scratchpad/build", "Remove-Item $env:TEMP\\a -Recurse",
                "rm -rf /tmp/a", "Remove-Item C:\\x\\pytest-tmp\\a -Recurse"]:
        r = tr.route(cmd)
        assert r is None or r["route"] != "permanent-delete", cmd


def test_tree_copy_row_alone_lets_a_robocopy_listing_through():
    # robocopy /L is a size listing (the folder-size row answers it first); the tree-copy row must not claim it either,
    # so the answer does not depend on the order of the rows
    pat = {name: p for name, p, _, _ in tr.ROUTES}["tree-copy-by-hand"]
    assert not pat.search("robocopy C:\\a C:\\b /L /E /BYTES")
    assert pat.search("robocopy C:\\a C:\\b /E")


def test_a_page_that_tells_a_user_to_run_a_habit_is_data_not_a_command():
    # the orchestrator's release pages tell USERS to check a download with Get-FileHash (2026-10-19)
    page = ("cat >> RELEASE-BODY.md <<'EOF'\nCheck your download: Get-FileHash .\\firefox-158.0.en-US.win64.installer.exe\n"
            "or certutil -hashfile setup.exe SHA256; then Remove-Item D:\\old -Recurse\nEOF")
    assert tr.route(page) is None


def test_a_script_run_with_file_is_not_judged_only_its_command_line():
    # the gate launcher and generated .ps1 files: their contents are reviewed where they are written, not here
    for cmd in [r"powershell -NoProfile -ExecutionPolicy Bypass -File D:\gate\leakgate-run.ps1",
                r'Start-Process powershell -ArgumentList "-NoProfile","-File","D:\x\fix-telemetry.ps1" -Verb RunAs']:
        assert tr.route(cmd) is None, cmd


def test_every_route_has_its_habit_tests():
    names = {r[0] for r in tr.ROUTES}
    assert names - {"recursive-file-search", "folder-size", "text-search-in-tree"} == set(HABITS), \
        "a ROUTES row without hit and look-alike tests (add it to HABITS)"


def test_the_claude_hook_denies_with_the_tool_command(capsys):
    call = {"tool_name": "PowerShell", "tool_input": {"command": "Get-ChildItem C:\\ -Recurse -Filter a.exe"}}
    tr.hook(io.StringIO(json.dumps(call)))
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "size find" in out["permissionDecisionReason"]
    tr.hook(io.StringIO(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}})))
    assert capsys.readouterr().out == ""
