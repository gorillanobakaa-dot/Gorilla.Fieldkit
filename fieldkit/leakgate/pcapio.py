"""One reader for every packet capture the leak gate meets (P27): classic pcap (tcpdump on Linux, all four magics:
micro/nano, either byte order) and pcapng (pktmon on Windows). The format is read from the file's first four bytes,
never from the platform. Link types: Ethernet, Linux cooked v1/v2, raw IP."""
import struct

_PCAP_MAGICS = {0xa1b2c3d4, 0xd4c3b2a1, 0xa1b23c4d, 0x4d3cb2a1}
_PCAPNG_MAGIC = 0x0a0d0d0a


def detect(head: bytes) -> str:
    """-> "pcap" or "pcapng"; ValueError for anything else."""
    if len(head) < 4:
        raise ValueError("capture too short to name its format")
    (m,) = struct.unpack("<I", head[:4])
    if m in _PCAP_MAGICS:
        return "pcap"
    if m == _PCAPNG_MAGIC:
        return "pcapng"
    raise ValueError(f"not a pcap or pcapng capture (magic {head[:4].hex()})")


def open_reader(f):
    """-> (reader, datalink) for the open binary file F."""
    import dpkt
    kind = detect(f.read(4))
    f.seek(0)
    reader = dpkt.pcap.Reader(f) if kind == "pcap" else dpkt.pcapng.Reader(f)
    try:
        dl = reader.datalink()
    except Exception:  # noqa: BLE001
        dl = 1
    return reader, dl


def _ip(dl, buf):
    import dpkt
    if dl == 1:
        return dpkt.ethernet.Ethernet(buf).data
    if dl == 113:
        return dpkt.sll.SLL(buf).data
    if dl == 276:
        return dpkt.sll2.SLL2(buf).data
    if dl in (101, 12, 228, 229):
        return dpkt.ip.IP(buf) if buf[:1] and buf[0] >> 4 == 4 else dpkt.ip6.IP6(buf)
    raise ValueError(f"link type {dl}")


def packets(path):
    """-> (timestamp, ip packet) pairs of the capture at PATH; a bad frame is skipped, a bad file raises ValueError."""
    with open(path, "rb") as f:
        reader, dl = open_reader(f)
        for ts, buf in reader:
            try:
                yield ts, _ip(dl, buf)
            except Exception:  # noqa: BLE001
                continue
