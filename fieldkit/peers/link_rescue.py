# Version: 1.0.0 · updated 26-10-26-15-00
"""The rescue door: a second, tiny listener that can only report on the cable listener and restart it.

    fieldkit peers link rescue serve              run it (the systemd user unit fieldkit-link-rescue.service does)
    fieldkit peers link rescue status [--device NAME]    ask the peer's rescue door: listening? keep-alive? log tails
    fieldkit peers link rescue restart [--device NAME]   ask it to restart the peer's listener

2026-10-26: the VAIO's listener was down and there was no second way in (no sshd), so a person walked to it. This
door is the second way in, made as small as it can be:
  - WHERE: the cable address(es) of this device only, port = the link port + 1 (47078), answering only devices the
    household marks link: cable. Nothing on the LAN or the internet can reach it.
  - WHO: whoever holds the household key (link.key) - the same people who can already use the link itself. Requests
    are signed (HMAC-SHA256), stamped (120 s) and never replayed: the link's own Verifier.
  - WHAT: two verbs. `rescue-status` returns the listener's state and the last lines of watchdog.log, serve.log,
    link.log and the user journal of the two units. `rescue-restart` restarts the listener through the service
    manager. No shell, no file, no session start, no argument is ever run.
  - LOGGED: every request, refused or answered, goes to rescue.log.
"""
import ipaddress
import json
import socket
import subprocess
import sys
import time

from . import link

MAX = 4096
TAIL = 40
VERBS = ("rescue-status", "rescue-restart")


def port_for(dev, h):
    return int(dev.get("rescue_port") or int(dev.get("port") or h.get("port", link.PORT)) + 1)


def _log(text, home=None):
    home = home or link.home()
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "rescue.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")


def tail(path, n=TAIL):
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError as e:
        return [f"({path.name}: {type(e).__name__})"]


def journal(run=subprocess.run, n=TAIL):
    if not sys.platform.startswith("linux"):
        return []
    from . import link_keepalive as K
    try:
        r = run(["journalctl", "--user", "-u", K.SERVICE, "-u", K.RESCUE, "-n", str(n), "--no-pager", "-o",
                 "short-iso"], capture_output=True, text=True, timeout=10)
        return (r.stdout or r.stderr or "").splitlines()[-n:]
    except (OSError, subprocess.SubprocessError) as e:
        return [f"(journalctl: {e})"]


def status_answer(listening_fn=None, keepalive_fn=None, journal_fn=None):
    from . import link_keepalive as K
    from . import link_setup
    me = (link.config() or {}).get("me")
    listening = (listening_fn or (lambda: bool(me) and link_setup.listening(me)))()
    rows = (keepalive_fn or K.status)()
    home = link.home()
    return {"ok": True, "host": socket.gethostname(), "listening": listening, "keepalive": rows,
            "logs": {name: tail(home / name) for name in ("watchdog.log", "serve.log", "link.log")},
            "journal": (journal_fn or journal)()}


def restart_answer(manager=None, fallback=None):
    from . import link_keepalive as K
    from . import link_ops
    handled, code, lines = (manager or K.restart_via_manager)()
    if not handled:                          # keep-alive not installed: the old way (a child of this service)
        code, lines = (fallback or link_ops.restart)()
    return {"ok": code == 0, "lines": lines}


def handle(req, verifier, answers=None):
    answers = answers or {"rescue-status": status_answer, "rescue-restart": restart_answer}
    bad = verifier.check(req)
    if bad:
        return {"ok": False, "error": bad}
    verb = req.get("verb")
    if verb not in VERBS:
        return {"ok": False, "error": f"the rescue door answers {', '.join(VERBS)} only"}
    return answers[verb]()


def serve(seconds=None, allow_loopback=False, ready=None, answers=None):
    h, k = link.household(), link.key()
    if not h or not k:
        raise SystemExit("not paired: nothing to rescue")
    mine = link.device(h, h.get("me")) or {}
    cable = {ipaddress.ip_address(a): d for d in link.others(h) if (d.get("link") or "lan") == "cable"
             for a in d.get("address", [])}
    if not cable:
        raise SystemExit("no device of this household is on the cable: the rescue door stays closed")
    v = link.Verifier(k)
    port = port_for(mine, h)
    home = link.home()
    socks = []
    for me in mine.get("address", []):
        link.check_address(me, allow_loopback)
        s = socket.socket(socket.AF_INET6 if ":" in me else socket.AF_INET)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((me, port))
        s.listen(4)
        s.settimeout(1.0)
        socks.append(s)
    _log(f"rescue door on {', '.join(s.getsockname()[0] for s in socks)}:{port} for "
         f"{', '.join(sorted(map(str, cable)))} only", home)
    if ready:
        ready(socks[0].getsockname())
    end = time.time() + seconds if seconds else None
    try:
        while end is None or time.time() < end:
            for s in socks:
                try:
                    c, addr = s.accept()
                except (socket.timeout, BlockingIOError, InterruptedError):
                    continue
                with c:
                    if ipaddress.ip_address(addr[0].split("%")[0]) not in cable:
                        _log(f"REFUSED {addr[0]}: not a cable device of this household", home)
                        continue
                    c.settimeout(10)
                    buf = b""
                    try:
                        while b"\n" not in buf and len(buf) <= MAX:
                            chunk = c.recv(1024)
                            if not chunk:
                                break
                            buf += chunk
                        req = json.loads(buf.split(b"\n", 1)[0].decode("utf-8")) if len(buf) <= MAX else None
                    except (OSError, ValueError):
                        req = None
                    res = handle(req, v, answers) if isinstance(req, dict) else {"ok": False, "error": "bad request"}
                    _log(f"{addr[0]} {req.get('verb') if isinstance(req, dict) else '?'}: "
                         f"{'ok' if res.get('ok') else res.get('error', 'failed')}", home)
                    try:
                        c.sendall(json.dumps(res, ensure_ascii=False).encode("utf-8") + b"\n")
                    except OSError:
                        pass
    finally:
        for s in socks:
            s.close()


def call(verb, device=None, timeout=20):
    """Ask a device's rescue door. -> its answer, or {ok: False, error}."""
    h, k = link.household(), link.key()
    if not h or not k:
        return {"ok": False, "error": "not paired"}
    try:
        dev = link.device_named(h, device)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if (dev.get("link") or "lan") != "cable":
        return {"ok": False, "error": f"{dev['name']} is not on the cable: it has no rescue door"}
    data = json.dumps(link.sign({"verb": verb}, k)).encode("utf-8") + b"\n"
    last = "no address"
    for a in dev.get("address", []):
        try:
            with socket.create_connection((a, port_for(dev, h)), timeout=timeout) as c:
                c.sendall(data)
                buf = b""
                while b"\n" not in buf:
                    chunk = c.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        except (OSError, ValueError) as e:
            last = f"{a}:{port_for(dev, h)} {e}"
    return {"ok": False, "error": f"the rescue door did not answer ({last})"}


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit peers link rescue")
    ap.add_argument("action", choices=["serve", "status", "restart"])
    ap.add_argument("--device")
    a = ap.parse_args(argv)
    if a.action == "serve":
        serve()
        return 0
    r = call("rescue-" + a.action, a.device)
    print(json.dumps(r, indent=1, ensure_ascii=False))
    return 0 if r.get("ok") else 3
