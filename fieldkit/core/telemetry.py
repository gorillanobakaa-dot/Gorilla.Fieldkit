# Version: 1.0.0 · updated 26-10-18
"""Telemetry off and cut out: a deterministic check for every project and for this machine.

Owner, 2026-10-18: "rule of thumb, for everything, for both sessions, is turn the telemetry off, excise it from the
code source entirely ... with deterministic tools, and deterministic checks, for all the projects we are building or
are going to build from now on." Found that night: MSVC's link.exe starts vctip.exe on every link and vctip calls
Microsoft (the leak gate's quiet holder stopped it 69 times in one hour of another session's Rust builds); Microsoft's
own page says the Visual Studio opt-out does not cover "required" diagnostic data
(https://learn.microsoft.com/en-us/visualstudio/ide/visual-studio-experience-improvement-program). So: opt out where
the vendor documents it, AND block the program at the firewall, AND check both.

    fieldkit telemetry scan PATH              source: telemetry SDKs, endpoints and crash reporters still in the code
    fieldkit telemetry machine                this computer: toolchain opt-outs and blocks, each a row
    fieldkit telemetry machine --fix OUT.ps1  the one script the owner runs (one UAC click) to close what is open

A project finding is excised in the source, not disabled; a line that is a deliberate refusal or a test fixture
carries 'telemetry-ok: <why>' on the same line and is reported as such, never silently passed.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

# -- the source scan --------------------------------------------------------------------------------------------------
#: (kind, pattern). Package names, SDK entry points and collector hosts of the common telemetry and crash services.
SOURCE_PATTERNS = [
    ("sdk", re.compile(r"\b(sentry[-_]sdk|@sentry/|sentry_sdk|Sentry\.init|SentrySdk\.Init|raven[-.]js|"  # telemetry-ok: the scanner's own pattern table
                       r"posthog|mixpanel|amplitude|@segment/|analytics-node|segment\.io|rudderstack|"  # telemetry-ok: the scanner's own pattern table
                       r"applicationinsights|ApplicationInsights|appcenter[-_.](analytics|crashes)|bugsnag|rollbar|"  # telemetry-ok: the scanner's own pattern table
                       r"datadog[-_]?(rum|browser)|newrelic|honeybadger|firebase[-/]analytics|FirebaseAnalytics|"  # telemetry-ok: the scanner's own pattern table
                       r"crashlytics|@vercel/analytics|plausible-tracker|matomo|piwik|heap-analytics|logrocket|"  # telemetry-ok: the scanner's own pattern table
                       r"fullstory|hotjar|opentelemetry[-_.]exporter|OTLPExporter|otlp_exporter|statsig|launchdarkly)\b",  # telemetry-ok: the scanner's own pattern table
                       re.I)),
    # 2026-10-18 (tool-harness review): Gorilla OpenCode's binary linked go.opentelemetry.io/otel through Google's  telemetry-ok: the scanner's own pattern table
    # genai SDK; no file of ours imported it and the scanner, which looked for exporters by name, missed it
    ("module", re.compile(r"(go\.opentelemetry\.io/|go\.opencensus\.io|opencensus-|@opentelemetry/|"  # telemetry-ok: the scanner's own pattern table
                          r"\bopentelemetry(_sdk|_api|-sdk|-api|-otlp|\b)|\bopentelemetry-proto\b|"
                          r"\btracing-opentelemetry\b|\bsentry-core\b|\bsentry-rust\b)", re.I)),  # telemetry-ok: the scanner's own pattern table
    ("endpoint", re.compile(r"\b(google-analytics\.com|googletagmanager\.com|analytics\.google\.com|"
                            r"sentry\.io|ingest\.sentry|api\.mixpanel\.com|api\.amplitude\.com|api\.segment\.io|"  # telemetry-ok: the scanner's own pattern table
                            r"app\.posthog\.com|us\.i\.posthog\.com|dc\.services\.visualstudio\.com|"  # telemetry-ok: the scanner's own pattern table
                            r"vortex\.data\.microsoft\.com|telemetry\.mozilla\.org|incoming\.telemetry\.mozilla\.org|"
                            r"browser\.events\.data\.microsoft\.com|mobile\.events\.data\.microsoft\.com|"
                            r"bam\.nr-data\.net|rum\.browser-intake-datadoghq\.com|notify\.bugsnag\.com|"  # telemetry-ok: the scanner's own pattern table
                            r"api\.rollbar\.com|crash-reports\.mozilla\.com|crash-stats\.mozilla\.org)\b", re.I)),  # telemetry-ok: the scanner's own pattern table
    ("collector", re.compile(r"\b(sendTelemetry|send_telemetry|trackEvent|track_event|reportCrash|uploadMinidump|"
                             r"telemetry\.(send|submit|record)|captureException|captureMessage)\s*\(", re.I)),
]
OK_MARK = re.compile(r"telemetry-ok:\s*\S", re.I)    # telemetry-ok: the marker itself
SOURCE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rs", ".go", ".c", ".cc", ".cpp", ".h", ".hpp",
              ".cs", ".java", ".kt", ".swift", ".rb", ".php", ".toml", ".json", ".yaml", ".yml", ".ps1", ".sh",
              ".html", ".xhtml", ".cfg", ".ini", ".gradle", ".lock", ".mod", ".sum"}
#: data, evidence and generated copies, not code: gate logs of what a browser TRIED to reach, rendered doc payloads
#: (2026-10-18 sweep: about 120 of Fieldkit's 157 findings were state/ evidence and *.prep/*.filled copies)
SKIP_DATA_DIRS = {"state", "_staging"}
SKIP_DATA_SUFFIX = (".prep.json", ".filled.json", "_layman.json", "_developer.json", "_audit.json")
SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", "__pycache__", ".venv", "venv", "obj", "objdir",
             ".mozbuild", "_build", "site-packages"}


def scan(root, max_bytes=2_000_000):
    """-> {"findings": [{file, line, kind, text}], "marked": [...], "files": n}. Lockfiles count (a dependency that
    ships telemetry is telemetry); vendored and generated folders are skipped by name."""
    root = Path(root)
    found, marked, n = [], [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d not in SKIP_DATA_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() not in SOURCE_EXT or fn.lower().endswith(SKIP_DATA_SUFFIX):
                continue
            try:
                if p.stat().st_size > max_bytes:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            n += 1
            for i, line in enumerate(text.splitlines(), 1):
                for kind, pat in SOURCE_PATTERNS:
                    m = pat.search(line)
                    if m:
                        row = {"file": p.relative_to(root).as_posix(), "line": i, "kind": kind,
                               "match": m.group(0), "text": line.strip()[:160]}
                        (marked if OK_MARK.search(line) else found).append(row)
                        break
    return {"findings": found, "marked": marked, "files": n}


ARTIFACT_PATTERNS = [("module", SOURCE_PATTERNS[1][1]), ("sdk", SOURCE_PATTERNS[0][1]), ("endpoint", SOURCE_PATTERNS[2][1])]


def scan_artifact(path, chunk=8 << 20):
    """A built program (Go, Rust, C#, an Electron app's .asar): every telemetry module path, SDK name or collector host
    its bytes carry. Go and Rust embed their module paths as plain text, so no toolchain is needed (2026-10-18: the
    OpenCode binary carried go.opentelemetry.io/otel 1.35.0). -> {"findings": [{kind, match, count}], "bytes": n}"""  # telemetry-ok: the scanner's own pattern table
    data = Path(path).read_bytes()
    text = data.decode("latin-1")
    hits = {}
    for kind, pat in ARTIFACT_PATTERNS:
        for m in pat.finditer(text):
            key = (kind, m.group(0).lower())
            hits[key] = hits.get(key, 0) + 1
    return {"findings": [{"kind": k, "match": m, "count": c} for (k, m), c in sorted(hits.items())], "bytes": len(data)}


# -- the machine ------------------------------------------------------------------------------------------------------
#: opt-out variables each tool's vendor documents (machine-wide environment)
MACHINE_ENV = {
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",        # .NET SDK (learn.microsoft.com/dotnet/core/tools/telemetry)
    "POWERSHELL_TELEMETRY_OPTOUT": "1",        # PowerShell 7 (about_Telemetry)
    "VSCMD_SKIP_SENDTELEMETRY": "1",           # Visual Studio developer command prompt scripts
    "NEXT_TELEMETRY_DISABLED": "1",            # Next.js
    "GATSBY_TELEMETRY_DISABLED": "1",          # Gatsby
    "HF_HUB_DISABLE_TELEMETRY": "1",           # Hugging Face hub
    "DO_NOT_TRACK": "1",                       # the cross-tool convention many CLIs honor
    "AZURE_CORE_COLLECT_TELEMETRY": "0",       # Azure CLI
    "SAM_CLI_TELEMETRY": "0",                  # AWS SAM CLI
}
#: registry opt-outs Microsoft documents for Visual Studio 2022 (OptIn DWORD 0)
VS_REGISTRY = [r"HKLM:\SOFTWARE\Policies\Microsoft\VisualStudio\SQM",
               r"HKLM:\SOFTWARE\Wow6432Node\Microsoft\VSCommon\17.0\SQM"]
RULE_PREFIX = "Gorilla telemetry block: "
VCTIP_ROOTS = [r"C:\Program Files\Microsoft Visual Studio", r"C:\Program Files (x86)\Microsoft Visual Studio",
               str(Path.home() / ".mozbuild" / "vs")]


def vctip_paths(roots=None):
    """Every vctip.exe of every MSVC toolset under the Visual Studio roots (a fixed layout: VC/Tools/MSVC/<ver>/bin/
    Host<arch>/<arch>/vctip.exe), found with a glob of that layout, not a crawl of the disk."""
    out = []
    for r in roots or VCTIP_ROOTS:
        base = Path(r)
        if base.is_dir():
            out += [str(p) for p in base.glob("**/VC/Tools/MSVC/*/bin/*/*/vctip.exe")]
    return sorted(set(out))


def _machine_env(name):
    """A machine-wide environment variable as stored (HKLM ... Session Manager\\Environment), or None."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return None


def _ps(cmd, run=subprocess.run):
    r = run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=120)
    return r.stdout.strip()


def machine_rows(run=subprocess.run, env=None, paths=None):
    """-> rows [{check, ok, evidence}] for this computer. Windows only; elsewhere one row says so (Linux: the same
    variables are checked in the environment)."""
    rows = []
    if env is None:
        # Windows: the MACHINE value as stored (2026-10-18: read from this process's environment, which predates the
        # fix, every variable looked unset after the owner had set all nine); elsewhere the running environment
        env = _machine_env if sys.platform == "win32" else (lambda k: os.environ.get(k))
    for k, v in MACHINE_ENV.items():
        cur = env(k)
        rows.append({"check": f"opt-out variable {k}={v}", "ok": cur == v, "evidence": f"is {cur!r}"})
    if sys.platform != "win32":
        return rows
    for key in VS_REGISTRY:
        cur = _ps(f"(Get-ItemProperty -Path '{key}' -Name OptIn -ErrorAction SilentlyContinue).OptIn", run)
        rows.append({"check": f"Visual Studio CEIP opted out: {key} OptIn=0", "ok": cur == "0", "evidence": f"is {cur or 'unset'}"})
    paths = vctip_paths() if paths is None else paths
    blocked = _ps("Get-NetFirewallRule -DisplayName '" + RULE_PREFIX + "*' -ErrorAction SilentlyContinue | "
                  "Where-Object { $_.Enabled -eq 'True' -and $_.Action -eq 'Block' -and $_.Direction -eq 'Outbound' } | "
                  "Get-NetFirewallApplicationFilter | Select-Object -ExpandProperty Program", run).lower().splitlines()
    for p in paths:
        ok = p.lower() in {b.strip() for b in blocked}
        rows.append({"check": f"MSVC compiler telemetry blocked at the firewall: {p}", "ok": ok,
                     "evidence": "outbound block rule present" if ok else "no outbound block rule"})
    return rows


def fix_script(rows, paths=None):
    """The one script the owner runs elevated: closes every open row, backs up first, checks itself, ends with a
    verdict line. Idempotent. Each change runs on its own (2026-10-18, first run: the first missing registry parent
    threw and the single try skipped every other change); a missing key is created level by level, an existing one is
    never re-created (on the registry, New-Item -Force on an existing key re-creates it empty)."""
    paths = vctip_paths() if paths is None else paths
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    lines = ["# Generated by fieldkit telemetry machine --fix (owner rule 2026-10-18: telemetry off everywhere)",
             "$ErrorActionPreference = 'Stop'",
             "$log = Join-Path $env:TEMP ('telemetry-fix-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')",
             "Start-Transcript -Path $log | Out-Null",
             "$bad = 0",
             "function Step($what, [scriptblock]$do) {",
             "  try { & $do; Write-Host ('done: ' + $what) } catch { Write-Host ('ERROR ' + $what + ': ' + $_); $script:bad++ } }",
             "function Ensure-Key($path) {",
             "  # create each missing level, never touch an existing one",
             "  $parts = $path -split '\\\\'; $cur = $parts[0]",
             "  foreach ($p in $parts[1..($parts.Count - 1)]) { $cur = $cur + '\\' + $p; if (-not (Test-Path $cur)) { New-Item -Path $cur | Out-Null } } }"]
    for key in VS_REGISTRY:
        reg = key.replace("HKLM:\\", "HKLM\\")
        lines += [f"Step {q('Visual Studio opt-out ' + key)} {{",
                  f"  if (Test-Path {q(key)}) {{ $null = & reg.exe export {q(reg)} (Join-Path $env:TEMP ('vs-sqm-backup-' + [guid]::NewGuid() + '.reg')) /y }}",
                  f"  Ensure-Key {q(key)}",
                  f"  New-ItemProperty -Path {q(key)} -Name OptIn -PropertyType DWord -Value 0 -Force | Out-Null }}"]
    for k, v in MACHINE_ENV.items():
        lines.append(f"Step {q('variable ' + k)} {{ [Environment]::SetEnvironmentVariable({q(k)}, {q(v)}, 'Machine') }}")
    for path in paths:
        name = RULE_PREFIX + path
        lines += [f"Step {q('firewall block ' + path)} {{ if (-not (Get-NetFirewallRule -DisplayName {q(name)} -ErrorAction SilentlyContinue)) {{",
                  f"  New-NetFirewallRule -DisplayName {q(name)} -Direction Outbound -Program {q(path)} -Action Block -Profile Any | Out-Null }} }}"]
    lines.append("# check every change")
    for key in VS_REGISTRY:
        lines.append(f"if ((Get-ItemProperty -Path {q(key)} -Name OptIn -ErrorAction SilentlyContinue).OptIn -ne 0) "
                     f"{{ Write-Host 'FAIL {key}'; $bad++ }}")
    for k, v in MACHINE_ENV.items():
        lines.append(f"if ([Environment]::GetEnvironmentVariable({q(k)}, 'Machine') -ne {q(v)}) {{ Write-Host 'FAIL {k}'; $bad++ }}")
    for path in paths:
        lines.append(f"if (-not (Get-NetFirewallRule -DisplayName {q(RULE_PREFIX + path)} -ErrorAction SilentlyContinue)) "
                     f"{{ Write-Host 'FAIL firewall {path}'; $bad++ }}")
    lines += ["if ($bad -eq 0) { Write-Host '=== FINAL VERDICT: PASS (telemetry opt-outs set, vctip blocked)' }",
              "else { Write-Host ('=== FINAL VERDICT: FAIL (' + $bad + ' check(s))') }",
              "Stop-Transcript | Out-Null"]
    return "\n".join(lines) + "\n"

if __name__ == "__main__":
    # 2026-10-18 (tool-harness review): "python -m fieldkit.core.telemetry" printed nothing and exited 0, which an
    # agent would read as a clean pass; the module runs the real command instead
    from fieldkit.cli import main as _main
    sys.exit(_main(["telemetry"] + sys.argv[1:]))
