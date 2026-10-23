# Version: 1.0.0 · updated 26-10-15-20-07
"""Binary patches (pictures, icons) in the patch set: are they in the tree, byte for byte? Restore the ones that are not.

    fieldkit build-harness binary-patches TASK [--apply]
    preflight row `binary-patches` (BLOCKER) before every Linux build

Born 2026-10-15. The installed 158 drew a blurry 500 px gorilla on every page and the owner asked why years of theming
work were gone. Measured: 0 of the 31 `GIT binary patch` sections of the shared set had reached the 158 tree (the
1400 px master logo of D-157-35, the icon ladder 16..64 of D-157-24, the private-window logos, the .ico files). The
mechanical apply carried text hunks only, and every check that came after read text: nothing could see a picture.

Deterministic, no model:
  sections(set)   every `diff --git` section holding `GIT binary patch`, the LAST one per file in patch order wins
                  (group folders sorted, files sorted: the order the set is applied in); deletions included
  state(tree, s)  git's own blob hash of the tree file against the section's post-image hash (the `index A..B` line):
                  applied | not-applied | deleted-as-intended | should-be-deleted
  decode(s)       the `literal` payload (git's base85 + zlib) -> bytes; a `delta` payload is refused, never guessed
  restore(tree)   writes each not-applied literal, then checks git hash-object == B before keeping it; a mismatch is
                  put back and reported. Then one checkpoint commit names every restored file and its patch.
"""
import os
import re
import subprocess
import zlib
from pathlib import Path

B85 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!#$%&()*+-;<=>?@^_`{|}~"
B85_VAL = {c: i for i, c in enumerate(B85)}
INDEX = re.compile(r"^index ([0-9a-f]+)\.\.([0-9a-f]+)", re.M)
ZERO = re.compile(r"^0+$")
IDENT = {"GIT_AUTHOR_NAME": "build-harness", "GIT_AUTHOR_EMAIL": "build-harness@localhost",
         "GIT_COMMITTER_NAME": "build-harness", "GIT_COMMITTER_EMAIL": "build-harness@localhost"}


def patch_files(patchset, policy=None, platform=None):
    """The patches this platform applies: enabled groups, minus `platforms` it is not in, `exclude` and
    `exclude_on.<platform>`. 2026-10-23, Linux 158.0-2 preflight: every enabled group's patches were read, so the 11
    Windows-only msix pictures of 23.REBASE.158 (exclude_on.linux) were demanded in the Linux tree."""
    from .firefox import this_platform
    platform = platform or this_platform()
    groups = (policy or {}).get("groups", {})
    for grp in sorted(p for p in Path(patchset).iterdir() if p.is_dir()):
        spec = groups.get(grp.name, {})
        if spec.get("status", "enabled") != "enabled" or (spec.get("platforms") and platform not in spec["platforms"]):
            continue
        skip = set(spec.get("exclude", [])) | set((spec.get("exclude_on") or {}).get(platform, []))
        yield from (pf for pf in sorted(grp.glob("*.patch")) if pf.name not in skip)


def sections(patchset, policy=None, platform=None):
    """-> {path: {"patch", "pre", "post", "body", "deleted"}}: the last binary section per file."""
    out = {}
    for p in patch_files(patchset, policy, platform):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "GIT binary patch" not in text:
            continue
        for chunk in text.split("\ndiff --git a/")[1:] if not text.startswith("diff --git a/") else \
                ("\n" + text).split("\ndiff --git a/")[1:]:
            if "GIT binary patch" not in chunk:
                continue
            head, _, body = chunk.partition("\n")             # "X b/X"
            idx = INDEX.search(body)
            if not idx:
                continue
            path = head.split(" b/", 1)[1].strip() if " b/" in head else head.split()[0]
            out[path] = {"patch": str(p.relative_to(patchset)), "pre": idx.group(1), "post": idx.group(2),
                         "body": body, "deleted": "deleted file mode" in body or bool(ZERO.match(idx.group(2)))}
    return out


def blob_hash(path):
    r = subprocess.run(["git", "hash-object", str(path)], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def state(tree, path, s):
    t = Path(tree) / path
    if s["deleted"]:
        return "should-be-deleted" if t.exists() else "deleted-as-intended"
    if not t.is_file():
        return "not-applied"
    h = blob_hash(t) or ""
    return "applied" if h.startswith(s["post"]) else "not-applied"


def _b85(line):
    n = (ord(line[0]) - ord("A") + 1) if line[0] <= "Z" else (ord(line[0]) - ord("a") + 27)
    data = bytearray()
    enc = line[1:]
    for i in range(0, len(enc), 5):
        acc = 0
        for c in enc[i:i + 5]:
            acc = acc * 85 + B85_VAL[c]
        data += acc.to_bytes(4, "big")
    return bytes(data[:n])


def decode(body):
    """The forward payload of a binary section -> bytes. `literal N` only; `delta` raises ValueError."""
    lines = body.split("GIT binary patch\n", 1)[1].splitlines()
    head = lines[0]
    if not head.startswith("literal "):
        raise ValueError(f"{head.split()[0]} payload: not decoded (only literal is restored)")
    size = int(head.split()[1])
    raw = b"".join(_b85(l) for l in lines[1:_end(lines)])
    data = zlib.decompress(raw)
    if len(data) != size:
        raise ValueError(f"decoded {len(data)} bytes, the patch says {size}")
    return data


def _end(lines):
    """The forward payload ends at the first blank line (the reverse payload follows it)."""
    for i, l in enumerate(lines[1:], 1):
        if not l.strip():
            return i
    return len(lines)


def exemptions(policy, platform=None):
    """{path: reason} for THIS platform: `binary_exempt_on` {platform: {path: reason}}, plus the older unscoped
    `binary_exempt` read as Linux-only (2026-10-16: the three NSIS installer bitmaps were exempted for Linux, "on
    Windows this exemption does not apply", and the unscoped key let them pass on Windows too)."""
    from .firefox import this_platform
    platform = platform or this_platform()
    policy = policy or {}
    out = dict((policy.get("binary_exempt_on") or {}).get(platform, {}))
    if platform == "linux":
        out.update(policy.get("binary_exempt", {}))
    return out


def audit(tree, patchset, policy=None, platform=None):
    """`binary_exempt_on` {platform: {path: reason}}: a section a person decided THIS platform does without (recorded,
    shown, never silently passed: its state is `exempt` with the reason)."""
    exempt = exemptions(policy, platform)
    rows = []
    for path, s in sorted(sections(patchset, policy, platform).items()):
        st = state(tree, path, s)
        if st != "applied" and path in exempt:
            st = "exempt"
        rows.append({"path": path, "patch": s["patch"], "post": s["post"][:10], "state": st,
                     "reason": exempt.get(path)})
    bad = [r for r in rows if r["state"] in ("not-applied", "should-be-deleted")]
    return {"ok": not bad, "rows": rows, "bad": bad}


def restore(tree, patchset, policy=None, commit=True):
    secs = sections(patchset, policy)
    done, failed = [], []
    for path, s in sorted(secs.items()):
        st = state(tree, path, s)
        t = Path(tree) / path
        if st == "should-be-deleted":
            failed.append(f"{path}: the set deletes it; a person moves it out (never deleted by this tool)")
            continue
        if st != "not-applied":
            continue
        try:
            data = decode(s["body"])
        except (ValueError, zlib.error, KeyError) as e:
            failed.append(f"{path}: {e}")
            continue
        old = t.read_bytes() if t.is_file() else None
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(data)
        if not (blob_hash(t) or "").startswith(s["post"]):
            if old is None:
                t.unlink()
            else:
                t.write_bytes(old)
            failed.append(f"{path}: decoded bytes do not hash to {s['post'][:10]}; tree left as it was")
            continue
        done.append(f"{path} <- {s['patch']}")
    if done and commit:
        env = {**os.environ, **IDENT}
        subprocess.run(["git", "-C", str(tree), "add", "--", *[d.split(" <- ")[0] for d in done]], capture_output=True)
        r = subprocess.run(["git", "-C", str(tree), "commit", "-q", "--no-verify", "-m",
                            "binary-patches: pictures the set carries restored, each verified by its git hash\n\n"
                            + "\n".join(done)], capture_output=True, text=True, env=env)
        if r.returncode != 0:
            failed.append("checkpoint commit failed: " + (r.stdout + r.stderr).strip()[:200])
    return {"ok": not failed, "restored": done, "failed": failed}


def lines(r):
    out = [f"  {x['state']:20} {x['path']}  <- {x['patch']}" + (f" ({x['reason']})" if x.get("reason") else "")
           for x in r["rows"] if x["state"] != "applied"]
    n = {k: sum(x["state"] == k for x in r["rows"]) for k in ("applied", "not-applied", "deleted-as-intended",
                                                               "should-be-deleted", "exempt")}
    out.append(f"BINARY PATCHES {'OK' if r['ok'] else 'NOT IN THE TREE'}: " + ", ".join(f"{k} {v}" for k, v in n.items()))
    return out
