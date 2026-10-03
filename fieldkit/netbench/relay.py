"""The link relay: an HTTP proxy (CONNECT only) on 127.0.0.1 that puts the emulated link between the browser and the
local origin.

- Only the *.netbench.test names in `routes` are served, and only to 127.0.0.1 ports. Every other request the
  browser makes through the proxy (its own background traffic) is refused with 403 and counted by host: nothing
  leaves the machine, and the result shows what the browser tried.
- A shaped tunnel costs one round trip before the proxy answers (the TCP handshake to the far origin), then every
  byte goes through the link's shared bottleneck (links.Direction): serialisation at the link rate, half the round
  trip of delay, loss stalls, in-order delivery per connection, and a per-connection cap on bytes in flight so a
  reader that stops reading stops the sender (as a TCP receive window does).
- The report host is passed through unshaped: it is the benches' control channel.

Limitation: the browser's own TCP socket ends at this proxy on loopback, so the kernel sees a loopback round trip.
Effects that depend on the browser's socket buffers meeting a long round trip (the F2 HTTP/2 send-buffer cap) are
therefore NOT reproduced here; that needs kernel-level delay (study B6).
"""
import asyncio
import collections
import time

from . import links

clock = time.perf_counter


class Relay:
    def __init__(self, link=None, seed=1):
        self.routes = {}
        self.refused = collections.Counter()
        self.counts = collections.defaultdict(lambda: {"up": 0, "down": 0, "tunnels": 0})
        self.tunnels = []
        self._writers = set()
        self.set_link(link, seed)

    def set_link(self, link, seed=1):
        """A new link (or None for unshaped); counters and the bottleneck start fresh."""
        self.link = link
        self.up = links.Direction(link.up_bps, link.rtt, link.loss, seed) if link else None
        self.down = links.Direction(link.down_bps, link.rtt, link.loss, seed + 1) if link else None

    def totals(self):
        return {h: dict(v) for h, v in self.counts.items()}

    def refused_hosts(self):
        return dict(self.refused)

    def close_all(self):
        for w in list(self._writers):
            try:
                w.close()
            except Exception:                         # noqa: BLE001
                pass

    async def handle(self, reader, writer):
        self._writers.add(writer)
        try:
            await self._handle(reader, writer)
        except (ConnectionError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            pass
        finally:
            self._writers.discard(writer)
            try:
                writer.close()
            except Exception:                         # noqa: BLE001
                pass

    async def _handle(self, reader, writer):
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 60)
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split(" ")
        method = parts[0] if parts else ""
        target = parts[1] if len(parts) > 1 else ""
        host = target.rsplit(":", 1)[0].lower() if method == "CONNECT" else (target.split("/")[2].lower() if "//" in target else target)
        route = self.routes.get(host) if method == "CONNECT" else None
        if not route:
            self.refused[host or "?"] += 1
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        port, shaped = route
        shaped = shaped and self.link is not None
        if shaped:
            await asyncio.sleep(self.link.rtt)                 # SYN out, SYN-ACK back over the link
        ur, uw = await asyncio.open_connection("127.0.0.1", port)
        self._writers.add(uw)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        c = self.counts[host]
        c["tunnels"] += 1
        t = {"host": host, "opened": clock(), "closed": None}
        self.tunnels.append(t)
        try:
            if shaped:
                await asyncio.gather(self._shaped(reader, uw, self.up, c, "up"),
                                     self._shaped(ur, writer, self.down, c, "down"))
            else:
                await asyncio.gather(self._plain(reader, uw, c, "up"), self._plain(ur, writer, c, "down"))
        finally:
            t["closed"] = clock()
            self._writers.discard(uw)
            uw.close()

    @staticmethod
    async def _plain(src, dst, c, key):
        try:
            while True:
                b = await src.read(65536)
                if not b:
                    break
                dst.write(b)
                c[key] += len(b)
                await dst.drain()
            if dst.can_write_eof():
                dst.write_eof()
        except (ConnectionError, OSError):
            pass

    @staticmethod
    async def _shaped(src, dst, d, c, key):
        """Read from `src` at the pace the link allows, deliver to `dst` when each chunk would arrive."""
        q = asyncio.Queue()
        pending = [0]
        room = asyncio.Event()
        room.set()
        window = max(4 * d.chunk_size(), int(d.rate * d.rtt) + 262144)
        broken = [False]

        async def deliver():
            while True:
                arrive, data = await q.get()
                wait = arrive - clock()
                if wait > 0:
                    await asyncio.sleep(wait)
                if data is None:
                    try:
                        if dst.can_write_eof():
                            dst.write_eof()
                    except (ConnectionError, OSError):
                        pass
                    return
                try:
                    dst.write(data)
                    c[key] += len(data)
                    await dst.drain()
                except (ConnectionError, OSError):
                    broken[0] = True
                    room.set()
                    return
                pending[0] -= len(data)
                if pending[0] <= window:
                    room.set()

        task = asyncio.ensure_future(deliver())
        last = 0.0
        try:
            while not broken[0]:
                lag = d.backlog(clock()) - d.queue_limit()
                if lag > 0:                                    # the bottleneck queue is full: stop reading
                    await asyncio.sleep(lag)
                    continue
                if pending[0] > window:                        # the receiver is not reading: its window is shut
                    room.clear()
                    await room.wait()
                    continue
                b = await src.read(d.chunk_size())
                if not b:
                    break
                arrive = max(d.schedule(len(b), clock()), last)
                last = arrive
                pending[0] += len(b)
                q.put_nowait((arrive, b))
        except (ConnectionError, OSError):
            pass
        q.put_nowait((max(last, clock() + d.owd), None))
        try:
            await task
        except (ConnectionError, OSError):
            pass


async def calibrate(relay_port, link, seconds=3.0):
    """Measure the emulated link from Python through the relay: connect time, round trip (median of 5), and the
    download and upload rates of a transfer that lasts about `seconds` at the nominal rate."""
    out = {}
    r, w = await asyncio.open_connection("127.0.0.1", relay_port)
    try:
        t0 = clock()
        w.write(b"CONNECT calib.netbench.test:443 HTTP/1.1\r\nHost: calib.netbench.test:443\r\n\r\n")
        await w.drain()
        head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 30)
        out["connect_ms"] = (clock() - t0) * 1000
        if b" 200 " not in head.split(b"\r\n")[0]:
            return {"error": head.split(b"\r\n")[0].decode("latin-1")}
        rtts = []
        for _ in range(5):
            t0 = clock()
            w.write(b"PING\n")
            await w.drain()
            await asyncio.wait_for(r.readline(), 60)
            rtts.append((clock() - t0) * 1000)
        rtts.sort()
        out["rtt_ms"] = rtts[len(rtts) // 2]
        out["rtt_samples_ms"] = [round(x, 2) for x in rtts]
        n = links.transfer_size(link.down_bps, seconds, lo=16 * 1024, hi=32 * 1024 * 1024)
        w.write(f"DOWN {n}\n".encode())
        await w.drain()
        first = await asyncio.wait_for(r.read(65536), 600)          # rate from first byte to last byte
        t0 = clock()
        await asyncio.wait_for(r.readexactly(n - len(first)), 600)
        dt = clock() - t0
        out["down_bytes"] = n
        out["down_mbps"] = (n - len(first)) * 8 / dt / 1e6 if dt > 0 else None
        n = links.transfer_size(link.up_bps, seconds, lo=16 * 1024, hi=16 * 1024 * 1024)
        t0 = clock()
        w.write(f"UP {n}\n".encode())
        payload = bytes(65536)
        left = n
        while left:
            k = min(left, 65536)
            w.write(payload[:k])
            left -= k
            await w.drain()
        await asyncio.wait_for(r.readline(), 600)
        dt = clock() - t0 - out["rtt_ms"] / 1000                  # the OK comes back one round trip after the last byte
        out["up_bytes"] = n
        out["up_mbps"] = n * 8 / dt / 1e6 if dt > 0 else None
        out["down_ratio"] = out["down_mbps"] / (link.down_bps / 1e6) if out["down_mbps"] else None
        out["up_ratio"] = out["up_mbps"] / (link.up_bps / 1e6) if out["up_mbps"] else None
        out["rtt_ratio"] = out["rtt_ms"] / link.rtt_ms if link.rtt_ms else None
    finally:
        w.close()
    return out
