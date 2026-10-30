"""A minimal WebSocket client (RFC 6455) on the standard library: text frames, masking, ping/pong, fragments.

Just enough for WebDriver BiDi to a browser on 127.0.0.1. No TLS (ws:// only), no compression, no extensions.
"""
import base64
import hashlib
import os
import socket
import struct
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(OSError):
    pass


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def encode_frame(payload, opcode=0x1, mask=True):
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    n = len(data)
    head = bytes([0x80 | opcode])
    mbit = 0x80 if mask else 0
    if n < 126:
        head += bytes([mbit | n])
    elif n < 1 << 16:
        head += bytes([mbit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([mbit | 127]) + struct.pack(">Q", n)
    if not mask:
        return head + data
    key = os.urandom(4)
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(data))


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    @classmethod
    def connect(cls, url, timeout=30):
        u = urlsplit(url)
        if u.scheme != "ws" or not u.hostname:
            raise WebSocketError(f"only ws:// URLs: {url}")
        sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port or 80}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                      ).encode("ascii"))
        ws = cls(sock)
        head = ws._read_until(b"\r\n\r\n").decode("latin-1")
        lines = head.split("\r\n")
        if " 101" not in lines[0]:
            sock.close()
            raise WebSocketError(f"handshake refused: {lines[0]}")
        hdr = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        if hdr.get("sec-websocket-accept") != accept_key(key):
            sock.close()
            raise WebSocketError("handshake: Sec-WebSocket-Accept does not match")
        return ws

    def _read_until(self, marker):
        while marker not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            self.buf += chunk
        i = self.buf.index(marker) + len(marker)
        out, self.buf = self.buf[:i - len(marker)], self.buf[i:]
        return out

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketError("connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        self.sock.sendall(encode_frame(text, 0x1, mask=True))

    def recv(self):
        """-> the next text message (fragments joined). Answers ping, raises WebSocketError on close."""
        parts, kind = [], None
        while True:
            b0, b1 = self._read(2)
            op, fin, masked, n = b0 & 0x0F, b0 & 0x80, b1 & 0x80, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            key = self._read(4) if masked else None
            data = self._read(n)
            if key:
                data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
            if op == 0x8:
                raise WebSocketError("closed by the peer")
            if op == 0x9:
                self.sock.sendall(encode_frame(data, 0xA, mask=True))
                continue
            if op == 0xA:
                continue
            if op in (0x1, 0x2):
                kind = op
            parts.append(data)
            if fin:
                msg = b"".join(parts)
                return msg.decode("utf-8") if kind == 0x1 else msg

    def close(self):
        try:
            self.sock.sendall(encode_frame(b"", 0x8, mask=True))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
