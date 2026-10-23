# linkcheck

Two computers joined by one network cable, no router: put a private address on each end, then measure the cable.
Windows and Linux, Python standard library only, one file. Card `linkcheck` in `fieldkit/desk/tools.yaml`; tests
`tests/test_linkcheck.py`; mutation plan `mutations.yaml`.

```
python linkcheck.py setup --ip 10.77.0.1/30 --iface Ethernet     # this end (Windows: one UAC prompt)
python3 linkcheck.py setup --ip 10.77.0.2/30 --iface enp0s25     # the other end (Linux: NetworkManager)
python3 linkcheck.py serve --bind 10.77.0.2 --allow 10.77.0.1    # the other end, in a terminal
python linkcheck.py check 10.77.0.2                              # this end
python linkcheck.py status --iface Ethernet
python linkcheck.py doctor
```

`check` prints one line such as
`LINK: 941.2 Mbit/s up, 939.8 Mbit/s down, RTT p50 0.21 ms p99 0.40 ms jitter 0.03 ms, loss 0.0 %, MTU 1500, 1000 Mbit/s full duplex, EEE on`
or `LINK: FAIL <why>`, then `VERDICT` and `NEXT`. Exit 0 fine, 2 a person must act, 3 a failed link or setup.

## Doors

- `setup` adds no gateway and no DNS, turns DHCP off on that card only, refuses a public address, and reads the
  result back from the system. `--undo` removes only what it set.
- `serve` is a foreground program, not a service: it binds one address (never 0.0.0.0), answers one peer, echoes
  only its own 12-byte packets (no amplification), and stops after 10 minutes with no client.
- No iperf3 and nothing downloaded. Nothing leaves the two machines.

## What it measures, and how

| number | how |
|---|---|
| speed, duplex | Windows `Get-NetAdapter`; Linux `/sys/class/net/IF/speed`, `duplex` |
| Energy Efficient Ethernet | Windows `Get-NetAdapterAdvancedProperty` (Intel `EEELinkAdvertisement`, Realtek `*EEE`); Linux `ethtool --show-eee` |
| RTT p50/p99, jitter, loss | `--count` UDP echoes (default 1000), one outstanding; jitter = mean change between consecutive round trips |
| throughput up/down | one TCP stream per direction for `--seconds` (default 5), counted by the receiver |
| MTU | ping with don't-fragment, binary search below the card's MTU |

Results are kept as JSON in `<Fieldkit>/state/linkcheck/` (or `LINKCHECK_RESULTS`).
