"""e2e: fieldkit installed through its real entry point, on a throwaway origin + installed clone.

BEHIND is found (exit 3), sync moves it (exit 0, HEAD = origin/main), check passes; a dirty tree is refused and left
untouched; a diverged tree is refused."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          capture_output=True, text=True, check=True).stdout.strip()


def fk(env, *args):
    p = subprocess.run([sys.executable, "-m", "fieldkit", "installed", *args], env=env, capture_output=True,
                       text=True, timeout=300)
    print(f"$ fieldkit installed {' '.join(args)} -> {p.returncode}\n{p.stdout}{p.stderr}")
    return p.returncode, p.stdout


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        origin, work, inst = td / "origin.git", td / "work", td / "inst"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
        (work / "a.txt").write_text("1")
        git(work, "add", "."); git(work, "commit", "-qm", "one"); git(work, "push", "-q", "origin", "HEAD:main")
        subprocess.run(["git", "clone", "-q", str(origin), str(inst)], check=True)
        git(inst, "checkout", "-q", "--detach")
        (work / "a.txt").write_text("2")
        git(work, "commit", "-qam", "two"); git(work, "push", "-q", "origin", "HEAD:main")
        git(inst, "fetch", "-q", "origin", "main")
        env = dict(os.environ, FIELDKIT_INSTALLED_DIR=str(inst))
        code, out = fk(env, "check"); assert code == 3 and "FAIL BEHIND" in out, out
        (inst / "a.txt").write_text("dirty")
        code, out = fk(env, "sync"); assert code == 3 and "FAIL" in out and (inst / "a.txt").read_text() == "dirty", out
        git(inst, "checkout", "--", "a.txt")
        # the smoke test loads the interpreter's own fieldkit, which is the repo under test
        code, out = fk(env, "sync"); assert code == 0 and "PASS" in out, out
        assert git(inst, "rev-parse", "HEAD") == git(origin, "rev-parse", "main")
        code, out = fk(env, "check"); assert code == 0 and "PASS" in out, out
        (inst / "b.txt").write_text("x"); git(inst, "add", "."); git(inst, "commit", "-qm", "local")
        code, out = fk(env, "sync"); assert code == 3 and "refused" in out, out
    print("E2E installed PASS")


if __name__ == "__main__":
    main()
